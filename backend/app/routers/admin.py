"""管理员用户导入、工具接入、OpenAPI 导入、路由发布与审计接口。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile
from PIL import Image, UnidentifiedImageError
from jsonpath_ng import parse as parse_jsonpath
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import (
    AdminAuditEvent,
    ApiEndpoint,
    ApiKey,
    EndpointAccessPolicy,
    EndpointStatus,
    EmailOutbox,
    FileSyncJob,
    FileWorkerHeartbeat,
    GatewayRequest,
    ImportBatchStatus,
    KeyStatus,
    OpenApiDiffItem,
    OpenApiSpec,
    ApprovalStatus,
    PlatformSettings,
    PublishedRoute,
    RiskLevel,
    Role,
    RoleName,
    Tool,
    ToolConfigRevision,
    ToolImportBatch,
    ToolIntegrationRoute,
    ToolStatus,
    ToolUpstream,
    ToolUpstreamInstance,
    User,
    UserImportBatch,
    UserRole,
)
from ..username import normalize_username_key
from ..schemas import (
    AdminApiKeyView,
    AdminUserProfileSummary,
    AdminUserOverview,
    AdminUserStats,
    AdminUserView,
    AdminResetPasswordResult,
    AdminUserRateLimitUpdateRequest,
    AdminUserStorageQuotaUpdateRequest,
    BulkPublishRequest,
    BulkPublishResult,
    BulkDiffDecisionRequest,
    BulkDiffDecisionResult,
    DiffDecisionRequest,
    EndpointCatalogPage,
    EndpointUpdateRequest,
    ImportBatchConfirmRequest,
    EndpointView,
    ImportBatchView,
    OpenApiDiffView,
    OpenApiParseRequest,
    GatewayCallPage,
    GatewayCallView,
    GatewaySettingsUpdateRequest,
    PublishRouteRequest,
    PlatformSettingsView,
    SiteSettingsUpdateRequest,
    EmailSettingsUpdateRequest,
    TestEmailRequest,
    ToolConfigRequest,
    ToolConfigurationCandidateRequest,
    ToolConfigurationRollbackRequest,
    ToolConfigurationRevisionView,
    ToolConfigurationTestResult,
    ToolConfigurationUpdateRequest,
    ToolConfigurationView,
    TaskAdapterUpdateRequest,
    ToolTokenUpdateRequest,
    ToolView,
    UserApprovalRequest,
    UserDefaultsUpdateRequest,
    UserRejectRequest,
    UserEventPage,
)
from ..services.audit import log_admin_event
from ..services.call_history import gateway_call_view, gateway_call_views
from ..services.email_delivery import test_smtp_connection
from ..services.email_templates import account_approved, account_disabled, account_rejected, smtp_test
from ..services.openapi_import import MAX_OPENAPI_DOCUMENT_BYTES, OpenApiImportError, load_openapi_document, parse_openapi_document_detailed
from ..services.resource_policies import access_policy_view, empty_resource_rules, suggest_access_policy, upsert_endpoint_access_policy, validate_access_policy
from ..services.security import decrypt_secret, encrypt_secret, hash_password, validate_password
from ..services.gateway_proxy import api_key_is_expired, mask_url, upstream_headers, validate_upstream_url
from ..services.platform_settings import default_user_rate_limit, ensure_platform_settings, get_effective_platform_settings
from ..services.remote_files import remote_file_usage
from ..services.user_lifecycle import add_notification, queue_email, utc_now
from .deps import get_user_roles, request_id, require_admin, validate_csrf


router = APIRouter(prefix="/api/admin", tags=["管理员"])
MAX_IMPORT_BYTES = 1_048_576
RISK_RANK = {
    RiskLevel.INFO.value: 0,
    RiskLevel.LOW.value: 1,
    RiskLevel.MEDIUM.value: 2,
    RiskLevel.HIGH.value: 3,
    RiskLevel.BLOCKER.value: 4,
}
AUDIT_ACTION_GROUPS = {
    "accounts": {
        "user_registered", "user_approved", "user_rejected", "user_disabled", "user_enabled",
        "user_import", "user_logged_in", "user_login_failed", "user_logged_out", "password_changed",
        "password_reset", "admin_password_reset", "email_verified", "email_changed", "profile_updated",
        "email_queued", "email_delivered", "email_delivery_failed", "user_rate_limit_updated", "user_storage_quota_updated",
    },
    "keys": {"api_key_created", "api_key_disabled", "api_key_enabled", "admin_api_key_disabled", "admin_api_key_enabled"},
    "tools": {
        "tool_created", "tool_updated", "tool_health_tested", "tool_release_validated",
        "tool_token_replaced", "tool_task_adapter_updated", "tool_configuration_tested",
        "tool_configuration_updated", "tool_configuration_rolled_back",
    },
    "interfaces": {
        "openapi_parsed", "openapi_uploaded", "openapi_imported", "openapi_sync_diff_created",
        "openapi_import_confirmed", "openapi_diff_decision", "openapi_diff_bulk_decision",
        "endpoint_updated", "endpoint_excluded", "endpoint_blocked", "route_published",
        "route_disabled", "routes_bulk_published",
    },
    "settings": {
        "platform_email_settings_updated", "platform_user_defaults_updated", "smtp_connection_tested",
        "smtp_test_email_queued", "user_rate_limit_updated", "platform_gateway_settings_updated", "platform_site_settings_updated", "platform_brand_image_updated",
    },
    "files": {
        "admin_file_download_authorized", "admin_file_download_completed", "admin_file_download_failed",
        "admin_file_download_interrupted", "remote_file_quarantined", "remote_file_sync_retried",
        "task_file_sync_retried", "tool_storage_endpoint_removed", "tool_integration_route_updated",
        "tool_file_access_updated",
    },
}


class ImportSizeExceeded(Exception):
    """导入输入超过单批允许的最大字节数。"""


class BoundedHashingReader(io.RawIOBase):
    """给 CSV 文本解码器提供带上限且计算摘要的二进制流。"""

    def __init__(self, source: BinaryIO, limit: int) -> None:
        self.source = source
        self.limit = limit
        self.bytes_read = 0
        self.digest = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:
        remaining = self.limit - self.bytes_read
        if remaining < 0:
            raise ImportSizeExceeded()
        data = self.source.read(min(len(buffer), remaining + 1))
        if not data:
            return 0
        self.bytes_read += len(data)
        if self.bytes_read > self.limit:
            raise ImportSizeExceeded()
        self.digest.update(data)
        buffer[: len(data)] = data
        return len(data)


def _json_loads(value: str, fallback: object) -> object:
    try:
        return json.loads(value or "")
    except json.JSONDecodeError:
        return fallback


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_source_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.hostname:
        return ""
    return f"{parsed.scheme}://{parsed.hostname}{parsed.path}"


def _parse_datetime_query(value: str | None, field_name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"{field_name} 时间格式不合法") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_dict(value: str) -> dict[str, object]:
    loaded = _json_loads(value, {})
    return loaded if isinstance(loaded, dict) else {}


def _json_list(value: str) -> list[object]:
    loaded = _json_loads(value, [])
    return loaded if isinstance(loaded, list) else []


def _monitor_conditions(
    *,
    user_id: str | None = None,
    tool_slug: str | None = None,
    status_code: int | None = None,
    request_id_filter: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[object]:
    conditions: list[object] = []
    if user_id:
        conditions.append(GatewayRequest.user_id == user_id)
    if tool_slug:
        conditions.append(GatewayRequest.tool_slug == tool_slug)
    if status_code is not None:
        conditions.append(GatewayRequest.status_code == status_code)
    if request_id_filter:
        conditions.append(GatewayRequest.id == request_id_filter)
    if start_time:
        conditions.append(GatewayRequest.created_at >= start_time)
    if end_time:
        conditions.append(GatewayRequest.created_at <= end_time)
    return conditions


def _monitor_filters(**kwargs: object):
    return select(GatewayRequest).where(*_monitor_conditions(**kwargs))


def _resolve_monitor_user_id(session: Session, user_id: str | None, username: str | None) -> str | None:
    if user_id:
        return user_id
    if not username:
        return None
    user = session.scalar(select(User).where(User.username_key == normalize_username_key(username)))
    return user.id if user else "__no_such_user__"


def _monitor_window(start: str | None, end: str | None, window_minutes: int) -> tuple[datetime, datetime]:
    """计算监控查询窗口；未指定开始时间时默认查看最近一段时间。"""

    effective_end = _parse_datetime_query(end, "end") or datetime.now(timezone.utc)
    effective_start = _parse_datetime_query(start, "start") or (effective_end - timedelta(minutes=window_minutes))
    return effective_start, effective_end


def _percentile_ms(values: list[int], percentile: float) -> int:
    """用最近秩法计算延迟分位数，适合本地 SQLite MVP 的轻量统计。"""

    if not values:
        return 0
    ordered = sorted(max(0, int(item)) for item in values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _rate(value: int, total: int) -> float:
    return round(value / total, 4) if total else 0


def validate_openapi_source_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="接口文档 URL 必须是 http 或 https 地址")
    if parsed.hostname.lower() not in get_settings().upstream_hosts:
        raise HTTPException(status_code=422, detail="接口文档 URL 不在允许上游主机白名单中")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or ".." in parsed.path:
        raise HTTPException(status_code=422, detail="接口文档 URL 不允许携带凭据、查询参数、片段或路径穿越")
    return url


def _looks_like_html_document(content: bytes, content_type: str) -> bool:
    """识别管理员误填的 Swagger UI/ReDoc HTML 页面，避免把 HTML 当规范文档解析。"""

    normalized_content_type = content_type.lower()
    if "text/html" in normalized_content_type or "application/xhtml" in normalized_content_type:
        return True
    prefix = content[:512].lstrip().lower()
    return prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html")


_OPENAPI_HTML_SPEC_PATTERN = re.compile(
    r"""(?ix)
    (?:url|href)\s*[:=]\s*
    ["'](?P<target>[^"']*(?:openapi|swagger|api-docs)[^"']*)["']
    """
)


def _openapi_discovery_candidates(source_url: str, content: bytes = b"") -> list[str]:
    """从同源文档页推导 OpenAPI/Swagger 规范地址，所有候选仍必须通过 URL 安全校验。"""

    parsed = urlparse(source_url)
    source_origin = (parsed.scheme, parsed.netloc)
    candidates: list[str] = []

    def add_candidate(raw_candidate: str) -> None:
        absolute_url = urljoin(source_url, raw_candidate.strip())
        candidate = urlparse(absolute_url)
        if (candidate.scheme, candidate.netloc) != source_origin:
            return
        try:
            safe_url = validate_openapi_source_url(absolute_url)
        except HTTPException:
            return
        if safe_url != source_url and safe_url not in candidates:
            candidates.append(safe_url)

    html_text = content[:65_536].decode("utf-8", errors="ignore")
    for match in _OPENAPI_HTML_SPEC_PATTERN.finditer(html_text):
        add_candidate(match.group("target"))

    directory = parsed.path.rsplit("/", 1)[0] if "/" in parsed.path else ""
    directory_prefix = directory.rstrip("/")
    sibling_prefix = directory_prefix if directory_prefix else ""
    for path in (
        f"{sibling_prefix}/openapi.json",
        f"{sibling_prefix}/openapi.yaml",
        f"{sibling_prefix}/openapi.yml",
        f"{sibling_prefix}/swagger.json",
        "/openapi.json",
        "/openapi.yaml",
        "/openapi.yml",
        "/swagger.json",
        "/v3/api-docs",
        "/api-docs",
    ):
        add_candidate(path)
    return candidates


async def _get_openapi_url_content(client: httpx.AsyncClient, source_url: str, upstream: ToolUpstream, current_request_id: str) -> tuple[bytes, str]:
    try:
        response = await client.get(
            source_url,
            headers=upstream_headers(upstream, current_request_id, accept="application/json, application/yaml, text/yaml, text/html"),
        )
    except httpx.ConnectError as error:
        raise HTTPException(status_code=502, detail="无法连接接口文档来源") from error
    except httpx.TimeoutException as error:
        raise HTTPException(status_code=504, detail="获取接口文档超时") from error
    if 300 <= response.status_code < 400:
        raise HTTPException(status_code=502, detail="接口文档来源返回了不允许的重定向")
    if response.status_code >= 400:
        raise HTTPException(status_code=422, detail=f"获取接口文档失败，状态码 {response.status_code}")
    content = response.content
    if len(content) > MAX_OPENAPI_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="接口文档不能超过 2 MiB")
    return content, response.headers.get("content-type", "")


async def _load_openapi_from_url(client: httpx.AsyncClient, source_url: str, upstream: ToolUpstream, current_request_id: str) -> tuple[object, str]:
    content, content_type = await _get_openapi_url_content(client, source_url, upstream, current_request_id)
    try:
        return load_openapi_document(content), source_url
    except OpenApiImportError as error:
        if not _looks_like_html_document(content, content_type):
            raise
        last_error: Exception = error
        for candidate_url in _openapi_discovery_candidates(source_url, content):
            try:
                candidate_content, _candidate_content_type = await _get_openapi_url_content(client, candidate_url, upstream, current_request_id)
                return load_openapi_document(candidate_content), candidate_url
            except (HTTPException, OpenApiImportError) as candidate_error:
                last_error = candidate_error
                continue
        raise OpenApiImportError(
            "接口文档来源返回的是 Swagger UI/HTML 页面；平台已尝试同源 /openapi.json、/swagger.json、/v3/api-docs 等规范地址但未成功，请填写 JSON/YAML 格式的 OpenAPI/Swagger 规范地址。"
        ) from last_error


async def check_tool_health(upstream: ToolUpstream, current_request_id: str = "health-check", base_url: str | None = None) -> dict[str, object]:
    health_path = upstream.health_path or "/health"
    if not health_path.startswith("/"):
        health_path = f"/{health_path}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5, connect=3), follow_redirects=False, verify=upstream.verify_tls) as client:
            response = await client.get(f"{(base_url or upstream.base_url).rstrip('/')}{health_path}", headers=upstream_headers(upstream, current_request_id))
        status = "ok" if response.status_code < 500 else "unavailable"
        return {"status": status, "reachable": response.status_code < 500, "status_code": response.status_code, "path": health_path}
    except httpx.HTTPError:
        return {"status": "unavailable", "reachable": False, "status_code": None, "path": health_path}


def _stored_tool_health(instance: ToolUpstreamInstance | None, upstream: ToolUpstream) -> dict[str, object]:
    """返回最近一次已记录的健康状态，避免列表读取被上游网络阻塞。"""
    status = instance.health_status if instance else "unknown"
    return {
        "status": status,
        "reachable": status in {"ok", "degraded"},
        "status_code": instance.last_health_status_code if instance else None,
        "path": upstream.health_path or "/health",
        "checked_at": instance.last_health_checked_at if instance else None,
    }


def _stored_tool_health_by_id(session: Session) -> dict[str, ToolUpstreamInstance]:
    """批量读取每个工具的默认上游状态，避免列表接口产生逐项查询。"""
    return {
        instance.tool_id: instance
        for instance in session.scalars(
            select(ToolUpstreamInstance).where(
                ToolUpstreamInstance.environment_name == "prod",
                ToolUpstreamInstance.instance_name == "default",
            )
        ).all()
    }


def _record_tool_health(instance: ToolUpstreamInstance | None, health: dict[str, object]) -> None:
    """把主动健康检查结果保存为列表页可直接读取的快照。"""
    if not instance:
        return
    instance.health_status = str(health.get("status") or "unknown")
    instance.last_health_status_code = health.get("status_code") if isinstance(health.get("status_code"), int) else None
    instance.last_health_checked_at = datetime.now(timezone.utc)
    instance.failure_count = 0 if health.get("reachable") else instance.failure_count + 1
    instance.last_error = "" if health.get("reachable") else "HEALTH_CHECK_FAILED"
    instance.updated_at = instance.last_health_checked_at


def _endpoint_counts(session: Session, tool_id: str) -> dict[str, int]:
    rows = session.execute(select(ApiEndpoint.status, func.count(ApiEndpoint.id)).where(ApiEndpoint.tool_id == tool_id).group_by(ApiEndpoint.status)).all()
    counts = {str(status): int(count) for status, count in rows}
    published_count = session.scalar(
        select(func.count(PublishedRoute.id))
        .join(ApiEndpoint, ApiEndpoint.id == PublishedRoute.endpoint_id)
        .where(
            PublishedRoute.tool_id == tool_id,
            PublishedRoute.is_enabled.is_(True),
            ApiEndpoint.status == EndpointStatus.PUBLISHED.value,
        )
    ) or 0
    inconsistent_route_count = session.scalar(
        select(func.count(PublishedRoute.id))
        .join(ApiEndpoint, ApiEndpoint.id == PublishedRoute.endpoint_id)
        .where(
            PublishedRoute.tool_id == tool_id,
            PublishedRoute.is_enabled.is_(True),
            ApiEndpoint.status != EndpointStatus.PUBLISHED.value,
        )
    ) or 0
    return {
        "endpoint_count": sum(counts.values()),
        "published_count": int(published_count),
        "draft_count": counts.get(EndpointStatus.DRAFT.value, 0) + counts.get(EndpointStatus.CANDIDATE.value, 0),
        "excluded_count": counts.get(EndpointStatus.EXCLUDED.value, 0),
        "blocked_count": counts.get(EndpointStatus.BLOCKED.value, 0),
        "inconsistent_route_count": int(inconsistent_route_count),
    }


def _latest_batch(session: Session, tool_id: str) -> ToolImportBatch | None:
    return session.scalar(select(ToolImportBatch).where(ToolImportBatch.tool_id == tool_id).order_by(ToolImportBatch.created_at.desc()).limit(1))


def _import_batch_governance_summary(session: Session, batch: ToolImportBatch) -> dict[str, object]:
    rows = session.scalars(select(OpenApiDiffItem).where(OpenApiDiffItem.batch_id == batch.id)).all()
    pending = sum(1 for item in rows if item.decision == "pending")
    blocked = sum(1 for item in rows if item.risk_level == RiskLevel.BLOCKER.value)
    excluded = sum(1 for item in rows if item.decision == "exclude")
    accepted = sum(1 for item in rows if item.decision == "accept")
    return {
        "diff_count": len(rows),
        "pending_decision_count": pending,
        "blocked_count": blocked,
        "excluded_count": excluded,
        "accepted_count": accepted,
        "requires_decision": pending > 0 or blocked > 0,
    }


def _tool_onboarding(session: Session, tool: Tool, upstream: ToolUpstream, health: dict[str, object] | None = None) -> dict[str, object]:
    latest_batch = _latest_batch(session, tool.id)
    counts = _endpoint_counts(session, tool.id)
    token_configured = bool(decrypt_secret(upstream.token_ciphertext))
    endpoint_rows = session.execute(
        select(ApiEndpoint, EndpointAccessPolicy)
        .outerjoin(EndpointAccessPolicy, EndpointAccessPolicy.endpoint_id == ApiEndpoint.id)
        .where(ApiEndpoint.tool_id == tool.id)
    ).all()
    pending_policy_count = sum(1 for endpoint, policy in endpoint_rows if endpoint.status not in {EndpointStatus.BLOCKED.value, EndpointStatus.EXCLUDED.value} and (not policy or not policy.confirmed_at))
    batch_summary = _import_batch_governance_summary(session, latest_batch) if latest_batch else {}
    pending_decision_count = int(batch_summary.get("pending_decision_count", 0))
    release_ready = bool(latest_batch and latest_batch.status == ImportBatchStatus.APPLIED.value and pending_decision_count == 0)
    steps = [
        {
            "key": "basic_info",
            "label": "工具基本信息",
            "done": bool(tool.slug and tool.name and tool.description.strip()),
            "hint": "补齐名称、slug、用途说明和不可发布边界。",
        },
        {
            "key": "deployment_server",
            "label": "部署服务器",
            "done": bool(upstream.base_url),
            "hint": "配置唯一上游根地址，地址必须命中白名单。",
        },
        {
            "key": "server_auth",
            "label": "服务端认证",
            "done": upstream.auth_type == "none" or token_configured,
            "hint": "Bearer/Header API Key 只允许替换，不回显。",
        },
        {
            "key": "health_check",
            "label": "健康检查",
            "done": bool((health or {}).get("reachable")),
            "hint": "执行工具连接测试，确认部署服务器真实可达。",
        },
        {
            "key": "api_source",
            "label": "OpenAPI/Swagger 来源",
            "done": latest_batch is not None,
            "hint": "通过 URL、上传或文本粘贴导入规范。",
        },
        {
            "key": "import_preview",
            "label": "导入预览与风险分析",
            "done": latest_batch is not None and counts["endpoint_count"] > 0,
            "hint": "导入后检查候选、阻断、排除和路径冲突。",
        },
        {
            "key": "access_policy",
            "label": "接口鉴权策略",
            "done": pending_policy_count == 0,
            "hint": "每个放行接口必须确认平台鉴权或资源归属策略。",
        },
        {
            "key": "route_publish",
            "label": "发布确认",
            "done": counts["published_count"] > 0,
            "hint": "一次性发布全部放行接口，发布动作必须写入审计原因。",
        },
    ]
    blockers = [step for step in steps if not step["done"]]
    return {
        "steps": steps,
        "completed_count": len(steps) - len(blockers),
        "total_count": len(steps),
        "next_action": blockers[0]["hint"] if blockers else "接入闭环已完成，可进入监控和版本维护。",
        "pending_access_policy_count": pending_policy_count,
        "latest_batch_id": latest_batch.id if latest_batch else None,
        "latest_batch_status": latest_batch.status if latest_batch else None,
        "latest_batch_pending_count": pending_decision_count,
        "release_ready": release_ready,
    }


def _sync_single_upstream_instance(session: Session, tool: Tool, upstream: ToolUpstream) -> None:
    instance = session.scalar(
        select(ToolUpstreamInstance).where(
            ToolUpstreamInstance.tool_id == tool.id,
            ToolUpstreamInstance.environment_name == "prod",
            ToolUpstreamInstance.instance_name == "default",
        )
    )
    if not instance:
        instance = ToolUpstreamInstance(
            tool_id=tool.id,
            upstream_config_id=upstream.id,
            environment_name="prod",
            instance_name="default",
            base_url=upstream.base_url,
        )
        session.add(instance)
    instance.upstream_config_id = upstream.id
    instance.base_url = upstream.base_url
    instance.priority = 100
    instance.is_enabled = True
    instance.updated_at = datetime.now(timezone.utc)
    stale_items = session.scalars(
        select(ToolUpstreamInstance).where(
            ToolUpstreamInstance.tool_id == tool.id,
            ToolUpstreamInstance.id != instance.id,
        )
    ).all()
    for item in stale_items:
        item.is_enabled = False
        item.updated_at = datetime.now(timezone.utc)


def tool_view(session: Session, tool: Tool, upstream: ToolUpstream, health: dict[str, object] | None = None) -> ToolView:
    latest_spec = session.scalar(select(OpenApiSpec).where(OpenApiSpec.tool_id == tool.id).order_by(OpenApiSpec.imported_at.desc()).limit(1))
    latest_batch = _latest_batch(session, tool.id)
    counts = _endpoint_counts(session, tool.id)
    return ToolView(
        id=tool.id,
        slug=tool.slug,
        name=tool.name,
        description=tool.description,
        status=tool.status,
        is_enabled=tool.is_enabled,
        base_url_masked=mask_url(upstream.base_url),
        rate_limit_per_minute=upstream.rate_limit_per_minute,
        auth_type=upstream.auth_type,
        token_configured=bool(decrypt_secret(upstream.token_ciphertext)),
        token_label=upstream.token_label,
        health_path=upstream.health_path,
        environment_mode="single",
        environments=[],
        discovery_type=upstream.discovery_type,
        network_zone=upstream.network_zone,
        verify_tls=upstream.verify_tls,
        retry_count=upstream.retry_count,
        network_isolation_mode=upstream.network_isolation_mode,
        network_isolation_note=upstream.network_isolation_note,
        network_isolation_confirmed=upstream.network_isolation_confirmed_at is not None,
        health=health,
        latest_openapi_version=latest_spec.version if latest_spec else None,
        latest_openapi_sha256=latest_spec.sha256 if latest_spec else None,
        latest_openapi_imported_at=latest_spec.imported_at if latest_spec else None,
        latest_import_summary=json.loads(latest_batch.summary_json) if latest_batch else None,
        onboarding=_tool_onboarding(session, tool, upstream, health),
        task_adapter=_json_loads(tool.task_adapter_json, {}),
        file_retention_days=tool.file_retention_days,
        storage_quota_bytes=tool.storage_quota_bytes,
        max_file_bytes=tool.max_file_bytes,
        file_access_enabled=tool.file_access_enabled,
        file_quarantined=tool.file_quarantined,
        storage_total_bytes=tool.storage_total_bytes,
        storage_free_bytes=tool.storage_free_bytes,
        storage_checked_at=tool.storage_checked_at,
        storage_risk_acknowledged=tool.storage_risk_acknowledged,
        **counts,
    )


def _validated_task_adapter(session: Session, tool: Tool, value: dict[str, object]) -> dict[str, object]:
    """任务和文件适配器只能引用同一工具的已导入接口或已启用路由。"""
    if not value:
        return {}
    version = value.get("version")
    if version not in {1, 2}:
        raise HTTPException(status_code=422, detail="任务适配器 version 必须为 1 或 2")
    id_param = str(value.get("id_param") or "job_id").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", id_param):
        raise HTTPException(status_code=422, detail="任务适配器 id_param 不合法")
    routes = value.get("routes")
    if not isinstance(routes, dict):
        raise HTTPException(status_code=422, detail="任务适配器 routes 必须是对象")
    allowed_keys = {"status", "logs", "manifest", "artifacts", "download", "bundle_download", "file_download", "file_delete", "bundle_delete", "delete", "storage", "storage_watermark"}
    normalized_routes: dict[str, str] = {}
    for name, route_value in routes.items():
        if name not in allowed_keys:
            raise HTTPException(status_code=422, detail=f"任务适配器路由配置不合法: {name}")
        if isinstance(route_value, str) and route_value:
            route = session.get(PublishedRoute, route_value)
            integration = session.get(ToolIntegrationRoute, route_value)
            if route and route.tool_id == tool.id and route.is_enabled:
                normalized_routes[name] = route.id
            elif integration and integration.tool_id == tool.id and integration.is_enabled:
                normalized_routes[name] = integration.id
            else:
                raise HTTPException(status_code=422, detail=f"任务适配器 {name} 必须引用本工具的有效路由")
            continue
        if version != 2 or not isinstance(route_value, dict):
            raise HTTPException(status_code=422, detail=f"任务适配器路由配置不合法: {name}")
        endpoint_id = str(route_value.get("endpoint_id") or "")
        endpoint = session.get(ApiEndpoint, endpoint_id)
        if not endpoint or endpoint.tool_id != tool.id:
            raise HTTPException(status_code=422, detail=f"任务适配器 {name} 必须选择本工具已导入的接口")
        method = str(route_value.get("method") or endpoint.method).upper()
        path_template = str(route_value.get("path_template") or endpoint.upstream_path).strip()
        allowed_methods = {"DELETE", "POST"} if name in {"file_delete", "bundle_delete", "delete"} else {"GET", "HEAD"}
        if method not in allowed_methods or path_template != endpoint.upstream_path:
            raise HTTPException(status_code=422, detail=f"任务适配器 {name} 的方法或路径与导入接口不一致")
        config = {key: raw for key, raw in route_value.items() if key not in {"endpoint_id", "method", "path_template"}}
        integration = session.scalar(select(ToolIntegrationRoute).where(ToolIntegrationRoute.tool_id == tool.id, ToolIntegrationRoute.capability == name))
        if not integration:
            integration = ToolIntegrationRoute(tool_id=tool.id, endpoint_id=endpoint.id, capability=name, method=method, path_template=path_template)
            session.add(integration)
        integration.endpoint_id = endpoint.id
        integration.method = method
        integration.path_template = path_template
        integration.config_json = _json_dumps(config)
        integration.is_enabled = True
        session.flush()
        normalized_routes[name] = integration.id
    statuses = value.get("terminal_statuses", ["succeeded", "failed", "cancelled"])
    if not isinstance(statuses, list) or not statuses or not all(isinstance(item, str) and item for item in statuses):
        raise HTTPException(status_code=422, detail="任务适配器 terminal_statuses 不合法")
    result: dict[str, object] = {
        "version": version,
        "id_param": id_param,
        "status_selector": str(value.get("status_selector") or "$.status"),
        "terminal_statuses": [str(item) for item in statuses],
        "routes": normalized_routes,
    }
    if version == 2:
        result["file_id_param"] = str(value.get("file_id_param") or "file_id")
        result["task_id_selector"] = str(value.get("task_id_selector") or "$.job_id")
        for section in ("artifacts", "response_files"):
            config = value.get(section)
            if config is not None and not isinstance(config, dict):
                raise HTTPException(status_code=422, detail=f"任务适配器 {section} 必须是对象")
            if isinstance(config, dict):
                for key, selector in config.items():
                    if key.endswith("selector"):
                        try:
                            parse_jsonpath(str(selector))
                        except Exception as error:
                            raise HTTPException(status_code=422, detail=f"任务适配器 {section}.{key} JSONPath 不合法") from error
                result[section] = config
    return result


def admin_user_view(
    session: Session,
    user: User,
    key_stats: tuple[int, int, datetime | None] | None = None,
) -> AdminUserView:
    key_count, active_key_count, last_api_activity_at = key_stats or (0, 0, None)
    return AdminUserView(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        email_verified=user.email_verified_at is not None,
        roles=get_user_roles(session, user.id),
        is_active=user.is_active,
        approval_status=user.approval_status,
        must_change_password=user.must_change_password,
        registered_at=user.registered_at,
        approved_by_user_id=user.approved_by_user_id,
        approved_at=user.approved_at,
        rejected_at=user.rejected_at,
        rejection_reason=user.rejection_reason,
        disabled_at=user.disabled_at,
        disabled_by_user_id=user.disabled_by_user_id,
        disable_reason=user.disable_reason,
        registration_note=user.registration_note,
        rate_limit_per_minute=user.rate_limit_per_minute,
        storage_quota_bytes=user.storage_quota_bytes,
        storage_used_bytes=remote_file_usage(session, user.id),
        api_key_count=key_count,
        active_api_key_count=active_key_count,
        last_api_activity_at=last_api_activity_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def platform_settings_view(session: Session) -> PlatformSettingsView:
    effective = get_effective_platform_settings(session)
    row = session.get(PlatformSettings, 1)
    return PlatformSettingsView(
        smtp_enabled=effective.smtp_enabled,
        smtp_configured=effective.smtp_configured,
        smtp_host=effective.smtp_host,
        smtp_port=effective.smtp_port,
        smtp_username=effective.smtp_username,
        smtp_password_configured=bool(effective.smtp_password),
        smtp_from_email=effective.smtp_from_email,
        smtp_from_name=effective.smtp_from_name,
        smtp_use_tls=effective.smtp_use_tls,
        default_user_rate_limit_per_minute=effective.default_user_rate_limit_per_minute,
        default_user_storage_quota_bytes=effective.default_user_storage_quota_bytes,
        default_file_retention_days=effective.default_file_retention_days,
        public_gateway_base_url=effective.public_gateway_base_url,
        gateway_source=effective.gateway_source,
        source=effective.source,
        updated_at=row.updated_at if row else None,
        site_name=effective.site_name,
        site_subtitle=effective.site_subtitle,
        registration_enabled=effective.registration_enabled,
        brand_image_url=effective.brand_image_url,
    )


def _admin_user_key_stats(session: Session, user_id: str) -> tuple[int, int, datetime | None]:
    now = datetime.now(timezone.utc)
    row = session.execute(
        select(
            func.count(ApiKey.id),
            func.sum(
                case(
                    (
                        (ApiKey.status == KeyStatus.ACTIVE.value)
                        & (ApiKey.expires_at.is_(None) | (ApiKey.expires_at > now)),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.max(ApiKey.last_used_at),
        ).where(ApiKey.user_id == user_id)
    ).one()
    return int(row[0] or 0), int(row[1] or 0), row[2]


def admin_api_key_view(session: Session, api_key: ApiKey) -> AdminApiKeyView:
    owner = session.get(User, api_key.user_id)
    scopes = [item.strip() for item in api_key.scopes.split(",") if item.strip()]
    expired = api_key_is_expired(api_key)
    if api_key.status == KeyStatus.REVOKED.value:
        rotation_hint = "该 Key 已撤销且不可恢复；请通知用户生成新 Key。"
    elif expired:
        rotation_hint = "该 Key 已过期；请通知用户轮换。"
    elif api_key.status == KeyStatus.DISABLED.value:
        rotation_hint = "该 Key 已禁用；确认风险解除后，管理员可以重新启用。"
    elif api_key.last_used_at is None:
        rotation_hint = "该 Key 尚未使用；可提醒用户先执行快速接入最小调用。"
    else:
        rotation_hint = "如出现异常调用，可先禁用该 Key，再让用户生成新 Key。"
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_failure_count = session.scalar(
        select(func.count(GatewayRequest.id)).where(
            GatewayRequest.api_key_id == api_key.id,
            GatewayRequest.created_at >= since,
            GatewayRequest.status_code >= 400,
        )
    ) or 0
    recent_rate_limited_count = session.scalar(
        select(func.count(GatewayRequest.id)).where(
            GatewayRequest.api_key_id == api_key.id,
            GatewayRequest.created_at >= since,
            GatewayRequest.status_code == 429,
        )
    ) or 0
    latest_error = session.scalar(
        select(GatewayRequest.error_code)
        .where(GatewayRequest.api_key_id == api_key.id, GatewayRequest.status_code >= 400)
        .order_by(GatewayRequest.created_at.desc())
        .limit(1)
    )
    return AdminApiKeyView(
        id=api_key.id,
        user_id=api_key.user_id,
        username=owner.username if owner else "",
        label=api_key.label,
        prefix=api_key.prefix,
        status=api_key.status,
        scopes=scopes or ["*"],
        expires_at=api_key.expires_at,
        last_used_at=api_key.last_used_at,
        disabled_at=api_key.disabled_at,
        disabled_by_user_id=api_key.disabled_by_user_id,
        disable_reason=api_key.disable_reason,
        can_enable=(api_key.status == KeyStatus.DISABLED.value and not expired),
        is_expired=expired,
        rotation_hint=rotation_hint,
        created_at=api_key.created_at,
        recent_failure_count=int(recent_failure_count),
        recent_rate_limited_count=int(recent_rate_limited_count),
        last_error_code=latest_error,
    )


def get_tool_and_upstream(session: Session, tool_id: str) -> tuple[Tool, ToolUpstream]:
    tool = session.get(Tool, tool_id)
    upstream = session.scalar(select(ToolUpstream).where(ToolUpstream.tool_id == tool_id))
    if not tool or not upstream:
        raise HTTPException(status_code=404, detail="工具不存在")
    return tool, upstream


def _tool_configuration_data(tool: Tool, upstream: ToolUpstream) -> dict[str, object]:
    """生成可审计的工具配置快照，密钥密文由独立字段保存。"""
    return {
        "name": tool.name,
        "description": tool.description,
        "status": tool.status,
        "default_gateway_prefix": tool.default_gateway_prefix,
        "base_url": upstream.base_url,
        "health_path": upstream.health_path,
        "auth_type": upstream.auth_type,
        "token_label": upstream.token_label,
        "rate_limit_per_minute": upstream.rate_limit_per_minute,
        "network_zone": upstream.network_zone,
        "verify_tls": upstream.verify_tls,
        "retry_count": upstream.retry_count,
        "connect_timeout_seconds": upstream.connect_timeout_seconds,
        "request_timeout_seconds": upstream.request_timeout_seconds,
        "network_isolation_mode": upstream.network_isolation_mode,
        "network_isolation_note": upstream.network_isolation_note,
        "network_isolation_confirmed": upstream.network_isolation_confirmed_at is not None,
        "file_retention_days": tool.file_retention_days,
        "storage_quota_bytes": tool.storage_quota_bytes,
        "max_file_bytes": tool.max_file_bytes,
        "file_access_enabled": tool.file_access_enabled,
        "storage_risk_acknowledged": tool.storage_risk_acknowledged,
    }


def _tool_configuration_view(tool: Tool, upstream: ToolUpstream) -> ToolConfigurationView:
    return ToolConfigurationView(
        tool_id=tool.id,
        slug=tool.slug,
        is_enabled=tool.is_enabled,
        token_configured=bool(decrypt_secret(upstream.token_ciphertext)),
        config_revision=upstream.config_revision,
        updated_at=upstream.updated_at,
        **_tool_configuration_data(tool, upstream),
    )


def _validate_tool_candidate(
    upstream: ToolUpstream,
    payload: ToolConfigurationCandidateRequest,
) -> tuple[str, str]:
    try:
        base_url = validate_upstream_url(payload.base_url)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if payload.upstream_token and payload.clear_upstream_token:
        raise HTTPException(status_code=422, detail="不能同时填写新密钥并清除现有密钥")
    if not payload.network_zone:
        raise HTTPException(status_code=422, detail="网络区域不能为空")
    if payload.auth_type == "header_api_key" and not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", payload.token_label):
        raise HTTPException(status_code=422, detail="Header API Key 模式必须填写合法的请求头名称")

    if payload.auth_type == "none":
        token_ciphertext = ""
    elif payload.upstream_token:
        token_ciphertext = encrypt_secret(payload.upstream_token)
    elif payload.clear_upstream_token:
        token_ciphertext = ""
    else:
        token_ciphertext = upstream.token_ciphertext
    if payload.auth_type != "none" and not decrypt_secret(token_ciphertext):
        raise HTTPException(status_code=422, detail="当前认证模式必须配置上游密钥")
    return base_url, token_ciphertext


def _candidate_upstream(
    upstream: ToolUpstream,
    payload: ToolConfigurationCandidateRequest,
    base_url: str,
    token_ciphertext: str,
) -> ToolUpstream:
    return ToolUpstream(
        id=upstream.id,
        tool_id=upstream.tool_id,
        base_url=base_url,
        token_ciphertext=token_ciphertext,
        auth_type=payload.auth_type,
        token_label=payload.token_label if payload.auth_type == "header_api_key" else "",
        health_path=payload.health_path,
        verify_tls=payload.verify_tls,
        retry_count=payload.retry_count,
        rate_limit_per_minute=payload.rate_limit_per_minute,
        connect_timeout_seconds=payload.connect_timeout_seconds,
        request_timeout_seconds=payload.request_timeout_seconds,
        network_zone=payload.network_zone,
        network_isolation_mode=payload.network_isolation_mode,
        network_isolation_note=payload.network_isolation_note,
    )


def _candidate_configuration_data(
    payload: ToolConfigurationCandidateRequest,
    base_url: str,
) -> dict[str, object]:
    return {
        "name": payload.name,
        "description": payload.description,
        "status": payload.status,
        "default_gateway_prefix": payload.default_gateway_prefix,
        "base_url": base_url,
        "health_path": payload.health_path,
        "auth_type": payload.auth_type,
        "token_label": payload.token_label if payload.auth_type == "header_api_key" else "",
        "rate_limit_per_minute": payload.rate_limit_per_minute,
        "network_zone": payload.network_zone,
        "verify_tls": payload.verify_tls,
        "retry_count": payload.retry_count,
        "connect_timeout_seconds": payload.connect_timeout_seconds,
        "request_timeout_seconds": payload.request_timeout_seconds,
        "network_isolation_mode": payload.network_isolation_mode,
        "network_isolation_note": payload.network_isolation_note,
        "network_isolation_confirmed": payload.network_isolation_confirmed,
        "file_retention_days": payload.file_retention_days,
        "storage_quota_bytes": payload.storage_quota_bytes,
        "max_file_bytes": payload.max_file_bytes,
        "file_access_enabled": payload.file_access_enabled,
        "storage_risk_acknowledged": payload.storage_risk_acknowledged,
    }


def _configuration_changed_fields(
    tool: Tool,
    upstream: ToolUpstream,
    payload: ToolConfigurationCandidateRequest,
    base_url: str,
    token_ciphertext: str,
) -> list[str]:
    current = _tool_configuration_data(tool, upstream)
    candidate = _candidate_configuration_data(payload, base_url)
    changed = [key for key, value in candidate.items() if current.get(key) != value]
    if upstream.token_ciphertext != token_ciphertext:
        changed.append("upstream_token")
    return changed


def _requires_candidate_health_check(tool: Tool, payload: ToolConfigurationCandidateRequest, changed_fields: list[str]) -> bool:
    routing_fields = {"base_url", "health_path", "auth_type", "upstream_token", "verify_tls"}
    return payload.status == ToolStatus.ACTIVE.value and (
        tool.status != ToolStatus.ACTIVE.value or bool(routing_fields.intersection(changed_fields))
    )


def _snapshot_tool_configuration(
    session: Session,
    tool: Tool,
    upstream: ToolUpstream,
    admin: User,
    reason: str,
) -> None:
    existing = session.scalar(
        select(ToolConfigRevision).where(
            ToolConfigRevision.tool_id == tool.id,
            ToolConfigRevision.config_revision == upstream.config_revision,
        )
    )
    if existing:
        return
    session.add(
        ToolConfigRevision(
            tool_id=tool.id,
            config_revision=upstream.config_revision,
            config_json=_json_dumps(_tool_configuration_data(tool, upstream)),
            token_ciphertext=upstream.token_ciphertext,
            changed_by_user_id=admin.id,
            reason=reason.strip(),
        )
    )


def _apply_tool_configuration(
    session: Session,
    tool: Tool,
    upstream: ToolUpstream,
    data: dict[str, object],
    token_ciphertext: str,
    admin: User,
) -> None:
    now = datetime.now(timezone.utc)
    tool.name = str(data["name"])
    tool.description = str(data["description"])
    tool.status = str(data["status"])
    tool.default_gateway_prefix = str(data["default_gateway_prefix"])
    tool.file_retention_days = int(data.get("file_retention_days") or 0)
    tool.storage_quota_bytes = int(data.get("storage_quota_bytes") or 0)
    tool.max_file_bytes = int(data.get("max_file_bytes") or 0)
    tool.file_access_enabled = bool(data.get("file_access_enabled", True))
    tool.storage_risk_acknowledged = bool(data.get("storage_risk_acknowledged", False))
    tool.is_enabled = tool.status != ToolStatus.DISABLED.value
    tool.updated_at = now
    upstream.base_url = str(data["base_url"])
    upstream.health_path = str(data["health_path"])
    upstream.auth_type = str(data["auth_type"])
    upstream.token_label = str(data["token_label"])
    upstream.token_ciphertext = token_ciphertext
    upstream.rate_limit_per_minute = int(data["rate_limit_per_minute"])
    upstream.network_zone = str(data["network_zone"])
    upstream.verify_tls = bool(data["verify_tls"])
    upstream.retry_count = int(data["retry_count"])
    upstream.connect_timeout_seconds = int(data["connect_timeout_seconds"])
    upstream.request_timeout_seconds = int(data["request_timeout_seconds"])
    upstream.network_isolation_mode = str(data["network_isolation_mode"])
    upstream.network_isolation_note = str(data["network_isolation_note"])
    if bool(data["network_isolation_confirmed"]):
        upstream.network_isolation_confirmed_by_user_id = admin.id
        upstream.network_isolation_confirmed_at = now
    else:
        upstream.network_isolation_confirmed_by_user_id = None
        upstream.network_isolation_confirmed_at = None
    upstream.environment_mode = "single"
    upstream.environments_json = _json_dumps(
        [{"name": "prod", "instance_name": "default", "base_url": upstream.base_url, "role": "production", "enabled": True, "priority": 100}]
    )
    upstream.config_revision += 1
    upstream.updated_at = now
    session.flush()
    _sync_single_upstream_instance(session, tool, upstream)


def _endpoint_view_with_related(
    endpoint: ApiEndpoint,
    route: PublishedRoute | None,
    access_policy_row: EndpointAccessPolicy | None,
) -> EndpointView:
    content_types = _json_loads(endpoint.content_types, [])
    risk_flags = _json_loads(endpoint.risk_flags_json, [])
    parameters = _json_loads(endpoint.parameters_json, [])
    request_body = _json_loads(endpoint.request_body_json, {})
    responses = _json_loads(endpoint.responses_json, {})
    governance = _json_loads(endpoint.governance_json, {})
    route_policy = None
    if route:
        route_policy = {
            "id": route.id,
            "method": route.method,
            "gateway_path": route.gateway_path,
            "upstream_path": route.upstream_path,
            "allowed_content_types": _json_loads(route.allowed_content_types_json, []),
            "max_request_bytes": route.max_request_bytes,
            "max_response_bytes": route.max_response_bytes,
            "request_timeout_seconds": route.request_timeout_seconds,
            "allow_stream_upload": route.allow_stream_upload,
            "allow_stream_download": route.allow_stream_download,
            "access_mode": route.access_mode,
            "resource_policy": _json_loads(route.resource_policy_json, {}),
            "policy_version": route.policy_version,
            "require_idempotency_key": route.require_idempotency_key,
            "allow_retry": route.allow_retry,
            "risk_level": route.risk_level,
            "route_version": route.route_version,
            "policy": _json_loads(route.policy_json, {}),
        }
    suggestion = suggest_access_policy(
        method=endpoint.method,
        path=endpoint.upstream_path,
        summary=f"{endpoint.summary or ''} {endpoint.operation_id or ''}",
        risk_flags=set(risk_flags if isinstance(risk_flags, list) else []),
        request_body=request_body,
        responses=responses,
    )
    return EndpointView(
        id=endpoint.id,
        method=endpoint.method,
        upstream_path=endpoint.upstream_path,
        gateway_path=endpoint.gateway_path or endpoint.upstream_path,
        operation_id=endpoint.operation_id,
        summary=endpoint.summary,
        content_types=content_types if isinstance(content_types, list) else [],
        group_name=endpoint.group_name,
        status=endpoint.status,
        import_action=endpoint.import_action,
        risk_level=endpoint.risk_level,
        risk_flags=risk_flags if isinstance(risk_flags, list) else [],
        governance=governance if isinstance(governance, dict) else {},
        parameters=parameters if isinstance(parameters, list) else [],
        request_body=request_body if isinstance(request_body, dict) else {},
        responses=responses if isinstance(responses, dict) else {},
        exclusion_reason=endpoint.exclusion_reason,
        public_description=endpoint.public_description,
        route_policy=route_policy,
        access_policy=access_policy_view(access_policy_row, suggestion),
        published=bool(route),
        enabled=route.is_enabled if route else False,
    )


def _endpoint_view(session: Session, endpoint: ApiEndpoint) -> EndpointView:
    route = session.scalar(select(PublishedRoute).where(PublishedRoute.endpoint_id == endpoint.id))
    access_policy_row = session.scalar(select(EndpointAccessPolicy).where(EndpointAccessPolicy.endpoint_id == endpoint.id))
    return _endpoint_view_with_related(endpoint, route, access_policy_row)


def _disable_endpoint_route(
    session: Session,
    endpoint: ApiEndpoint,
    *,
    reason: str,
    admin_user_id: str | None = None,
) -> PublishedRoute | None:
    """停用接口已有路由，保证治理状态和数据面状态一致。"""
    route = session.scalar(select(PublishedRoute).where(PublishedRoute.endpoint_id == endpoint.id))
    if not route or not route.is_enabled:
        return route
    route.is_enabled = False
    route.published_by_user_id = admin_user_id or route.published_by_user_id
    route.publish_reason = reason
    route.route_version = (route.route_version or 1) + 1
    route.updated_at = datetime.now(timezone.utc)
    return route


def _batch_view(session: Session, batch: ToolImportBatch) -> ImportBatchView:
    summary = _json_loads(batch.summary_json, {})
    return ImportBatchView(
        id=batch.id,
        tool_id=batch.tool_id,
        spec_id=batch.spec_id,
        source_type=batch.source_type,
        source_url=batch.source_url,
        source_sha256=batch.source_sha256,
        openapi_version=batch.openapi_version,
        total_operations=batch.total_operations,
        selected_operations=batch.selected_operations,
        added_count=batch.added_count,
        changed_count=batch.changed_count,
        unchanged_count=batch.unchanged_count,
        excluded_count=batch.excluded_count,
        blocked_count=batch.blocked_count,
        status=batch.status,
        summary=summary if isinstance(summary, dict) else {},
        governance_summary=_import_batch_governance_summary(session, batch),
        created_at=batch.created_at,
    )


def _diff_view(session: Session, diff: OpenApiDiffItem) -> OpenApiDiffView:
    risk_flags = _json_loads(diff.risk_flags_json, [])
    detail = _json_loads(diff.detail_json, {})
    operation = detail.get("operation") if isinstance(detail, dict) else {}
    description = operation.get("description") if isinstance(operation, dict) and isinstance(operation.get("description"), str) else ""
    endpoint = session.get(ApiEndpoint, diff.endpoint_id) if diff.endpoint_id else None
    suggestion = suggest_access_policy(
        method=diff.method,
        path=diff.upstream_path,
        summary=f"{diff.summary} {operation.get('operation_id', '') if isinstance(operation, dict) else ''}",
        risk_flags=set(risk_flags if isinstance(risk_flags, list) else []),
        request_body=operation.get("request_body", {}) if isinstance(operation, dict) else {},
        responses=operation.get("responses", {}) if isinstance(operation, dict) else {},
    )
    policy_row = session.scalar(select(EndpointAccessPolicy).where(EndpointAccessPolicy.endpoint_id == endpoint.id)) if endpoint else None
    policy_view = access_policy_view(policy_row, suggestion)
    return OpenApiDiffView(
        id=diff.id,
        batch_id=diff.batch_id,
        endpoint_id=diff.endpoint_id,
        method=diff.method,
        upstream_path=diff.upstream_path,
        gateway_path=diff.gateway_path,
        summary=diff.summary,
        description=description,
        diff_type=diff.diff_type,
        risk_level=diff.risk_level,
        risk_flags=risk_flags if isinstance(risk_flags, list) else [],
        decision=diff.decision,
        detail=detail if isinstance(detail, dict) else {},
        access_policy_suggestion=suggestion,
        access_policy=policy_view,
        access_policy_complete=bool(policy_view.get("complete") and policy_view.get("confirmed")),
        access_policy_missing_fields=[str(item) for item in policy_view.get("missing_fields", [])],
        created_at=diff.created_at,
    )


def _operation_governance(operation: dict[str, object], upstream: ToolUpstream) -> dict[str, object]:
    return {
        "operation_hash": operation["operation_hash"],
        "timeout_seconds": upstream.request_timeout_seconds,
        "rate_limit_per_minute": upstream.rate_limit_per_minute,
        "audit_required": operation["risk_level"] in {"high", "blocker"},
        "max_upload_bytes": get_settings().max_upload_bytes,
    }


def _route_policy_from_endpoint(endpoint: ApiEndpoint, tool: Tool, upstream: ToolUpstream) -> dict[str, object]:
    """根据导入元数据生成发布路由的执行策略快照。"""
    settings = get_settings()
    content_types = _json_loads(endpoint.content_types, [])
    content_type_set = {str(item).lower() for item in content_types if isinstance(item, str)}
    responses = _json_loads(endpoint.responses_json, {})
    response_content_types: set[str] = set()
    if isinstance(responses, dict):
        for response_meta in responses.values():
            if isinstance(response_meta, dict):
                content = response_meta.get("content")
                if isinstance(content, dict):
                    response_content_types.update(str(item).lower() for item in content.keys())
    risk_flags = _json_loads(endpoint.risk_flags_json, [])
    risk_flag_set = set(risk_flags if isinstance(risk_flags, list) else [])
    governance = _json_loads(endpoint.governance_json, {})
    storage_action = governance.get("storage_action", "none") if isinstance(governance, dict) else "none"
    if storage_action not in {"none", "upload_file", "create_task", "produce_files", "delete_file"}:
        storage_action = "none"
    identity_text = f"{endpoint.operation_id or ''} {endpoint.summary or ''} {endpoint.upstream_path}".lower()
    allow_stream_upload = bool(content_type_set.intersection({"multipart/form-data", "application/octet-stream"}))
    allow_stream_download = bool(
        response_content_types.intersection({"application/octet-stream", "application/zip", "application/x-zip-compressed"})
        or any(marker in identity_text for marker in ("download", "artifact", "export", "zip"))
    )
    return {
        "allowed_content_types": sorted(content_type_set),
        "max_request_bytes": settings.max_upload_bytes,
        "max_response_bytes": settings.max_download_bytes,
        "request_timeout_seconds": upstream.request_timeout_seconds,
        "allow_stream_upload": allow_stream_upload,
        "allow_stream_download": allow_stream_download,
        "require_idempotency_key": False,
        "allow_retry": endpoint.method.upper() == "GET",
        "risk_level": endpoint.risk_level,
        "storage_action": storage_action,
        "policy_json": {
            "risk_flags": sorted(str(item) for item in risk_flag_set),
            "operation_id": endpoint.operation_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def _apply_route_policy(route: PublishedRoute, policy: dict[str, object]) -> None:
    route.allowed_content_types_json = json.dumps(policy["allowed_content_types"], ensure_ascii=False)
    route.max_request_bytes = int(policy["max_request_bytes"])
    route.max_response_bytes = int(policy["max_response_bytes"])
    route.request_timeout_seconds = int(policy["request_timeout_seconds"])
    route.allow_stream_upload = bool(policy["allow_stream_upload"])
    route.allow_stream_download = bool(policy["allow_stream_download"])
    route.require_idempotency_key = bool(policy["require_idempotency_key"])
    route.allow_retry = bool(policy["allow_retry"])
    route.risk_level = str(policy["risk_level"])
    route.storage_action = str(policy["storage_action"])
    route.policy_json = json.dumps(policy["policy_json"], ensure_ascii=False, separators=(",", ":"))


def _has_gateway_conflict(session: Session, tool_id: str, endpoint_id: str | None, method: str, gateway_path: str) -> bool:
    statement = select(ApiEndpoint).where(ApiEndpoint.tool_id == tool_id, ApiEndpoint.method == method, ApiEndpoint.gateway_path == gateway_path)
    if endpoint_id:
        statement = statement.where(ApiEndpoint.id != endpoint_id)
    return session.scalar(statement.limit(1)) is not None


def _apply_import_operation_to_endpoint(
    endpoint: ApiEndpoint,
    *,
    operation: dict[str, object],
    upstream: ToolUpstream,
    spec_id: str | None,
    diff_type: str,
    risk_level: str,
    risk_flags: list[str],
) -> None:
    """在确认发布阶段把暂存的 OpenAPI 操作应用到正式接口。"""
    previous_summary = endpoint.summary
    previous_public_description = endpoint.public_description
    imported_description = str(operation.get("description") or "").strip()
    endpoint.gateway_path = str(operation["gateway_path"])
    endpoint.operation_id = operation["operation_id"] if isinstance(operation.get("operation_id"), str) else None
    endpoint.summary = str(operation["summary"])
    endpoint.content_types = _json_dumps(operation.get("content_types", []))
    endpoint.group_name = str(operation.get("group_name") or "")
    endpoint.import_action = diff_type
    endpoint.risk_level = risk_level
    endpoint.risk_flags_json = _json_dumps(sorted(set(risk_flags)))
    endpoint.governance_json = _json_dumps(_operation_governance(operation, upstream))
    endpoint.parameters_json = _json_dumps(operation.get("parameters", []))
    endpoint.request_body_json = _json_dumps(operation.get("request_body", {}))
    endpoint.responses_json = _json_dumps(operation.get("responses", {}))
    endpoint.source_spec_id = spec_id
    endpoint.exclusion_reason = str(operation.get("exclusion_reason") or "")
    if imported_description and (not previous_public_description or previous_public_description == previous_summary):
        endpoint.public_description = imported_description
    elif not previous_public_description:
        endpoint.public_description = imported_description or endpoint.summary
    endpoint.updated_at = datetime.now(timezone.utc)


def apply_openapi_document(
    session: Session,
    *,
    tool: Tool,
    upstream: ToolUpstream,
    admin: User,
    document: object,
    source_type: str,
    source_url: str,
) -> tuple[ToolImportBatch, list[OpenApiDiffItem]]:
    metadata, operations = parse_openapi_document_detailed(document, gateway_prefix=tool.default_gateway_prefix)
    spec = OpenApiSpec(
        tool_id=tool.id,
        version=str(metadata["version"]),
        sha256=str(metadata["sha256"]),
        source_type=source_type,
        source_url=_safe_source_url(source_url),
        title=str(metadata.get("title") or ""),
        summary_json=_json_dumps(metadata),
        document_json=json.dumps(document, ensure_ascii=False, separators=(",", ":")),
        imported_by_user_id=admin.id,
    )
    session.add(spec)
    session.flush()

    existing_rows = session.scalars(select(ApiEndpoint).where(ApiEndpoint.tool_id == tool.id)).all()
    existing_by_operation = {(item.method, item.upstream_path): item for item in existing_rows}
    seen_keys: set[tuple[str, str]] = set()
    counts = {"new": 0, "changed": 0, "unchanged": 0, "excluded": 0, "blocked": 0}
    batch = ToolImportBatch(
        tool_id=tool.id,
        spec_id=spec.id,
        source_type=source_type,
        source_url=_safe_source_url(source_url),
        source_sha256=str(metadata["sha256"]),
        openapi_version=str(metadata["version"]),
        total_operations=len(operations),
        selected_operations=0,
        added_count=0,
        changed_count=0,
        unchanged_count=0,
        excluded_count=0,
        blocked_count=0,
        status=ImportBatchStatus.PARSED.value,
        summary_json="{}",
        created_by_user_id=admin.id,
    )
    session.add(batch)
    session.flush()

    diffs: list[OpenApiDiffItem] = []
    for operation in operations:
        method = str(operation["method"])
        upstream_path = str(operation["upstream_path"])
        gateway_path = str(operation["gateway_path"])
        seen_keys.add((method, upstream_path))
        endpoint = existing_by_operation.get((method, upstream_path))
        existing_governance = _json_loads(endpoint.governance_json, {}) if endpoint else {}
        existing_hash = existing_governance.get("operation_hash") if isinstance(existing_governance, dict) else None
        diff_type = "new" if endpoint is None else ("unchanged" if existing_hash == operation["operation_hash"] else "changed")
        risk_flags = list(operation["risk_flags"]) if isinstance(operation["risk_flags"], list) else []
        risk_level = str(operation["risk_level"])
        status = str(operation["status"])
        if _has_gateway_conflict(session, tool.id, endpoint.id if endpoint else None, method, gateway_path):
            risk_flags.append("path_conflict")
            risk_level = RiskLevel.BLOCKER.value
            status = EndpointStatus.BLOCKED.value
        if risk_level == RiskLevel.BLOCKER.value:
            status = EndpointStatus.BLOCKED.value
        is_new_endpoint = endpoint is None
        if endpoint is None:
            endpoint = ApiEndpoint(tool_id=tool.id, method=method, upstream_path=upstream_path)
            session.add(endpoint)
            session.flush()
        route = session.scalar(select(PublishedRoute).where(PublishedRoute.endpoint_id == endpoint.id))
        if is_new_endpoint:
            _apply_import_operation_to_endpoint(
                endpoint,
                operation=operation,
                upstream=upstream,
                spec_id=spec.id,
                diff_type=diff_type,
                risk_level=risk_level,
                risk_flags=risk_flags,
            )
            endpoint.status = status
        current_access_policy = session.scalar(select(EndpointAccessPolicy).where(EndpointAccessPolicy.endpoint_id == endpoint.id))
        should_refresh_auto_policy = is_new_endpoint and (
            diff_type != "unchanged"
            or current_access_policy is None
            or (current_access_policy.source == "auto" and current_access_policy.confirmed_at is None)
        )
        if should_refresh_auto_policy:
            suggestion = suggest_access_policy(
                method=endpoint.method,
                path=endpoint.upstream_path,
                summary=f"{endpoint.summary or ''} {endpoint.operation_id or ''}",
                risk_flags=set(risk_flags),
                request_body=operation["request_body"],
                responses=operation["responses"],
            )
            suggested_rules = dict(suggestion["rules"])
            if suggestion["access_mode"] == "shared" and not suggestion["complete"]:
                suggested_rules["requires_shared_confirmation"] = True
            upsert_endpoint_access_policy(
                session,
                endpoint=endpoint,
                access_mode=str(suggestion["access_mode"]),
                rules=suggested_rules,
                source="auto",
                confirmed_by_user_id=None,
                confirmed=False,
            )
        counts[diff_type] += 1
        if status == EndpointStatus.EXCLUDED.value:
            counts["excluded"] += 1
        if status == EndpointStatus.BLOCKED.value:
            counts["blocked"] += 1
        if diff_type == "unchanged" and risk_level != RiskLevel.BLOCKER.value:
            if endpoint.status == EndpointStatus.PUBLISHED.value and route and route.is_enabled:
                default_decision = "accept"
            elif endpoint.status == EndpointStatus.EXCLUDED.value:
                default_decision = "exclude"
            elif endpoint.status == EndpointStatus.BLOCKED.value:
                default_decision = "block"
            else:
                default_decision = "pending"
        else:
            default_decision = "exclude" if status == EndpointStatus.EXCLUDED.value else "pending"
        diff = OpenApiDiffItem(
            batch_id=batch.id,
            tool_id=tool.id,
            endpoint_id=endpoint.id,
            method=method,
            upstream_path=upstream_path,
            gateway_path=gateway_path,
            summary=str(operation["summary"]),
            diff_type=diff_type,
            risk_level=risk_level,
            risk_flags_json=_json_dumps(sorted(set(risk_flags))),
            decision=default_decision,
            detail_json=_json_dumps({"operation": operation, "previous_hash": existing_hash}),
        )
        session.add(diff)
        diffs.append(diff)

    for endpoint in existing_rows:
        key = (endpoint.method, endpoint.upstream_path)
        if key in seen_keys:
            continue
        route = session.scalar(select(PublishedRoute).where(PublishedRoute.endpoint_id == endpoint.id, PublishedRoute.is_enabled.is_(True)))
        risk_flags = ["upstream_deleted"]
        risk_level = RiskLevel.BLOCKER.value if route else RiskLevel.HIGH.value
        diff = OpenApiDiffItem(
            batch_id=batch.id,
            tool_id=tool.id,
            endpoint_id=endpoint.id,
            method=endpoint.method,
            upstream_path=endpoint.upstream_path,
            gateway_path=endpoint.gateway_path or endpoint.upstream_path,
            summary=endpoint.summary,
            diff_type="deleted",
            risk_level=risk_level,
            risk_flags_json=_json_dumps(risk_flags),
            decision="block",
            detail_json=_json_dumps({"reason": "上游规范中已删除该接口，需管理员确认后重新治理"}),
        )
        session.add(diff)
        diffs.append(diff)
        counts["blocked"] += 1

    batch.added_count = counts["new"]
    batch.changed_count = counts["changed"]
    batch.unchanged_count = counts["unchanged"]
    batch.excluded_count = counts["excluded"]
    batch.blocked_count = counts["blocked"]
    batch.selected_operations = max(0, batch.total_operations - batch.excluded_count - batch.blocked_count)
    batch.summary_json = _json_dumps(
        {
            "title": metadata.get("title"),
            "kind": metadata.get("kind"),
            "version": metadata.get("version"),
            "source_type": source_type,
            "source_url_masked": _safe_source_url(source_url),
            "counts": {
                "new": batch.added_count,
                "changed": batch.changed_count,
                "unchanged": batch.unchanged_count,
                "excluded": batch.excluded_count,
                "blocked": batch.blocked_count,
            },
        }
    )
    return batch, diffs


async def _fetch_openapi_payload(payload: OpenApiParseRequest, upstream: ToolUpstream, current_request_id: str) -> tuple[object, str, str]:
    if payload.source_type == "text":
        if not payload.document_text.strip():
            raise HTTPException(status_code=422, detail="文本导入必须提供接口文档内容")
        return load_openapi_document(payload.document_text), "text", ""
    if payload.source_type == "url":
        source_url = validate_openapi_source_url(payload.source_url)
    else:
        source_url = f"{upstream.base_url.rstrip('/')}/openapi.json"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(upstream.request_timeout_seconds, connect=upstream.connect_timeout_seconds),
        follow_redirects=False,
        verify=upstream.verify_tls,
    ) as client:
        document, resolved_source_url = await _load_openapi_from_url(client, source_url, upstream, current_request_id)
    return document, payload.source_type, resolved_source_url


@router.get("/settings", response_model=PlatformSettingsView)
def get_platform_settings(
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> PlatformSettingsView:
    return platform_settings_view(session)


@router.put("/settings/email", response_model=PlatformSettingsView)
def update_email_settings(
    payload: EmailSettingsUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> PlatformSettingsView:
    validate_csrf(request)
    if payload.clear_password and payload.smtp_password:
        raise HTTPException(status_code=422, detail="不能同时提交新密码和清除密码")
    if payload.smtp_enabled and (not payload.smtp_host or not payload.smtp_from_email):
        raise HTTPException(status_code=422, detail="启用邮件服务前必须填写 SMTP 主机和发件人邮箱")

    row = ensure_platform_settings(session, updated_by_user_id=admin.id)
    new_password = payload.smtp_password or None
    password_will_exist = bool(new_password) or (bool(row.smtp_password_ciphertext) and not payload.clear_password)
    if payload.smtp_enabled and payload.smtp_username and not password_will_exist:
        raise HTTPException(status_code=422, detail="填写 SMTP 用户名后必须配置密码")

    row.smtp_enabled = payload.smtp_enabled
    row.smtp_host = payload.smtp_host
    row.smtp_port = payload.smtp_port
    row.smtp_username = payload.smtp_username
    row.smtp_from_email = payload.smtp_from_email
    row.smtp_from_name = payload.smtp_from_name or "API Gateway"
    # 465 是隐式 TLS，不能把它保存成需要 STARTTLS 的表述，避免后台显示与实际连接方式不一致。
    row.smtp_use_tls = payload.smtp_use_tls if payload.smtp_port != 465 else False
    if payload.clear_password:
        row.smtp_password_ciphertext = ""
    elif new_password is not None:
        row.smtp_password_ciphertext = encrypt_secret(new_password)
    row.updated_at = utc_now()
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="platform_email_settings_updated",
        target_type="platform_settings",
        target_id="1",
        request_id=request_id(request),
        detail={
            "smtp_enabled": row.smtp_enabled,
            "smtp_host": row.smtp_host,
            "smtp_port": row.smtp_port,
            "smtp_username_configured": bool(row.smtp_username),
            "smtp_password_changed": bool(new_password is not None or payload.clear_password),
            "smtp_from_email": row.smtp_from_email,
            "smtp_from_name": row.smtp_from_name,
            "smtp_use_tls": row.smtp_use_tls,
        },
    )
    session.commit()
    return platform_settings_view(session)


@router.put("/settings/user-defaults", response_model=PlatformSettingsView)
def update_user_defaults(
    payload: UserDefaultsUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> PlatformSettingsView:
    validate_csrf(request)
    row = ensure_platform_settings(session, updated_by_user_id=admin.id)
    previous = {
        "rate_limit_per_minute": row.default_user_rate_limit_per_minute,
        "storage_quota_bytes": row.default_user_storage_quota_bytes,
        "file_retention_days": row.default_file_retention_days,
    }
    row.default_user_rate_limit_per_minute = payload.rate_limit_per_minute
    row.default_user_storage_quota_bytes = payload.storage_quota_bytes
    row.default_file_retention_days = payload.file_retention_days
    row.updated_at = utc_now()
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="platform_user_defaults_updated",
        target_type="platform_settings",
        target_id="1",
        request_id=request_id(request),
        detail={"previous": previous, "rate_limit_per_minute": payload.rate_limit_per_minute, "storage_quota_bytes": payload.storage_quota_bytes, "file_retention_days": payload.file_retention_days},
    )
    session.commit()
    return platform_settings_view(session)


@router.put("/settings/gateway", response_model=PlatformSettingsView)
def update_gateway_settings(
    payload: GatewaySettingsUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> PlatformSettingsView:
    validate_csrf(request)
    if payload.public_gateway_base_url and get_settings().app_env not in {"development", "test"}:
        if urlparse(payload.public_gateway_base_url).scheme != "https":
            raise HTTPException(status_code=422, detail="生产环境公开网关地址必须使用 HTTPS")
    row = ensure_platform_settings(session, updated_by_user_id=admin.id)
    previous = row.public_gateway_base_url
    row.public_gateway_base_url = payload.public_gateway_base_url
    row.updated_at = utc_now()
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="platform_gateway_settings_updated",
        target_type="platform_settings",
        target_id="1",
        request_id=request_id(request),
        detail={
            "previous_gateway_base_url": previous,
            "public_gateway_base_url": row.public_gateway_base_url,
            "fallback_to_browser_origin": not bool(row.public_gateway_base_url),
        },
    )
    session.commit()
    return platform_settings_view(session)


@router.put("/settings/site", response_model=PlatformSettingsView)
def update_site_settings(
    payload: SiteSettingsUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> PlatformSettingsView:
    validate_csrf(request)
    row = ensure_platform_settings(session, updated_by_user_id=admin.id)
    previous = {"site_name": row.site_name, "site_subtitle": row.site_subtitle, "registration_enabled": row.registration_enabled}
    row.site_name = payload.site_name
    row.site_subtitle = payload.site_subtitle
    row.registration_enabled = payload.registration_enabled
    row.updated_at = utc_now()
    log_admin_event(session, admin_user_id=admin.id, action="platform_site_settings_updated", target_type="platform_settings", target_id="1", request_id=request_id(request), detail={"previous": previous, "site_name": row.site_name, "site_subtitle": row.site_subtitle, "registration_enabled": row.registration_enabled})
    session.commit()
    return platform_settings_view(session)


@router.post("/settings/brand-image", response_model=PlatformSettingsView)
async def upload_brand_image(
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> PlatformSettingsView:
    validate_csrf(request)
    max_bytes = 2 * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="品牌图片不能超过 2MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            image_format = (image.format or "").upper()
            width, height = image.size
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(status_code=422, detail="品牌图片格式无效") from error
    allowed_formats = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
    if image_format not in allowed_formats:
        raise HTTPException(status_code=422, detail="品牌图片仅支持 PNG、JPEG 或 WebP")
    if width < 16 or height < 16 or width > 2048 or height > 2048:
        raise HTTPException(status_code=422, detail="品牌图片尺寸必须在 16 到 2048 像素之间")
    settings = get_settings()
    asset_dir = Path(settings.brand_asset_dir).expanduser().resolve()
    asset_dir.mkdir(parents=True, exist_ok=True)
    filename = f"brand-{secrets.token_hex(16)}.{allowed_formats[image_format]}"
    target = asset_dir / filename
    target.write_bytes(data)
    row = ensure_platform_settings(session, updated_by_user_id=admin.id)
    previous_url = row.brand_image_url
    row.brand_image_url = f"/brand-assets/{filename}"
    row.updated_at = utc_now()
    log_admin_event(session, admin_user_id=admin.id, action="platform_brand_image_updated", target_type="platform_settings", target_id="1", request_id=request_id(request), detail={"previous_brand_image_url": previous_url, "brand_image_url": row.brand_image_url, "format": image_format, "width": width, "height": height, "bytes": len(data)})
    try:
        session.commit()
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if previous_url.startswith("/brand-assets/"):
        old_path = asset_dir / Path(previous_url).name
        if old_path != target:
            old_path.unlink(missing_ok=True)
    return platform_settings_view(session)


@router.delete("/settings/brand-image", response_model=PlatformSettingsView)
def remove_brand_image(
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> PlatformSettingsView:
    validate_csrf(request)
    row = ensure_platform_settings(session, updated_by_user_id=admin.id)
    previous_url = row.brand_image_url
    row.brand_image_url = ""
    row.updated_at = utc_now()
    log_admin_event(session, admin_user_id=admin.id, action="platform_brand_image_updated", target_type="platform_settings", target_id="1", request_id=request_id(request), detail={"previous_brand_image_url": previous_url, "brand_image_url": "", "removed": True})
    session.commit()
    if previous_url.startswith("/brand-assets/"):
        (Path(get_settings().brand_asset_dir).expanduser().resolve() / Path(previous_url).name).unlink(missing_ok=True)
    return platform_settings_view(session)


@router.post("/settings/email/test-connection")
def test_platform_email_connection(
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    validate_csrf(request)
    try:
        test_smtp_connection(session)
    except Exception as error:
        smtp_stage = getattr(error, "smtp_stage", "noop")
        stage_labels = {
            "connect": "连接欢迎语",
            "ehlo": "EHLO 协商",
            "starttls": "STARTTLS 握手",
            "ehlo_tls": "TLS 后 EHLO 协商",
            "auth": "SMTP 认证",
            "noop": "连接保活检查",
        }
        stage_label = stage_labels.get(smtp_stage, smtp_stage)
        log_admin_event(
            session,
            admin_user_id=admin.id,
            action="smtp_connection_tested",
            target_type="platform_settings",
            target_id="1",
            request_id=request_id(request),
            detail={"success": False, "error_type": type(error).__name__, "smtp_stage": smtp_stage},
        )
        session.commit()
        raise HTTPException(
            status_code=502,
            detail={
                "code": "SMTP_CONNECTION_FAILED",
                "message": f"SMTP 连接或认证失败（失败阶段：{stage_label}）",
                "details": {"stage": smtp_stage, "error_type": type(error).__name__},
            },
        ) from error
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="smtp_connection_tested",
        target_type="platform_settings",
        target_id="1",
        request_id=request_id(request),
        detail={"success": True},
    )
    session.commit()
    return {"ok": True, "message": "SMTP 连接和认证测试通过"}


@router.post("/settings/email/test-message", status_code=202)
def send_platform_test_email(
    payload: TestEmailRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    validate_csrf(request)
    content = smtp_test(payload.recipient, brand_name=get_effective_platform_settings(session).site_name)
    item = queue_email(
        session,
        recipient=payload.recipient,
        subject=content.subject,
        body_text=content.body_text,
    )
    if item is None:
        raise HTTPException(status_code=409, detail="邮件服务未启用或配置不完整")
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="smtp_test_email_queued",
        target_type="email_outbox",
        target_id=item.id,
        request_id=request_id(request),
        detail={"recipient": payload.recipient, "status": "pending"},
    )
    session.commit()
    return {"ok": True, "message": "测试邮件已加入 Outbox", "outbox_id": item.id}


@router.get("/users", response_model=list[AdminUserView])
def list_users(
    approval_status: str | None = Query(default=None, max_length=24),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> list[AdminUserView]:
    statement = select(User).order_by(User.created_at.desc())
    if approval_status:
        statement = statement.where(User.approval_status == approval_status)
    rows = session.scalars(statement.limit(200)).all()
    return [admin_user_view(session, user) for user in rows]


@router.get("/users/overview", response_model=AdminUserOverview)
def user_overview(
    search: str = Query(default="", max_length=100),
    state: str = Query(default="all", max_length=24),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=20, ge=10, le=100),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminUserOverview:
    """返回用户管理所需的统计、筛选结果和 Key 使用摘要。"""

    state_conditions = {
        "all": None,
        "pending": (User.approval_status == ApprovalStatus.PENDING.value) & User.email_verified_at.is_not(None),
        "active": (User.approval_status == ApprovalStatus.APPROVED.value) & User.is_active.is_(True),
        "disabled": (User.approval_status == ApprovalStatus.APPROVED.value) & User.is_active.is_(False),
        "rejected": User.approval_status == ApprovalStatus.REJECTED.value,
    }
    if state not in state_conditions:
        raise HTTPException(status_code=422, detail="用户状态筛选值不合法")

    filters: list[object] = []
    normalized_search = search.strip()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(
            or_(
                User.username.ilike(pattern),
                User.display_name.ilike(pattern),
                User.email.ilike(pattern),
            )
        )
    if state_conditions[state] is not None:
        filters.append(state_conditions[state])

    total = session.scalar(select(func.count(User.id)).where(*filters)) or 0
    order_priority = case(
        (User.approval_status == ApprovalStatus.PENDING.value, 0),
        ((User.approval_status == ApprovalStatus.APPROVED.value) & User.is_active.is_(True), 1),
        ((User.approval_status == ApprovalStatus.APPROVED.value) & User.is_active.is_(False), 2),
        (User.approval_status == ApprovalStatus.REJECTED.value, 3),
        else_=4,
    )
    users = session.scalars(
        select(User)
        .where(*filters)
        .order_by(order_priority, User.updated_at.desc(), User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    key_stats_by_user: dict[str, tuple[int, int, datetime | None]] = {}
    if users:
        now = datetime.now(timezone.utc)
        key_rows = session.execute(
            select(
                ApiKey.user_id,
                func.count(ApiKey.id),
                func.sum(
                    case(
                        (
                            (ApiKey.status == KeyStatus.ACTIVE.value)
                            & (ApiKey.expires_at.is_(None) | (ApiKey.expires_at > now)),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.max(ApiKey.last_used_at),
            )
            .where(ApiKey.user_id.in_([user.id for user in users]))
            .group_by(ApiKey.user_id)
        ).all()
        key_stats_by_user = {
            user_id: (int(key_count or 0), int(active_count or 0), last_used_at)
            for user_id, key_count, active_count, last_used_at in key_rows
        }

    stat_row = session.execute(
        select(
            func.count(User.id),
            func.sum(
                case(
                    (
                        (User.approval_status == ApprovalStatus.PENDING.value)
                        & User.email_verified_at.is_not(None),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (((User.approval_status == ApprovalStatus.APPROVED.value) & User.is_active.is_(True)), 1),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (((User.approval_status == ApprovalStatus.APPROVED.value) & User.is_active.is_(False)), 1),
                    else_=0,
                )
            ),
            func.sum(case((User.approval_status == ApprovalStatus.REJECTED.value, 1), else_=0)),
        )
    ).one()
    stats = AdminUserStats(
        total=int(stat_row[0] or 0),
        pending=int(stat_row[1] or 0),
        active=int(stat_row[2] or 0),
        disabled=int(stat_row[3] or 0),
        rejected=int(stat_row[4] or 0),
    )
    return AdminUserOverview(
        items=[admin_user_view(session, user, key_stats_by_user.get(user.id)) for user in users],
        total=int(total),
        page=page,
        page_size=page_size,
        stats=stats,
    )


@router.get("/users/pending", response_model=list[AdminUserView])
def list_pending_users(admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> list[AdminUserView]:
    rows = session.scalars(
        select(User)
        .where(
            User.approval_status == ApprovalStatus.PENDING.value,
            User.email_verified_at.is_not(None),
        )
        .order_by(User.registered_at.desc(), User.created_at.desc())
        .limit(200)
    ).all()
    return [admin_user_view(session, user) for user in rows]


@router.post("/users/{user_id}/approve", response_model=AdminUserView)
def approve_user(
    user_id: str,
    payload: UserApprovalRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminUserView:
    validate_csrf(request)
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if "admin" in get_user_roles(session, user.id):
        raise HTTPException(status_code=422, detail="管理员账号不通过审批接口变更")
    if user.approval_status == ApprovalStatus.APPROVED.value:
        raise HTTPException(status_code=409, detail="该用户已审批通过，请刷新列表")
    if user.email and user.email_verified_at is None:
        raise HTTPException(status_code=422, detail="用户尚未完成邮箱验证，不能审批")
    user.approval_status = ApprovalStatus.APPROVED.value
    user.is_active = True
    user.approved_by_user_id = admin.id
    user.approved_at = datetime.now(timezone.utc)
    user.rejected_at = None
    user.rejection_reason = ""
    user.disabled_at = None
    user.disabled_by_user_id = None
    user.disable_reason = ""
    user.session_version += 1
    add_notification(
        session,
        user_id=user.id,
        kind="account",
        title="注册申请已通过",
        body="账号已启用，现在可以登录并创建平台全局 API Key。",
        action_url="/portal",
    )
    if user.email:
        content = account_approved(user.display_name or user.username, f"{get_settings().frontend_origin.rstrip('/')}/login", brand_name=get_effective_platform_settings(session).site_name)
        queue_email(
            session,
            recipient=user.email,
            subject=content.subject,
            body_text=content.body_text,
        )
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="user_approved",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={"username": user.username, "reason": payload.reason},
    )
    session.commit()
    return admin_user_view(session, user)


@router.post("/users/{user_id}/reject", response_model=AdminUserView)
def reject_user(
    user_id: str,
    payload: UserRejectRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminUserView:
    validate_csrf(request)
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if "admin" in get_user_roles(session, user.id):
        raise HTTPException(status_code=422, detail="管理员账号不通过审批接口变更")
    if user.approval_status == ApprovalStatus.APPROVED.value:
        raise HTTPException(status_code=422, detail="已审批通过的用户请使用停用/启用管理访问状态")
    if user.approval_status == ApprovalStatus.REJECTED.value:
        raise HTTPException(status_code=409, detail="该申请已被拒绝，请刷新列表")
    user.approval_status = ApprovalStatus.REJECTED.value
    user.is_active = False
    user.rejected_at = datetime.now(timezone.utc)
    user.rejection_reason = payload.reason.strip()
    user.session_version += 1
    add_notification(
        session,
        user_id=user.id,
        kind="account",
        title="注册申请未通过",
        body=user.rejection_reason,
        action_url="/login",
    )
    if user.email:
        content = account_rejected(user.display_name or user.username, user.rejection_reason, brand_name=get_effective_platform_settings(session).site_name)
        queue_email(
            session,
            recipient=user.email,
            subject=content.subject,
            body_text=content.body_text,
        )
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="user_rejected",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={"username": user.username, "reason": user.rejection_reason},
    )
    session.commit()
    return admin_user_view(session, user)


@router.post("/users/{user_id}/disable", response_model=AdminUserView)
def disable_user(
    user_id: str,
    payload: UserRejectRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminUserView:
    validate_csrf(request)
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id:
        raise HTTPException(status_code=422, detail="不能停用当前登录管理员")
    if user.approval_status != ApprovalStatus.APPROVED.value:
        raise HTTPException(status_code=422, detail="只有已审批通过的用户可以停用")
    if not user.is_active:
        raise HTTPException(status_code=409, detail="该用户已处于停用状态，请刷新列表")
    user.is_active = False
    user.disabled_at = datetime.now(timezone.utc)
    user.disabled_by_user_id = admin.id
    user.disable_reason = payload.reason.strip()
    user.session_version += 1
    add_notification(
        session,
        user_id=user.id,
        kind="account",
        title="账号已停用",
        body=user.disable_reason,
        action_url="/login",
    )
    if user.email:
        content = account_disabled(user.display_name or user.username, user.disable_reason, brand_name=get_effective_platform_settings(session).site_name)
        queue_email(
            session,
            recipient=user.email,
            subject=content.subject,
            body_text=content.body_text,
        )
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="user_disabled",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={"username": user.username, "reason": user.disable_reason},
    )
    session.commit()
    return admin_user_view(session, user)


@router.post("/users/{user_id}/enable", response_model=AdminUserView)
def enable_user(
    user_id: str,
    payload: UserApprovalRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminUserView:
    validate_csrf(request)
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.approval_status != ApprovalStatus.APPROVED.value:
        raise HTTPException(status_code=422, detail="只有已审批通过的用户可以启用")
    if user.is_active:
        raise HTTPException(status_code=409, detail="该用户已处于启用状态，请刷新列表")
    user.is_active = True
    user.disabled_at = None
    user.disabled_by_user_id = None
    user.disable_reason = ""
    user.session_version += 1
    add_notification(
        session,
        user_id=user.id,
        kind="account",
        title="账号已重新启用",
        body=payload.reason.strip() or "管理员已恢复你的账号访问。",
        action_url="/portal",
    )
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="user_enabled",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={"username": user.username, "reason": payload.reason},
    )
    session.commit()
    return admin_user_view(session, user)


@router.post("/users/{user_id}/reset-password", response_model=AdminResetPasswordResult)
def admin_reset_password(
    user_id: str,
    payload: UserRejectRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminResetPasswordResult:
    validate_csrf(request)
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id:
        raise HTTPException(status_code=422, detail="管理员不能通过该入口重置自己的密码")
    if user.approval_status != ApprovalStatus.APPROVED.value:
        raise HTTPException(status_code=422, detail="只能重置已审批账号的密码")
    temporary_password = f"Tmp{secrets.token_urlsafe(12)}9"
    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    user.password_changed_at = utc_now()
    user.session_version += 1
    add_notification(
        session,
        user_id=user.id,
        kind="security",
        title="管理员已重置账号密码",
        body="请使用临时密码登录并立即设置新密码。",
        action_url="/change-password",
    )
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="admin_password_reset",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={"username": user.username, "reason": payload.reason.strip()},
    )
    session.commit()
    return AdminResetPasswordResult(temporary_password=temporary_password)


def _admin_user_or_404(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


def _user_call_conditions(
    user_id: str,
    *,
    tool_slug: str | None = None,
    status_code: int | None = None,
    result: str | None = None,
    method: str | None = None,
    api_key_id: str | None = None,
    request_id_filter: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[object]:
    conditions: list[object] = [GatewayRequest.user_id == user_id]
    if tool_slug:
        conditions.append(GatewayRequest.tool_slug == tool_slug.strip())
    if status_code is not None:
        conditions.append(GatewayRequest.status_code == status_code)
    if result == "success":
        conditions.append(GatewayRequest.status_code < 400)
    elif result == "failed":
        conditions.append(GatewayRequest.status_code >= 400)
    elif result == "rate_limited":
        conditions.append(GatewayRequest.status_code == 429)
    if method:
        conditions.append(GatewayRequest.method == method)
    if api_key_id:
        conditions.append(GatewayRequest.api_key_id == api_key_id)
    if request_id_filter:
        conditions.append(GatewayRequest.id == request_id_filter.strip())
    if start_time:
        conditions.append(GatewayRequest.created_at >= start_time)
    if end_time:
        conditions.append(GatewayRequest.created_at <= end_time)
    return conditions


@router.get("/users/{user_id}", response_model=AdminUserView)
def get_admin_user(
    user_id: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminUserView:
    user = _admin_user_or_404(session, user_id)
    return admin_user_view(session, user, _admin_user_key_stats(session, user.id))


@router.patch("/users/{user_id}/rate-limit", response_model=AdminUserView)
def update_admin_user_rate_limit(
    user_id: str,
    payload: AdminUserRateLimitUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminUserView:
    validate_csrf(request)
    user = _admin_user_or_404(session, user_id)
    previous = user.rate_limit_per_minute
    user.rate_limit_per_minute = payload.rate_limit_per_minute
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="user_rate_limit_updated",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={
            "username": user.username,
            "previous_rate_limit_per_minute": previous,
            "rate_limit_per_minute": payload.rate_limit_per_minute,
        },
    )
    session.commit()
    return admin_user_view(session, user, _admin_user_key_stats(session, user.id))


@router.patch("/users/{user_id}/storage-quota", response_model=AdminUserView)
def update_admin_user_storage_quota(
    user_id: str,
    payload: AdminUserStorageQuotaUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminUserView:
    validate_csrf(request)
    user = _admin_user_or_404(session, user_id)
    previous = user.storage_quota_bytes
    user.storage_quota_bytes = payload.storage_quota_bytes
    log_admin_event(session, admin_user_id=admin.id, action="user_storage_quota_updated", target_type="user", target_id=user.id, request_id=request_id(request), detail={"username": user.username, "previous_storage_quota_bytes": previous, "storage_quota_bytes": payload.storage_quota_bytes})
    session.commit()
    return admin_user_view(session, user, _admin_user_key_stats(session, user.id))


@router.get("/users/{user_id}/summary", response_model=AdminUserProfileSummary)
def get_admin_user_summary(
    user_id: str,
    window_days: int = Query(default=30, ge=1, le=3650),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    user = _admin_user_or_404(session, user_id)
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=window_days)
    conditions = _user_call_conditions(user.id, start_time=window_start, end_time=window_end)
    aggregate = session.execute(
        select(
            func.count(GatewayRequest.id),
            func.coalesce(func.sum(case((GatewayRequest.status_code < 400, 1), else_=0)), 0),
            func.coalesce(func.sum(case((GatewayRequest.status_code >= 400, 1), else_=0)), 0),
            func.coalesce(func.sum(case((GatewayRequest.status_code == 429, 1), else_=0)), 0),
            func.coalesce(func.avg(GatewayRequest.duration_ms), 0),
            func.coalesce(func.sum(GatewayRequest.request_bytes), 0),
            func.coalesce(func.sum(GatewayRequest.response_bytes), 0),
            func.max(GatewayRequest.created_at),
        ).where(*conditions)
    ).one()
    total = int(aggregate[0] or 0)
    success = int(aggregate[1] or 0)
    durations = list(session.scalars(select(GatewayRequest.duration_ms).where(*conditions)).all())
    tool_rows = session.execute(
        select(
            GatewayRequest.tool_slug,
            func.count(GatewayRequest.id),
            func.coalesce(func.sum(case((GatewayRequest.status_code >= 400, 1), else_=0)), 0),
            func.coalesce(func.avg(GatewayRequest.duration_ms), 0),
        )
        .where(*conditions)
        .group_by(GatewayRequest.tool_slug)
        .order_by(func.count(GatewayRequest.id).desc())
        .limit(8)
    ).all()
    recent_failures = list(
        session.scalars(
            select(GatewayRequest)
            .where(*conditions, GatewayRequest.status_code >= 400)
            .order_by(GatewayRequest.created_at.desc(), GatewayRequest.id.desc())
            .limit(8)
        ).all()
    )
    return {
        "user": admin_user_view(session, user, _admin_user_key_stats(session, user.id)),
        "window_days": window_days,
        "window_start": window_start,
        "window_end": window_end,
        "metrics": {
            "total": total,
            "success": success,
            "failed": int(aggregate[2] or 0),
            "rate_limited": int(aggregate[3] or 0),
            "success_rate": round(success / total, 4) if total else 0,
            "avg_duration_ms": round(float(aggregate[4] or 0), 2),
            "p95_duration_ms": _percentile_ms(durations, 0.95),
            "request_bytes": int(aggregate[5] or 0),
            "response_bytes": int(aggregate[6] or 0),
            "last_called_at": aggregate[7],
        },
        "top_tools": [
            {
                "tool_slug": row[0] or "未知工具",
                "total": int(row[1] or 0),
                "failed": int(row[2] or 0),
                "avg_duration_ms": round(float(row[3] or 0), 2),
            }
            for row in tool_rows
        ],
        "recent_failures": gateway_call_views(session, recent_failures, admin=True),
    }


@router.get("/users/{user_id}/calls", response_model=GatewayCallPage)
def list_admin_user_calls(
    user_id: str,
    tool_slug: str | None = Query(default=None, max_length=64),
    status_code: int | None = Query(default=None, ge=100, le=599),
    result: str | None = Query(default=None, pattern="^(success|failed|rate_limited)$"),
    method: str | None = Query(default=None, pattern="^(GET|POST|PUT|PATCH|DELETE|HEAD)$"),
    api_key_id: str | None = Query(default=None, max_length=36),
    call_request_id: str | None = Query(default=None, alias="request_id", max_length=64),
    start: str | None = Query(default=None, max_length=40),
    end: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=10, le=100),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    user = _admin_user_or_404(session, user_id)
    start_time = _parse_datetime_query(start, "开始")
    end_time = _parse_datetime_query(end, "结束")
    if start_time and end_time and start_time > end_time:
        raise HTTPException(status_code=422, detail="开始时间不能晚于结束时间")
    conditions = _user_call_conditions(
        user.id,
        tool_slug=tool_slug,
        status_code=status_code,
        result=result,
        method=method,
        api_key_id=api_key_id,
        request_id_filter=call_request_id,
        start_time=start_time,
        end_time=end_time,
    )
    total = int(session.scalar(select(func.count(GatewayRequest.id)).where(*conditions)) or 0)
    rows = list(
        session.scalars(
            select(GatewayRequest)
            .where(*conditions)
            .order_by(GatewayRequest.created_at.desc(), GatewayRequest.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return {
        "items": gateway_call_views(session, rows, admin=True),
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
    }


@router.get("/users/{user_id}/events", response_model=UserEventPage)
def list_admin_user_events(
    user_id: str,
    category: str = Query(default="all", pattern="^(all|account|key)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=10, le=100),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    user = _admin_user_or_404(session, user_id)
    key_ids = list(session.scalars(select(ApiKey.id).where(ApiKey.user_id == user.id)).all())
    ownership = [
        (AdminAuditEvent.target_type == "user") & (AdminAuditEvent.target_id == user.id),
    ]
    if key_ids:
        ownership.append((AdminAuditEvent.target_type == "api_key") & (AdminAuditEvent.target_id.in_(key_ids)))
    conditions: list[object] = [or_(*ownership)]
    if category == "account":
        conditions.append(AdminAuditEvent.action.in_(AUDIT_ACTION_GROUPS["accounts"]))
    elif category == "key":
        conditions.append(AdminAuditEvent.action.in_(AUDIT_ACTION_GROUPS["keys"]))
    total = int(session.scalar(select(func.count(AdminAuditEvent.id)).where(*conditions)) or 0)
    rows = list(
        session.scalars(
            select(AdminAuditEvent)
            .where(*conditions)
            .order_by(AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    actor_ids = {item.admin_user_id for item in rows}
    actors = {item.id: item.username for item in session.scalars(select(User).where(User.id.in_(actor_ids))).all()} if actor_ids else {}
    return {
        "items": [
            {
                "id": item.id,
                "action": item.action,
                "actor_user_id": item.admin_user_id,
                "actor_username": actors.get(item.admin_user_id, "未知用户"),
                "target_type": item.target_type,
                "target_id": item.target_id,
                "request_id": item.request_id,
                "detail": _json_dict(item.detail_json),
                "created_at": item.created_at,
            }
            for item in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
    }


@router.get("/calls/{call_request_id}", response_model=GatewayCallView)
def get_admin_call(
    call_request_id: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    row = session.get(GatewayRequest, call_request_id)
    if not row:
        raise HTTPException(status_code=404, detail="调用记录不存在")
    return gateway_call_view(session, row, admin=True)


@router.get("/users/{user_id}/api-keys", response_model=list[AdminApiKeyView])
def list_user_api_keys(
    user_id: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> list[AdminApiKeyView]:
    user = _admin_user_or_404(session, user_id)
    keys = session.scalars(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())).all()
    return [admin_api_key_view(session, item) for item in keys]


@router.get("/api-keys", response_model=list[AdminApiKeyView])
def search_api_keys(
    prefix: str | None = Query(default=None, min_length=3, max_length=24),
    username: str | None = Query(default=None, max_length=64),
    status_filter: str | None = Query(default=None, alias="status", max_length=16),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> list[AdminApiKeyView]:
    """按脱敏前缀、用户或状态检索 Key；永不返回完整密钥。"""
    statement = select(ApiKey)
    if prefix:
        statement = statement.where(ApiKey.prefix.like(f"{prefix.strip()}%"))
    if status_filter:
        statement = statement.where(ApiKey.status == status_filter)
    if username:
        user = session.scalar(select(User).where(User.username_key == normalize_username_key(username)))
        if not user:
            return []
        statement = statement.where(ApiKey.user_id == user.id)
    keys = session.scalars(statement.order_by(ApiKey.created_at.desc()).limit(100)).all()
    return [admin_api_key_view(session, item) for item in keys]


@router.patch("/api-keys/{key_id}/disable", response_model=AdminApiKeyView)
def admin_disable_api_key(
    key_id: str,
    payload: UserApprovalRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminApiKeyView:
    validate_csrf(request)
    api_key = session.get(ApiKey, key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    if api_key.status == KeyStatus.REVOKED.value:
        raise HTTPException(status_code=409, detail="已撤销的 API Key 不可禁用")
    if api_key.status != KeyStatus.ACTIVE.value or api_key_is_expired(api_key):
        raise HTTPException(status_code=409, detail="该 API Key 已不可用，请刷新后重试")
    owner = session.get(User, api_key.user_id)
    api_key.status = KeyStatus.DISABLED.value
    api_key.disabled_at = datetime.now(timezone.utc)
    api_key.disabled_by_user_id = admin.id
    api_key.disable_reason = payload.reason
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="admin_api_key_disabled",
        target_type="api_key",
        target_id=api_key.id,
        request_id=request_id(request),
        detail={
            "user_id": api_key.user_id,
            "username": owner.username if owner else "",
            "key_prefix": api_key.prefix,
            "reason": payload.reason,
        },
    )
    session.commit()
    return admin_api_key_view(session, api_key)


@router.patch("/api-keys/{key_id}/enable", response_model=AdminApiKeyView)
def admin_enable_api_key(
    key_id: str,
    request: Request,
    payload: UserApprovalRequest | None = None,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminApiKeyView:
    """管理员恢复仍在有效期内的禁用 API Key；已撤销或过期 Key 必须重新生成。"""

    validate_csrf(request)
    api_key = session.get(ApiKey, key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    if api_key.status == KeyStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail="API Key 已启用，请刷新列表")
    if api_key.status == KeyStatus.REVOKED.value:
        raise HTTPException(status_code=409, detail="已撤销的 API Key 不可恢复")
    if api_key_is_expired(api_key):
        raise HTTPException(status_code=409, detail="已过期的 API Key 不可恢复，请重新生成")
    if api_key.status != KeyStatus.DISABLED.value:
        raise HTTPException(status_code=409, detail="该 API Key 当前状态不可恢复")

    owner = session.get(User, api_key.user_id)
    previous = {
        "user_id": api_key.user_id,
        "username": owner.username if owner else "",
        "prefix": api_key.prefix,
        "disabled_at": api_key.disabled_at.isoformat() if api_key.disabled_at else None,
        "disabled_by_user_id": api_key.disabled_by_user_id,
        "disable_reason": api_key.disable_reason,
        "enable_reason": payload.reason.strip() if payload and payload.reason.strip() else "管理员重新启用",
    }
    api_key.status = KeyStatus.ACTIVE.value
    api_key.disabled_at = None
    api_key.disabled_by_user_id = None
    api_key.disable_reason = ""
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="admin_api_key_enabled",
        target_type="api_key",
        target_id=api_key.id,
        request_id=request_id(request),
        detail=previous,
    )
    session.commit()
    return admin_api_key_view(session, api_key)


@router.post("/users/import")
def import_users(
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    validate_csrf(request)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="仅支持 CSV 文件")
    stream = BoundedHashingReader(file.file, MAX_IMPORT_BYTES)
    try:
        reader = csv.DictReader(io.TextIOWrapper(io.BufferedReader(stream), encoding="utf-8-sig", newline=""))
        if not reader.fieldnames or not {"username", "password"}.issubset(set(reader.fieldnames)):
            raise HTTPException(status_code=422, detail="CSV 必须包含 username,password 列")
    except ImportSizeExceeded as error:
        raise HTTPException(status_code=413, detail="用户导入 CSV 不能超过 1 MiB") from error
    except (UnicodeDecodeError, csv.Error) as error:
        raise HTTPException(status_code=422, detail="CSV 必须是 UTF-8 且包含有效表头") from error

    user_role = session.scalar(select(Role).where(Role.name == RoleName.USER.value))
    if not user_role:
        raise RuntimeError("系统角色未初始化")
    errors: list[dict[str, object]] = []
    accepted = 0
    total = 0
    seen: set[str] = set()
    try:
        for line, row in enumerate(reader, start=2):
            total += 1
            username = (row.get("username") or "").strip()
            password = row.get("password") or ""
            email = (row.get("email") or "").strip() or None
            display_name = (row.get("display_name") or "").strip() or None
            requested_role = (row.get("role") or "user").strip().lower()
            try:
                if requested_role != "user":
                    raise ValueError("CSV 仅允许导入普通用户，不能指定管理员角色")
                if not username or len(username) > 64 or not username.replace("_", "").replace("-", "").isalnum():
                    raise ValueError("用户名只能包含字母、数字、下划线和连字符")
                if username in seen or session.scalar(select(User.id).where(User.username == username)):
                    raise ValueError("用户名重复")
                if email and session.scalar(select(User.id).where(User.email == email)):
                    raise ValueError("邮箱重复")
                validate_password(password, username)
            except ValueError as error:
                if len(errors) < 50:
                    errors.append({"line": line, "message": str(error)})
                continue
            user = User(
                username=username,
                display_name=display_name,
                email=email,
                email_verified_at=datetime.now(timezone.utc) if email else None,
                password_hash=hash_password(password),
                is_active=True,
                approval_status=ApprovalStatus.APPROVED.value,
                approved_by_user_id=admin.id,
                approved_at=datetime.now(timezone.utc),
                rate_limit_per_minute=default_user_rate_limit(session),
                must_change_password=True,
            )
            session.add(user)
            session.flush()
            session.add(UserRole(user_id=user.id, role_id=user_role.id))
            seen.add(username)
            accepted += 1
    except ImportSizeExceeded as error:
        session.rollback()
        raise HTTPException(status_code=413, detail="用户导入 CSV 不能超过 1 MiB") from error
    except (UnicodeDecodeError, csv.Error) as error:
        session.rollback()
        raise HTTPException(status_code=422, detail="CSV 必须是 UTF-8 且包含有效表头") from error
    batch = UserImportBatch(
        admin_user_id=admin.id,
        source_sha256=stream.digest.hexdigest(),
        total_rows=total,
        accepted_rows=accepted,
        rejected_rows=total - accepted,
        errors_json=json.dumps(errors, ensure_ascii=False),
    )
    session.add(batch)
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="user_import",
        target_type="user_import_batch",
        target_id=batch.id,
        request_id=request_id(request),
        detail={"total": total, "accepted": accepted, "rejected": total - accepted},
    )
    session.commit()
    return {"batch_id": batch.id, "total": total, "accepted": accepted, "rejected": total - accepted, "errors": errors}


@router.get("/tools", response_model=list[ToolView])
async def list_tools(admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> list[ToolView]:
    rows = session.execute(select(Tool, ToolUpstream).join(ToolUpstream, ToolUpstream.tool_id == Tool.id).order_by(Tool.created_at.desc())).all()
    instances = _stored_tool_health_by_id(session)
    return [tool_view(session, tool, upstream, _stored_tool_health(instances.get(tool.id), upstream)) for tool, upstream in rows]


@router.get("/dashboard/tools")
async def dashboard_tools(admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = session.execute(select(Tool, ToolUpstream).join(ToolUpstream, ToolUpstream.tool_id == Tool.id).order_by(Tool.created_at.desc())).all()
    instances = _stored_tool_health_by_id(session)
    endpoint_counts: dict[str, dict[str, int]] = {}
    for tool_id, status, count in session.execute(
        select(ApiEndpoint.tool_id, ApiEndpoint.status, func.count(ApiEndpoint.id)).group_by(ApiEndpoint.tool_id, ApiEndpoint.status)
    ).all():
        endpoint_counts.setdefault(str(tool_id), {})[str(status)] = int(count)
    published_counts = {
        str(tool_id): int(count)
        for tool_id, count in session.execute(
            select(PublishedRoute.tool_id, func.count(PublishedRoute.id))
            .where(PublishedRoute.is_enabled.is_(True))
            .group_by(PublishedRoute.tool_id)
        ).all()
    }
    return [
        {
            "id": tool.id,
            "slug": tool.slug,
            "name": tool.name,
            "health": _stored_tool_health(instances.get(tool.id), upstream),
            "published_count": published_counts.get(tool.id, 0),
            "blocked_count": endpoint_counts.get(tool.id, {}).get(EndpointStatus.BLOCKED.value, 0),
        }
        for tool, upstream in rows
    ]


@router.get("/platform/onboarding")
def platform_onboarding(admin: User = Depends(require_admin)) -> dict[str, object]:
    settings = get_settings()
    return {
        "allowed_upstream_hosts": sorted(settings.upstream_hosts),
        "gateway_pattern": "/gateway/{tool_slug}{gateway_path}",
        "supported_spec_versions": ["OpenAPI 3.0/3.1", "Swagger 2.0"],
        "supported_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
        "max_openapi_document_bytes": MAX_OPENAPI_DOCUMENT_BYTES,
        "max_upload_bytes": settings.max_upload_bytes,
        "max_download_bytes": settings.max_download_bytes,
        "access_modes": ["authenticated", "owner", "shared", "admin_only"],
        "standard_steps": [
            "工具基本信息",
            "所属服务器/环境",
            "上游地址",
            "服务端认证",
            "健康检查路径",
            "OpenAPI/Swagger 文档来源",
            "接口导入预览",
            "风险分析",
            "路由映射",
            "发布确认",
            "审计留痕",
        ],
    }


@router.post("/tools", response_model=ToolView, status_code=201)
def create_or_update_tool(
    payload: ToolConfigRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> ToolView:
    validate_csrf(request)
    try:
        base_url = validate_upstream_url(payload.base_url)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    tool = session.scalar(select(Tool).where(Tool.slug == payload.slug))
    environments = [{"name": "prod", "instance_name": "default", "base_url": base_url, "role": "production", "enabled": True, "priority": 100}]
    if not tool:
        tool = Tool(
            slug=payload.slug,
            name=payload.name,
            description=payload.description,
            status=payload.status,
            default_gateway_prefix=payload.default_gateway_prefix,
            created_by_user_id=admin.id,
            is_enabled=payload.status != ToolStatus.DISABLED.value,
            task_adapter_json="{}",
        )
        session.add(tool)
        session.flush()
        upstream = ToolUpstream(tool_id=tool.id, base_url=base_url, token_ciphertext=encrypt_secret(payload.upstream_token))
        session.add(upstream)
        action = "tool_created"
    else:
        if tool.slug != payload.slug:
            raise HTTPException(status_code=409, detail="工具 slug 创建后不可修改")
        published_count = session.scalar(
            select(func.count(PublishedRoute.id)).where(
                PublishedRoute.tool_id == tool.id,
                PublishedRoute.is_enabled.is_(True),
            )
        ) or 0
        if published_count:
            raise HTTPException(status_code=409, detail="已有发布路由的工具必须通过工具配置页安全修改")
        tool.name = payload.name
        tool.description = payload.description
        tool.status = payload.status
        tool.default_gateway_prefix = payload.default_gateway_prefix
        tool.is_enabled = payload.status != ToolStatus.DISABLED.value
        tool.updated_at = datetime.now(timezone.utc)
        upstream = session.scalar(select(ToolUpstream).where(ToolUpstream.tool_id == tool.id))
        if not upstream:
            raise RuntimeError("工具上游配置丢失")
        upstream.base_url = base_url
        if payload.upstream_token:
            upstream.token_ciphertext = encrypt_secret(payload.upstream_token)
        action = "tool_updated"
    if payload.task_adapter is not None:
        tool.task_adapter_json = _json_dumps(_validated_task_adapter(session, tool, payload.task_adapter))
    upstream.auth_type = payload.auth_type
    upstream.token_label = payload.token_label
    upstream.health_path = payload.health_path
    upstream.environment_mode = "single"
    upstream.environments_json = _json_dumps(environments)
    upstream.discovery_type = payload.discovery_type
    upstream.network_zone = payload.network_zone
    upstream.verify_tls = payload.verify_tls
    upstream.retry_count = payload.retry_count
    upstream.rate_limit_per_minute = payload.rate_limit_per_minute
    upstream.connect_timeout_seconds = payload.connect_timeout_seconds
    upstream.request_timeout_seconds = payload.request_timeout_seconds
    upstream.network_isolation_mode = payload.network_isolation_mode
    upstream.network_isolation_note = payload.network_isolation_note.strip()
    if payload.network_isolation_confirmed:
        upstream.network_isolation_confirmed_by_user_id = admin.id
        upstream.network_isolation_confirmed_at = datetime.now(timezone.utc)
    upstream.updated_at = datetime.now(timezone.utc)
    session.flush()
    _sync_single_upstream_instance(session, tool, upstream)
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action=action,
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={"slug": tool.slug, "base_url": mask_url(base_url), "auth_type": upstream.auth_type, "network_isolation_mode": upstream.network_isolation_mode},
    )
    session.commit()
    return tool_view(session, tool, upstream)


@router.get("/tools/{tool_id}/configuration", response_model=ToolConfigurationView)
def get_tool_configuration(
    tool_id: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> ToolConfigurationView:
    tool, upstream = get_tool_and_upstream(session, tool_id)
    return _tool_configuration_view(tool, upstream)


@router.post("/tools/{tool_id}/configuration/test", response_model=ToolConfigurationTestResult)
async def test_tool_configuration(
    tool_id: str,
    payload: ToolConfigurationCandidateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> ToolConfigurationTestResult:
    validate_csrf(request)
    tool, upstream = get_tool_and_upstream(session, tool_id)
    base_url, token_ciphertext = _validate_tool_candidate(upstream, payload)
    changed_fields = _configuration_changed_fields(tool, upstream, payload, base_url, token_ciphertext)
    candidate = _candidate_upstream(upstream, payload, base_url, token_ciphertext)
    health = await check_tool_health(candidate, request_id(request))
    requires_health_check = _requires_candidate_health_check(tool, payload, changed_fields)
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="tool_configuration_tested",
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={
            "slug": tool.slug,
            "changed_fields": changed_fields,
            "candidate_base_url": mask_url(base_url),
            "status": health.get("status"),
            "status_code": health.get("status_code"),
        },
    )
    session.commit()
    return ToolConfigurationTestResult(
        health=health,
        changed_fields=changed_fields,
        requires_health_check=requires_health_check,
        candidate_base_url_masked=mask_url(base_url),
    )


@router.patch("/tools/{tool_id}/configuration", response_model=ToolConfigurationView)
async def update_tool_configuration(
    tool_id: str,
    payload: ToolConfigurationUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> ToolConfigurationView:
    validate_csrf(request)
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="必须确认本次工具配置变更")
    tool, upstream = get_tool_and_upstream(session, tool_id)
    if upstream.config_revision != payload.expected_revision:
        raise HTTPException(status_code=409, detail="工具配置已被其他管理员修改，请刷新后重新确认")
    base_url, token_ciphertext = _validate_tool_candidate(upstream, payload)
    changed_fields = _configuration_changed_fields(tool, upstream, payload, base_url, token_ciphertext)
    if not changed_fields:
        raise HTTPException(status_code=422, detail="没有可保存的配置变化")
    requires_health_check = _requires_candidate_health_check(tool, payload, changed_fields)
    health: dict[str, object] | None = None
    if requires_health_check:
        candidate = _candidate_upstream(upstream, payload, base_url, token_ciphertext)
        health = await check_tool_health(candidate, request_id(request))
        if not health.get("reachable"):
            raise HTTPException(status_code=422, detail="候选上游健康检查失败，未修改线上配置")

    previous_url = upstream.base_url
    previous_revision = upstream.config_revision
    _snapshot_tool_configuration(session, tool, upstream, admin, payload.reason)
    _apply_tool_configuration(
        session,
        tool,
        upstream,
        _candidate_configuration_data(payload, base_url),
        token_ciphertext,
        admin,
    )
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="tool_configuration_updated",
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={
            "slug": tool.slug,
            "reason": payload.reason.strip(),
            "changed_fields": changed_fields,
            "previous_revision": previous_revision,
            "new_revision": upstream.config_revision,
            "previous_base_url": mask_url(previous_url),
            "new_base_url": mask_url(upstream.base_url),
            "health_status": health.get("status") if health else "not_required",
        },
    )
    session.commit()
    return _tool_configuration_view(tool, upstream)


@router.get("/tools/{tool_id}/configuration/revisions", response_model=list[ToolConfigurationRevisionView])
def list_tool_configuration_revisions(
    tool_id: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> list[ToolConfigurationRevisionView]:
    tool, _upstream = get_tool_and_upstream(session, tool_id)
    rows = session.scalars(
        select(ToolConfigRevision)
        .where(ToolConfigRevision.tool_id == tool.id)
        .order_by(ToolConfigRevision.config_revision.desc())
        .limit(50)
    ).all()
    result: list[ToolConfigurationRevisionView] = []
    for row in rows:
        data = _json_dict(row.config_json)
        result.append(
            ToolConfigurationRevisionView(
                id=row.id,
                config_revision=row.config_revision,
                base_url_masked=mask_url(str(data.get("base_url") or "")),
                status=str(data.get("status") or "unknown"),
                changed_by_user_id=row.changed_by_user_id,
                reason=row.reason,
                created_at=row.created_at,
            )
        )
    return result


@router.post("/tools/{tool_id}/configuration/rollback", response_model=ToolConfigurationView)
async def rollback_tool_configuration(
    tool_id: str,
    payload: ToolConfigurationRollbackRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> ToolConfigurationView:
    validate_csrf(request)
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="必须确认本次配置回滚")
    tool, upstream = get_tool_and_upstream(session, tool_id)
    if upstream.config_revision != payload.expected_revision:
        raise HTTPException(status_code=409, detail="工具配置已被其他管理员修改，请刷新后重新确认")
    revision = session.scalar(
        select(ToolConfigRevision).where(
            ToolConfigRevision.id == payload.revision_id,
            ToolConfigRevision.tool_id == tool.id,
        )
    )
    if not revision:
        raise HTTPException(status_code=404, detail="配置历史版本不存在")
    raw_data = _json_dict(revision.config_json)
    try:
        candidate_payload = ToolConfigurationCandidateRequest.model_validate(
            {**raw_data, "upstream_token": "", "clear_upstream_token": False}
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail="历史配置格式已不兼容，不能自动回滚") from error
    historical_upstream = ToolUpstream(
        tool_id=tool.id,
        base_url=str(raw_data.get("base_url") or ""),
        token_ciphertext=revision.token_ciphertext,
    )
    base_url, token_ciphertext = _validate_tool_candidate(historical_upstream, candidate_payload)
    candidate_data = _candidate_configuration_data(candidate_payload, base_url)
    if candidate_payload.status == ToolStatus.ACTIVE.value:
        candidate = _candidate_upstream(upstream, candidate_payload, base_url, token_ciphertext)
        health = await check_tool_health(candidate, request_id(request))
        if not health.get("reachable"):
            raise HTTPException(status_code=422, detail="历史上游当前不可达，未执行回滚")

    previous_revision = upstream.config_revision
    previous_url = upstream.base_url
    _snapshot_tool_configuration(session, tool, upstream, admin, payload.reason)
    _apply_tool_configuration(session, tool, upstream, candidate_data, token_ciphertext, admin)
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="tool_configuration_rolled_back",
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={
            "slug": tool.slug,
            "reason": payload.reason.strip(),
            "source_revision": revision.config_revision,
            "previous_revision": previous_revision,
            "new_revision": upstream.config_revision,
            "previous_base_url": mask_url(previous_url),
            "new_base_url": mask_url(upstream.base_url),
        },
    )
    session.commit()
    return _tool_configuration_view(tool, upstream)


@router.get("/tools/{tool_id}", response_model=ToolView)
async def get_tool_detail(tool_id: str, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> ToolView:
    tool, upstream = get_tool_and_upstream(session, tool_id)
    instance = _stored_tool_health_by_id(session).get(tool.id)
    return tool_view(session, tool, upstream, _stored_tool_health(instance, upstream))


@router.patch("/tools/{tool_id}/task-adapter", response_model=ToolView)
def update_tool_task_adapter(
    tool_id: str,
    payload: TaskAdapterUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> ToolView:
    validate_csrf(request)
    tool, upstream = get_tool_and_upstream(session, tool_id)
    normalized = _validated_task_adapter(session, tool, payload.task_adapter)
    tool.task_adapter_json = _json_dumps(normalized)
    tool.updated_at = utc_now()
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="tool_task_adapter_updated",
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={
            "slug": tool.slug,
            "reason": payload.reason.strip(),
            "capabilities": sorted((normalized.get("routes") or {}).keys()),
            "version": normalized.get("version"),
        },
    )
    session.commit()
    return tool_view(session, tool, upstream)


@router.get("/tools/{tool_id}/onboarding")
async def get_tool_onboarding(tool_id: str, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> dict[str, object]:
    tool, upstream = get_tool_and_upstream(session, tool_id)
    instance = _stored_tool_health_by_id(session).get(tool.id)
    return _tool_onboarding(session, tool, upstream, _stored_tool_health(instance, upstream))


@router.get("/tools/{tool_id}/versions")
def list_tool_versions(tool_id: str, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> dict[str, object]:
    tool, upstream = get_tool_and_upstream(session, tool_id)
    specs = session.scalars(select(OpenApiSpec).where(OpenApiSpec.tool_id == tool.id).order_by(OpenApiSpec.imported_at.desc())).all()
    batches = session.scalars(select(ToolImportBatch).where(ToolImportBatch.tool_id == tool.id).order_by(ToolImportBatch.created_at.desc())).all()
    return {
        "tool_id": tool.id,
        "tool_slug": tool.slug,
        "network_isolation_mode": upstream.network_isolation_mode,
        "specs": [
            {
                "id": item.id,
                "version": item.version,
                "sha256": item.sha256,
                "source_type": item.source_type,
                "source_url": item.source_url,
                "title": item.title,
                "summary": _json_loads(item.summary_json, {}),
                "imported_at": item.imported_at,
            }
            for item in specs
        ],
        "batches": [_batch_view(session, item) for item in batches],
    }


@router.post("/tools/{tool_id}/health/test")
async def test_tool_health(
    tool_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    validate_csrf(request)
    tool, upstream = get_tool_and_upstream(session, tool_id)
    health = await check_tool_health(upstream, request_id(request))
    instance = session.scalar(
        select(ToolUpstreamInstance).where(
            ToolUpstreamInstance.tool_id == tool.id,
            ToolUpstreamInstance.environment_name == "prod",
            ToolUpstreamInstance.instance_name == "default",
        )
    )
    _record_tool_health(instance, health)
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="tool_health_tested",
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={"slug": tool.slug, "status": health.get("status"), "status_code": health.get("status_code")},
    )
    session.commit()
    return {"tool_id": tool.id, **health}


@router.post("/tools/{tool_id}/validation")
async def validate_tool_release(
    tool_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    """在最终发布前执行连通性、认证配置和接口策略静态校验。"""
    validate_csrf(request)
    tool, upstream = get_tool_and_upstream(session, tool_id)
    health = await check_tool_health(upstream, request_id(request))
    latest_batch = _latest_batch(session, tool.id)
    diffs = session.scalars(select(OpenApiDiffItem).where(OpenApiDiffItem.batch_id == latest_batch.id)).all() if latest_batch else []
    pending = [item for item in diffs if item.decision in {"pending", "defer"}]
    policy_problems: list[dict[str, str]] = []
    for diff in diffs:
        if diff.decision != "accept" or not diff.endpoint_id:
            continue
        endpoint = session.get(ApiEndpoint, diff.endpoint_id)
        if not endpoint:
            policy_problems.append({"diff_id": diff.id, "reason": "接口记录不存在"})
            continue
        problem = _publish_validation_error(session, endpoint)
        if problem:
            policy_problems.append({"diff_id": diff.id, "reason": problem})
    token_ready = upstream.auth_type == "none" or bool(decrypt_secret(upstream.token_ciphertext))
    checks = [
        {"key": "health", "label": "上游健康检查", "passed": bool(health.get("reachable")), "detail": f"{health.get('status')} / {health.get('status_code')}"},
        {"key": "upstream_auth", "label": "网关机器凭证", "passed": token_ready, "detail": "无认证模式" if upstream.auth_type == "none" else "已加密配置" if token_ready else "缺少凭证"},
        {"key": "decisions", "label": "接口判断", "passed": not pending, "detail": f"待判断 {len(pending)} 个"},
        {"key": "access_policies", "label": "接口鉴权策略", "passed": not policy_problems, "detail": f"问题 {len(policy_problems)} 个"},
        {"key": "network_isolation", "label": "上游网络隔离声明", "passed": True, "warning": upstream.network_isolation_confirmed_at is None, "detail": upstream.network_isolation_mode},
    ]
    ready = all(bool(item["passed"]) for item in checks)
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="tool_release_validated",
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={"ready": ready, "checks": checks, "policy_problems": policy_problems[:20]},
    )
    session.commit()
    return {"tool_id": tool.id, "ready": ready, "checks": checks, "health": health, "policy_problems": policy_problems}


@router.patch("/tools/{tool_id}/token", response_model=ToolView)
async def replace_tool_token(
    tool_id: str,
    payload: ToolTokenUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> ToolView:
    validate_csrf(request)
    tool, upstream = get_tool_and_upstream(session, tool_id)
    token_ciphertext = encrypt_secret(payload.upstream_token)
    candidate_payload = ToolConfigurationCandidateRequest.model_validate(
        {
            **_tool_configuration_data(tool, upstream),
            "upstream_token": payload.upstream_token,
            "clear_upstream_token": False,
        }
    )
    if tool.status == ToolStatus.ACTIVE.value:
        candidate = _candidate_upstream(upstream, candidate_payload, upstream.base_url, token_ciphertext)
        health = await check_tool_health(candidate, request_id(request))
        if not health.get("reachable"):
            raise HTTPException(status_code=422, detail="新上游密钥健康检查失败，未替换当前密钥")
    previous_revision = upstream.config_revision
    _snapshot_tool_configuration(session, tool, upstream, admin, payload.reason)
    upstream.token_ciphertext = token_ciphertext
    upstream.config_revision += 1
    upstream.updated_at = datetime.now(timezone.utc)
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="tool_token_replaced",
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={
            "slug": tool.slug,
            "reason": payload.reason,
            "token": "***",
            "previous_revision": previous_revision,
            "new_revision": upstream.config_revision,
        },
    )
    session.commit()
    return tool_view(session, tool, upstream)


@router.post("/tools/{tool_id}/openapi/parse")
async def parse_openapi(
    tool_id: str,
    payload: OpenApiParseRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    validate_csrf(request)
    tool, upstream = get_tool_and_upstream(session, tool_id)
    try:
        document, source_type, source_url = await _fetch_openapi_payload(payload, upstream, request_id(request))
        batch, diffs = apply_openapi_document(session, tool=tool, upstream=upstream, admin=admin, document=document, source_type=source_type, source_url=source_url)
    except OpenApiImportError as error:
        raise HTTPException(status_code=422, detail=f"接口文档导入失败: {error}") from error
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="openapi_parsed",
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={"slug": tool.slug, "batch_id": batch.id, "source_type": batch.source_type, "source_url": batch.source_url, "summary": json.loads(batch.summary_json)},
    )
    session.commit()
    return {"batch": _batch_view(session, batch), "diffs": [_diff_view(session, item) for item in diffs]}


@router.post("/tools/{tool_id}/openapi/upload")
async def upload_openapi(
    tool_id: str,
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    validate_csrf(request)
    tool, upstream = get_tool_and_upstream(session, tool_id)
    raw = await file.read(MAX_OPENAPI_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_OPENAPI_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="接口文档不能超过 2 MiB")
    try:
        document = load_openapi_document(raw)
        batch, diffs = apply_openapi_document(session, tool=tool, upstream=upstream, admin=admin, document=document, source_type="upload", source_url=file.filename or "")
    except OpenApiImportError as error:
        raise HTTPException(status_code=422, detail=f"接口文档导入失败: {error}") from error
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="openapi_uploaded",
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={"slug": tool.slug, "batch_id": batch.id, "filename": file.filename, "summary": json.loads(batch.summary_json)},
    )
    session.commit()
    return {"batch": _batch_view(session, batch), "diffs": [_diff_view(session, item) for item in diffs]}


@router.post("/tools/{tool_id}/import-openapi")
async def import_openapi(
    tool_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    validate_csrf(request)
    tool, upstream = get_tool_and_upstream(session, tool_id)
    document, source_type, source_url = await _fetch_openapi_payload(OpenApiParseRequest(source_type="upstream"), upstream, request_id(request))
    try:
        batch, diffs = apply_openapi_document(session, tool=tool, upstream=upstream, admin=admin, document=document, source_type=source_type, source_url=source_url)
    except OpenApiImportError as error:
        raise HTTPException(status_code=422, detail=f"OpenAPI 导入失败: {error}") from error
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="openapi_imported",
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={"version": batch.openapi_version, "operation_count": batch.total_operations, "sha256": batch.source_sha256, "batch_id": batch.id},
    )
    session.commit()
    return {"tool_id": tool.id, "version": batch.openapi_version, "sha256": batch.source_sha256, "operation_count": batch.total_operations, "batch": _batch_view(session, batch), "diffs": [_diff_view(session, item) for item in diffs]}


@router.post("/tools/{tool_id}/sync-diff")
async def create_sync_diff(
    tool_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    validate_csrf(request)
    tool, upstream = get_tool_and_upstream(session, tool_id)
    document, source_type, source_url = await _fetch_openapi_payload(OpenApiParseRequest(source_type="upstream"), upstream, request_id(request))
    try:
        batch, diffs = apply_openapi_document(session, tool=tool, upstream=upstream, admin=admin, document=document, source_type=source_type, source_url=source_url)
    except OpenApiImportError as error:
        raise HTTPException(status_code=422, detail=f"同步差异生成失败: {error}") from error
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="openapi_sync_diff_created",
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={"slug": tool.slug, "batch_id": batch.id, "summary": json.loads(batch.summary_json)},
    )
    session.commit()
    return {"batch": _batch_view(session, batch), "diffs": [_diff_view(session, item) for item in diffs]}


@router.get("/import-batches/{batch_id}", response_model=ImportBatchView)
def get_import_batch(batch_id: str, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> ImportBatchView:
    batch = session.get(ToolImportBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    return _batch_view(session, batch)


@router.get("/tools/{tool_id}/import-batches/latest")
def get_latest_import_batch(
    tool_id: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    get_tool_and_upstream(session, tool_id)
    batch = _latest_batch(session, tool_id)
    if not batch:
        return {"batch": None, "diffs": []}
    diffs = session.scalars(
        select(OpenApiDiffItem)
        .where(OpenApiDiffItem.batch_id == batch.id)
        .order_by(OpenApiDiffItem.diff_type, OpenApiDiffItem.upstream_path)
    ).all()
    return {"batch": _batch_view(session, batch), "diffs": [_diff_view(session, item) for item in diffs]}


@router.get("/import-batches/{batch_id}/diffs", response_model=list[OpenApiDiffView])
def list_import_diffs(batch_id: str, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> list[OpenApiDiffView]:
    diffs = session.scalars(select(OpenApiDiffItem).where(OpenApiDiffItem.batch_id == batch_id).order_by(OpenApiDiffItem.diff_type, OpenApiDiffItem.upstream_path)).all()
    return [_diff_view(session, item) for item in diffs]


@router.post("/import-batches/{batch_id}/confirm", response_model=ImportBatchView)
def confirm_import_batch(
    batch_id: str,
    request: Request,
    payload: ImportBatchConfirmRequest | None = Body(default=None),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> ImportBatchView:
    validate_csrf(request)
    if not payload or not payload.confirm:
        raise HTTPException(status_code=422, detail="确认导入批次必须二次确认")
    batch = session.get(ToolImportBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    unresolved_decisions = session.scalar(
        select(func.count(OpenApiDiffItem.id)).where(
            OpenApiDiffItem.batch_id == batch.id,
            OpenApiDiffItem.decision.in_(["pending", "defer"]),
        )
    ) or 0
    if unresolved_decisions:
        raise HTTPException(status_code=422, detail=f"仍有 {unresolved_decisions} 个接口未决策，不能确认导入批次")
    batch.status = ImportBatchStatus.APPLIED.value
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="openapi_import_confirmed",
        target_type="tool_import_batch",
        target_id=batch.id,
        request_id=request_id(request),
        detail={"tool_id": batch.tool_id, "reason": (payload.reason.strip() if payload else ""), "summary": json.loads(batch.summary_json)},
    )
    session.commit()
    return _batch_view(session, batch)


@router.get("/tools/{tool_id}/endpoints", response_model=list[EndpointView])
def list_endpoints(
    tool_id: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> list[EndpointView]:
    get_tool_and_upstream(session, tool_id)
    endpoint_rows = session.scalars(select(ApiEndpoint).where(ApiEndpoint.tool_id == tool_id).order_by(ApiEndpoint.gateway_path, ApiEndpoint.method)).all()
    return [_endpoint_view(session, item) for item in endpoint_rows]


@router.get("/tools/{tool_id}/endpoint-catalog", response_model=EndpointCatalogPage)
def endpoint_catalog(
    tool_id: str,
    search: str = Query(default="", max_length=160),
    status: str = Query(default="", max_length=24),
    method: str = Query(default="", max_length=8),
    risk_level: str = Query(default="", max_length=24),
    group_name: str = Query(default="", max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> EndpointCatalogPage:
    get_tool_and_upstream(session, tool_id)
    conditions: list[object] = [ApiEndpoint.tool_id == tool_id]
    normalized_search = search.strip().lower()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        conditions.append(
            or_(
                func.lower(ApiEndpoint.summary).like(pattern),
                func.lower(ApiEndpoint.operation_id).like(pattern),
                func.lower(ApiEndpoint.gateway_path).like(pattern),
                func.lower(ApiEndpoint.upstream_path).like(pattern),
            )
        )
    if status.strip():
        conditions.append(ApiEndpoint.status == status.strip().lower())
    if method.strip():
        conditions.append(ApiEndpoint.method == method.strip().upper())
    if risk_level.strip():
        conditions.append(ApiEndpoint.risk_level == risk_level.strip().lower())
    if group_name.strip():
        conditions.append(ApiEndpoint.group_name == group_name.strip())

    total = int(session.scalar(select(func.count(ApiEndpoint.id)).where(*conditions)) or 0)
    page_count = max(1, (total + page_size - 1) // page_size)
    effective_page = min(page, page_count)
    endpoint_rows = list(
        session.scalars(
            select(ApiEndpoint)
            .where(*conditions)
            .order_by(ApiEndpoint.group_name, ApiEndpoint.gateway_path, ApiEndpoint.method)
            .offset((effective_page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    endpoint_ids = [item.id for item in endpoint_rows]
    routes = {
        item.endpoint_id: item
        for item in session.scalars(select(PublishedRoute).where(PublishedRoute.endpoint_id.in_(endpoint_ids))).all()
    } if endpoint_ids else {}
    policies = {
        item.endpoint_id: item
        for item in session.scalars(select(EndpointAccessPolicy).where(EndpointAccessPolicy.endpoint_id.in_(endpoint_ids))).all()
    } if endpoint_ids else {}
    methods = list(
        session.scalars(
            select(ApiEndpoint.method)
            .where(ApiEndpoint.tool_id == tool_id)
            .distinct()
            .order_by(ApiEndpoint.method)
        ).all()
    )
    groups = list(
        session.scalars(
            select(ApiEndpoint.group_name)
            .where(ApiEndpoint.tool_id == tool_id, ApiEndpoint.group_name != "")
            .distinct()
            .order_by(ApiEndpoint.group_name)
        ).all()
    )
    return EndpointCatalogPage(
        items=[_endpoint_view_with_related(item, routes.get(item.id), policies.get(item.id)) for item in endpoint_rows],
        total=total,
        page=effective_page,
        page_size=page_size,
        page_count=page_count,
        methods=methods,
        groups=groups,
    )


@router.get("/endpoints/{endpoint_id}", response_model=EndpointView)
def get_endpoint(
    endpoint_id: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> EndpointView:
    endpoint = session.get(ApiEndpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="接口不存在")
    return _endpoint_view(session, endpoint)


@router.patch("/endpoints/{endpoint_id}", response_model=EndpointView)
def update_endpoint(
    endpoint_id: str,
    payload: EndpointUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> EndpointView:
    validate_csrf(request)
    endpoint = session.get(ApiEndpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="接口不存在")
    status_changed = payload.status is not None and payload.status != endpoint.status
    risk_changed = payload.risk_level is not None and payload.risk_level != endpoint.risk_level
    if (status_changed or risk_changed) and len(payload.reason.strip()) < 4:
        raise HTTPException(status_code=422, detail="修改治理状态或风险等级必须填写至少 4 个字符的审计原因")
    effective_status = payload.status or endpoint.status
    effective_risk_level = payload.risk_level or endpoint.risk_level
    if effective_risk_level == RiskLevel.BLOCKER.value and effective_status not in {
        EndpointStatus.BLOCKED.value,
        EndpointStatus.EXCLUDED.value,
    }:
        raise HTTPException(status_code=422, detail="阻断级风险必须保持阻断或排除状态，降低风险后才能改为候选或草稿")
    if payload.gateway_path is not None:
        gateway_path = payload.gateway_path.strip()
        if not gateway_path.startswith("/") or "?" in gateway_path or "#" in gateway_path or ".." in gateway_path:
            raise HTTPException(status_code=422, detail="Gateway 路径不合法")
        if _has_gateway_conflict(session, endpoint.tool_id, endpoint.id, endpoint.method, gateway_path):
            raise HTTPException(status_code=409, detail="Gateway 路径与同工具下其他接口冲突")
        endpoint.gateway_path = gateway_path
    if payload.summary is not None:
        endpoint.summary = payload.summary
    if payload.public_description is not None:
        endpoint.public_description = payload.public_description
    if payload.content_types is not None:
        endpoint.content_types = _json_dumps(sorted({item.lower() for item in payload.content_types if item}))
    if payload.governance is not None:
        existing = _json_loads(endpoint.governance_json, {})
        merged = existing if isinstance(existing, dict) else {}
        merged.update(payload.governance)
        endpoint.governance_json = _json_dumps(merged)
    if payload.storage_action is not None:
        existing = _json_loads(endpoint.governance_json, {})
        merged = existing if isinstance(existing, dict) else {}
        merged["storage_action"] = payload.storage_action
        endpoint.governance_json = _json_dumps(merged)
        route = session.scalar(select(PublishedRoute).where(PublishedRoute.endpoint_id == endpoint.id))
        if route:
            route.storage_action = payload.storage_action
            route.route_version = (route.route_version or 1) + 1
    if payload.status is not None:
        if payload.status != endpoint.status:
            if endpoint.status == EndpointStatus.PUBLISHED.value:
                raise HTTPException(status_code=422, detail="已发布接口不能通过精修表单修改状态，请使用停用、排除或阻断操作")
            endpoint.status = payload.status
            if payload.status in {EndpointStatus.EXCLUDED.value, EndpointStatus.BLOCKED.value}:
                _disable_endpoint_route(
                    session,
                    endpoint,
                    reason=f"endpoint_updated: {payload.reason.strip() or '管理员修改接口治理状态'}",
                    admin_user_id=admin.id,
                )
    if payload.exclusion_reason is not None:
        endpoint.exclusion_reason = payload.exclusion_reason
    if payload.risk_level is not None:
        endpoint.risk_level = payload.risk_level
    if payload.risk_flags is not None:
        endpoint.risk_flags_json = _json_dumps(sorted(set(payload.risk_flags)))
    endpoint.updated_at = datetime.now(timezone.utc)
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="endpoint_updated",
        target_type="api_endpoint",
        target_id=endpoint.id,
        request_id=request_id(request),
        detail={"method": endpoint.method, "gateway_path": endpoint.gateway_path, "reason": payload.reason},
    )
    session.commit()
    return _endpoint_view(session, endpoint)


def _set_endpoint_governance_status(
    session: Session,
    *,
    endpoint: ApiEndpoint,
    status_value: str,
    reason: str,
    admin: User,
    request: Request,
    action: str,
) -> EndpointView:
    endpoint.status = status_value
    endpoint.exclusion_reason = reason
    endpoint.updated_at = datetime.now(timezone.utc)
    route = _disable_endpoint_route(
        session,
        endpoint,
        reason=f"{action}: {reason}",
        admin_user_id=admin.id,
    )
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action=action,
        target_type="api_endpoint",
        target_id=endpoint.id,
        request_id=request_id(request),
        detail={
            "method": endpoint.method,
            "gateway_path": endpoint.gateway_path or endpoint.upstream_path,
            "reason": reason,
            "route_disabled": bool(route),
        },
    )
    session.commit()
    return _endpoint_view(session, endpoint)


@router.post("/endpoints/{endpoint_id}/exclude", response_model=EndpointView)
def exclude_endpoint(
    endpoint_id: str,
    payload: UserRejectRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> EndpointView:
    validate_csrf(request)
    endpoint = session.get(ApiEndpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="接口不存在")
    return _set_endpoint_governance_status(
        session,
        endpoint=endpoint,
        status_value=EndpointStatus.EXCLUDED.value,
        reason=payload.reason,
        admin=admin,
        request=request,
        action="endpoint_excluded",
    )


@router.post("/endpoints/{endpoint_id}/block", response_model=EndpointView)
def block_endpoint(
    endpoint_id: str,
    payload: UserRejectRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> EndpointView:
    validate_csrf(request)
    endpoint = session.get(ApiEndpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="接口不存在")
    return _set_endpoint_governance_status(
        session,
        endpoint=endpoint,
        status_value=EndpointStatus.BLOCKED.value,
        reason=payload.reason,
        admin=admin,
        request=request,
        action="endpoint_blocked",
    )


@router.patch("/diffs/{diff_id}/decision", response_model=OpenApiDiffView)
def update_diff_decision(
    diff_id: str,
    payload: DiffDecisionRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> OpenApiDiffView:
    validate_csrf(request)
    diff = session.get(OpenApiDiffItem, diff_id)
    if not diff:
        raise HTTPException(status_code=404, detail="差异项不存在")
    decision_reason = payload.reason.strip() or "管理员在接口治理列表中确认接口决策"
    _apply_diff_decision(session, diff=diff, payload=payload, admin=admin, reason=decision_reason)
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="openapi_diff_decision",
        target_type="openapi_diff_item",
        target_id=diff.id,
        request_id=request_id(request),
        detail={"decision": payload.decision, "reason": decision_reason, "method": diff.method, "path": diff.gateway_path},
    )
    session.commit()
    return _diff_view(session, diff)


def _apply_diff_decision(
    session: Session,
    *,
    diff: OpenApiDiffItem,
    payload: DiffDecisionRequest,
    admin: User,
    reason: str,
) -> None:
    batch = session.get(ToolImportBatch, diff.batch_id)
    if batch and batch.status == ImportBatchStatus.APPLIED.value:
        raise HTTPException(status_code=422, detail="已确认的导入批次不可再修改决策，请重新生成同步差异")
    endpoint = session.get(ApiEndpoint, diff.endpoint_id) if diff.endpoint_id else None
    if payload.decision == "accept":
        if not endpoint:
            raise HTTPException(status_code=422, detail="差异项没有可发布接口")
        if not batch:
            raise HTTPException(status_code=422, detail="差异项所属导入批次不存在")
        risk_flags = _json_loads(diff.risk_flags_json, [])
        detail = _json_loads(diff.detail_json, {})
        operation = detail.get("operation", {}) if isinstance(detail, dict) else {}
        if not isinstance(operation, dict):
            raise HTTPException(status_code=422, detail="差异项缺少可应用的接口结构")
        _tool, upstream = get_tool_and_upstream(session, batch.tool_id)
        # 单接口发布和批量发布必须使用同一份已接受 operation，避免治理信息与数据面不一致。
        _apply_import_operation_to_endpoint(
            endpoint,
            operation=operation,
            upstream=upstream,
            spec_id=batch.spec_id,
            diff_type=diff.diff_type,
            risk_level=diff.risk_level,
            risk_flags=risk_flags if isinstance(risk_flags, list) else [],
        )
        suggestion = suggest_access_policy(
            method=diff.method,
            path=diff.upstream_path,
            summary=diff.summary,
            risk_flags=set(risk_flags if isinstance(risk_flags, list) else []),
            request_body=operation.get("request_body", {}) if isinstance(operation, dict) else {},
            responses=operation.get("responses", {}) if isinstance(operation, dict) else {},
        )
        if payload.access_policy:
            access_mode = payload.access_policy.access_mode
            rules = payload.access_policy.rules
            shared_confirm = payload.access_policy.shared_confirm
            source = "manual"
        elif payload.use_suggestion and suggestion["complete"]:
            access_mode = str(suggestion["access_mode"])
            rules = dict(suggestion["rules"])
            shared_confirm = False
            source = "auto"
        else:
            missing = ", ".join(str(item) for item in suggestion["missing_fields"])
            raise HTTPException(status_code=422, detail=f"接口访问策略尚未完整：{missing or '需要管理员确认'}")
        is_global_list = "global_list_endpoint" in set(risk_flags if isinstance(risk_flags, list) else [])
        if access_mode == "shared" and is_global_list and (not shared_confirm or not reason.strip()):
            raise HTTPException(status_code=422, detail="全局列表设为共享数据必须明确确认并填写审计原因")
        normalized_rules, errors = validate_access_policy(access_mode, rules, shared_confirm=shared_confirm)
        if errors:
            raise HTTPException(status_code=422, detail={"message": "接口访问策略不完整", "errors": errors})
        upsert_endpoint_access_policy(
            session,
            endpoint=endpoint,
            access_mode=access_mode,
            rules=normalized_rules,
            source=source,
            confirmed_by_user_id=admin.id,
            confirmed=True,
        )
    diff.decision = payload.decision
    diff.updated_at = datetime.now(timezone.utc)
    detail = _json_loads(diff.detail_json, {})
    detail = detail if isinstance(detail, dict) else {}
    detail["decision_reason"] = reason
    diff.detail_json = _json_dumps(detail)
    if endpoint:
        if payload.decision == "exclude":
            endpoint.status = EndpointStatus.EXCLUDED.value
            endpoint.exclusion_reason = reason
        elif payload.decision == "block":
            endpoint.status = EndpointStatus.BLOCKED.value
        elif payload.decision == "accept" and endpoint.status != EndpointStatus.PUBLISHED.value:
            endpoint.status = EndpointStatus.CANDIDATE.value
        endpoint.updated_at = datetime.now(timezone.utc)
        if payload.decision in {"exclude", "block"}:
            _disable_endpoint_route(
                session,
                endpoint,
                reason=f"openapi_diff_{payload.decision}: {reason}",
                admin_user_id=admin.id,
            )


@router.post("/diffs/bulk-decision", response_model=BulkDiffDecisionResult)
def bulk_update_diff_decisions(
    payload: BulkDiffDecisionRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> BulkDiffDecisionResult:
    validate_csrf(request)
    rows = session.scalars(select(OpenApiDiffItem).where(OpenApiDiffItem.id.in_(payload.diff_ids))).all()
    by_id = {item.id: item for item in rows}
    updated: list[OpenApiDiffView] = []
    failed: list[dict[str, str]] = []
    for diff_id in payload.diff_ids:
        diff = by_id.get(diff_id)
        if not diff:
            failed.append({"diff_id": diff_id, "reason": "差异项不存在"})
            continue
        decision_payload = DiffDecisionRequest(
            decision=payload.decision,
            reason=payload.reason,
            access_policy=payload.access_policy,
            use_suggestion=payload.use_suggestions,
        )
        try:
            with session.begin_nested():
                _apply_diff_decision(session, diff=diff, payload=decision_payload, admin=admin, reason=payload.reason.strip() or "管理员批量确认接口决策")
                session.flush()
        except HTTPException as error:
            reason = error.detail.get("message", str(error.detail)) if isinstance(error.detail, dict) else str(error.detail)
            failed.append({"diff_id": diff.id, "reason": reason})
            continue
        updated.append(_diff_view(session, diff))
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="openapi_diff_bulk_decision",
        target_type="openapi_diff_item",
        target_id=rows[0].batch_id if rows else "",
        request_id=request_id(request),
        detail={"decision": payload.decision, "updated_count": len(updated), "failed_count": len(failed), "reason": payload.reason},
    )
    session.commit()
    return BulkDiffDecisionResult(updated=updated, failed=failed)


def _publish_validation_error(session: Session, endpoint: ApiEndpoint, *, max_risk_level: str | None = None) -> str | None:
    gateway_path = endpoint.gateway_path or endpoint.upstream_path
    if not gateway_path.startswith("/") or "?" in gateway_path or "#" in gateway_path or ".." in gateway_path:
        return "Gateway 路径不合法"
    if _has_gateway_conflict(session, endpoint.tool_id, endpoint.id, endpoint.method, gateway_path):
        return "Gateway 路径冲突"
    risk_flags = _json_loads(endpoint.risk_flags_json, [])
    risk_flag_set = set(risk_flags if isinstance(risk_flags, list) else [])
    if risk_flag_set.intersection({"path_conflict", "upstream_deleted"}):
        return "路径冲突或上游已删除接口不能发布"
    if endpoint.status in {EndpointStatus.BLOCKED.value, EndpointStatus.EXCLUDED.value}:
        return "已阻断或排除的接口不能直接发布，请先完成整改并将治理状态改为候选或草稿"
    if endpoint.risk_level == RiskLevel.BLOCKER.value:
        return "阻断级接口不能发布，请先完成风险整改并降低风险等级"
    if max_risk_level is not None and RISK_RANK.get(endpoint.risk_level, RISK_RANK[RiskLevel.BLOCKER.value]) > RISK_RANK[max_risk_level]:
        return f"接口风险等级 {endpoint.risk_level} 超过本次发布上限 {max_risk_level}"
    access_policy = session.scalar(select(EndpointAccessPolicy).where(EndpointAccessPolicy.endpoint_id == endpoint.id))
    if not access_policy or not access_policy.confirmed_at:
        return "接口访问策略尚未确认"
    rules = _json_loads(access_policy.rules_json, {})
    _, errors = validate_access_policy(access_policy.access_mode, rules, shared_confirm=True)
    if errors:
        return f"接口访问策略不完整：{'; '.join(errors)}"
    return None


def _publish_endpoint_without_commit(session: Session, endpoint: ApiEndpoint, admin: User, reason: str) -> PublishedRoute:
    gateway_path = endpoint.gateway_path or endpoint.upstream_path
    route = session.scalar(select(PublishedRoute).where(PublishedRoute.endpoint_id == endpoint.id))
    tool, upstream = get_tool_and_upstream(session, endpoint.tool_id)
    policy = _route_policy_from_endpoint(endpoint, tool, upstream)
    access_policy = session.scalar(select(EndpointAccessPolicy).where(EndpointAccessPolicy.endpoint_id == endpoint.id))
    if not access_policy or not access_policy.confirmed_at:
        raise HTTPException(status_code=422, detail="接口访问策略尚未确认")
    if not route:
        route = PublishedRoute(
            tool_id=endpoint.tool_id,
            endpoint_id=endpoint.id,
            method=endpoint.method,
            gateway_path=gateway_path,
            upstream_path=endpoint.upstream_path,
            scopes="*",
            published_by_user_id=admin.id,
            publish_reason=reason,
            is_enabled=True,
            route_version=1,
        )
        session.add(route)
    else:
        route.gateway_path = gateway_path
        route.upstream_path = endpoint.upstream_path
        route.scopes = "*"
        route.is_enabled = True
        route.published_by_user_id = admin.id
        route.publish_reason = reason
        route.route_version = (route.route_version or 1) + 1
        route.updated_at = datetime.now(timezone.utc)
    _apply_route_policy(route, policy)
    route.access_mode = access_policy.access_mode
    route.resource_policy_json = access_policy.rules_json
    route.policy_version = access_policy.version
    endpoint.status = EndpointStatus.PUBLISHED.value
    endpoint.updated_at = datetime.now(timezone.utc)
    return route


@router.post("/tools/{tool_id}/endpoints/bulk-publish", response_model=BulkPublishResult)
def bulk_publish_safe_endpoints(
    tool_id: str,
    payload: BulkPublishRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> BulkPublishResult:
    validate_csrf(request)
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="批量发布必须二次确认")
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="批量发布必须填写审计原因")
    tool, upstream = get_tool_and_upstream(session, tool_id)
    latest_batch = _latest_batch(session, tool.id)
    if not latest_batch:
        raise HTTPException(status_code=422, detail="工具尚未生成导入批次")
    if latest_batch.status != ImportBatchStatus.APPLIED.value:
        raise HTTPException(status_code=422, detail="最近导入批次尚未确认，不能发布接口")
    diffs = session.scalars(select(OpenApiDiffItem).where(OpenApiDiffItem.batch_id == latest_batch.id).order_by(OpenApiDiffItem.gateway_path, OpenApiDiffItem.method)).all()
    pending = [item for item in diffs if item.decision in {"pending", "defer"}]
    if pending:
        raise HTTPException(status_code=422, detail={"message": "仍有接口未完成放行或阻断判断", "problems": [{"diff_id": item.id, "method": item.method, "gateway_path": item.gateway_path, "reason": "接口尚未判断"} for item in pending]})
    accepted_endpoints = [session.get(ApiEndpoint, item.endpoint_id) for item in diffs if item.decision == "accept" and item.endpoint_id]
    endpoints = [item for item in accepted_endpoints if item is not None]
    publishable: list[ApiEndpoint] = []
    skipped: list[dict[str, str]] = []
    problems = []
    accepted_diffs_by_endpoint = {item.endpoint_id: item for item in diffs if item.decision == "accept" and item.endpoint_id}
    for endpoint in endpoints:
        diff = accepted_diffs_by_endpoint[endpoint.id]
        detail = _json_loads(diff.detail_json, {})
        operation = detail.get("operation") if isinstance(detail, dict) else None
        if not isinstance(operation, dict):
            problems.append({"endpoint_id": endpoint.id, "method": endpoint.method, "gateway_path": endpoint.gateway_path or endpoint.upstream_path, "reason": "导入批次缺少可应用的接口结构"})
            continue
        _apply_import_operation_to_endpoint(
            endpoint,
            operation=operation,
            upstream=upstream,
            spec_id=latest_batch.spec_id,
            diff_type=diff.diff_type,
            risk_level=diff.risk_level,
            risk_flags=_json_loads(diff.risk_flags_json, []),
        )
        validation_error = _publish_validation_error(session, endpoint, max_risk_level=payload.max_risk_level)
        if validation_error:
            item = {"endpoint_id": endpoint.id, "method": endpoint.method, "gateway_path": endpoint.gateway_path or endpoint.upstream_path, "reason": validation_error}
            if endpoint.risk_level in {RiskLevel.HIGH.value, RiskLevel.BLOCKER.value}:
                skipped.append(item)
            else:
                problems.append(item)
        else:
            publishable.append(endpoint)
    if problems:
        raise HTTPException(status_code=422, detail={"message": "发布前校验失败，未修改任何线上路由", "problems": problems})
    published: list[dict[str, str]] = []
    tool.status = ToolStatus.ACTIVE.value
    tool.is_enabled = True
    for endpoint in publishable:
        gateway_path = endpoint.gateway_path or endpoint.upstream_path
        _publish_endpoint_without_commit(session, endpoint, admin, reason)
        published.append({"endpoint_id": endpoint.id, "method": endpoint.method, "gateway_path": gateway_path})
    for item in skipped:
        endpoint = session.get(ApiEndpoint, item["endpoint_id"])
        if not endpoint:
            continue
        endpoint.status = EndpointStatus.BLOCKED.value if endpoint.risk_level == RiskLevel.BLOCKER.value else EndpointStatus.CANDIDATE.value
        endpoint.updated_at = datetime.now(timezone.utc)
        _disable_endpoint_route(
            session,
            endpoint,
            reason=f"bulk_publish_risk_limit: {reason}",
            admin_user_id=admin.id,
        )
    for diff in diffs:
        if diff.decision not in {"block", "exclude"} or not diff.endpoint_id:
            continue
        endpoint = session.get(ApiEndpoint, diff.endpoint_id)
        if endpoint:
            endpoint.status = EndpointStatus.EXCLUDED.value if diff.decision == "exclude" else EndpointStatus.BLOCKED.value
            endpoint.updated_at = datetime.now(timezone.utc)
            _disable_endpoint_route(
                session,
                endpoint,
                reason=f"bulk_publish_{diff.decision}: {reason}",
                admin_user_id=admin.id,
            )
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="routes_bulk_published",
        target_type="tool",
        target_id=tool.id,
        request_id=request_id(request),
        detail={
            "tool_slug": tool.slug,
            "reason": reason,
            "max_risk_level": payload.max_risk_level,
            "published_count": len(published),
            "skipped_count": len(skipped),
        },
    )
    session.commit()
    return BulkPublishResult(tool_id=tool.id, published_count=len(published), skipped_count=len(skipped), published=published, skipped=skipped)


@router.patch("/endpoints/{endpoint_id}/publish", response_model=EndpointView)
def publish_endpoint(
    endpoint_id: str,
    payload: PublishRouteRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> EndpointView:
    validate_csrf(request)
    endpoint = session.get(ApiEndpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="接口不存在")
    tool = session.get(Tool, endpoint.tool_id)
    if not tool or not tool.is_enabled or tool.status == ToolStatus.DISABLED.value:
        raise HTTPException(status_code=422, detail="工具未启用，不能发布接口")
    if payload.is_enabled and not payload.confirm:
        raise HTTPException(status_code=422, detail="发布接口必须二次确认")
    if payload.is_enabled:
        validation_error = _publish_validation_error(session, endpoint)
        if validation_error:
            raise HTTPException(status_code=422, detail=validation_error)
    if payload.is_enabled and endpoint.risk_level in {RiskLevel.HIGH.value, RiskLevel.BLOCKER.value} and not payload.reason.strip():
        raise HTTPException(status_code=422, detail="高风险接口发布必须填写审计原因")
    gateway_path = endpoint.gateway_path or endpoint.upstream_path
    if _has_gateway_conflict(session, endpoint.tool_id, endpoint.id, endpoint.method, gateway_path):
        raise HTTPException(status_code=409, detail="Gateway 路径冲突，不能发布")
    if payload.is_enabled:
        route = _publish_endpoint_without_commit(session, endpoint, admin, payload.reason)
    else:
        route = session.scalar(select(PublishedRoute).where(PublishedRoute.endpoint_id == endpoint.id))
        if not route:
            raise HTTPException(status_code=404, detail="接口尚未发布")
        _disable_endpoint_route(
            session,
            endpoint,
            reason=payload.reason.strip() or "管理员直接停用单接口路由",
            admin_user_id=admin.id,
        )
        endpoint.status = EndpointStatus.DRAFT.value
        endpoint.updated_at = datetime.now(timezone.utc)
    log_admin_event(
        session,
        admin_user_id=admin.id,
        action="route_published" if payload.is_enabled else "route_disabled",
        target_type="api_endpoint",
        target_id=endpoint.id,
        request_id=request_id(request),
        detail={
            "method": endpoint.method,
            "gateway_path": gateway_path,
            "reason": payload.reason,
            "risk_level": endpoint.risk_level,
            "route_version": route.route_version,
            "policy": _json_loads(route.policy_json, {}),
        },
    )
    session.commit()
    return _endpoint_view(session, endpoint)


@router.get("/tools/{tool_id}/health")
async def tool_health(
    tool_id: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    tool, upstream = get_tool_and_upstream(session, tool_id)
    health = await check_tool_health(upstream)
    return {"tool_id": tool.id, **health}


def _request_rows_to_views(session: Session, rows: list[GatewayRequest]) -> list[dict[str, object]]:
    return gateway_call_views(session, rows, admin=True)


@router.get("/monitor/summary")
def monitor_summary(
    user_id: str | None = Query(default=None, max_length=36),
    username: str | None = Query(default=None, max_length=64),
    tool_slug: str | None = Query(default=None, max_length=64),
    status_code: int | None = Query(default=None, ge=100, le=599),
    monitor_request_id: str | None = Query(default=None, alias="request_id", max_length=64),
    start: str | None = Query(default=None, max_length=40),
    end: str | None = Query(default=None, max_length=40),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    resolved_user_id = _resolve_monitor_user_id(session, user_id, username)
    conditions = _monitor_conditions(
        user_id=resolved_user_id,
        tool_slug=tool_slug,
        status_code=status_code,
        request_id_filter=monitor_request_id,
        start_time=_parse_datetime_query(start, "start"),
        end_time=_parse_datetime_query(end, "end"),
    )
    aggregate = session.execute(
        select(
            func.count(GatewayRequest.id),
            func.coalesce(func.avg(GatewayRequest.duration_ms), 0),
            func.coalesce(func.sum(GatewayRequest.request_bytes), 0),
            func.coalesce(func.sum(GatewayRequest.response_bytes), 0),
            func.coalesce(func.sum(case((GatewayRequest.status_code < 400, 1), else_=0)), 0),
            func.coalesce(func.sum(case((GatewayRequest.status_code >= 400, 1), else_=0)), 0),
        ).where(*conditions)
    ).one()
    total = int(aggregate[0] or 0)
    success = int(aggregate[4] or 0)
    failed = int(aggregate[5] or 0)
    recent_failures = session.scalars(
        select(GatewayRequest).where(*conditions, GatewayRequest.status_code >= 400).order_by(GatewayRequest.created_at.desc()).limit(8)
    ).all()
    return {
        "total_requests": total,
        "success_requests": success,
        "failed_requests": failed,
        "success_rate": round(success / total, 4) if total else 0,
        "avg_duration_ms": round(float(aggregate[1] or 0), 2),
        "request_bytes": int(aggregate[2] or 0),
        "response_bytes": int(aggregate[3] or 0),
        "recent_failures": _request_rows_to_views(session, list(recent_failures)),
    }


def _key_anomaly_candidates(session: Session, conditions: list[object]) -> list[dict[str, object]]:
    rows = session.execute(
        select(
            GatewayRequest.api_key_id,
            ApiKey.prefix,
            User.username,
            func.count(GatewayRequest.id),
            func.coalesce(func.sum(case((GatewayRequest.status_code >= 400, 1), else_=0)), 0),
            func.coalesce(func.sum(case((GatewayRequest.status_code == 429, 1), else_=0)), 0),
            func.max(GatewayRequest.created_at),
        )
        .join(ApiKey, ApiKey.id == GatewayRequest.api_key_id, isouter=True)
        .join(User, User.id == GatewayRequest.user_id, isouter=True)
        .where(*conditions, GatewayRequest.api_key_id.is_not(None))
        .group_by(GatewayRequest.api_key_id, ApiKey.prefix, User.username)
        .order_by(func.count(GatewayRequest.id).desc())
        .limit(100)
    ).all()
    candidates: list[dict[str, object]] = []
    for row in rows:
        total = int(row[3] or 0)
        failed = int(row[4] or 0)
        rate_limited = int(row[5] or 0)
        failure_rate = _rate(failed, total)
        if total >= 3 and (failure_rate >= 0.5 or failed >= 10 or rate_limited >= 3):
            candidates.append(
                {
                    "api_key_id": row[0],
                    "api_key_prefix": row[1] or "",
                    "username": row[2] or "",
                    "total_requests": total,
                    "failed_requests": failed,
                    "rate_limited_requests": rate_limited,
                    "failure_rate": failure_rate,
                    "last_called_at": row[6],
                }
            )
    return candidates


def _monitor_alerts(session: Session, conditions: list[object], tool_slug_filter: str | None = None) -> list[dict[str, object]]:
    """基于近期调用记录计算实时告警。"""

    alerts: list[dict[str, object]] = []
    for item in _key_anomaly_candidates(session, conditions):
        severity = "critical" if int(item["failed_requests"]) >= 20 or float(item["failure_rate"]) >= 0.75 else "warning"
        alerts.append(
            {
                "id": f"key-failure-{item['api_key_id']}",
                "type": "key_failure_rate",
                "severity": severity,
                "title": "Key 失败率异常",
                "message": f"{item['username'] or '未知用户'} 的 Key {item['api_key_prefix']} 近期失败率达到 {float(item['failure_rate']) * 100:.1f}%。",
                "api_key_prefix": item["api_key_prefix"],
                "username": item["username"],
                "count": item["failed_requests"],
                "failure_rate": item["failure_rate"],
                "last_seen_at": item["last_called_at"],
                "details": item,
            }
        )

    tool_rows = session.execute(
        select(
            GatewayRequest.tool_slug,
            func.count(GatewayRequest.id),
            func.coalesce(func.sum(case((GatewayRequest.status_code >= 500, 1), else_=0)), 0),
            func.max(GatewayRequest.created_at),
        )
        .where(*conditions, GatewayRequest.tool_slug != "")
        .group_by(GatewayRequest.tool_slug)
        .order_by(func.coalesce(func.sum(case((GatewayRequest.status_code >= 500, 1), else_=0)), 0).desc())
        .limit(100)
    ).all()
    for row in tool_rows:
        total = int(row[1] or 0)
        server_errors = int(row[2] or 0)
        error_rate = _rate(server_errors, total)
        if server_errors >= 3 or (total >= 5 and error_rate >= 0.3):
            alerts.append(
                {
                    "id": f"tool-5xx-{row[0]}",
                    "type": "tool_5xx_spike",
                    "severity": "critical" if server_errors >= 10 or error_rate >= 0.6 else "warning",
                    "title": "工具 5xx 异常升高",
                    "message": f"工具 {row[0]} 近期出现 {server_errors} 次 5xx/服务不可用类失败。",
                    "tool_slug": row[0],
                    "count": server_errors,
                    "failure_rate": error_rate,
                    "last_seen_at": row[3],
                    "details": {"total_requests": total, "server_error_requests": server_errors},
                }
            )

    settings = get_settings()
    upload_threshold = max(1, int(settings.max_upload_bytes * 0.8))
    download_threshold = max(1, int(settings.max_download_bytes * 0.8))
    traffic_rows = session.scalars(
        select(GatewayRequest)
        .where(*conditions)
        .where((GatewayRequest.request_bytes >= upload_threshold) | (GatewayRequest.response_bytes >= download_threshold))
        .order_by(GatewayRequest.created_at.desc())
        .limit(10)
    ).all()
    for row in traffic_rows:
        alerts.append(
            {
                "id": f"traffic-{row.id}",
                "type": "traffic_anomaly",
                "severity": "warning",
                "title": "上传/下载流量接近上限",
                "message": f"请求 {row.id} 的请求或响应体量接近平台限制，请结合业务预期确认。",
                "request_id": row.id,
                "tool_slug": row.tool_slug,
                "status_code": row.status_code,
                "count": max(row.request_bytes, row.response_bytes),
                "last_seen_at": row.created_at,
                "details": {"request_bytes": row.request_bytes, "response_bytes": row.response_bytes},
            }
        )

    now = utc_now()
    worker_required = bool(session.scalar(select(FileSyncJob.id).where(FileSyncJob.job_type.in_(["poll", "verify", "delete"])).limit(1)))
    worker_online = bool(session.scalar(select(FileWorkerHeartbeat.instance_id).where(FileWorkerHeartbeat.last_seen_at >= now - timedelta(seconds=30)).limit(1)))
    if worker_required and not worker_online:
        alerts.append({
            "id": "file-worker-offline",
            "type": "file_worker_offline",
            "severity": "critical",
            "title": "文件同步进程离线",
            "message": "任务状态、文件清单、删除和存储水位不会继续后台同步。",
            "count": 1,
            "last_seen_at": now,
            "details": {},
        })
    retry_count = int(session.scalar(select(func.count(FileSyncJob.id)).where(FileSyncJob.status == "retry", FileSyncJob.attempts > 0)) or 0)
    if retry_count:
        alerts.append({
            "id": "file-sync-retries",
            "type": "file_sync_retries",
            "severity": "critical" if retry_count >= 10 else "warning",
            "title": "文件同步持续失败",
            "message": f"当前有 {retry_count} 个文件同步或清理任务等待重试。",
            "count": retry_count,
            "last_seen_at": now,
            "details": {"retry_jobs": retry_count},
        })
    watermark_query = select(Tool).where(Tool.storage_total_bytes.is_not(None))
    if tool_slug_filter:
        watermark_query = watermark_query.where(Tool.slug == tool_slug_filter)
    for tool in session.scalars(watermark_query).all():
        checked_at = tool.storage_checked_at.replace(tzinfo=timezone.utc) if tool.storage_checked_at and tool.storage_checked_at.tzinfo is None else tool.storage_checked_at
        stale = bool(checked_at and checked_at < now - timedelta(seconds=get_settings().storage_watermark_max_age_seconds))
        ratio = tool.storage_free_bytes / tool.storage_total_bytes if tool.storage_total_bytes and tool.storage_free_bytes is not None else None
        if stale or (ratio is not None and ratio < 0.15):
            critical = ratio is not None and ratio < 0.05
            alerts.append({
                "id": f"tool-storage-{tool.id}",
                "type": "tool_storage_watermark",
                "severity": "critical" if critical or stale else "warning",
                "title": "工具存储水位异常" if not stale else "工具存储水位已过期",
                "message": f"工具 {tool.slug} 的存储水位需要管理员处理。",
                "tool_slug": tool.slug,
                "count": tool.storage_free_bytes or 0,
                "last_seen_at": checked_at or now,
                "details": {"free_ratio": ratio, "checked_at": checked_at, "stale": stale},
            })

    severity_order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(alerts, key=lambda item: (severity_order.get(str(item.get("severity")), 9), str(item.get("last_seen_at") or "")))


@router.get("/monitor/metrics")
def monitor_metrics(
    user_id: str | None = Query(default=None, max_length=36),
    username: str | None = Query(default=None, max_length=64),
    tool_slug: str | None = Query(default=None, max_length=64),
    status_code: int | None = Query(default=None, ge=100, le=599),
    monitor_request_id: str | None = Query(default=None, alias="request_id", max_length=64),
    start: str | None = Query(default=None, max_length=40),
    end: str | None = Query(default=None, max_length=40),
    window_minutes: int = Query(default=60, ge=1, le=10_080),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    resolved_user_id = _resolve_monitor_user_id(session, user_id, username)
    effective_start, effective_end = _monitor_window(start, end, window_minutes)
    conditions = _monitor_conditions(
        user_id=resolved_user_id,
        tool_slug=tool_slug,
        status_code=status_code,
        request_id_filter=monitor_request_id,
        start_time=effective_start,
        end_time=effective_end,
    )
    aggregate = session.execute(
        select(
            func.count(GatewayRequest.id),
            func.coalesce(func.avg(GatewayRequest.duration_ms), 0),
            func.coalesce(func.sum(GatewayRequest.request_bytes), 0),
            func.coalesce(func.sum(GatewayRequest.response_bytes), 0),
            func.coalesce(func.sum(case((GatewayRequest.status_code < 400, 1), else_=0)), 0),
            func.coalesce(func.sum(case((GatewayRequest.status_code >= 400, 1), else_=0)), 0),
            func.coalesce(func.sum(case((GatewayRequest.status_code == 429, 1), else_=0)), 0),
        ).where(*conditions)
    ).one()
    durations = [int(row[0] or 0) for row in session.execute(select(GatewayRequest.duration_ms).where(*conditions)).all()]
    total = int(aggregate[0] or 0)
    success = int(aggregate[4] or 0)
    failed = int(aggregate[5] or 0)
    alerts = _monitor_alerts(session, conditions, tool_slug)
    return {
        "window": {"start": effective_start, "end": effective_end, "minutes": window_minutes},
        "requests": {
            "total": total,
            "success": success,
            "failed": failed,
            "success_rate": _rate(success, total),
            "error_rate": _rate(failed, total),
            "rate_limited": int(aggregate[6] or 0),
        },
        "latency_ms": {
            "avg": round(float(aggregate[1] or 0), 2),
            "p50": _percentile_ms(durations, 0.50),
            "p95": _percentile_ms(durations, 0.95),
            "p99": _percentile_ms(durations, 0.99),
        },
        "traffic_bytes": {"request": int(aggregate[2] or 0), "response": int(aggregate[3] or 0), "total": int((aggregate[2] or 0) + (aggregate[3] or 0))},
        "key_anomalies": {"count": sum(1 for item in alerts if item["type"] == "key_failure_rate")},
        "alerts": {"count": len(alerts), "critical_count": sum(1 for item in alerts if item["severity"] == "critical")},
    }


@router.get("/monitor/alerts")
def monitor_alerts(
    user_id: str | None = Query(default=None, max_length=36),
    username: str | None = Query(default=None, max_length=64),
    tool_slug: str | None = Query(default=None, max_length=64),
    status_code: int | None = Query(default=None, ge=100, le=599),
    monitor_request_id: str | None = Query(default=None, alias="request_id", max_length=64),
    start: str | None = Query(default=None, max_length=40),
    end: str | None = Query(default=None, max_length=40),
    window_minutes: int = Query(default=60, ge=1, le=10_080),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> list[dict[str, object]]:
    resolved_user_id = _resolve_monitor_user_id(session, user_id, username)
    effective_start, effective_end = _monitor_window(start, end, window_minutes)
    conditions = _monitor_conditions(
        user_id=resolved_user_id,
        tool_slug=tool_slug,
        status_code=status_code,
        request_id_filter=monitor_request_id,
        start_time=effective_start,
        end_time=effective_end,
    )
    return _monitor_alerts(session, conditions, tool_slug)


@router.get("/monitor/requests")
def monitor_requests(
    user_id: str | None = Query(default=None, max_length=36),
    username: str | None = Query(default=None, max_length=64),
    tool_slug: str | None = Query(default=None, max_length=64),
    status_code: int | None = Query(default=None, ge=100, le=599),
    monitor_request_id: str | None = Query(default=None, alias="request_id", max_length=64),
    start: str | None = Query(default=None, max_length=40),
    end: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=300),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> list[dict[str, object]]:
    resolved_user_id = _resolve_monitor_user_id(session, user_id, username)
    rows = session.scalars(
        _monitor_filters(
            user_id=resolved_user_id,
            tool_slug=tool_slug,
            status_code=status_code,
            request_id_filter=monitor_request_id,
            start_time=_parse_datetime_query(start, "start"),
            end_time=_parse_datetime_query(end, "end"),
        )
        .order_by(GatewayRequest.created_at.desc())
        .limit(limit)
    ).all()
    return _request_rows_to_views(session, list(rows))


@router.get("/monitor/tools")
def monitor_tools(admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = session.execute(
        select(
            GatewayRequest.tool_slug,
            func.count(GatewayRequest.id),
            func.coalesce(func.avg(GatewayRequest.duration_ms), 0),
            func.coalesce(func.sum(case((GatewayRequest.status_code >= 400, 1), else_=0)), 0),
            func.max(GatewayRequest.created_at),
        )
        .group_by(GatewayRequest.tool_slug)
        .order_by(func.count(GatewayRequest.id).desc())
        .limit(100)
    ).all()
    return [
        {
            "tool_slug": row[0] or "",
            "total_requests": int(row[1] or 0),
            "avg_duration_ms": round(float(row[2] or 0), 2),
            "failed_requests": int(row[3] or 0),
            "last_called_at": row[4],
        }
        for row in rows
    ]


@router.get("/monitor/users")
def monitor_users(admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = session.execute(
        select(
            GatewayRequest.user_id,
            User.username,
            func.count(GatewayRequest.id),
            func.coalesce(func.avg(GatewayRequest.duration_ms), 0),
            func.coalesce(func.sum(case((GatewayRequest.status_code >= 400, 1), else_=0)), 0),
            func.max(GatewayRequest.created_at),
        )
        .join(User, User.id == GatewayRequest.user_id, isouter=True)
        .group_by(GatewayRequest.user_id, User.username)
        .order_by(func.count(GatewayRequest.id).desc())
        .limit(100)
    ).all()
    return [
        {
            "user_id": row[0],
            "username": row[1] or "",
            "total_requests": int(row[2] or 0),
            "avg_duration_ms": round(float(row[3] or 0), 2),
            "failed_requests": int(row[4] or 0),
            "last_called_at": row[5],
        }
        for row in rows
    ]


@router.get("/audit")
def list_audit(
    category: str | None = Query(default=None, max_length=32),
    action: str | None = Query(default=None, max_length=100),
    audit_request_id: str | None = Query(default=None, alias="request_id", max_length=64),
    target_type: str | None = Query(default=None, max_length=64),
    target_id: str | None = Query(default=None, max_length=128),
    admin_username: str | None = Query(default=None, max_length=64),
    start: str | None = Query(default=None, max_length=40),
    end: str | None = Query(default=None, max_length=40),
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> list[dict[str, object]] | dict[str, object]:
    conditions: list[object] = []
    if category:
        category_actions = AUDIT_ACTION_GROUPS.get(category)
        if not category_actions:
            raise HTTPException(status_code=422, detail="未知审计动作分组")
        conditions.append(AdminAuditEvent.action.in_(category_actions))
    if action:
        conditions.append(AdminAuditEvent.action == action.strip())
    if audit_request_id:
        conditions.append(AdminAuditEvent.request_id == audit_request_id.strip())
    if target_type:
        conditions.append(AdminAuditEvent.target_type == target_type.strip())
    if target_id:
        conditions.append(AdminAuditEvent.target_id == target_id.strip())
    start_time = _parse_datetime_query(start, "start")
    end_time = _parse_datetime_query(end, "end")
    if start_time and end_time and start_time > end_time:
        raise HTTPException(status_code=422, detail="开始时间不能晚于结束时间")
    if start_time:
        conditions.append(AdminAuditEvent.created_at >= start_time)
    if end_time:
        conditions.append(AdminAuditEvent.created_at <= end_time)
    if admin_username:
        actor_ids = session.scalars(
            select(User.id).where(func.lower(User.username).like(f"%{admin_username.strip().lower()}%"))
        ).all()
        conditions.append(AdminAuditEvent.admin_user_id.in_(actor_ids or ["__no_such_admin__"]))

    statement = select(AdminAuditEvent).where(*conditions).order_by(AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc())
    if page is None:
        events = session.scalars(statement.limit(100)).all()
    else:
        events = session.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()

    actor_ids = list({event.admin_user_id for event in events})
    actors = {
        actor.id: actor.username
        for actor in session.scalars(select(User).where(User.id.in_(actor_ids))).all()
    } if actor_ids else {}

    items: list[dict[str, object]] = []
    for event in events:
        try:
            parsed_detail = json.loads(event.detail_json)
            detail = parsed_detail if isinstance(parsed_detail, dict) else {"value": parsed_detail}
        except (json.JSONDecodeError, TypeError):
            detail = {"detail_unavailable": True}
        items.append({
            "id": event.id,
            "action": event.action,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "admin_user_id": event.admin_user_id,
            "admin_username": actors.get(event.admin_user_id, "未知账号"),
            "request_id": event.request_id,
            "created_at": event.created_at,
            "detail": detail,
        })

    if page is None:
        return items

    total = int(session.scalar(select(func.count(AdminAuditEvent.id)).where(*conditions)) or 0)
    available_actions = list(session.scalars(select(AdminAuditEvent.action).distinct().order_by(AdminAuditEvent.action)).all())
    available_target_types = list(session.scalars(select(AdminAuditEvent.target_type).distinct().order_by(AdminAuditEvent.target_type)).all())
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
        "available_actions": available_actions,
        "available_target_types": available_target_types,
    }


@router.get("/monitor/email-outbox")
def monitor_email_outbox(
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    """返回邮件队列健康状态，不返回正文、令牌或其它加密内容。"""

    count_rows = session.execute(
        select(EmailOutbox.status, func.count(EmailOutbox.id)).group_by(EmailOutbox.status)
    ).all()
    counts = {"pending": 0, "retry": 0, "failed": 0, "sent": 0}
    for status_value, count in count_rows:
        counts[str(status_value)] = int(count or 0)
    rows = session.scalars(
        select(EmailOutbox)
        .where(EmailOutbox.status.in_(["retry", "failed"]))
        .order_by(EmailOutbox.updated_at.desc(), EmailOutbox.id.desc())
        .limit(20)
    ).all()
    return {
        "smtp_configured": get_effective_platform_settings(session).smtp_configured,
        "counts": counts,
        "items": [
            {
                "id": item.id,
                "recipient": item.recipient,
                "subject": item.subject,
                "status": item.status,
                "attempts": item.attempts,
                "last_error": item.last_error,
                "next_attempt_at": item.next_attempt_at,
                "updated_at": item.updated_at,
            }
            for item in rows
        ],
    }
