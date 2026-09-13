"""增加站点品牌与公开注册开关。"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "a6b7c8d9e0f1"
down_revision: str | None = "a5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("platform_settings") as batch:
        batch.add_column(sa.Column("site_name", sa.String(length=128), nullable=False, server_default="API Gateway"))
        batch.add_column(sa.Column("site_subtitle", sa.String(length=255), nullable=False, server_default="开发者统一工作台"))
        batch.add_column(sa.Column("registration_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("brand_image_url", sa.String(length=512), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("platform_settings") as batch:
        batch.drop_column("brand_image_url")
        batch.drop_column("registration_enabled")
        batch.drop_column("site_subtitle")
        batch.drop_column("site_name")
