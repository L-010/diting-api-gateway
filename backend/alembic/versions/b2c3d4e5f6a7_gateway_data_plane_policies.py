"""gateway_data_plane_policies

Revision ID: b2c3d4e5f6a7
Revises: a9b0c1d2e3f4
Create Date: 2026-07-26 21:30:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 发布路由策略显式落库，Gateway 执行时不再只依赖全局默认值。
    with op.batch_alter_table("published_routes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("allowed_content_types_json", sa.Text(), nullable=False, server_default=sa.text("('[]')")))
        batch_op.add_column(sa.Column("max_request_bytes", sa.Integer(), nullable=False, server_default="104857600"))
        batch_op.add_column(sa.Column("max_response_bytes", sa.Integer(), nullable=False, server_default="1073741824"))
        batch_op.add_column(sa.Column("request_timeout_seconds", sa.Integer(), nullable=False, server_default="60"))
        batch_op.add_column(sa.Column("allow_stream_upload", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("allow_stream_download", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("requires_resource_adapter", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("require_idempotency_key", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("allow_retry", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("risk_level", sa.String(length=24), nullable=False, server_default="info"))
        batch_op.add_column(sa.Column("route_version", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("policy_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")))

    # API Key 生命周期补充禁用时间、操作者和原因；完整密钥仍不可恢复。
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.add_column(sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("disabled_by_user_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("disable_reason", sa.Text(), nullable=False, server_default=sa.text("('')")))
        batch_op.create_index(batch_op.f("ix_api_keys_disabled_by_user_id"), ["disabled_by_user_id"], unique=False)
        batch_op.create_foreign_key("fk_api_keys_disabled_by_user_id_users", "users", ["disabled_by_user_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.drop_constraint("fk_api_keys_disabled_by_user_id_users", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_api_keys_disabled_by_user_id"))
        batch_op.drop_column("disable_reason")
        batch_op.drop_column("disabled_by_user_id")
        batch_op.drop_column("disabled_at")

    with op.batch_alter_table("published_routes", schema=None) as batch_op:
        batch_op.drop_column("policy_json")
        batch_op.drop_column("route_version")
        batch_op.drop_column("risk_level")
        batch_op.drop_column("allow_retry")
        batch_op.drop_column("require_idempotency_key")
        batch_op.drop_column("requires_resource_adapter")
        batch_op.drop_column("allow_stream_download")
        batch_op.drop_column("allow_stream_upload")
        batch_op.drop_column("request_timeout_seconds")
        batch_op.drop_column("max_response_bytes")
        batch_op.drop_column("max_request_bytes")
        batch_op.drop_column("allowed_content_types_json")


