"""生产目标迁移执行器；默认拒绝执行，且不提供清库或删除能力。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON = next(
    (candidate for candidate in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python") if candidate.exists()),
    ROOT / ".venv" / "Scripts" / "python.exe",
)
EXPECTED_HEAD = os.getenv("EXPECTED_ALEMBIC_HEAD", "a6b7c8d9e0f1")


def main() -> int:
    """只有显式确认、备份验证和目标环境标记同时满足时才调用 upgrade head。"""

    required = {
        "target": os.getenv("RELEASE_TARGET_ENV") == "production",
        "ack": os.getenv("RELEASE_VALIDATION_ACK") == "I_UNDERSTAND_TARGET_ENV",
        "backup": os.getenv("BACKUP_VERIFIED", "").lower() == "true" and bool(os.getenv("BACKUP_VERIFICATION_ID", "").strip()),
        "approval": os.getenv("MIGRATION_CHANGE_APPROVED", "").lower() == "true",
        "execute_flag": "--execute" in sys.argv[1:],
        "venv": PYTHON.exists(),
    }
    for name, ok in required.items():
        print(f"{name}={'ok' if ok else 'failed'}")
    if not all(required.values()):
        print("migration_execute=refused")
        return 1

    result = subprocess.run(
        [str(PYTHON), "scripts/migrate.py"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )
    print(f"upgrade_exit={result.returncode}")
    if result.returncode != 0:
        print("migration_execute=failed reason=upgrade_failed")
        return result.returncode or 1

    current = subprocess.run(
        [str(PYTHON), "-m", "alembic", "current"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        env=os.environ.copy(),
    )
    current_output = (current.stdout or b"") + (current.stderr or b"")
    head_ok = current.returncode == 0 and EXPECTED_HEAD in current_output.decode("utf-8", errors="replace")
    print(f"post_migration_head={'ok' if head_ok else 'failed'}")
    print(f"migration_execute={'passed' if head_ok else 'failed'}")
    return 0 if head_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
