"""developer_platform_users

Revision ID: d4f1a2b3c4d5
Revises: c1a9e3f7b4d1
Create Date: 2026-07-26 10:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "d4f1a2b3c4d5"
down_revision = "c1a9e3f7b4d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 开发者平台化后，公开注册用户先进入 pending，已有本地用户保持 approved。
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("approval_status", sa.String(length=24), nullable=False, server_default="approved"))
        batch_op.add_column(sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("approved_by_user_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("rejection_reason", sa.Text(), nullable=False, server_default=sa.text("('')")))
        batch_op.add_column(sa.Column("registration_note", sa.Text(), nullable=False, server_default=sa.text("('')")))
        batch_op.create_index(batch_op.f("ix_users_approved_by_user_id"), ["approved_by_user_id"], unique=False)
        batch_op.create_foreign_key("fk_users_approved_by_user_id_users", "users", ["approved_by_user_id"], ["id"])

    # 产品口径调整为“一个全局 Key 调全部已发布工具”，旧数据直接升级为全局 scope。
    op.execute("UPDATE api_keys SET scopes = '*' WHERE scopes IS NULL OR scopes = '' OR scopes IN ('tomodd:*', 'tool:tomodd:*,tomodd:*')")
    op.execute("UPDATE published_routes SET scopes = '*' WHERE scopes IS NULL OR scopes = '' OR scopes IN ('tomodd:*', 'tool:tomodd:*,tomodd:*')")


def downgrade() -> None:
    op.execute("UPDATE published_routes SET scopes = 'tomodd:*' WHERE scopes = '*'")
    op.execute("UPDATE api_keys SET scopes = 'tomodd:*' WHERE scopes = '*'")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("fk_users_approved_by_user_id_users", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_users_approved_by_user_id"))
        batch_op.drop_column("registration_note")
        batch_op.drop_column("rejection_reason")
        batch_op.drop_column("rejected_at")
        batch_op.drop_column("approved_at")
        batch_op.drop_column("approved_by_user_id")
        batch_op.drop_column("registered_at")
        batch_op.drop_column("approval_status")


