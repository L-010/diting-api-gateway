"""增加公开网关地址、工具配置版本和按工具隔离的限流桶。

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("platform_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("public_gateway_base_url", sa.String(length=512), nullable=False, server_default=""))

    with op.batch_alter_table("tool_upstreams", schema=None) as batch_op:
        batch_op.add_column(sa.Column("config_revision", sa.Integer(), nullable=False, server_default="1"))

    op.create_table(
        "tool_config_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tool_id", sa.String(length=36), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("token_ciphertext", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column("changed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tool_id", "config_revision", name="uq_tool_config_revision"),
    )
    op.create_index("ix_tool_config_revisions_tool_id", "tool_config_revisions", ["tool_id"], unique=False)
    op.create_index("ix_tool_config_revisions_changed_by_user_id", "tool_config_revisions", ["changed_by_user_id"], unique=False)

    # 旧限流桶只有一分钟寿命且无法判断所属工具，迁移时清空比错误归属更安全。
    op.drop_index("ix_rate_limit_buckets_window_start", table_name="rate_limit_buckets")
    op.drop_index("ix_rate_limit_buckets_api_key_id", table_name="rate_limit_buckets")
    op.drop_table("rate_limit_buckets")
    op.create_table(
        "rate_limit_buckets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("api_key_id", sa.String(length=36), nullable=False),
        sa.Column("tool_id", sa.String(length=36), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key_id", "tool_id", "window_start", name="uq_rate_limit_window"),
    )
    op.create_index("ix_rate_limit_buckets_api_key_id", "rate_limit_buckets", ["api_key_id"], unique=False)
    op.create_index("ix_rate_limit_buckets_tool_id", "rate_limit_buckets", ["tool_id"], unique=False)
    op.create_index("ix_rate_limit_buckets_window_start", "rate_limit_buckets", ["window_start"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_rate_limit_buckets_window_start", table_name="rate_limit_buckets")
    op.drop_index("ix_rate_limit_buckets_tool_id", table_name="rate_limit_buckets")
    op.drop_index("ix_rate_limit_buckets_api_key_id", table_name="rate_limit_buckets")
    op.drop_table("rate_limit_buckets")
    op.create_table(
        "rate_limit_buckets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("api_key_id", sa.String(length=36), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key_id", "window_start", name="uq_rate_limit_window"),
    )
    op.create_index("ix_rate_limit_buckets_api_key_id", "rate_limit_buckets", ["api_key_id"], unique=False)
    op.create_index("ix_rate_limit_buckets_window_start", "rate_limit_buckets", ["window_start"], unique=False)

    op.drop_index("ix_tool_config_revisions_changed_by_user_id", table_name="tool_config_revisions")
    op.drop_index("ix_tool_config_revisions_tool_id", table_name="tool_config_revisions")
    op.drop_table("tool_config_revisions")

    with op.batch_alter_table("tool_upstreams", schema=None) as batch_op:
        batch_op.drop_column("config_revision")
    with op.batch_alter_table("platform_settings", schema=None) as batch_op:
        batch_op.drop_column("public_gateway_base_url")


