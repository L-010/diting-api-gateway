"""tool_upstream_instances

Revision ID: f7b8c9d0e1a2
Revises: e6a1b2c3d4e5
Create Date: 2026-07-26 16:30:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "f7b8c9d0e1a2"
down_revision = "e6a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_upstream_instances",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tool_id", sa.String(length=36), nullable=False),
        sa.Column("upstream_config_id", sa.String(length=36), nullable=False),
        sa.Column("environment_name", sa.String(length=32), nullable=False, server_default="prod"),
        sa.Column("instance_name", sa.String(length=80), nullable=False, server_default="default"),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("last_health_status_code", sa.Integer(), nullable=True),
        sa.Column("last_health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["upstream_config_id"], ["tool_upstreams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tool_id", "environment_name", "instance_name", name="uq_tool_upstream_instance"),
    )
    op.create_index(op.f("ix_tool_upstream_instances_tool_id"), "tool_upstream_instances", ["tool_id"], unique=False)
    op.create_index(op.f("ix_tool_upstream_instances_upstream_config_id"), "tool_upstream_instances", ["upstream_config_id"], unique=False)
    op.create_index(op.f("ix_tool_upstream_instances_environment_name"), "tool_upstream_instances", ["environment_name"], unique=False)

    # 迁移旧单上游配置为 prod/default 实例，保证现有本地工具零改动可继续调用。
    bind = op.get_bind()
    identifier_expression = "UUID()" if bind.dialect.name in {"mysql", "mariadb"} else "lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-' || lower(hex(randomblob(2))) || '-' || lower(hex(randomblob(2))) || '-' || lower(hex(randomblob(6)))"
    op.execute(
        f"""
        INSERT INTO tool_upstream_instances (
            id, tool_id, upstream_config_id, environment_name, instance_name, base_url,
            priority, is_enabled, health_status, failure_count, last_error, created_at, updated_at
        )
        SELECT
            {identifier_expression},
            tool_id, id, 'prod', 'default', base_url, 100, 1, 'unknown', 0, '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM tool_upstreams
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tool_upstream_instances_environment_name"), table_name="tool_upstream_instances")
    op.drop_index(op.f("ix_tool_upstream_instances_upstream_config_id"), table_name="tool_upstream_instances")
    op.drop_index(op.f("ix_tool_upstream_instances_tool_id"), table_name="tool_upstream_instances")
    op.drop_table("tool_upstream_instances")


