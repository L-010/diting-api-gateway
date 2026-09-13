"""记录用户当前停用状态详情。

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("disabled_by_user_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("disable_reason", sa.Text(), nullable=False, server_default=sa.text("('')")))
        batch_op.create_index(op.f("ix_users_disabled_by_user_id"), ["disabled_by_user_id"], unique=False)
        batch_op.create_foreign_key("fk_users_disabled_by_user_id", "users", ["disabled_by_user_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("fk_users_disabled_by_user_id", type_="foreignkey")
        batch_op.drop_index(op.f("ix_users_disabled_by_user_id"))
        batch_op.drop_column("disable_reason")
        batch_op.drop_column("disabled_by_user_id")
        batch_op.drop_column("disabled_at")


