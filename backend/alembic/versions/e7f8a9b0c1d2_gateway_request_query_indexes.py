"""增加用户调用记录常用组合索引。

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
"""

from collections.abc import Sequence

from alembic import op


revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_gateway_requests_user_created_at", "gateway_requests", ["user_id", "created_at"], unique=False)
    op.create_index("ix_gateway_requests_api_key_created_at", "gateway_requests", ["api_key_id", "created_at"], unique=False)
    op.create_index("ix_gateway_requests_status_created_at", "gateway_requests", ["status_code", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_gateway_requests_status_created_at", table_name="gateway_requests")
    op.drop_index("ix_gateway_requests_api_key_created_at", table_name="gateway_requests")
    op.drop_index("ix_gateway_requests_user_created_at", table_name="gateway_requests")
