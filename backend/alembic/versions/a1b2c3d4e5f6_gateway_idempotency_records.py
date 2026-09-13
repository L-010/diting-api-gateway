"""新增 Gateway 幂等请求持久化记录。

Revision ID: a1b2c3d4e5f6
Revises: f9a0b1c2d3e4
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("api_key_id", sa.String(length=36), nullable=False),
        sa.Column("published_route_id", sa.String(length=36), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="processing"),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body_ciphertext", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column("response_headers_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["published_route_id"], ["published_routes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "api_key_id", "published_route_id", "key_hash", name="uq_idempotency_scope_key"),
    )
    op.create_index("ix_idempotency_records_api_key_id", "idempotency_records", ["api_key_id"], unique=False)
    op.create_index("ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"], unique=False)
    op.create_index("ix_idempotency_records_published_route_id", "idempotency_records", ["published_route_id"], unique=False)
    op.create_index("ix_idempotency_records_user_id", "idempotency_records", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_idempotency_records_user_id", table_name="idempotency_records")
    op.drop_index("ix_idempotency_records_published_route_id", table_name="idempotency_records")
    op.drop_index("ix_idempotency_records_expires_at", table_name="idempotency_records")
    op.drop_index("ix_idempotency_records_api_key_id", table_name="idempotency_records")
    op.drop_table("idempotency_records")


