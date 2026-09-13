"""本地 MVP 的持久化模型，字段命名与后续 PostgreSQL 迁移保持兼容。"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, event
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .username import normalize_username_key


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RoleName(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class KeyStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


class ToolStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"


class EndpointStatus(str, enum.Enum):
    CANDIDATE = "candidate"
    DRAFT = "draft"
    PUBLISHED = "published"
    EXCLUDED = "excluded"
    BLOCKED = "blocked"


class RiskLevel(str, enum.Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKER = "blocker"


class ImportBatchStatus(str, enum.Enum):
    PARSED = "parsed"
    APPLIED = "applied"


class ResourceKind(str, enum.Enum):
    DATASET = "dataset"
    JOB = "job"
    ARTIFACT = "artifact"


class RemoteFileStatus(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"
    DELETE_PENDING = "delete_pending"
    DELETED = "deleted"
    EXPIRED = "expired"
    MISSING = "missing"
    QUARANTINED = "quarantined"
    ERROR = "error"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    approval_status: Mapped[str] = mapped_column(String(24), default=ApprovalStatus.APPROVED.value)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str] = mapped_column(Text, default="")
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    disable_reason: Mapped[str] = mapped_column(Text, default="")
    registration_note: Mapped[str] = mapped_column(Text, default="")
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=0)
    storage_quota_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


@event.listens_for(User, "before_insert")
@event.listens_for(User, "before_update")
def 同步用户名规范化键(_: object, __: object, target: User) -> None:
    """用户名比较不区分大小写，持久化键必须使用同一规则防止并发重复。"""
    target.username_key = normalize_username_key(target.username)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), index=True)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(24), index=True)
    secret_hash: Mapped[str] = mapped_column(String(128), unique=True)
    scopes: Mapped[str] = mapped_column(Text, default="*")
    status: Mapped[str] = mapped_column(String(16), default=KeyStatus.ACTIVE.value)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    disable_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserImportBatch(Base):
    __tablename__ = "user_import_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    admin_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_sha256: Mapped[str] = mapped_column(String(64))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    accepted_rows: Mapped[int] = mapped_column(Integer, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default=ToolStatus.DRAFT.value)
    default_gateway_prefix: Mapped[str] = mapped_column(String(128), default="")
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    task_adapter_json: Mapped[str] = mapped_column(Text, default="{}")
    file_retention_days: Mapped[int] = mapped_column(Integer, default=0)
    storage_quota_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    max_file_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    file_access_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    file_quarantined: Mapped[bool] = mapped_column(Boolean, default=False)
    storage_total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    storage_free_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    storage_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    storage_risk_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ToolUpstream(Base):
    __tablename__ = "tool_upstreams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), unique=True, index=True)
    base_url: Mapped[str] = mapped_column(String(512))
    token_ciphertext: Mapped[str] = mapped_column(Text, default="")
    auth_type: Mapped[str] = mapped_column(String(32), default="bearer")
    token_label: Mapped[str] = mapped_column(String(128), default="")
    health_path: Mapped[str] = mapped_column(String(128), default="/health")
    environment_mode: Mapped[str] = mapped_column(String(32), default="single")
    environments_json: Mapped[str] = mapped_column(Text, default="[]")
    discovery_type: Mapped[str] = mapped_column(String(64), default="fixed_url")
    network_zone: Mapped[str] = mapped_column(String(64), default="local")
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    connect_timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    network_isolation_mode: Mapped[str] = mapped_column(String(32), default="firewall_allowlist")
    network_isolation_note: Mapped[str] = mapped_column(Text, default="")
    network_isolation_confirmed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    network_isolation_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ToolUpstreamInstance(Base):
    __tablename__ = "tool_upstream_instances"
    __table_args__ = (UniqueConstraint("tool_id", "environment_name", "instance_name", name="uq_tool_upstream_instance"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    upstream_config_id: Mapped[str] = mapped_column(ForeignKey("tool_upstreams.id", ondelete="CASCADE"), index=True)
    environment_name: Mapped[str] = mapped_column(String(32), default="prod", index=True)
    instance_name: Mapped[str] = mapped_column(String(80), default="default")
    base_url: Mapped[str] = mapped_column(String(512))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health_status: Mapped[str] = mapped_column(String(32), default="unknown")
    last_health_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class OpenApiSpec(Base):
    __tablename__ = "openapi_specs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(32), default="upstream")
    source_url: Mapped[str] = mapped_column(String(512), default="")
    title: Mapped[str] = mapped_column(String(255), default="")
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    document_json: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"))
    imported_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ToolImportBatch(Base):
    __tablename__ = "tool_import_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    spec_id: Mapped[str | None] = mapped_column(ForeignKey("openapi_specs.id", ondelete="SET NULL"), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="url")
    source_url: Mapped[str] = mapped_column(String(512), default="")
    source_sha256: Mapped[str] = mapped_column(String(64))
    openapi_version: Mapped[str] = mapped_column(String(32))
    total_operations: Mapped[int] = mapped_column(Integer, default=0)
    selected_operations: Mapped[int] = mapped_column(Integer, default=0)
    added_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    excluded_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default=ImportBatchStatus.PARSED.value)
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ApiEndpoint(Base):
    __tablename__ = "api_endpoints"
    __table_args__ = (UniqueConstraint("tool_id", "method", "upstream_path", name="uq_endpoint_operation"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(8))
    upstream_path: Mapped[str] = mapped_column(String(512))
    gateway_path: Mapped[str] = mapped_column(String(512), default="")
    operation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str] = mapped_column(String(255), default="")
    content_types: Mapped[str] = mapped_column(Text, default="[]")
    group_name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(24), default=EndpointStatus.CANDIDATE.value)
    import_action: Mapped[str] = mapped_column(String(24), default="new")
    risk_level: Mapped[str] = mapped_column(String(24), default=RiskLevel.INFO.value)
    risk_flags_json: Mapped[str] = mapped_column(Text, default="[]")
    governance_json: Mapped[str] = mapped_column(Text, default="{}")
    parameters_json: Mapped[str] = mapped_column(Text, default="[]")
    request_body_json: Mapped[str] = mapped_column(Text, default="{}")
    responses_json: Mapped[str] = mapped_column(Text, default="{}")
    source_spec_id: Mapped[str | None] = mapped_column(ForeignKey("openapi_specs.id", ondelete="SET NULL"), nullable=True, index=True)
    exclusion_reason: Mapped[str] = mapped_column(Text, default="")
    public_description: Mapped[str] = mapped_column(Text, default="")
    test_result_json: Mapped[str] = mapped_column(Text, default="{}")
    is_candidate: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class EndpointAccessPolicy(Base):
    __tablename__ = "endpoint_access_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("api_endpoints.id", ondelete="CASCADE"), unique=True, index=True)
    access_mode: Mapped[str] = mapped_column(String(32), default="authenticated")
    rules_json: Mapped[str] = mapped_column(Text, default="{}")
    source: Mapped[str] = mapped_column(String(16), default="auto")
    confirmed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class OpenApiDiffItem(Base):
    __tablename__ = "openapi_diff_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id: Mapped[str] = mapped_column(ForeignKey("tool_import_batches.id", ondelete="CASCADE"), index=True)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    endpoint_id: Mapped[str | None] = mapped_column(ForeignKey("api_endpoints.id", ondelete="SET NULL"), nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(8))
    upstream_path: Mapped[str] = mapped_column(String(512))
    gateway_path: Mapped[str] = mapped_column(String(512), default="")
    summary: Mapped[str] = mapped_column(String(255), default="")
    diff_type: Mapped[str] = mapped_column(String(32), default="new")
    risk_level: Mapped[str] = mapped_column(String(24), default=RiskLevel.INFO.value)
    risk_flags_json: Mapped[str] = mapped_column(Text, default="[]")
    decision: Mapped[str] = mapped_column(String(32), default="pending")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class PublishedRoute(Base):
    __tablename__ = "published_routes"
    __table_args__ = (UniqueConstraint("tool_id", "method", "gateway_path", name="uq_published_gateway_path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("api_endpoints.id", ondelete="CASCADE"), unique=True)
    method: Mapped[str] = mapped_column(String(8))
    gateway_path: Mapped[str] = mapped_column(String(512))
    upstream_path: Mapped[str] = mapped_column(String(512))
    scopes: Mapped[str] = mapped_column(Text, default="*")
    allowed_content_types_json: Mapped[str] = mapped_column(Text, default="[]")
    max_request_bytes: Mapped[int] = mapped_column(Integer, default=0)
    max_response_bytes: Mapped[int] = mapped_column(Integer, default=0)
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, default=0)
    allow_stream_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_stream_download: Mapped[bool] = mapped_column(Boolean, default=False)
    access_mode: Mapped[str] = mapped_column(String(32), default="authenticated")
    resource_policy_json: Mapped[str] = mapped_column(Text, default="{}")
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    require_idempotency_key: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_retry: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_level: Mapped[str] = mapped_column(String(24), default=RiskLevel.INFO.value)
    route_version: Mapped[int] = mapped_column(Integer, default=1)
    policy_json: Mapped[str] = mapped_column(Text, default="{}")
    storage_action: Mapped[str] = mapped_column(String(32), default="none")
    published_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    publish_reason: Mapped[str] = mapped_column(Text, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ExternalResource(Base):
    __tablename__ = "external_resources"
    __table_args__ = (UniqueConstraint("tool_id", "kind", "upstream_id", name="uq_external_resource"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    upstream_id: Mapped[str] = mapped_column(String(128), index=True)
    parent_upstream_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_route_id: Mapped[str | None] = mapped_column(ForeignKey("published_routes.id", ondelete="SET NULL"), nullable=True, index=True)
    source_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    storage_endpoint_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("tool_storage_endpoint_revisions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="created")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ToolStorageEndpointRevision(Base):
    __tablename__ = "tool_storage_endpoint_revisions"
    __table_args__ = (UniqueConstraint("tool_id", "revision", name="uq_tool_storage_endpoint_revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="RESTRICT"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    base_url: Mapped[str] = mapped_column(String(512))
    token_ciphertext: Mapped[str] = mapped_column(Text, default="")
    auth_type: Mapped[str] = mapped_column(String(32), default="bearer")
    token_label: Mapped[str] = mapped_column(String(128), default="")
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    connect_timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    adapter_json: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ToolIntegrationRoute(Base):
    __tablename__ = "tool_integration_routes"
    __table_args__ = (UniqueConstraint("tool_id", "capability", name="uq_tool_integration_capability"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    endpoint_id: Mapped[str | None] = mapped_column(ForeignKey("api_endpoints.id", ondelete="SET NULL"), nullable=True, index=True)
    capability: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(8), default="GET")
    path_template: Mapped[str] = mapped_column(String(512))
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RemoteFile(Base):
    __tablename__ = "remote_files"
    __table_args__ = (
        UniqueConstraint("identity_key", name="uq_remote_file_identity"),
        Index("ix_remote_files_owner_status_created", "owner_user_id", "status", "created_at"),
        Index("ix_remote_files_tool_status_created", "tool_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    identity_key: Mapped[str] = mapped_column(String(64))
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="RESTRICT"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    storage_endpoint_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("tool_storage_endpoint_revisions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    parent_resource_id: Mapped[str | None] = mapped_column(ForeignKey("external_resources.id", ondelete="SET NULL"), nullable=True, index=True)
    source_gateway_request_id: Mapped[str | None] = mapped_column(ForeignKey("gateway_requests.id", ondelete="SET NULL"), nullable=True, index=True)
    upstream_file_id: Mapped[str] = mapped_column(String(255))
    file_name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(32), default="intermediate")
    visibility: Mapped[str] = mapped_column(String(16), default="internal")
    status: Mapped[str] = mapped_column(String(24), default=RemoteFileStatus.PENDING.value)
    content_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    is_bundle: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    upstream_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RemoteFileLink(Base):
    __tablename__ = "remote_file_links"
    __table_args__ = (
        UniqueConstraint("remote_file_id", "resource_id", "gateway_request_id", "relation", name="uq_remote_file_link"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    remote_file_id: Mapped[str] = mapped_column(ForeignKey("remote_files.id", ondelete="CASCADE"), index=True)
    resource_id: Mapped[str | None] = mapped_column(ForeignKey("external_resources.id", ondelete="SET NULL"), nullable=True, index=True)
    gateway_request_id: Mapped[str | None] = mapped_column(ForeignKey("gateway_requests.id", ondelete="SET NULL"), nullable=True, index=True)
    relation: Mapped[str] = mapped_column(String(32), default="produced")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FileSyncJob(Base):
    __tablename__ = "file_sync_jobs"
    __table_args__ = (
        UniqueConstraint("target_key", name="uq_file_sync_target"),
        Index("ix_file_sync_jobs_status_next_attempt", "status", "next_attempt_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    target_key: Mapped[str] = mapped_column(String(96))
    resource_id: Mapped[str | None] = mapped_column(ForeignKey("external_resources.id", ondelete="CASCADE"), nullable=True, index=True)
    remote_file_id: Mapped[str | None] = mapped_column(ForeignKey("remote_files.id", ondelete="CASCADE"), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(24), default="poll")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    lease_token: Mapped[str] = mapped_column(String(64), default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class FileWorkerHeartbeat(Base):
    __tablename__ = "file_worker_heartbeats"
    __table_args__ = (Index("ix_file_worker_heartbeats_last_seen_at", "last_seen_at"),)

    instance_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    process_id: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    processed_jobs: Mapped[int] = mapped_column(BigInteger, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")


class FileDownloadAuthorization(Base):
    __tablename__ = "file_download_authorizations"
    __table_args__ = (Index("ix_file_download_authorizations_actor_expires", "actor_user_id", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    remote_file_id: Mapped[str] = mapped_column(ForeignKey("remote_files.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FileDownloadEvent(Base):
    __tablename__ = "file_download_events"
    __table_args__ = (
        Index("ix_file_download_events_file_created", "remote_file_id", "created_at"),
        Index("ix_file_download_events_actor_created", "actor_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    authorization_id: Mapped[str] = mapped_column(ForeignKey("file_download_authorizations.id", ondelete="SET NULL"), nullable=True, index=True)
    remote_file_id: Mapped[str] = mapped_column(ForeignKey("remote_files.id", ondelete="RESTRICT"), index=True)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="started")
    bytes_sent: Mapped[int] = mapped_column(BigInteger, default=0)
    error_code: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FileDownloadSlot(Base):
    __tablename__ = "file_download_slots"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_id", "slot_number", name="uq_file_download_scope_slot"),
        Index("ix_file_download_slots_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id: Mapped[str] = mapped_column(ForeignKey("file_download_events.id", ondelete="CASCADE"), index=True)
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[str] = mapped_column(String(36), index=True)
    slot_number: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class GatewayRequest(Base):
    __tablename__ = "gateway_requests"
    __table_args__ = (
        Index("ix_gateway_requests_user_created_at", "user_id", "created_at"),
        Index("ix_gateway_requests_api_key_created_at", "api_key_id", "created_at"),
        Index("ix_gateway_requests_status_created_at", "status_code", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    api_key_id: Mapped[str | None] = mapped_column(ForeignKey("api_keys.id"), nullable=True)
    tool_id: Mapped[str | None] = mapped_column(ForeignKey("tools.id"), nullable=True)
    endpoint_id: Mapped[str | None] = mapped_column(ForeignKey("api_endpoints.id"), nullable=True, index=True)
    published_route_id: Mapped[str | None] = mapped_column(ForeignKey("published_routes.id"), nullable=True, index=True)
    upstream_instance_id: Mapped[str | None] = mapped_column(ForeignKey("tool_upstream_instances.id"), nullable=True, index=True)
    upstream_environment: Mapped[str] = mapped_column(String(32), default="")
    upstream_instance_name: Mapped[str] = mapped_column(String(80), default="")
    tool_slug: Mapped[str] = mapped_column(String(64), default="", index=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(512))
    upstream_path: Mapped[str] = mapped_column(String(512), default="")
    redacted_query_json: Mapped[str] = mapped_column(Text, default="{}")
    status_code: Mapped[int] = mapped_column(Integer)
    upstream_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    request_bytes: Mapped[int] = mapped_column(Integer, default=0)
    response_bytes: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_ip_hash: Mapped[str] = mapped_column(String(64), default="")
    user_agent_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IdempotencyRecord(Base):
    """仅为显式要求幂等的 Gateway 写接口保存受保护的结果。"""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("user_id", "api_key_id", "published_route_id", "key_hash", name="uq_idempotency_scope_key"),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    api_key_id: Mapped[str] = mapped_column(ForeignKey("api_keys.id", ondelete="CASCADE"))
    published_route_id: Mapped[str] = mapped_column(ForeignKey("published_routes.id", ondelete="CASCADE"))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="processing")
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body_ciphertext: Mapped[str] = mapped_column(Text, default="")
    response_headers_json: Mapped[str] = mapped_column(Text, default="{}")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    admin_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuthActionToken(Base):
    __tablename__ = "auth_action_tokens"
    __table_args__ = (
        Index("ix_auth_action_tokens_user_purpose", "user_id", "purpose"),
        Index("ix_auth_action_tokens_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    target_email: Mapped[str] = mapped_column(String(255), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_created_at", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text, default="")
    action_url: Mapped[str] = mapped_column(String(512), default="")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    __table_args__ = (Index("ix_email_outbox_status_next_attempt", "status", "next_attempt_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    recipient: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    body_ciphertext: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class PlatformSettings(Base):
    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    smtp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    smtp_username: Mapped[str] = mapped_column(String(255), default="")
    smtp_password_ciphertext: Mapped[str] = mapped_column(Text, default="")
    smtp_from_email: Mapped[str] = mapped_column(String(255), default="")
    smtp_from_name: Mapped[str] = mapped_column(String(128), default="API Gateway")
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    default_user_rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=0)
    default_user_storage_quota_bytes: Mapped[int] = mapped_column(BigInteger, default=107_374_182_400)
    default_file_retention_days: Mapped[int] = mapped_column(Integer, default=90)
    public_gateway_base_url: Mapped[str] = mapped_column(String(512), default="")
    site_name: Mapped[str] = mapped_column(String(128), default="API Gateway")
    site_subtitle: Mapped[str] = mapped_column(String(255), default="开发者统一工作台")
    registration_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    brand_image_url: Mapped[str] = mapped_column(String(512), default="")
    updated_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"
    __table_args__ = (UniqueConstraint("api_key_id", "tool_id", "window_start", name="uq_rate_limit_window"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    api_key_id: Mapped[str] = mapped_column(ForeignKey("api_keys.id", ondelete="CASCADE"), index=True)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)


class ToolConfigRevision(Base):
    __tablename__ = "tool_config_revisions"
    __table_args__ = (UniqueConstraint("tool_id", "config_revision", name="uq_tool_config_revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    config_revision: Mapped[int] = mapped_column(Integer)
    config_json: Mapped[str] = mapped_column(Text)
    token_ciphertext: Mapped[str] = mapped_column(Text, default="")
    changed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserRateLimitBucket(Base):
    __tablename__ = "user_rate_limit_buckets"
    __table_args__ = (UniqueConstraint("user_id", "window_start", name="uq_user_rate_limit_window"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
