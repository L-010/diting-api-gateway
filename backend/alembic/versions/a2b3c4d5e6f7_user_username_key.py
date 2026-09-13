"""为用户名增加大小写无关的唯一持久化键。

Revision ID: a2b3c4d5e6f7
Revises: a1b2c3d4e5f6
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a2b3c4d5e6f7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("username_key", sa.String(length=64), nullable=False, server_default=""),
    )
    op.execute(sa.text("UPDATE users SET username_key = lower(trim(username))"))
    op.create_index("uq_users_username_key", "users", ["username_key"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_username_key", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("username_key")
