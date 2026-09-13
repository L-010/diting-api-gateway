"""HTTP 输入输出模型，避免向客户端泄露内部数据库字段和敏感凭据。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
import re
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .username import normalize_username_key


class Message(BaseModel):
    message: str
    request_id: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=12, max_length=256)
    email: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=128)
    registration_note: str = Field(default="", max_length=1_000)

    @field_validator("username", mode="before")
    @classmethod
    def strip_required_username(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not value:
            raise ValueError("用户名不能为空")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("用户名不能包含换行或控制字符")
        if len(normalize_username_key(value)) > 64:
            raise ValueError("用户名规范化后不能超过 64 个字符")
        return value

    @field_validator("email", "display_name", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "@" not in value or value.startswith("@") or value.endswith("@") or ".." in value:
            raise ValueError("邮箱格式不正确")
        local, domain = value.rsplit("@", 1)
        if not local or "." not in domain or any(char.isspace() for char in value):
            raise ValueError("邮箱格式不正确")
        return value.lower()


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class UserView(BaseModel):
    id: str
    username: str
    display_name: str | None
    email: str | None = None
    email_verified: bool = False
    roles: list[str]
    approval_status: str = "approved"
    is_active: bool = True
    must_change_password: bool


class AdminUserView(BaseModel):
    id: str
    username: str
    display_name: str | None
    email: str | None
    email_verified: bool = False
    roles: list[str]
    is_active: bool
    approval_status: str
    must_change_password: bool
    registered_at: datetime | None
    approved_by_user_id: str | None
    approved_at: datetime | None
    rejected_at: datetime | None
    rejection_reason: str
    disabled_at: datetime | None
    disabled_by_user_id: str | None
    disable_reason: str
    registration_note: str
    rate_limit_per_minute: int = 0
    storage_quota_bytes: int = 0
    storage_used_bytes: int = 0
    api_key_count: int = 0
    active_api_key_count: int = 0
    last_api_activity_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PlatformSettingsView(BaseModel):
    smtp_enabled: bool
    smtp_configured: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password_configured: bool
    smtp_from_email: str
    smtp_from_name: str
    smtp_use_tls: bool
    default_user_rate_limit_per_minute: int
    default_user_storage_quota_bytes: int
    default_file_retention_days: int
    public_gateway_base_url: str
    gateway_source: Literal["browser", "environment", "database"]
    source: Literal["environment", "database"]
    updated_at: datetime | None = None
    site_name: str
    site_subtitle: str
    registration_enabled: bool
    brand_image_url: str


class EmailSettingsUpdateRequest(BaseModel):
    smtp_enabled: bool = False
    smtp_host: str = Field(default="", max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65_535)
    smtp_username: str = Field(default="", max_length=255)
    smtp_password: str | None = Field(default=None, max_length=1_024)
    clear_password: bool = False
    smtp_from_email: str = Field(default="", max_length=255)
    smtp_from_name: str = Field(default="API Gateway", max_length=128)
    smtp_use_tls: bool = True

    @field_validator("smtp_host", "smtp_username", "smtp_from_email", "smtp_from_name", mode="before")
    @classmethod
    def strip_email_setting(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("smtp_host")
    @classmethod
    def validate_smtp_host(cls, value: str) -> str:
        if value and ("://" in value or "/" in value or any(char.isspace() or ord(char) < 32 for char in value)):
            raise ValueError("SMTP 主机只能填写主机名或 IP，不能包含协议、路径或空白字符")
        return value

    @field_validator("smtp_from_email")
    @classmethod
    def validate_from_email(cls, value: str) -> str:
        if value and ("@" not in value or value.startswith("@") or value.endswith("@") or any(char.isspace() for char in value)):
            raise ValueError("发件人邮箱格式不正确")
        return value.lower()

    @field_validator("smtp_from_name", "smtp_username")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("SMTP 文本字段不能包含换行或控制字符")
        return value


class UserDefaultsUpdateRequest(BaseModel):
    rate_limit_per_minute: int = Field(ge=0, le=10_000)
    storage_quota_bytes: int = Field(default=107_374_182_400, ge=1_073_741_824, le=109_951_162_777_600)
    file_retention_days: int = Field(default=90, ge=1, le=3_650)


class GatewaySettingsUpdateRequest(BaseModel):
    public_gateway_base_url: str = Field(default="", max_length=512)

    @field_validator("public_gateway_base_url")
    @classmethod
    def validate_gateway_base_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned:
            return ""
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("公开网关地址必须是 http 或 https 根地址")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("公开网关地址不能包含凭据、路径、查询参数或片段")
        return cleaned


class SiteSettingsUpdateRequest(BaseModel):
    site_name: str = Field(min_length=1, max_length=128)
    site_subtitle: str = Field(default="", max_length=255)
    registration_enabled: bool = True

    @field_validator("site_name", "site_subtitle", mode="before")
    @classmethod
    def strip_site_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("site_name", "site_subtitle")
    @classmethod
    def reject_site_controls(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("站点文字不能包含控制字符")
        return value


class AdminUserRateLimitUpdateRequest(BaseModel):
    rate_limit_per_minute: int = Field(ge=0, le=10_000)


class AdminUserStorageQuotaUpdateRequest(BaseModel):
    storage_quota_bytes: int = Field(ge=0, le=109_951_162_777_600)


class TestEmailRequest(BaseModel):
    recipient: str = Field(min_length=3, max_length=255)

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@") or any(char.isspace() for char in cleaned):
            raise ValueError("收件人邮箱格式不正确")
        return cleaned


class UserApprovalRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class UserRejectRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class AdminUserStats(BaseModel):
    total: int
    pending: int
    active: int
    disabled: int
    rejected: int


class AdminUserOverview(BaseModel):
    items: list[AdminUserView]
    total: int
    page: int
    page_size: int
    stats: AdminUserStats


class ApiKeyCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    expires_at: datetime | None = None
    expires_in_days: Literal[30, 90, 180, 365] | None = 90
    permanent: bool = False

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class ApiKeyView(BaseModel):
    id: str
    label: str
    prefix: str
    status: str
    scopes: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    disabled_at: datetime | None = None
    disabled_by_user_id: str | None = None
    disable_reason: str = ""
    can_enable: bool = False
    is_expired: bool = False
    rotation_hint: str = ""
    created_at: datetime


class ApiKeyCreated(ApiKeyView):
    secret: str


class AdminApiKeyView(ApiKeyView):
    user_id: str
    username: str
    recent_failure_count: int = 0
    recent_rate_limited_count: int = 0
    last_error_code: str | None = None


class GatewayCallView(BaseModel):
    request_id: str
    user_id: str | None = None
    username: str = ""
    api_key_id: str | None = None
    api_key_prefix: str = ""
    tool_id: str | None = None
    tool_slug: str
    endpoint_id: str | None = None
    endpoint_summary: str = ""
    operation_id: str | None = None
    method: str
    path: str
    status_code: int
    upstream_status_code: int | None = None
    duration_ms: int
    request_bytes: int
    response_bytes: int
    error_code: str | None = None
    failure_stage: str
    query: list[dict[str, str]] = Field(default_factory=list)
    created_at: datetime
    upstream_path: str = ""
    client_ip_fingerprint: str = ""
    user_agent_fingerprint: str = ""


class GatewayCallPage(BaseModel):
    items: list[GatewayCallView]
    total: int
    page: int
    page_size: int
    has_more: bool


class UserCallMetrics(BaseModel):
    total: int
    success: int
    failed: int
    rate_limited: int
    success_rate: float
    avg_duration_ms: float
    p95_duration_ms: int
    request_bytes: int
    response_bytes: int
    last_called_at: datetime | None


class UserCallRanking(BaseModel):
    tool_slug: str
    total: int
    failed: int
    avg_duration_ms: float


class AdminUserProfileSummary(BaseModel):
    user: AdminUserView
    window_days: int
    window_start: datetime
    window_end: datetime
    metrics: UserCallMetrics
    top_tools: list[UserCallRanking]
    recent_failures: list[GatewayCallView]


class UserEventView(BaseModel):
    id: str
    action: str
    actor_user_id: str
    actor_username: str
    target_type: str
    target_id: str | None
    request_id: str
    detail: dict[str, Any]
    created_at: datetime


class UserEventPage(BaseModel):
    items: list[UserEventView]
    total: int
    page: int
    page_size: int
    has_more: bool


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)

    @field_validator("display_name", mode="before")
    @classmethod
    def clean_display_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class EmailAddressRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@") or any(char.isspace() for char in cleaned):
            raise ValueError("邮箱格式不正确")
        return cleaned


class TokenRequest(BaseModel):
    token: str = Field(min_length=32, max_length=512)


class ResetPasswordRequest(TokenRequest):
    new_password: str = Field(min_length=12, max_length=256)


class NotificationView(BaseModel):
    id: str
    kind: str
    title: str
    body: str
    action_url: str
    read_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationView]
    unread_count: int
    total: int
    page: int
    page_size: int
    has_more: bool


class AdminResetPasswordResult(BaseModel):
    temporary_password: str
    must_change_password: bool = True


class ToolEnvironmentConfig(BaseModel):
    name: str = Field(default="prod", min_length=1, max_length=64)
    instance_name: str = Field(default="default", min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=512)
    role: str = Field(default="production", max_length=64)
    enabled: bool = True
    priority: int = Field(default=100, ge=1, le=10_000)

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return value.rstrip("/")


class ToolConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9-]{2,64}$")
    name: str = Field(min_length=2, max_length=128)
    description: str = Field(default="", max_length=4_000)
    base_url: str = Field(min_length=8, max_length=512)
    upstream_token: str = Field(default="", max_length=2_048)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)
    status: Literal["draft", "active", "disabled"] = "draft"
    default_gateway_prefix: str = Field(default="", max_length=128)
    auth_type: Literal["none", "bearer", "header_api_key"] = "bearer"
    token_label: str = Field(default="", max_length=128)
    health_path: str = Field(default="/health", max_length=128)
    environment_mode: Literal["single", "multi"] = "single"
    environments: list[ToolEnvironmentConfig] = Field(default_factory=list)
    discovery_type: Literal["fixed_url", "openapi_url", "manual_upload", "text"] = "fixed_url"
    network_zone: str = Field(default="local", max_length=64)
    verify_tls: bool = True
    retry_count: int = Field(default=0, ge=0, le=5)
    connect_timeout_seconds: int = Field(default=10, ge=1, le=60)
    request_timeout_seconds: int = Field(default=60, ge=1, le=600)
    network_isolation_mode: Literal["firewall_allowlist", "private_network", "machine_credential_only"] = "firewall_allowlist"
    network_isolation_note: str = Field(default="", max_length=1_000)
    network_isolation_confirmed: bool = False
    task_adapter: dict[str, Any] | None = None

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("health_path")
    @classmethod
    def normalize_health_path(cls, value: str) -> str:
        if not value:
            return "/health"
        return value if value.startswith("/") else f"/{value}"

    @field_validator("default_gateway_prefix")
    @classmethod
    def normalize_gateway_prefix(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if cleaned and not cleaned.startswith("/"):
            cleaned = f"/{cleaned}"
        return cleaned

    @field_validator("token_label")
    @classmethod
    def validate_token_label(cls, value: str) -> str:
        if value and not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", value):
            raise ValueError("请求头名称格式不合法")
        return value


class ToolConfigurationCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=128)
    description: str = Field(default="", max_length=4_000)
    status: Literal["draft", "active", "disabled"] = "draft"
    default_gateway_prefix: str = Field(default="", max_length=128)
    base_url: str = Field(min_length=8, max_length=512)
    health_path: str = Field(default="/health", max_length=128)
    auth_type: Literal["none", "bearer", "header_api_key"] = "bearer"
    token_label: str = Field(default="", max_length=128)
    upstream_token: str = Field(default="", max_length=2_048)
    clear_upstream_token: bool = False
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)
    network_zone: str = Field(default="local", max_length=64)
    verify_tls: bool = True
    retry_count: int = Field(default=0, ge=0, le=5)
    connect_timeout_seconds: int = Field(default=10, ge=1, le=60)
    request_timeout_seconds: int = Field(default=60, ge=1, le=600)
    network_isolation_mode: Literal["firewall_allowlist", "private_network", "machine_credential_only"] = "firewall_allowlist"
    network_isolation_note: str = Field(default="", max_length=1_000)
    network_isolation_confirmed: bool = False
    file_retention_days: int = Field(default=0, ge=0, le=3_650)
    storage_quota_bytes: int = Field(default=0, ge=0, le=1_125_899_906_842_624)
    max_file_bytes: int = Field(default=0, ge=0, le=53_687_091_200)
    file_access_enabled: bool = True
    storage_risk_acknowledged: bool = False

    @field_validator("base_url")
    @classmethod
    def normalize_candidate_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @field_validator("health_path")
    @classmethod
    def normalize_candidate_health_path(cls, value: str) -> str:
        cleaned = value.strip() or "/health"
        if "?" in cleaned or "#" in cleaned or any(part == ".." for part in cleaned.split("/")):
            raise ValueError("健康检查路径不能包含查询参数、片段或路径穿越")
        return cleaned if cleaned.startswith("/") else f"/{cleaned}"

    @field_validator("default_gateway_prefix")
    @classmethod
    def normalize_candidate_gateway_prefix(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if "?" in cleaned or "#" in cleaned or any(part == ".." for part in cleaned.split("/")):
            raise ValueError("网关前缀不能包含查询参数、片段或路径穿越")
        if cleaned and not cleaned.startswith("/"):
            cleaned = f"/{cleaned}"
        return cleaned

    @field_validator("token_label")
    @classmethod
    def validate_token_label(cls, value: str) -> str:
        if value and not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", value):
            raise ValueError("请求头名称格式不合法")
        return value

    @field_validator("name", "description", "token_label", "network_zone", "network_isolation_note", mode="before")
    @classmethod
    def strip_candidate_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ToolConfigurationUpdateRequest(ToolConfigurationCandidateRequest):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=4, max_length=500)
    confirm: bool = False


class ToolConfigurationRollbackRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    revision_id: str = Field(min_length=36, max_length=36)
    reason: str = Field(min_length=4, max_length=500)
    confirm: bool = False


class ToolConfigurationView(BaseModel):
    tool_id: str
    slug: str
    name: str
    description: str
    status: str
    is_enabled: bool
    default_gateway_prefix: str
    base_url: str
    health_path: str
    auth_type: str
    token_label: str
    token_configured: bool
    rate_limit_per_minute: int
    network_zone: str
    verify_tls: bool
    retry_count: int
    connect_timeout_seconds: int
    request_timeout_seconds: int
    network_isolation_mode: str
    network_isolation_note: str
    network_isolation_confirmed: bool
    config_revision: int
    file_retention_days: int = 0
    storage_quota_bytes: int = 0
    max_file_bytes: int = 0
    file_access_enabled: bool = True
    file_quarantined: bool = False
    storage_risk_acknowledged: bool = False
    updated_at: datetime


class ToolConfigurationTestResult(BaseModel):
    health: dict[str, Any]
    changed_fields: list[str]
    requires_health_check: bool
    candidate_base_url_masked: str


class ToolConfigurationRevisionView(BaseModel):
    id: str
    config_revision: int
    base_url_masked: str
    status: str
    changed_by_user_id: str | None
    reason: str
    created_at: datetime


class ToolTokenUpdateRequest(BaseModel):
    upstream_token: str = Field(min_length=1, max_length=2_048)
    reason: str = Field(min_length=4, max_length=500)


class TaskAdapterUpdateRequest(BaseModel):
    task_adapter: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=4, max_length=500)


class ToolView(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    status: str = "draft"
    is_enabled: bool
    base_url_masked: str
    rate_limit_per_minute: int
    auth_type: str = "bearer"
    token_configured: bool = False
    token_label: str = ""
    health_path: str = "/health"
    environment_mode: str = "single"
    environments: list[dict[str, Any]] = Field(default_factory=list)
    discovery_type: str = "fixed_url"
    network_zone: str = "local"
    verify_tls: bool = True
    retry_count: int = 0
    network_isolation_mode: str = "firewall_allowlist"
    network_isolation_note: str = ""
    network_isolation_confirmed: bool = False
    endpoint_count: int = 0
    published_count: int = 0
    draft_count: int = 0
    excluded_count: int = 0
    blocked_count: int = 0
    inconsistent_route_count: int = 0
    health: dict[str, Any] | None = None
    latest_openapi_version: str | None = None
    latest_openapi_sha256: str | None = None
    latest_openapi_imported_at: datetime | None = None
    latest_import_summary: dict[str, Any] | None = None
    onboarding: dict[str, Any] = Field(default_factory=dict)
    task_adapter: dict[str, Any] = Field(default_factory=dict)
    file_retention_days: int = 0
    storage_quota_bytes: int = 0
    max_file_bytes: int = 0
    file_access_enabled: bool = True
    file_quarantined: bool = False
    storage_total_bytes: int | None = None
    storage_free_bytes: int | None = None
    storage_checked_at: datetime | None = None
    storage_risk_acknowledged: bool = False


class EndpointView(BaseModel):
    id: str
    method: str
    upstream_path: str
    gateway_path: str = ""
    operation_id: str | None
    summary: str
    content_types: list[str]
    group_name: str = ""
    status: str = "candidate"
    import_action: str = "new"
    risk_level: str = "info"
    risk_flags: list[str] = Field(default_factory=list)
    governance: dict[str, Any] = Field(default_factory=dict)
    parameters: list[Any] = Field(default_factory=list)
    request_body: dict[str, Any] = Field(default_factory=dict)
    responses: dict[str, Any] = Field(default_factory=dict)
    exclusion_reason: str = ""
    public_description: str = ""
    route_policy: dict[str, Any] | None = None
    access_policy: dict[str, Any] = Field(default_factory=dict)
    published: bool
    enabled: bool


class EndpointCatalogPage(BaseModel):
    items: list[EndpointView]
    total: int
    page: int
    page_size: int
    page_count: int
    methods: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)


class EndpointUpdateRequest(BaseModel):
    gateway_path: str | None = Field(default=None, max_length=512)
    summary: str | None = Field(default=None, max_length=255)
    public_description: str | None = Field(default=None, max_length=4_000)
    content_types: list[str] | None = None
    governance: dict[str, Any] | None = None
    status: Literal["candidate", "draft", "excluded", "blocked"] | None = None
    exclusion_reason: str | None = Field(default=None, max_length=1_000)
    risk_level: Literal["info", "low", "medium", "high", "blocker"] | None = None
    risk_flags: list[str] | None = None
    storage_action: Literal["none", "upload_file", "create_task", "produce_files", "delete_file"] | None = None
    reason: str = Field(default="", max_length=500)


class PublishRouteRequest(BaseModel):
    is_enabled: bool = True
    confirm: bool = False
    reason: str = Field(default="", max_length=500)


class BulkPublishRequest(BaseModel):
    confirm: bool = False
    reason: str = Field(default="", max_length=500)
    max_risk_level: Literal["info", "low", "medium"] = "medium"


class BulkPublishResult(BaseModel):
    tool_id: str
    published_count: int
    skipped_count: int
    published: list[dict[str, str]] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)


class OpenApiParseRequest(BaseModel):
    source_type: Literal["upstream", "url", "text"] = "upstream"
    source_url: str = Field(default="", max_length=512)
    document_text: str = Field(default="", max_length=2_097_152)


class ImportBatchConfirmRequest(BaseModel):
    confirm: bool = True
    reason: str = Field(min_length=4, max_length=500)


class ImportBatchView(BaseModel):
    id: str
    tool_id: str
    spec_id: str | None
    source_type: str
    source_url: str
    source_sha256: str
    openapi_version: str
    total_operations: int
    selected_operations: int
    added_count: int
    changed_count: int
    unchanged_count: int
    excluded_count: int
    blocked_count: int
    status: str
    summary: dict[str, Any]
    governance_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class OpenApiDiffView(BaseModel):
    id: str
    batch_id: str
    endpoint_id: str | None
    method: str
    upstream_path: str
    gateway_path: str
    summary: str
    description: str = ""
    diff_type: str
    risk_level: str
    risk_flags: list[str]
    decision: str
    detail: dict[str, Any]
    access_policy_suggestion: dict[str, Any] = Field(default_factory=dict)
    access_policy: dict[str, Any] = Field(default_factory=dict)
    access_policy_complete: bool = False
    access_policy_missing_fields: list[str] = Field(default_factory=list)
    created_at: datetime


class AccessPolicyRequest(BaseModel):
    access_mode: Literal["authenticated", "owner", "shared", "admin_only"]
    rules: dict[str, Any] = Field(default_factory=dict)
    shared_confirm: bool = False


class DiffDecisionRequest(BaseModel):
    decision: Literal["accept", "exclude", "defer", "block"] = "accept"
    reason: str = Field(default="", max_length=500)
    access_policy: AccessPolicyRequest | None = None
    use_suggestion: bool = True


class BulkDiffDecisionRequest(BaseModel):
    diff_ids: list[str] = Field(min_length=1, max_length=500)
    decision: Literal["accept", "block"]
    reason: str = Field(default="", max_length=500)
    access_policy: AccessPolicyRequest | None = None
    use_suggestions: bool = True


class BulkDiffDecisionResult(BaseModel):
    updated: list[OpenApiDiffView] = Field(default_factory=list)
    failed: list[dict[str, str]] = Field(default_factory=list)


class TaskView(BaseModel):
    id: str
    tool_slug: str
    tool_name: str
    upstream_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]
    capabilities: dict[str, bool] = Field(default_factory=dict)
    is_terminal: bool = False


class TaskDetailView(TaskView):
    upstream: dict[str, Any] | None = None


class AdminTaskDetailView(TaskDetailView):
    owner_user_id: str
    owner_username: str
    source_request_id: str | None = None
    storage_endpoint_revision: int | None = None


class TaskPage(BaseModel):
    items: list[TaskView]
    total: int
    page: int
    page_size: int
    has_more: bool


class RemoteFileView(BaseModel):
    id: str
    tool_id: str
    tool_slug: str
    tool_name: str
    owner_user_id: str
    owner_username: str = ""
    file_name: str
    role: Literal["input", "intermediate", "output", "log", "archive"]
    visibility: Literal["user", "admin", "internal"]
    status: Literal["pending", "ready", "delete_pending", "deleted", "expired", "missing", "quarantined", "error"]
    content_type: str
    size_bytes: int | None
    sha256: str
    is_bundle: bool = False
    source_request_id: str | None = None
    parent_resource_id: str | None = None
    parent_kind: str | None = None
    parent_platform_id: str | None = None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    downloadable: bool
    deletable: bool


class RemoteFilePage(BaseModel):
    items: list[RemoteFileView]
    total: int
    page: int
    page_size: int
    has_more: bool
    used_bytes: int
    quota_bytes: int


class FileDownloadAuthorizationView(BaseModel):
    authorization_id: str
    download_url: str
    expires_at: datetime


class FileDownloadEventView(BaseModel):
    id: str
    remote_file_id: str
    file_name: str
    tool_id: str
    tool_slug: str
    actor_user_id: str
    actor_username: str
    owner_user_id: str
    owner_username: str
    request_id: str
    is_admin: bool
    status: str
    bytes_sent: int
    error_code: str
    started_at: datetime
    completed_at: datetime | None


class FileDownloadEventPage(BaseModel):
    items: list[FileDownloadEventView]
    total: int
    page: int
    page_size: int
    has_more: bool


class FileDeleteRequest(BaseModel):
    confirm: bool = True


class FileAdminActionRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class ToolFileAccessUpdateRequest(BaseModel):
    file_access_enabled: bool
    file_quarantined: bool
    reason: str = Field(min_length=4, max_length=500)


class ToolIntegrationRouteUpdateRequest(BaseModel):
    endpoint_id: str = Field(min_length=36, max_length=36)
    capability: Literal["status", "manifest", "artifacts", "file_download", "bundle_download", "file_delete", "bundle_delete", "storage_watermark"]
    is_enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=4, max_length=500)


class ToolIntegrationRouteView(BaseModel):
    id: str
    tool_id: str
    endpoint_id: str | None
    capability: str
    method: str
    path_template: str
    config: dict[str, Any]
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class ToolStorageEndpointRevisionView(BaseModel):
    id: str
    tool_id: str
    revision: int
    base_url_masked: str
    auth_type: str
    token_configured: bool
    verify_tls: bool
    is_active: bool
    file_count: int
    task_count: int
    pending_delete_count: int
    created_at: datetime
    retired_at: datetime | None
