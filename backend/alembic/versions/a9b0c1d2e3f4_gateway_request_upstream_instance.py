"""gateway_request_upstream_instance

Revision ID: a9b0c1d2e3f4
Revises: f7b8c9d0e1a2
Create Date: 2026-07-26 18:20:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a9b0c1d2e3f4"
down_revision = "f7b8c9d0e1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 记录 Gateway 实际命中的上游实例，便于管理员按用户、工具和后端实例追踪调用结果。
    with op.batch_alter_table("gateway_requests", schema=None) as batch_op:
        batch_op.add_column(sa.Column("upstream_instance_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("upstream_environment", sa.String(length=32), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("upstream_instance_name", sa.String(length=80), nullable=False, server_default=""))
        batch_op.create_index(batch_op.f("ix_gateway_requests_upstream_instance_id"), ["upstream_instance_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_gateway_requests_upstream_instance_id_tool_upstream_instances",
            "tool_upstream_instances",
            ["upstream_instance_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("gateway_requests", schema=None) as batch_op:
        batch_op.drop_constraint("fk_gateway_requests_upstream_instance_id_tool_upstream_instances", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_gateway_requests_upstream_instance_id"))
        batch_op.drop_column("upstream_instance_name")
        batch_op.drop_column("upstream_environment")
        batch_op.drop_column("upstream_instance_id")
