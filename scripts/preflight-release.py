"""执行不输出敏感值的发布前配置和迁移头检查。"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))


def main() -> int:
    from app.config import get_settings

    settings = get_settings()
    settings.validate_runtime()
    config = Config(str(ROOT_DIR / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if heads != ["a6b7c8d9e0f1"]:
        raise RuntimeError(f"迁移必须保持单一已知 head，当前 head 数量: {len(heads)}")
    print("发布前检查通过：安全配置存在，迁移链为单一 head。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
