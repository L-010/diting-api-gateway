"""增加任务来源请求和文件同步进程心跳。

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_resources", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_request_id", sa.String(length=36), nullable=True))
        batch_op.create_index("ix_external_resources_source_request_id", ["source_request_id"], unique=False)

    op.create_table(
        "file_worker_heartbeats",
        sa.Column("instance_id", sa.String(length=36), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_jobs", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.PrimaryKeyConstraint("instance_id"),
    )
    op.create_index("ix_file_worker_heartbeats_last_seen_at", "file_worker_heartbeats", ["last_seen_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_file_worker_heartbeats_last_seen_at", table_name="file_worker_heartbeats")
    op.drop_table("file_worker_heartbeats")
    with op.batch_alter_table("external_resources", schema=None) as batch_op:
        batch_op.drop_index("ix_external_resources_source_request_id")
        batch_op.drop_column("source_request_id")


