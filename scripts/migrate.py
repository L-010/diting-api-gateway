"""执行项目数据库迁移，供本地初始化和部署流水线调用。"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))
sys.path.insert(0, str(ROOT_DIR))


def main() -> int:
    from app.config import get_settings
    from scripts.release_audit.migration_precheck import ensure_no_username_conflicts

    ensure_no_username_conflicts(get_settings().database_url)
    config = Config(str(ROOT_DIR / "alembic.ini"))
    command.upgrade(config, "head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
