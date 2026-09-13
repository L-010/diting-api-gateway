"""以 Unicode 规范化规则重建用户名唯一键。

Revision ID: a3b4c5d6e7f8
Revises: a2b3c4d5e6f7
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a3b4c5d6e7f8"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


def _username_key(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _has_conflicts(rows: list[tuple[object, object]]) -> bool:
    keys: dict[str, int] = defaultdict(int)
    for _, username in rows:
        key = _username_key(username)
        if not key or len(key) > 64 or _CONTROL_CHARACTER.search(str(username or "")):
            return True
        keys[key] += 1
    return any(count > 1 for count in keys.values())


def upgrade() -> None:
    """先验证再写入，直接执行迁移遇到冲突时也不会静默覆盖用户名。"""

    bind = op.get_bind()
    rows = list(bind.execute(sa.text("SELECT id, username FROM users")))
    if _has_conflicts(rows):
        raise RuntimeError("检测到用户名规范化冲突；请先运行迁移前检查并完成业务决策")
    op.drop_index("uq_users_username_key", table_name="users")
    for user_id, username in rows:
        bind.execute(
            sa.text("UPDATE users SET username_key = :username_key WHERE id = :user_id"),
            {"username_key": _username_key(username), "user_id": user_id},
        )
    op.create_index("uq_users_username_key", "users", ["username_key"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_username_key", table_name="users")
    op.execute(sa.text("UPDATE users SET username_key = lower(trim(username))"))
    op.create_index("uq_users_username_key", "users", ["username_key"], unique=True)
