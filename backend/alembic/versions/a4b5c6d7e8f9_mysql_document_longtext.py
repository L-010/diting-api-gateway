"""扩大 OpenAPI 文档字段，兼容 MySQL 的 UTF-8 字节长度。"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT


revision: str = "a4b5c6d7e8f9"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name in {"mysql", "mariadb"}:
        op.execute("ALTER TABLE openapi_specs MODIFY document_json LONGTEXT NOT NULL")


def downgrade() -> None:
    if op.get_bind().dialect.name in {"mysql", "mariadb"}:
        op.execute("ALTER TABLE openapi_specs MODIFY document_json TEXT NOT NULL")
