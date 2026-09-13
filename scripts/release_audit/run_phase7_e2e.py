"""在临时数据库、Mock 上游和本地浏览器中运行第七阶段 E2E。"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
NODE = shutil.which("node.exe") or shutil.which("node")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _StubHandler(BaseHTTPRequestHandler):
    """只返回合成 OpenAPI、Gateway 和文件响应，不记录请求头。"""

    def _reply(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/openapi.json":
            document = {
                "openapi": "3.0.3",
                "info": {"title": "本地演示工具", "version": "1.0"},
                "paths": {
                    "/echo": {"post": {"operationId": "echo", "summary": "回显", "responses": {"201": {"description": "ok"}}}},
                },
            }
            self._reply(200, json.dumps(document).encode("utf-8"))
            return
        if self.path.startswith("/files/"):
            self._reply(200, b"phase7 synthetic file\n", "text/plain")
            return
        self._reply(404, b'{"detail":"not found"}')

    def do_POST(self) -> None:  # noqa: N802
        if self.path in {"/echo", "/idempotent"}:
            self._reply(201, b'{"ok":true}')
            return
        if self.path == "/fail":
            self._reply(500, b'{"error":"synthetic upstream failure"}')
            return
        self._reply(404, b'{"detail":"not found"}')

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path.startswith("/files/"):
            self._reply(204, b"")
            return
        self._reply(404, b'{"detail":"not found"}')

    def log_message(self, format: str, *args: object) -> None:
        return


def _wait(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("本地服务启动失败")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if 200 <= response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.25)
    raise RuntimeError(f"本地服务未在时限内就绪：{url}")


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _seed(database_url: str, upstream_url: str, user_password: str, admin_password: str, scope_key: str) -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.database import SessionLocal
    from app.models import (
        ApiEndpoint,
        ApiKey,
        ApprovalStatus,
        ExternalResource,
        KeyStatus,
        PublishedRoute,
        RemoteFile,
        Role,
        RoleName,
        Tool,
        ToolIntegrationRoute,
        ToolStatus,
        ToolUpstream,
        User,
        UserRole,
    )
    from app.services.remote_files import ensure_storage_endpoint
    from app.services.security import hash_api_key, hash_password

    with SessionLocal() as session:
        user_role = Role(name=RoleName.USER.value, description="普通用户")
        admin_role = Role(name=RoleName.ADMIN.value, description="管理员")
        admin = User(
            username="phase7-admin",
            password_hash=hash_password(admin_password),
            is_active=True,
            approval_status=ApprovalStatus.APPROVED.value,
            must_change_password=False,
        )
        user = User(
            username="phase7-user",
            password_hash=hash_password(user_password),
            is_active=True,
            approval_status=ApprovalStatus.APPROVED.value,
            must_change_password=False,
        )
        tool = Tool(
            slug="specdemo",
            name="本地演示工具",
            status=ToolStatus.ACTIVE.value,
            is_enabled=True,
            storage_risk_acknowledged=True,
        )
        gateway_tool = Tool(
            slug="demo",
            name="本地 Gateway 工具",
            status=ToolStatus.ACTIVE.value,
            is_enabled=True,
            storage_risk_acknowledged=True,
        )
        session.add_all([user_role, admin_role, admin, user, tool, gateway_tool])
        session.flush()
        session.add_all([UserRole(user_id=admin.id, role_id=admin_role.id), UserRole(user_id=user.id, role_id=user_role.id)])
        upstream = ToolUpstream(tool_id=tool.id, base_url=upstream_url, auth_type="none")
        gateway_upstream = ToolUpstream(tool_id=gateway_tool.id, base_url=upstream_url, auth_type="none")
        endpoint = ApiEndpoint(tool_id=gateway_tool.id, method="POST", upstream_path="/idempotent", status="published")
        failed_endpoint = ApiEndpoint(tool_id=gateway_tool.id, method="POST", upstream_path="/fail", status="published")
        session.add_all([upstream, gateway_upstream, endpoint, failed_endpoint])
        session.flush()
        session.add(
            PublishedRoute(
                tool_id=gateway_tool.id,
                endpoint_id=endpoint.id,
                method="POST",
                gateway_path="/idempotent",
                upstream_path="/idempotent",
                access_mode="authenticated",
                require_idempotency_key=True,
            )
        )
        session.add(
            PublishedRoute(
                tool_id=gateway_tool.id,
                endpoint_id=failed_endpoint.id,
                method="POST",
                gateway_path="/fail",
                upstream_path="/fail",
                access_mode="authenticated",
            )
        )
        session.add(ApiKey(user_id=user.id, label="范围拒绝验证", prefix=scope_key[:16], secret_hash=hash_api_key(scope_key), scopes="other:*", status=KeyStatus.ACTIVE.value))
        session.add(ToolIntegrationRoute(tool_id=gateway_tool.id, endpoint_id=None, capability="file_delete", method="DELETE", path_template="/files/{file_id}"))
        session.add(ToolIntegrationRoute(tool_id=gateway_tool.id, endpoint_id=None, capability="file_download", method="GET", path_template="/files/{file_id}"))
        session.flush()
        storage_endpoint = ensure_storage_endpoint(session, gateway_tool, gateway_upstream)
        session.add(
            ExternalResource(
                tool_id=gateway_tool.id,
                owner_user_id=user.id,
                kind="job",
                upstream_id="phase7-failed-task",
                status="failed",
                storage_endpoint_revision_id=storage_endpoint.id,
            )
        )
        session.add(
            RemoteFile(
                identity_key="phase7-file",
                tool_id=gateway_tool.id,
                owner_user_id=user.id,
                storage_endpoint_revision_id=storage_endpoint.id,
                upstream_file_id="phase7-result",
                file_name="phase7-result.txt",
                role="output",
                visibility="user",
                status="ready",
                content_type="text/plain",
                size_bytes=22,
            )
        )
        session.commit()
    from app.database import engine

    engine.dispose()


def main() -> int:
    if not PYTHON.exists() or not NODE:
        raise RuntimeError("缺少项目虚拟环境或本地 Node 命令")
    backend_port, frontend_port, stub_port = _free_port(), _free_port(), _free_port()
    user_password = f"Aa1{secrets.token_urlsafe(24)}"
    admin_password = f"Aa1{secrets.token_urlsafe(24)}"
    scope_key = f"agw_{secrets.token_urlsafe(32)}"
    with tempfile.TemporaryDirectory(prefix="phase7-e2e-") as temporary:
        database = Path(temporary) / "phase7-e2e.sqlite"
        environment = os.environ.copy()
        environment.update(
            {
                "DATABASE_URL": f"sqlite:///{database.as_posix()}",
                "APP_ENV": "test",
                "EMAIL_TEST_MODE": "true",
                "SMTP_HOST": "smtp.test.invalid",
                "SMTP_FROM": "gateway@example.test",
                "FRONTEND_ORIGIN": f"http://127.0.0.1:{frontend_port}",
                "BACKEND_ORIGIN": f"http://127.0.0.1:{backend_port}",
                "E2E_BASE_URL": f"http://127.0.0.1:{frontend_port}",
                "E2E_BACKEND_ORIGIN": f"http://127.0.0.1:{backend_port}",
                "E2E_USER_PASSWORD": user_password,
                "E2E_ADMIN_PASSWORD": admin_password,
                "E2E_SCOPE_KEY": scope_key,
                "PLAYWRIGHT_BROWSERS_PATH": "0",
                "NODE_PATH": str(ROOT / "frontend" / "node_modules"),
            }
        )
        migrated = subprocess.run([str(PYTHON), "scripts/migrate.py"], cwd=ROOT, env=environment, capture_output=True, check=False)
        if migrated.returncode:
            raise RuntimeError("临时数据库迁移失败")
        os.environ.update(environment)
        stub = ThreadingHTTPServer(("127.0.0.1", stub_port), _StubHandler)
        stub_thread = threading.Thread(target=stub.serve_forever, daemon=True)
        stub_thread.start()
        _seed(environment["DATABASE_URL"], f"http://127.0.0.1:{stub_port}", user_password, admin_password, scope_key)
        backend = frontend = None
        try:
            backend = subprocess.Popen(
                [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(backend_port)],
                cwd=ROOT / "backend",
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _wait(f"http://127.0.0.1:{backend_port}/livez", backend)
            frontend = subprocess.Popen(
                [str(NODE), str(ROOT / "frontend" / "node_modules" / "next" / "dist" / "bin" / "next"), "dev", "--hostname", "127.0.0.1", "--port", str(frontend_port)],
                cwd=ROOT / "frontend",
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _wait(f"http://127.0.0.1:{frontend_port}/login", frontend)
            result = subprocess.run(
                [str(NODE), str(ROOT / "frontend" / "node_modules" / "@playwright" / "test" / "cli.js"), "test", "--config", "playwright.config.ts"],
                cwd=ROOT / "frontend",
                env=environment,
                check=False,
            )
            return result.returncode
        finally:
            _stop(frontend)
            _stop(backend)
            stub.shutdown()
            stub.server_close()
            stub_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
