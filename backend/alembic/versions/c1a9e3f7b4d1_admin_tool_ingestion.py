"""admin_tool_ingestion

Revision ID: c1a9e3f7b4d1
Revises: 754f34fb52cb
Create Date: 2026-07-23 10:20:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c1a9e3f7b4d1"
down_revision = "754f34fb52cb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 管理员侧工具接入需要保留草稿、导入批次、风险治理和同步差异信息。
    with op.batch_alter_table("tools", schema=None) as batch_op:
        batch_op.add_column(sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"))
        batch_op.add_column(sa.Column("adapter_kind", sa.String(length=32), nullable=False, server_default="generic"))
        batch_op.add_column(sa.Column("default_gateway_prefix", sa.String(length=128), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("created_by_user_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f("ix_tools_created_by_user_id"), ["created_by_user_id"], unique=False)
        batch_op.create_foreign_key("fk_tools_created_by_user_id_users", "users", ["created_by_user_id"], ["id"])

    with op.batch_alter_table("tool_upstreams", schema=None) as batch_op:
        batch_op.add_column(sa.Column("auth_type", sa.String(length=32), nullable=False, server_default="bearer"))
        batch_op.add_column(sa.Column("token_label", sa.String(length=128), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("health_path", sa.String(length=128), nullable=False, server_default="/health"))
        batch_op.add_column(sa.Column("environment_mode", sa.String(length=32), nullable=False, server_default="single"))
        batch_op.add_column(sa.Column("environments_json", sa.Text(), nullable=False, server_default=sa.text("('[]')")))
        batch_op.add_column(sa.Column("discovery_type", sa.String(length=64), nullable=False, server_default="fixed_url"))
        batch_op.add_column(sa.Column("network_zone", sa.String(length=64), nullable=False, server_default="local"))
        batch_op.add_column(sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.text("1")))
        batch_op.add_column(sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))

    with op.batch_alter_table("openapi_specs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_type", sa.String(length=32), nullable=False, server_default="upstream"))
        batch_op.add_column(sa.Column("source_url", sa.String(length=512), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("title", sa.String(length=255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("summary_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")))

    op.create_table(
        "tool_import_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tool_id", sa.String(length=36), nullable=False),
        sa.Column("spec_id", sa.String(length=36), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.String(length=512), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("openapi_version", sa.String(length=32), nullable=False),
        sa.Column("total_operations", sa.Integer(), nullable=False),
        sa.Column("selected_operations", sa.Integer(), nullable=False),
        sa.Column("added_count", sa.Integer(), nullable=False),
        sa.Column("changed_count", sa.Integer(), nullable=False),
        sa.Column("unchanged_count", sa.Integer(), nullable=False),
        sa.Column("excluded_count", sa.Integer(), nullable=False),
        sa.Column("blocked_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["spec_id"], ["openapi_specs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tool_import_batches", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tool_import_batches_created_by_user_id"), ["created_by_user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_tool_import_batches_spec_id"), ["spec_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_tool_import_batches_tool_id"), ["tool_id"], unique=False)

    with op.batch_alter_table("api_endpoints", schema=None) as batch_op:
        batch_op.add_column(sa.Column("gateway_path", sa.String(length=512), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("group_name", sa.String(length=128), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("status", sa.String(length=24), nullable=False, server_default="candidate"))
        batch_op.add_column(sa.Column("import_action", sa.String(length=24), nullable=False, server_default="new"))
        batch_op.add_column(sa.Column("risk_level", sa.String(length=24), nullable=False, server_default="info"))
        batch_op.add_column(sa.Column("risk_flags_json", sa.Text(), nullable=False, server_default=sa.text("('[]')")))
        batch_op.add_column(sa.Column("governance_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")))
        batch_op.add_column(sa.Column("parameters_json", sa.Text(), nullable=False, server_default=sa.text("('[]')")))
        batch_op.add_column(sa.Column("request_body_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")))
        batch_op.add_column(sa.Column("responses_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")))
        batch_op.add_column(sa.Column("source_spec_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("exclusion_reason", sa.Text(), nullable=False, server_default=sa.text("('')")))
        batch_op.add_column(sa.Column("public_description", sa.Text(), nullable=False, server_default=sa.text("('')")))
        batch_op.add_column(sa.Column("test_result_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f("ix_api_endpoints_source_spec_id"), ["source_spec_id"], unique=False)
        batch_op.create_foreign_key("fk_api_endpoints_source_spec_id_openapi_specs", "openapi_specs", ["source_spec_id"], ["id"], ondelete="SET NULL")

    op.create_table(
        "openapi_diff_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("tool_id", sa.String(length=36), nullable=False),
        sa.Column("endpoint_id", sa.String(length=36), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("upstream_path", sa.String(length=512), nullable=False),
        sa.Column("gateway_path", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.String(length=255), nullable=False),
        sa.Column("diff_type", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=24), nullable=False),
        sa.Column("risk_flags_json", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["tool_import_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["endpoint_id"], ["api_endpoints.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("openapi_diff_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_openapi_diff_items_batch_id"), ["batch_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_openapi_diff_items_endpoint_id"), ["endpoint_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_openapi_diff_items_tool_id"), ["tool_id"], unique=False)

    with op.batch_alter_table("published_routes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("published_by_user_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("publish_reason", sa.Text(), nullable=False, server_default=sa.text("('')")))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f("ix_published_routes_published_by_user_id"), ["published_by_user_id"], unique=False)
        batch_op.create_foreign_key("fk_published_routes_published_by_user_id_users", "users", ["published_by_user_id"], ["id"])


def downgrade() -> None:
    # 本地开发回滚只移除本次管理员工具接入增强。
    with op.batch_alter_table("published_routes", schema=None) as batch_op:
        batch_op.drop_constraint("fk_published_routes_published_by_user_id_users", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_published_routes_published_by_user_id"))
        batch_op.drop_column("updated_at")
        batch_op.drop_column("publish_reason")
        batch_op.drop_column("published_by_user_id")

    with op.batch_alter_table("openapi_diff_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_openapi_diff_items_tool_id"))
        batch_op.drop_index(batch_op.f("ix_openapi_diff_items_endpoint_id"))
        batch_op.drop_index(batch_op.f("ix_openapi_diff_items_batch_id"))
    op.drop_table("openapi_diff_items")

    with op.batch_alter_table("api_endpoints", schema=None) as batch_op:
        batch_op.drop_constraint("fk_api_endpoints_source_spec_id_openapi_specs", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_api_endpoints_source_spec_id"))
        batch_op.drop_column("updated_at")
        batch_op.drop_column("test_result_json")
        batch_op.drop_column("public_description")
        batch_op.drop_column("exclusion_reason")
        batch_op.drop_column("source_spec_id")
        batch_op.drop_column("responses_json")
        batch_op.drop_column("request_body_json")
        batch_op.drop_column("parameters_json")
        batch_op.drop_column("governance_json")
        batch_op.drop_column("risk_flags_json")
        batch_op.drop_column("risk_level")
        batch_op.drop_column("import_action")
        batch_op.drop_column("status")
        batch_op.drop_column("group_name")
        batch_op.drop_column("gateway_path")

    with op.batch_alter_table("tool_import_batches", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tool_import_batches_tool_id"))
        batch_op.drop_index(batch_op.f("ix_tool_import_batches_spec_id"))
        batch_op.drop_index(batch_op.f("ix_tool_import_batches_created_by_user_id"))
    op.drop_table("tool_import_batches")

    with op.batch_alter_table("openapi_specs", schema=None) as batch_op:
        batch_op.drop_column("summary_json")
        batch_op.drop_column("title")
        batch_op.drop_column("source_url")
        batch_op.drop_column("source_type")

    with op.batch_alter_table("tool_upstreams", schema=None) as batch_op:
        batch_op.drop_column("retry_count")
        batch_op.drop_column("verify_tls")
        batch_op.drop_column("network_zone")
        batch_op.drop_column("discovery_type")
        batch_op.drop_column("environments_json")
        batch_op.drop_column("environment_mode")
        batch_op.drop_column("health_path")
        batch_op.drop_column("token_label")
        batch_op.drop_column("auth_type")

    with op.batch_alter_table("tools", schema=None) as batch_op:
        batch_op.drop_constraint("fk_tools_created_by_user_id_users", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_tools_created_by_user_id"))
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_by_user_id")
        batch_op.drop_column("default_gateway_prefix")
        batch_op.drop_column("adapter_kind")
        batch_op.drop_column("status")


