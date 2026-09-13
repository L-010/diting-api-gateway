"""gateway_monitoring

Revision ID: e6a1b2c3d4e5
Revises: d4f1a2b3c4d5
Create Date: 2026-07-26 16:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e6a1b2c3d4e5"
down_revision = "d4f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 调用记录升级为运营监控数据源；全部字段保持可空或默认值，兼容已有 MVP 数据。
    with op.batch_alter_table("gateway_requests", schema=None) as batch_op:
        batch_op.add_column(sa.Column("endpoint_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("published_route_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("tool_slug", sa.String(length=64), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("upstream_path", sa.String(length=512), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("redacted_query_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")))
        batch_op.add_column(sa.Column("upstream_status_code", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("request_bytes", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("response_bytes", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("client_ip_hash", sa.String(length=64), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("user_agent_hash", sa.String(length=64), nullable=False, server_default=""))
        batch_op.create_index(batch_op.f("ix_gateway_requests_endpoint_id"), ["endpoint_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_gateway_requests_published_route_id"), ["published_route_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_gateway_requests_tool_slug"), ["tool_slug"], unique=False)
        batch_op.create_foreign_key("fk_gateway_requests_endpoint_id_api_endpoints", "api_endpoints", ["endpoint_id"], ["id"])
        batch_op.create_foreign_key("fk_gateway_requests_published_route_id_published_routes", "published_routes", ["published_route_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("gateway_requests", schema=None) as batch_op:
        batch_op.drop_constraint("fk_gateway_requests_published_route_id_published_routes", type_="foreignkey")
        batch_op.drop_constraint("fk_gateway_requests_endpoint_id_api_endpoints", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_gateway_requests_tool_slug"))
        batch_op.drop_index(batch_op.f("ix_gateway_requests_published_route_id"))
        batch_op.drop_index(batch_op.f("ix_gateway_requests_endpoint_id"))
        batch_op.drop_column("user_agent_hash")
        batch_op.drop_column("client_ip_hash")
        batch_op.drop_column("response_bytes")
        batch_op.drop_column("request_bytes")
        batch_op.drop_column("upstream_status_code")
        batch_op.drop_column("redacted_query_json")
        batch_op.drop_column("upstream_path")
        batch_op.drop_column("tool_slug")
        batch_op.drop_column("published_route_id")
        batch_op.drop_column("endpoint_id")


