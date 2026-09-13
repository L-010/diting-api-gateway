"""第四阶段本机部署演练，只使用系统临时目录和合成配置。"""

from __future__ import annotations

import base64
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def request_status(url: str) -> int:
    """读取本机健康接口状态码。"""

    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except (OSError, urllib.error.URLError):
        return 0


def wait_status(url: str, expected: int = 200) -> int:
    """等待隔离服务出现预期状态，超时返回最后一次状态。"""

    status = 0
    for _ in range(30):
        status = request_status(url)
        if status == expected:
            return status
        time.sleep(0.25)
    return status


def stop_process(process: subprocess.Popen[bytes]) -> None:
    """只结束本脚本启动的进程树，避免触碰其它本机服务。"""

    if process.poll() is not None:
        return
    subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def process_diagnostic(path: Path) -> str:
    """仅提取启动日志末尾的非敏感诊断，避免输出环境变量。"""

    if not path.exists():
        return "log_missing"
    text = path.read_text(encoding="utf-8", errors="replace")[-1_000:]
    if "APP_ENCRYPTION_KEY 不是有效 Fernet 密钥" in text:
        return "invalid_fernet_key"
    if "缺少必要安全配置" in text:
        return "missing_required_config"
    if "无法加载 ASGI 应用" in text or "Could not import module" in text:
        return "asgi_import_failed"
    if "Address already in use" in text:
        return "port_in_use"
    last_line = next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")
    sanitized = re.sub(r"(?i)(app_secret_key|app_encryption_key|api_key_pepper|password|token)=\S+", r"\1=[REDACTED]", last_line)
    return f"startup_failed:{sanitized[:240]}" if sanitized else "log_empty"


def run(command: list[str], env: dict[str, str], *, stdout: int | object = subprocess.PIPE) -> subprocess.CompletedProcess[bytes]:
    """运行演练命令，不输出环境变量值。"""

    return subprocess.run(command, cwd=ROOT, env=env, check=False, stdout=stdout, stderr=subprocess.STDOUT)


def main() -> int:
    if not PYTHON.exists():
        print("python=missing")
        return 2
    temp_dir = Path(tempfile.mkdtemp(prefix="api-mvp-phase4-"))
    db_path = temp_dir / "rehearsal.db"
    dry_run_path = temp_dir / "migration-dry-run.sql"
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "production",
            "APP_SECRET_KEY": "phase4-synthetic-session-signing-key-000000000000",
            "APP_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"0" * 32).decode("ascii"),
            "API_KEY_PEPPER": "phase4-synthetic-api-key-pepper-000000000",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "ALLOWED_UPSTREAM_HOSTS": "127.0.0.1,localhost",
            "TOMODD_BASE_URL": "http://127.0.0.1:19091",
            "FRONTEND_ORIGIN": "http://127.0.0.1:3000",
            "AGW_HOST": "127.0.0.1",
            "AGW_PORT": "18081",
            "AGW_WORKERS": "1",
        }
    )
    first_server: subprocess.Popen[bytes] | None = None
    second_server: subprocess.Popen[bytes] | None = None
    first_log_handle = None
    second_log_handle = None
    try:
        preflight = run([str(PYTHON), "scripts/preflight-release.py"], environment)
        with dry_run_path.open("wb") as dry_run_output:
            dry_run = subprocess.run(
                [str(PYTHON), "-m", "alembic", "upgrade", "head", "--sql"],
                cwd=ROOT,
                env=environment,
                check=False,
                stdout=dry_run_output,
                stderr=subprocess.STDOUT,
            )
        migrate = run([str(PYTHON), "scripts/migrate.py"], environment)
        first_log_path = temp_dir / "first-server.log"
        first_log_handle = first_log_path.open("wb")
        first_server = subprocess.Popen(
            [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/run-backend-production.ps1"],
            cwd=ROOT,
            env=environment,
            stdout=first_log_handle,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        livez = wait_status("http://127.0.0.1:18081/livez")
        readyz = request_status("http://127.0.0.1:18081/readyz")
        docs = request_status("http://127.0.0.1:18081/docs")
        first_exit = first_server.poll()
        stop_process(first_server)
        first_server = None
        first_log_handle.close()
        first_log_handle = None
        first_diagnostic = process_diagnostic(first_log_path) if livez != 200 else "not_needed"

        with sqlite3.connect(db_path) as connection:
            connection.execute("INSERT INTO roles(name, description) VALUES (?, ?)", ("phase4_backup_sentinel", "synthetic"))
            connection.commit()
        backup_path = temp_dir / "rehearsal.backup.db"
        shutil.copy2(db_path, backup_path)
        with sqlite3.connect(db_path) as connection:
            connection.execute("INSERT INTO roles(name, description) VALUES (?, ?)", ("phase4_after_backup", "synthetic"))
            connection.commit()
        shutil.copy2(backup_path, db_path)
        with sqlite3.connect(db_path) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM roles")}
            migration_head = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        restored_sentinel = "phase4_backup_sentinel" in names
        restored_post_backup_absent = "phase4_after_backup" not in names

        second_log_path = temp_dir / "second-server.log"
        second_log_handle = second_log_path.open("wb")
        second_server = subprocess.Popen(
            [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/run-backend-production.ps1"],
            cwd=ROOT,
            env=environment,
            stdout=second_log_handle,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        restart_readyz = wait_status("http://127.0.0.1:18081/readyz")
        second_exit = second_server.poll()
        stop_process(second_server)
        second_server = None
        second_log_handle.close()
        second_log_handle = None
        second_diagnostic = process_diagnostic(second_log_path) if restart_readyz != 200 else "not_needed"

        missing_env = environment.copy()
        missing_env["APP_SECRET_KEY"] = ""
        missing_config = run([str(PYTHON), "scripts/preflight-release.py"], missing_env)
        print(
            "preflight_exit=%d dry_run_exit=%d dry_run_bytes=%d migrate_exit=%d livez=%d readyz=%d docs=%d "
            "first_server_exit=%s first_diagnostic=%s restored_sentinel=%s restored_post_backup_absent=%s migration_head=%s "
            "restart_readyz=%d second_server_exit=%s second_diagnostic=%s missing_config_exit=%d"
            % (
                preflight.returncode,
                dry_run.returncode,
                dry_run_path.stat().st_size,
                migrate.returncode,
                livez,
                readyz,
                docs,
                str(first_exit),
                first_diagnostic,
                str(restored_sentinel).lower(),
                str(restored_post_backup_absent).lower(),
                migration_head,
                restart_readyz,
                str(second_exit),
                second_diagnostic,
                missing_config.returncode,
            )
        )
        return 0 if all((preflight.returncode == 0, dry_run.returncode == 0, migrate.returncode == 0, livez == 200, readyz == 200, docs == 404, restored_sentinel, restored_post_backup_absent, migration_head == "a2b3c4d5e6f7", restart_readyz == 200, missing_config.returncode != 0)) else 1
    finally:
        if first_server is not None:
            stop_process(first_server)
        if second_server is not None:
            stop_process(second_server)
        if first_log_handle is not None:
            first_log_handle.close()
        if second_log_handle is not None:
            second_log_handle.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
