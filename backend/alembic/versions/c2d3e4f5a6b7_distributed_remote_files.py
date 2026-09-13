"""增加分布式工具文件、同步队列和下载审计。

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
"""

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("storage_quota_bytes", sa.BigInteger(), nullable=False, server_default="0"))
    with op.batch_alter_table("tools", schema=None) as batch_op:
        batch_op.add_column(sa.Column("file_retention_days", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("storage_quota_bytes", sa.BigInteger(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("max_file_bytes", sa.BigInteger(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("file_access_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("file_quarantined", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("storage_total_bytes", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("storage_free_bytes", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("storage_checked_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("storage_risk_acknowledged", sa.Boolean(), nullable=False, server_default=sa.true()))
    with op.batch_alter_table("published_routes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("storage_action", sa.String(length=32), nullable=False, server_default="none"))
    with op.batch_alter_table("platform_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("default_user_storage_quota_bytes", sa.BigInteger(), nullable=False, server_default="107374182400"))
        batch_op.add_column(sa.Column("default_file_retention_days", sa.Integer(), nullable=False, server_default="90"))

    op.create_table(
        "tool_storage_endpoint_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tool_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("token_ciphertext", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column("auth_type", sa.String(length=32), nullable=False, server_default="bearer"),
        sa.Column("token_label", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("connect_timeout_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("request_timeout_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("adapter_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tool_id", "revision", name="uq_tool_storage_endpoint_revision"),
    )
    op.create_index("ix_tool_storage_endpoint_revisions_tool_id", "tool_storage_endpoint_revisions", ["tool_id"])
    op.create_index("ix_tool_storage_endpoint_revisions_is_active", "tool_storage_endpoint_revisions", ["is_active"])

    op.create_table(
        "tool_integration_routes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tool_id", sa.String(length=36), nullable=False),
        sa.Column("endpoint_id", sa.String(length=36), nullable=True),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False, server_default="GET"),
        sa.Column("path_template", sa.String(length=512), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["endpoint_id"], ["api_endpoints.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tool_id", "capability", name="uq_tool_integration_capability"),
    )
    op.create_index("ix_tool_integration_routes_tool_id", "tool_integration_routes", ["tool_id"])
    op.create_index("ix_tool_integration_routes_endpoint_id", "tool_integration_routes", ["endpoint_id"])

    op.create_table(
        "remote_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("identity_key", sa.String(length=64), nullable=False),
        sa.Column("tool_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("storage_endpoint_revision_id", sa.String(length=36), nullable=False),
        sa.Column("parent_resource_id", sa.String(length=36), nullable=True),
        sa.Column("source_gateway_request_id", sa.String(length=36), nullable=True),
        sa.Column("upstream_file_id", sa.String(length=255), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="intermediate"),
        sa.Column("visibility", sa.String(length=16), nullable=False, server_default="internal"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("content_type", sa.String(length=255), nullable=False, server_default="application/octet-stream"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("is_bundle", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")),
        sa.Column("upstream_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_resource_id"], ["external_resources.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_gateway_request_id"], ["gateway_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["storage_endpoint_revision_id"], ["tool_storage_endpoint_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_key", name="uq_remote_file_identity"),
    )
    for name, columns in (
        ("ix_remote_files_tool_id", ["tool_id"]),
        ("ix_remote_files_owner_user_id", ["owner_user_id"]),
        ("ix_remote_files_storage_endpoint_revision_id", ["storage_endpoint_revision_id"]),
        ("ix_remote_files_parent_resource_id", ["parent_resource_id"]),
        ("ix_remote_files_source_gateway_request_id", ["source_gateway_request_id"]),
        ("ix_remote_files_expires_at", ["expires_at"]),
        ("ix_remote_files_owner_status_created", ["owner_user_id", "status", "created_at"]),
        ("ix_remote_files_tool_status_created", ["tool_id", "status", "created_at"]),
    ):
        op.create_index(name, "remote_files", columns)

    op.create_table(
        "remote_file_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("remote_file_id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=True),
        sa.Column("gateway_request_id", sa.String(length=36), nullable=True),
        sa.Column("relation", sa.String(length=32), nullable=False, server_default="produced"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["gateway_request_id"], ["gateway_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["remote_file_id"], ["remote_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["external_resources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("remote_file_id", "resource_id", "gateway_request_id", "relation", name="uq_remote_file_link"),
    )
    op.create_index("ix_remote_file_links_remote_file_id", "remote_file_links", ["remote_file_id"])
    op.create_index("ix_remote_file_links_resource_id", "remote_file_links", ["resource_id"])
    op.create_index("ix_remote_file_links_gateway_request_id", "remote_file_links", ["gateway_request_id"])

    op.create_table(
        "file_sync_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("target_key", sa.String(length=96), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=True),
        sa.Column("remote_file_id", sa.String(length=36), nullable=True),
        sa.Column("job_type", sa.String(length=24), nullable=False, server_default="poll"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resource_id"], ["external_resources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["remote_file_id"], ["remote_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("target_key", name="uq_file_sync_target"),
    )
    op.create_index("ix_file_sync_jobs_resource_id", "file_sync_jobs", ["resource_id"])
    op.create_index("ix_file_sync_jobs_remote_file_id", "file_sync_jobs", ["remote_file_id"])
    op.create_index("ix_file_sync_jobs_status_next_attempt", "file_sync_jobs", ["status", "next_attempt_at"])

    op.create_table(
        "file_download_authorizations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("remote_file_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["remote_file_id"], ["remote_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_file_download_authorizations_remote_file_id", "file_download_authorizations", ["remote_file_id"])
    op.create_index("ix_file_download_authorizations_actor_user_id", "file_download_authorizations", ["actor_user_id"])
    op.create_index("ix_file_download_authorizations_expires_at", "file_download_authorizations", ["expires_at"])
    op.create_index("ix_file_download_authorizations_actor_expires", "file_download_authorizations", ["actor_user_id", "expires_at"])

    op.create_table(
        "file_download_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("authorization_id", sa.String(length=36), nullable=True),
        sa.Column("remote_file_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="started"),
        sa.Column("bytes_sent", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["authorization_id"], ["file_download_authorizations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["remote_file_id"], ["remote_files.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_file_download_events_authorization_id", ["authorization_id"]),
        ("ix_file_download_events_remote_file_id", ["remote_file_id"]),
        ("ix_file_download_events_actor_user_id", ["actor_user_id"]),
        ("ix_file_download_events_owner_user_id", ["owner_user_id"]),
        ("ix_file_download_events_request_id", ["request_id"]),
        ("ix_file_download_events_file_created", ["remote_file_id", "created_at"]),
        ("ix_file_download_events_actor_created", ["actor_user_id", "created_at"]),
    ):
        op.create_index(name, "file_download_events", columns)

    _backfill_storage_endpoints_and_artifacts()
    _migrate_tomodd_adapter_v2()
    _queue_existing_task_syncs()


def _backfill_storage_endpoints_and_artifacts() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    endpoints: dict[str, str] = {}
    upstreams = bind.execute(sa.text("SELECT tool_id, base_url, token_ciphertext, auth_type, token_label, verify_tls, connect_timeout_seconds, request_timeout_seconds FROM tool_upstreams")).mappings()
    for item in upstreams:
        endpoint_id = str(uuid.uuid4())
        endpoints[str(item["tool_id"])] = endpoint_id
        bind.execute(
            sa.text("INSERT INTO tool_storage_endpoint_revisions (id, tool_id, revision, base_url, token_ciphertext, auth_type, token_label, verify_tls, connect_timeout_seconds, request_timeout_seconds, adapter_json, is_active, created_at) VALUES (:id, :tool_id, 1, :base_url, :token, :auth_type, :token_label, :verify_tls, :connect_timeout, :request_timeout, '{}', 1, :created_at)"),
            {"id": endpoint_id, "tool_id": item["tool_id"], "base_url": item["base_url"], "token": item["token_ciphertext"] or "", "auth_type": item["auth_type"] or "bearer", "token_label": item["token_label"] or "", "verify_tls": bool(item["verify_tls"]), "connect_timeout": int(item["connect_timeout_seconds"] or 10), "request_timeout": int(item["request_timeout_seconds"] or 60), "created_at": now},
        )
    artifacts = bind.execute(sa.text("SELECT id, tool_id, owner_user_id, upstream_id, parent_upstream_id, created_at, updated_at FROM external_resources WHERE kind = 'artifact' AND deleted_at IS NULL")).mappings()
    for item in artifacts:
        endpoint_id = endpoints.get(str(item["tool_id"]))
        if not endpoint_id:
            continue
        parent_id = bind.execute(sa.text("SELECT id FROM external_resources WHERE tool_id = :tool_id AND upstream_id = :upstream_id AND kind IN ('job', 'dataset') LIMIT 1"), {"tool_id": item["tool_id"], "upstream_id": item["parent_upstream_id"]}).scalar_one_or_none() if item["parent_upstream_id"] else None
        identity = hashlib.sha256(f'{item["tool_id"]}:{parent_id or ""}:{item["upstream_id"]}'.encode("utf-8")).hexdigest()
        created_at = _as_datetime(item["created_at"], now)
        updated_at = _as_datetime(item["updated_at"], created_at)
        bind.execute(
            sa.text("INSERT INTO remote_files (id, identity_key, tool_id, owner_user_id, storage_endpoint_revision_id, parent_resource_id, upstream_file_id, file_name, role, visibility, status, content_type, size_bytes, sha256, is_bundle, metadata_json, expires_at, last_error, created_at, updated_at) VALUES (:id, :identity, :tool_id, :owner_user_id, :endpoint_id, :parent_id, :upstream_id, '', 'intermediate', 'internal', 'pending', 'application/octet-stream', NULL, '', 0, '{}', :expires_at, '等待工具适配器补齐元信息', :created_at, :updated_at)"),
            {"id": str(uuid.uuid4()), "identity": identity, "tool_id": item["tool_id"], "owner_user_id": item["owner_user_id"], "endpoint_id": endpoint_id, "parent_id": parent_id, "upstream_id": item["upstream_id"], "expires_at": created_at + timedelta(days=90), "created_at": created_at, "updated_at": updated_at},
        )


def _as_datetime(value: object, fallback: datetime) -> datetime:
    """兼容 SQLite 旧库中以字符串保存的带或不带时区时间。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return fallback
    return fallback


def _migrate_tomodd_adapter_v2() -> None:
    """只依据 tomoDD 已导入的真实路由升级制品映射，缺少任一核心路由时保持 v1。"""
    bind = op.get_bind()
    tool = bind.execute(sa.text("SELECT id, task_adapter_json FROM tools WHERE lower(slug) = 'tomodd' LIMIT 1")).mappings().first()
    if not tool:
        return
    routes = bind.execute(sa.text("SELECT id, upstream_path, method FROM published_routes WHERE tool_id = :tool_id AND is_enabled = 1"), {"tool_id": tool["id"]}).mappings().all()
    by_path = {(str(item["method"]).upper(), str(item["upstream_path"])): str(item["id"]) for item in routes}
    required = {
        "status": by_path.get(("GET", "/api/v1/jobs/{job_id}")),
        "artifacts": by_path.get(("GET", "/api/v1/jobs/{job_id}/artifacts")),
        "file_download": by_path.get(("GET", "/api/v1/artifacts/{artifact_id}/download")),
    }
    if not all(required.values()):
        return
    try:
        previous = json.loads(tool["task_adapter_json"] or "{}")
    except ValueError:
        previous = {}
    previous_routes = previous.get("routes") if isinstance(previous.get("routes"), dict) else {}
    adapter_routes = {key: value for key, value in previous_routes.items() if key in {"logs", "manifest", "download"} and isinstance(value, str)}
    adapter_routes.update(required)
    adapter = {
        "version": 2,
        "id_param": "job_id",
        "file_id_param": "artifact_id",
        "task_id_selector": "$.job_id",
        "status_selector": "$.status",
        "terminal_statuses": previous.get("terminal_statuses") or ["succeeded", "failed", "cancelled", "canceled"],
        "routes": adapter_routes,
        "artifacts": {
            "items_selector": "$",
            "id_selector": "$.artifact_id",
            "name_selector": "$.name",
            "size_selector": "$.size_bytes",
            "content_type_selector": "$.media_type",
            "sha256_selector": "$.sha256",
            "created_at_selector": "$.created_at",
            "default_role": "output",
            "default_visibility": "user",
            "default_status": "ready"
        }
    }
    bind.execute(sa.text("UPDATE tools SET task_adapter_json = :adapter, storage_risk_acknowledged = 1 WHERE id = :tool_id"), {"adapter": json.dumps(adapter, ensure_ascii=False, separators=(",", ":")), "tool_id": tool["id"]})
    bind.execute(sa.text("UPDATE published_routes SET storage_action = 'upload_file' WHERE tool_id = :tool_id AND method = 'POST' AND upstream_path = '/api/v1/datasets/{dataset_id}/files'"), {"tool_id": tool["id"]})
    bind.execute(sa.text("UPDATE published_routes SET storage_action = 'create_task' WHERE tool_id = :tool_id AND method = 'POST' AND upstream_path LIKE '/api/v1/jobs/%' AND upstream_path NOT LIKE '%/example' AND upstream_path NOT LIKE '%/preview' AND upstream_path NOT LIKE '%/{job_id}/%'"), {"tool_id": tool["id"]})
    bind.execute(sa.text("UPDATE published_routes SET storage_action = 'create_task' WHERE tool_id = :tool_id AND method = 'POST' AND upstream_path = '/api/v1/jobs'"), {"tool_id": tool["id"]})


def _queue_existing_task_syncs() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    rows = bind.execute(sa.text("SELECT er.id FROM external_resources er JOIN tools t ON t.id = er.tool_id WHERE er.kind = 'job' AND er.deleted_at IS NULL AND t.task_adapter_json <> '{}'" )).all()
    for (resource_id,) in rows:
        bind.execute(
            sa.text("INSERT INTO file_sync_jobs (id, target_key, resource_id, remote_file_id, job_type, status, attempts, next_attempt_at, lease_token, last_error, created_at, updated_at) VALUES (:id, :target_key, :resource_id, NULL, 'poll', 'pending', 0, :now, '', '', :now, :now)"),
            {"id": str(uuid.uuid4()), "target_key": f"poll:{resource_id}", "resource_id": resource_id, "now": now},
        )


def downgrade() -> None:
    for name in (
        "ix_file_download_events_actor_created", "ix_file_download_events_file_created", "ix_file_download_events_request_id",
        "ix_file_download_events_owner_user_id", "ix_file_download_events_actor_user_id", "ix_file_download_events_remote_file_id",
        "ix_file_download_events_authorization_id",
    ):
        op.drop_index(name, table_name="file_download_events")
    op.drop_table("file_download_events")
    for name in ("ix_file_download_authorizations_actor_expires", "ix_file_download_authorizations_expires_at", "ix_file_download_authorizations_actor_user_id", "ix_file_download_authorizations_remote_file_id"):
        op.drop_index(name, table_name="file_download_authorizations")
    op.drop_table("file_download_authorizations")
    op.drop_index("ix_file_sync_jobs_status_next_attempt", table_name="file_sync_jobs")
    op.drop_index("ix_file_sync_jobs_remote_file_id", table_name="file_sync_jobs")
    op.drop_index("ix_file_sync_jobs_resource_id", table_name="file_sync_jobs")
    op.drop_table("file_sync_jobs")
    op.drop_index("ix_remote_file_links_gateway_request_id", table_name="remote_file_links")
    op.drop_index("ix_remote_file_links_resource_id", table_name="remote_file_links")
    op.drop_index("ix_remote_file_links_remote_file_id", table_name="remote_file_links")
    op.drop_table("remote_file_links")
    for name in ("ix_remote_files_tool_status_created", "ix_remote_files_owner_status_created", "ix_remote_files_expires_at", "ix_remote_files_source_gateway_request_id", "ix_remote_files_parent_resource_id", "ix_remote_files_storage_endpoint_revision_id", "ix_remote_files_owner_user_id", "ix_remote_files_tool_id"):
        op.drop_index(name, table_name="remote_files")
    op.drop_table("remote_files")
    op.drop_index("ix_tool_integration_routes_endpoint_id", table_name="tool_integration_routes")
    op.drop_index("ix_tool_integration_routes_tool_id", table_name="tool_integration_routes")
    op.drop_table("tool_integration_routes")
    op.drop_index("ix_tool_storage_endpoint_revisions_is_active", table_name="tool_storage_endpoint_revisions")
    op.drop_index("ix_tool_storage_endpoint_revisions_tool_id", table_name="tool_storage_endpoint_revisions")
    op.drop_table("tool_storage_endpoint_revisions")
    with op.batch_alter_table("platform_settings", schema=None) as batch_op:
        batch_op.drop_column("default_file_retention_days")
        batch_op.drop_column("default_user_storage_quota_bytes")
    with op.batch_alter_table("published_routes", schema=None) as batch_op:
        batch_op.drop_column("storage_action")
    with op.batch_alter_table("tools", schema=None) as batch_op:
        batch_op.drop_column("storage_risk_acknowledged")
        batch_op.drop_column("storage_checked_at")
        batch_op.drop_column("storage_free_bytes")
        batch_op.drop_column("storage_total_bytes")
        batch_op.drop_column("file_quarantined")
        batch_op.drop_column("file_access_enabled")
        batch_op.drop_column("max_file_bytes")
        batch_op.drop_column("storage_quota_bytes")
        batch_op.drop_column("file_retention_days")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("storage_quota_bytes")


