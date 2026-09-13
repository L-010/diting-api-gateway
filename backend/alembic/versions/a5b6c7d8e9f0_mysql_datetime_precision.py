"""为 MySQL 时间列启用微秒精度，避免迁移时截断审计和任务时间。"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "a5b6c7d8e9f0"
down_revision: str | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _alter_datetime_columns() -> None:
    bind = op.get_bind()
    if bind.dialect.name not in {"mysql", "mariadb"}:
        return
    inspector = inspect(bind)
    for table_name in inspector.get_table_names():
        if table_name == "alembic_version":
            continue
        for column in inspector.get_columns(table_name):
            column_type = column["type"]
            if not isinstance(column_type, sa.DateTime):
                continue
            nullable = "NULL" if column["nullable"] else "NOT NULL"
            op.execute(
                sa.text(
                    f"ALTER TABLE `{table_name}` MODIFY `{column['name']}` DATETIME(6) {nullable}"
                )
            )


def upgrade() -> None:
    _alter_datetime_columns()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name not in {"mysql", "mariadb"}:
        return
    inspector = inspect(bind)
    for table_name in inspector.get_table_names():
        if table_name == "alembic_version":
            continue
        for column in inspector.get_columns(table_name):
            if isinstance(column["type"], sa.DateTime):
                nullable = "NULL" if column["nullable"] else "NOT NULL"
                op.execute(sa.text(f"ALTER TABLE `{table_name}` MODIFY `{column['name']}` DATETIME {nullable}"))
