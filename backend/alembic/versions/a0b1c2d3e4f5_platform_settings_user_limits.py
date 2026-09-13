"""增加平台设置和用户级每分钟限流。

Revision ID: a0b1c2d3e4f5
Revises: f8a9b0c1d2e3
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a0b1c2d3e4f5"
down_revision: str | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="0")
        )

    op.create_table(
        "platform_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("smtp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("smtp_host", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("smtp_username", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("smtp_password_ciphertext", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column("smtp_from_email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("smtp_from_name", sa.String(length=128), nullable=False, server_default="API Gateway"),
        sa.Column("smtp_use_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("default_user_rate_limit_per_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_rate_limit_buckets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "window_start", name="uq_user_rate_limit_window"),
    )
    op.create_index("ix_user_rate_limit_buckets_user_id", "user_rate_limit_buckets", ["user_id"], unique=False)
    op.create_index("ix_user_rate_limit_buckets_window_start", "user_rate_limit_buckets", ["window_start"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_rate_limit_buckets_window_start", table_name="user_rate_limit_buckets")
    op.drop_index("ix_user_rate_limit_buckets_user_id", table_name="user_rate_limit_buckets")
    op.drop_table("user_rate_limit_buckets")
    op.drop_table("platform_settings")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("rate_limit_per_minute")


