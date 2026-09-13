"""unified_gateway_authorization

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-29 12:00:00.000000

"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


RESOURCE_MARKERS = ("dataset", "job", "task", "artifact", "file", "result", "log", "workspace", "project")


def _tomodd_rules(method: str, path: str) -> tuple[str, dict[str, object]]:
    method = method.upper()
    pre: list[dict[str, object]] = []
    post: list[dict[str, object]] = []
    list_filter: dict[str, object] | None = None
    if method == "GET" and path in {"/api/v1/datasets", "/api/v1/jobs"}:
        collection = "dataset" if path.endswith("datasets") else "job"
        list_filter = {
            "items_selectors": ["$", "$.items", f"$.{collection}s"],
            "id_selectors": [f"$.{collection}_id", "$.id"],
            "resource_kind": collection,
        }
    if path == "/api/v1/datasets" and method == "POST":
        post.append({"action": "register_owner", "resource_kind": "dataset", "source": "response_json", "selectors": ["$.dataset_id", "$.id"]})
    elif "{dataset_id}" in path:
        pre.append({"action": "require_owner", "resource_kind": "dataset", "source": "path", "selector": "dataset_id"})
        if method == "DELETE":
            post.append({"action": "mark_deleted", "resource_kind": "dataset", "source": "path", "selector": "dataset_id"})
    if path in {"/api/v1/jobs/pipeline", "/api/v1/jobs/phase-selection"} and method == "POST":
        pre.append({"action": "require_parent_owner", "resource_kind": "dataset", "source": "request_json", "selector": "$.dataset_id"})
        post.append({"action": "register_owner", "resource_kind": "job", "source": "response_json", "selectors": ["$.job_id", "$.id"], "parent": {"source": "request_json", "selector": "$.dataset_id"}})
    elif "{job_id}" in path:
        pre.append({"action": "require_owner", "resource_kind": "job", "source": "path", "selector": "job_id"})
        post.append({"action": "register_owner", "resource_kind": "job", "source": "path", "selector": "job_id", "optional": True})
        if "artifact" in path or "manifest" in path:
            post.append({"action": "register_owner", "resource_kind": "artifact", "source": "response_json", "selector": "$..artifact_id", "many": True, "optional": True, "parent": {"source": "path", "selector": "job_id"}})
    elif "{artifact_id}" in path:
        pre.append({"action": "require_owner", "resource_kind": "artifact", "source": "path", "selector": "artifact_id"})
    return ("owner" if pre or post or list_filter else "authenticated", {"version": 1, "pre_checks": pre, "post_actions": post, "list_filter": list_filter})


def upgrade() -> None:
    op.create_table(
        "endpoint_access_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tool_id", sa.String(length=36), nullable=False),
        sa.Column("endpoint_id", sa.String(length=36), nullable=False),
        sa.Column("access_mode", sa.String(length=32), nullable=False, server_default="authenticated"),
        sa.Column("rules_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="auto"),
        sa.Column("confirmed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["endpoint_id"], ["api_endpoints.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint_id"),
    )
    op.create_index(op.f("ix_endpoint_access_policies_tool_id"), "endpoint_access_policies", ["tool_id"], unique=False)
    op.create_index(op.f("ix_endpoint_access_policies_endpoint_id"), "endpoint_access_policies", ["endpoint_id"], unique=True)
    op.create_index(op.f("ix_endpoint_access_policies_confirmed_by_user_id"), "endpoint_access_policies", ["confirmed_by_user_id"], unique=False)

    with op.batch_alter_table("tool_upstreams", schema=None) as batch_op:
        batch_op.add_column(sa.Column("network_isolation_mode", sa.String(length=32), nullable=False, server_default="firewall_allowlist"))
        batch_op.add_column(sa.Column("network_isolation_note", sa.Text(), nullable=False, server_default=sa.text("('')")))
        batch_op.add_column(sa.Column("network_isolation_confirmed_by_user_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("network_isolation_confirmed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f("ix_tool_upstreams_network_isolation_confirmed_by_user_id"), ["network_isolation_confirmed_by_user_id"], unique=False)
        batch_op.create_foreign_key("fk_tool_upstreams_network_confirmed_user", "users", ["network_isolation_confirmed_by_user_id"], ["id"])

    with op.batch_alter_table("published_routes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("access_mode", sa.String(length=32), nullable=False, server_default="authenticated"))
        batch_op.add_column(sa.Column("resource_policy_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")))
        batch_op.add_column(sa.Column("policy_version", sa.Integer(), nullable=False, server_default="1"))

    with op.batch_alter_table("external_resources", schema=None) as batch_op:
        batch_op.alter_column("kind", existing_type=sa.String(length=16), type_=sa.String(length=64), existing_nullable=False)
        batch_op.add_column(sa.Column("source_route_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f("ix_external_resources_source_route_id"), ["source_route_id"], unique=False)
        batch_op.create_foreign_key("fk_external_resources_source_route", "published_routes", ["source_route_id"], ["id"], ondelete="SET NULL")

    connection = op.get_bind()
    now = datetime.now(timezone.utc)
    endpoints = connection.execute(sa.text("SELECT e.id, e.tool_id, e.method, e.upstream_path, e.risk_flags_json, t.adapter_kind FROM api_endpoints e JOIN tools t ON t.id = e.tool_id")).mappings().all()
    published = {row["endpoint_id"]: row for row in connection.execute(sa.text("SELECT endpoint_id, id FROM published_routes")).mappings().all()}
    for endpoint in endpoints:
        if endpoint["adapter_kind"] == "tomodd":
            mode, rules = _tomodd_rules(endpoint["method"], endpoint["upstream_path"])
        else:
            text = f"{endpoint['upstream_path']} {endpoint['risk_flags_json'] or ''}".lower()
            mode = "shared" if endpoint["id"] in published and any(marker in text for marker in RESOURCE_MARKERS) else "authenticated"
            rules = {"version": 1, "pre_checks": [], "post_actions": [], "list_filter": None}
            if mode == "shared":
                rules["migration_preserved"] = True
        policy_id = str(uuid.uuid4())
        connection.execute(
            sa.text("INSERT INTO endpoint_access_policies (id, tool_id, endpoint_id, access_mode, rules_json, source, confirmed_at, version, created_at, updated_at) VALUES (:id, :tool_id, :endpoint_id, :access_mode, :rules_json, 'auto', :confirmed_at, 1, :created_at, :updated_at)"),
            {"id": policy_id, "tool_id": endpoint["tool_id"], "endpoint_id": endpoint["id"], "access_mode": mode, "rules_json": json.dumps(rules, ensure_ascii=False), "confirmed_at": now, "created_at": now, "updated_at": now},
        )
        if endpoint["id"] in published:
            connection.execute(sa.text("UPDATE published_routes SET access_mode=:mode, resource_policy_json=:rules, policy_version=1 WHERE endpoint_id=:endpoint_id"), {"mode": mode, "rules": json.dumps(rules, ensure_ascii=False), "endpoint_id": endpoint["id"]})

    with op.batch_alter_table("published_routes", schema=None) as batch_op:
        batch_op.drop_column("requires_resource_adapter")
    with op.batch_alter_table("tools", schema=None) as batch_op:
        batch_op.drop_column("adapter_kind")


def downgrade() -> None:
    with op.batch_alter_table("tools", schema=None) as batch_op:
        batch_op.add_column(sa.Column("adapter_kind", sa.String(length=32), nullable=False, server_default="generic"))
    with op.batch_alter_table("published_routes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("requires_resource_adapter", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("external_resources", schema=None) as batch_op:
        batch_op.drop_constraint("fk_external_resources_source_route", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_external_resources_source_route_id"))
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("source_route_id")
        batch_op.alter_column("kind", existing_type=sa.String(length=64), type_=sa.String(length=16), existing_nullable=False)
    with op.batch_alter_table("published_routes", schema=None) as batch_op:
        batch_op.drop_column("policy_version")
        batch_op.drop_column("resource_policy_json")
        batch_op.drop_column("access_mode")
    with op.batch_alter_table("tool_upstreams", schema=None) as batch_op:
        batch_op.drop_constraint("fk_tool_upstreams_network_confirmed_user", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_tool_upstreams_network_isolation_confirmed_by_user_id"))
        batch_op.drop_column("network_isolation_confirmed_at")
        batch_op.drop_column("network_isolation_confirmed_by_user_id")
        batch_op.drop_column("network_isolation_note")
        batch_op.drop_column("network_isolation_mode")
    op.drop_index(op.f("ix_endpoint_access_policies_confirmed_by_user_id"), table_name="endpoint_access_policies")
    op.drop_index(op.f("ix_endpoint_access_policies_endpoint_id"), table_name="endpoint_access_policies")
    op.drop_index(op.f("ix_endpoint_access_policies_tool_id"), table_name="endpoint_access_policies")
    op.drop_table("endpoint_access_policies")


