"""补齐历史任务端点绑定和下载并发租约。

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_resources", schema=None) as batch_op:
        batch_op.add_column(sa.Column("storage_endpoint_revision_id", sa.String(length=36), nullable=True))
        batch_op.create_index("ix_external_resources_storage_endpoint_revision_id", ["storage_endpoint_revision_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_external_resources_storage_endpoint_revision_id",
            "tool_storage_endpoint_revisions",
            ["storage_endpoint_revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.execute(
        sa.text(
            """
            UPDATE external_resources
            SET storage_endpoint_revision_id = (
                SELECT endpoint.id
                FROM tool_storage_endpoint_revisions AS endpoint
                WHERE endpoint.tool_id = external_resources.tool_id
                ORDER BY endpoint.is_active DESC, endpoint.revision DESC
                LIMIT 1
            )
            WHERE kind = 'job' AND storage_endpoint_revision_id IS NULL
            """
        )
    )

    with op.batch_alter_table("remote_files", schema=None) as batch_op:
        batch_op.alter_column("storage_endpoint_revision_id", existing_type=sa.String(length=36), nullable=True)

    op.create_table(
        "file_download_slots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=36), nullable=False),
        sa.Column("slot_number", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["file_download_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_type", "scope_id", "slot_number", name="uq_file_download_scope_slot"),
    )
    op.create_index("ix_file_download_slots_event_id", "file_download_slots", ["event_id"], unique=False)
    op.create_index("ix_file_download_slots_scope_id", "file_download_slots", ["scope_id"], unique=False)
    op.create_index("ix_file_download_slots_expires_at", "file_download_slots", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_file_download_slots_expires_at", table_name="file_download_slots")
    op.drop_index("ix_file_download_slots_scope_id", table_name="file_download_slots")
    op.drop_index("ix_file_download_slots_event_id", table_name="file_download_slots")
    op.drop_table("file_download_slots")

    with op.batch_alter_table("remote_files", schema=None) as batch_op:
        batch_op.alter_column("storage_endpoint_revision_id", existing_type=sa.String(length=36), nullable=False)

    with op.batch_alter_table("external_resources", schema=None) as batch_op:
        batch_op.drop_constraint("fk_external_resources_storage_endpoint_revision_id", type_="foreignkey")
        batch_op.drop_index("ix_external_resources_storage_endpoint_revision_id")
        batch_op.drop_column("storage_endpoint_revision_id")
