"""分布式工具文件的登记、同步、配额和安全寻址。"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from jsonpath_ng import parse as parse_jsonpath
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    ExternalResource,
    FileSyncJob,
    Notification,
    PublishedRoute,
    RemoteFile,
    RemoteFileLink,
    RemoteFileStatus,
    ResourceKind,
    Tool,
    ToolIntegrationRoute,
    ToolStorageEndpointRevision,
    ToolUpstream,
    User,
)
from ..redaction import external_error_summary
from .security import decrypt_secret
from .user_lifecycle import add_notification, utc_now


FILE_ROLES = {"input", "intermediate", "output", "log", "archive"}
FILE_VISIBILITIES = {"user", "admin", "internal"}
FILE_STATES = {item.value for item in RemoteFileStatus}
TERMINAL_TASK_STATES = {"succeeded", "completed", "failed", "cancelled", "canceled"}
SAFE_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
SAFE_CONTENT_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+(?:\s*;\s*[A-Za-z0-9!#$&^_.+-]+=[A-Za-z0-9!#$&^_.+\-\"']+)*$")


def _json_object(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def sanitize_file_name(value: object) -> str:
    """只保留用于下载展示的文件名，绝不保留上游目录。"""
    raw = str(value or "file").replace("\\", "/")
    name = PurePath(raw).name
    cleaned = "".join(char for char in name if ord(char) >= 32 and ord(char) != 127).strip(" .")
    return (cleaned or "file")[:255]


def effective_user_quota(session: Session, user: User) -> int:
    if user.storage_quota_bytes > 0:
        return int(user.storage_quota_bytes)
    from .platform_settings import get_effective_platform_settings

    return int(get_effective_platform_settings(session).default_user_storage_quota_bytes)


def remote_file_usage(session: Session, user_id: str, tool_id: str | None = None) -> int:
    conditions: list[object] = [
        RemoteFile.owner_user_id == user_id,
        RemoteFile.status.notin_([RemoteFileStatus.DELETED.value, RemoteFileStatus.EXPIRED.value, RemoteFileStatus.MISSING.value]),
    ]
    if tool_id:
        conditions.append(RemoteFile.tool_id == tool_id)
    return int(session.scalar(select(func.coalesce(func.sum(RemoteFile.size_bytes), 0)).where(*conditions)) or 0)


def tool_remote_file_usage(session: Session, tool_id: str) -> int:
    return int(session.scalar(select(func.coalesce(func.sum(RemoteFile.size_bytes), 0)).where(RemoteFile.tool_id == tool_id, RemoteFile.status.notin_([RemoteFileStatus.DELETED.value, RemoteFileStatus.EXPIRED.value, RemoteFileStatus.MISSING.value]))) or 0)


def ensure_write_capacity(session: Session, user: User, tool: Tool, *, expected_bytes: int = 0) -> None:
    adapter = _json_object(tool.task_adapter_json)
    routes = adapter.get("routes") if isinstance(adapter.get("routes"), dict) else {}
    if adapter.get("version") == 2 and not any(name in routes for name in ("storage", "storage_watermark")) and not tool.storage_risk_acknowledged:
        raise HTTPException(status_code=503, detail={"code": "TOOL_STORAGE_RISK_UNCONFIRMED", "message": "工具未上报存储水位，管理员尚未确认写入风险"})
    used = remote_file_usage(session, user.id)
    user_quota = effective_user_quota(session, user)
    if used >= user_quota or used + max(0, expected_bytes) > user_quota:
        raise HTTPException(status_code=413, detail={"code": "STORAGE_QUOTA_EXCEEDED", "message": "文件配额已用尽，请先删除不再需要的文件"})
    if tool.storage_quota_bytes > 0:
        tool_used = tool_remote_file_usage(session, tool.id)
        if tool_used >= tool.storage_quota_bytes or tool_used + max(0, expected_bytes) > tool.storage_quota_bytes:
            raise HTTPException(status_code=413, detail={"code": "TOOL_STORAGE_QUOTA_EXCEEDED", "message": "当前工具文件配额已用尽"})
    if tool.file_quarantined:
        raise HTTPException(status_code=503, detail={"code": "TOOL_FILE_QUARANTINED", "message": "当前工具文件服务处于安全隔离状态"})
    checked_at = _aware(tool.storage_checked_at)
    if checked_at and checked_at < utc_now() - timedelta(seconds=get_settings().storage_watermark_max_age_seconds):
        raise HTTPException(status_code=503, detail={"code": "TOOL_STORAGE_STATUS_STALE", "message": "工具存储水位已过期，请等待后台同步恢复后再提交产文件任务"})
    if tool.storage_total_bytes and tool.storage_total_bytes > 0 and tool.storage_free_bytes is not None:
        if tool.storage_free_bytes / tool.storage_total_bytes < 0.05:
            raise HTTPException(status_code=503, detail={"code": "TOOL_STORAGE_READ_ONLY", "message": "工具服务器存储空间不足，当前只允许下载和删除"})


def _freeze_adapter(session: Session, tool: Tool) -> dict[str, Any]:
    """把可变路由引用展开为端点快照，历史任务不再依赖当前路由配置。"""
    adapter = _json_object(tool.task_adapter_json)
    routes = adapter.get("routes") if isinstance(adapter.get("routes"), dict) else {}
    frozen_routes: dict[str, object] = {}
    for capability, value in routes.items():
        if isinstance(value, dict):
            frozen_routes[str(capability)] = dict(value)
            continue
        if not isinstance(value, str):
            continue
        published = session.get(PublishedRoute, value)
        if published and published.tool_id == tool.id:
            frozen_routes[str(capability)] = {
                "endpoint_id": published.endpoint_id,
                "method": published.method,
                "path_template": published.upstream_path,
            }
            continue
        integration = session.get(ToolIntegrationRoute, value)
        if integration and integration.tool_id == tool.id:
            frozen_routes[str(capability)] = {
                "endpoint_id": integration.endpoint_id,
                "method": integration.method,
                "path_template": integration.path_template,
                **_json_object(integration.config_json),
            }
    if frozen_routes:
        adapter["routes"] = frozen_routes
    return adapter


def _endpoint_snapshot_matches(endpoint: ToolStorageEndpointRevision, upstream: ToolUpstream, adapter_json: str) -> bool:
    return all(
        (
            endpoint.base_url == upstream.base_url,
            endpoint.token_ciphertext == upstream.token_ciphertext,
            endpoint.auth_type == upstream.auth_type,
            endpoint.token_label == upstream.token_label,
            endpoint.verify_tls == upstream.verify_tls,
            endpoint.connect_timeout_seconds == upstream.connect_timeout_seconds,
            endpoint.request_timeout_seconds == upstream.request_timeout_seconds,
            endpoint.adapter_json == adapter_json,
        )
    )


def ensure_storage_endpoint(session: Session, tool: Tool, upstream: ToolUpstream) -> ToolStorageEndpointRevision:
    adapter_json = json.dumps(_freeze_adapter(session, tool), ensure_ascii=False, separators=(",", ":"))
    current = session.scalar(
        select(ToolStorageEndpointRevision)
        .where(ToolStorageEndpointRevision.tool_id == tool.id, ToolStorageEndpointRevision.is_active.is_(True))
        .order_by(ToolStorageEndpointRevision.revision.desc())
        .limit(1)
    )
    if current and _endpoint_snapshot_matches(current, upstream, adapter_json):
        return current
    if current:
        current.is_active = False
        current.retired_at = utc_now()
    revision = int(session.scalar(select(func.max(ToolStorageEndpointRevision.revision)).where(ToolStorageEndpointRevision.tool_id == tool.id)) or 0) + 1
    endpoint = ToolStorageEndpointRevision(
        tool_id=tool.id,
        revision=revision,
        base_url=upstream.base_url,
        token_ciphertext=upstream.token_ciphertext,
        auth_type=upstream.auth_type,
        token_label=upstream.token_label,
        verify_tls=upstream.verify_tls,
        connect_timeout_seconds=upstream.connect_timeout_seconds,
        request_timeout_seconds=upstream.request_timeout_seconds,
        adapter_json=adapter_json,
        is_active=True,
    )
    session.add(endpoint)
    session.flush()
    return endpoint


def bind_resource_storage_endpoint(session: Session, resource: ExternalResource) -> ToolStorageEndpointRevision:
    """任务一旦绑定端点版本就不再随工具服务器切换。"""
    if resource.storage_endpoint_revision_id:
        endpoint = session.get(ToolStorageEndpointRevision, resource.storage_endpoint_revision_id)
        if endpoint and endpoint.tool_id == resource.tool_id:
            return endpoint
        raise HTTPException(status_code=503, detail="任务绑定的历史存储端点不可用")
    tool = session.get(Tool, resource.tool_id)
    upstream = session.scalar(select(ToolUpstream).where(ToolUpstream.tool_id == resource.tool_id))
    if not tool or not upstream:
        raise HTTPException(status_code=503, detail="任务所属工具上游配置不可用")
    endpoint = ensure_storage_endpoint(session, tool, upstream)
    resource.storage_endpoint_revision_id = endpoint.id
    return endpoint


def queue_resource_sync(session: Session, resource: ExternalResource, *, delay_seconds: int = 0) -> FileSyncJob:
    if resource.storage_endpoint_revision_id or session.scalar(select(ToolUpstream.id).where(ToolUpstream.tool_id == resource.tool_id)):
        bind_resource_storage_endpoint(session, resource)
    target_key = f"poll:{resource.id}"
    job = session.scalar(select(FileSyncJob).where(FileSyncJob.target_key == target_key))
    due = utc_now() + timedelta(seconds=max(0, delay_seconds))
    if job:
        if job.status != "running":
            job.status = "pending"
            job.next_attempt_at = min(_aware(job.next_attempt_at) or due, due)
        return job
    job = FileSyncJob(target_key=target_key, resource_id=resource.id, job_type="poll", status="pending", next_attempt_at=due)
    session.add(job)
    return job


def queue_file_verify(session: Session, item: RemoteFile, *, delay_seconds: int | None = None) -> FileSyncJob:
    target_key = f"verify:{item.id}"
    job = session.scalar(select(FileSyncJob).where(FileSyncJob.target_key == target_key))
    delay = get_settings().file_verify_interval_seconds if delay_seconds is None else max(0, delay_seconds)
    due = utc_now() + timedelta(seconds=delay)
    if job:
        if job.status != "running":
            job.status = "pending"
            job.next_attempt_at = min(_aware(job.next_attempt_at) or due, due)
        return job
    job = FileSyncJob(target_key=target_key, remote_file_id=item.id, job_type="verify", status="pending", next_attempt_at=due)
    session.add(job)
    return job


def queue_file_delete(session: Session, item: RemoteFile) -> FileSyncJob:
    target_key = f"delete:{item.id}"
    job = session.scalar(select(FileSyncJob).where(FileSyncJob.target_key == target_key))
    if job:
        job.status = "pending"
        job.next_attempt_at = utc_now()
        return job
    job = FileSyncJob(target_key=target_key, remote_file_id=item.id, job_type="delete", status="pending", next_attempt_at=utc_now())
    session.add(job)
    return job


def _first(payload: object, selector: str, default: object = None) -> object:
    try:
        matches = parse_jsonpath(selector).find(payload)
    except Exception:
        return default
    return matches[0].value if matches else default


def _items(payload: object, selector: str) -> list[dict[str, Any]]:
    value = _first(payload, selector, payload if isinstance(payload, list) else [])
    if isinstance(value, dict):
        value = value.get("items") or value.get("artifacts") or value.get("files") or []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _manifest_mapping(adapter: dict[str, Any], *, response: bool = False) -> dict[str, str]:
    raw = adapter.get("response_files" if response else "artifacts")
    config = raw if isinstance(raw, dict) else {}
    return {
        "items": str(config.get("items_selector") or ("$.platform_files" if response else "$.artifacts")),
        "id": str(config.get("id_selector") or "$.file_id"),
        "name": str(config.get("name_selector") or "$.name"),
        "role": str(config.get("role_selector") or "$.role"),
        "visibility": str(config.get("visibility_selector") or "$.visibility"),
        "status": str(config.get("status_selector") or "$.status"),
        "size": str(config.get("size_selector") or "$.size_bytes"),
        "content_type": str(config.get("content_type_selector") or "$.content_type"),
        "sha256": str(config.get("sha256_selector") or "$.sha256"),
        "created_at": str(config.get("created_at_selector") or "$.created_at"),
        "expires_at": str(config.get("expires_at_selector") or "$.expires_at"),
        "default_role": str(config.get("default_role") or "intermediate"),
        "default_visibility": str(config.get("default_visibility") or "internal"),
        "default_status": str(config.get("default_status") or "ready"),
    }


def _identity(tool_id: str, parent_id: str | None, upstream_file_id: str) -> str:
    return hashlib.sha256(f"{tool_id}:{parent_id or ''}:{upstream_file_id}".encode("utf-8")).hexdigest()


def _retention_expiry(session: Session, tool: Tool, upstream_expiry: datetime | None) -> datetime:
    from .platform_settings import get_effective_platform_settings

    days = tool.file_retention_days or get_effective_platform_settings(session).default_file_retention_days
    policy_expiry = utc_now() + timedelta(days=days)
    value = _aware(upstream_expiry)
    return min(policy_expiry, value) if value else policy_expiry


def upsert_manifest_file(
    session: Session,
    *,
    tool: Tool,
    owner_user_id: str,
    endpoint: ToolStorageEndpointRevision,
    raw: dict[str, Any],
    mapping: dict[str, str],
    parent_resource: ExternalResource | None,
    request_id: str | None,
    is_bundle: bool = False,
) -> tuple[RemoteFile, bool]:
    upstream_id = str(_first(raw, mapping["id"], "") or "").strip()
    if not upstream_id or len(upstream_id) > 255 or any(ord(char) < 32 for char in upstream_id):
        raise HTTPException(status_code=502, detail={"code": "FILE_MANIFEST_INVALID", "message": "文件清单缺少稳定文件 ID"})
    identity = _identity(tool.id, parent_resource.id if parent_resource else None, upstream_id)
    existing = session.scalar(select(RemoteFile).where(RemoteFile.identity_key == identity))
    if existing and existing.owner_user_id != owner_user_id:
        existing.status = RemoteFileStatus.QUARANTINED.value
        existing.last_error = "上游文件身份与既有所有者冲突"
        raise HTTPException(status_code=502, detail={"code": "FILE_OWNERSHIP_CONFLICT", "message": "上游文件归属冲突"})
    size_value = _first(raw, mapping["size"])
    try:
        size_bytes = int(size_value) if size_value is not None else None
    except (TypeError, ValueError):
        size_bytes = None
    if size_bytes is not None and size_bytes < 0:
        size_bytes = None
    name = sanitize_file_name(_first(raw, mapping["name"], upstream_id))
    role = str(_first(raw, mapping["role"], "archive" if is_bundle else mapping["default_role"]) or mapping["default_role"]).lower()
    visibility = str(_first(raw, mapping["visibility"], mapping["default_visibility"]) or mapping["default_visibility"]).lower()
    upstream_status = str(_first(raw, mapping["status"], mapping["default_status"]) or mapping["default_status"]).lower()
    content_type = str(_first(raw, mapping["content_type"], "application/octet-stream") or "application/octet-stream")[:255]
    if not SAFE_CONTENT_TYPE.fullmatch(content_type):
        content_type = "application/octet-stream"
    checksum = str(_first(raw, mapping["sha256"], "") or "").lower()
    if checksum and not SAFE_SHA256.fullmatch(checksum):
        checksum = ""
    role = role if role in FILE_ROLES else "intermediate"
    visibility = visibility if visibility in FILE_VISIBILITIES else "internal"
    normalized_status = upstream_status if upstream_status in FILE_STATES else "ready"
    if normalized_status == "ready" and size_bytes is None:
        normalized_status = "pending"
    max_bytes = tool.max_file_bytes or get_settings().max_remote_file_bytes
    effective_max_bytes = min(max_bytes, get_settings().max_remote_file_bytes)
    if size_bytes is not None and size_bytes > effective_max_bytes:
        normalized_status = "error"
    created_at = _parse_datetime(_first(raw, mapping["created_at"]))
    expires_at = _retention_expiry(session, tool, _parse_datetime(_first(raw, mapping["expires_at"])))
    created = existing is None
    item = existing or RemoteFile(
        identity_key=identity,
        tool_id=tool.id,
        owner_user_id=owner_user_id,
        storage_endpoint_revision_id=endpoint.id,
        parent_resource_id=parent_resource.id if parent_resource else None,
        source_gateway_request_id=request_id,
        upstream_file_id=upstream_id,
    )
    item.file_name = name
    item.role = role
    item.visibility = visibility
    item.status = normalized_status
    item.content_type = content_type
    item.size_bytes = size_bytes
    item.sha256 = checksum
    item.is_bundle = is_bundle
    item.upstream_created_at = created_at
    item.expires_at = expires_at
    # 同一上游文件重新出现时清除旧删除墓碑，避免后续清理任务再次擦除有效元数据。
    item.deleted_at = None
    item.last_verified_at = utc_now()
    item.last_error = "文件超过工具或平台单文件上限" if normalized_status == "error" and size_bytes and size_bytes > effective_max_bytes else ""
    item.metadata_json = json.dumps({"schema_version": 1}, separators=(",", ":"))
    if created:
        session.add(item)
        session.flush()
    if item.status in {RemoteFileStatus.PENDING.value, RemoteFileStatus.READY.value}:
        queue_file_verify(session, item)
    if parent_resource or request_id:
        link = session.scalar(
            select(RemoteFileLink).where(
                RemoteFileLink.remote_file_id == item.id,
                RemoteFileLink.resource_id == (parent_resource.id if parent_resource else None),
                RemoteFileLink.gateway_request_id == request_id,
                RemoteFileLink.relation == "produced",
            )
        )
        if not link:
            session.add(RemoteFileLink(remote_file_id=item.id, resource_id=parent_resource.id if parent_resource else None, gateway_request_id=request_id, relation="produced"))
    return item, created


def capture_response_files(session: Session, *, tool: Tool, owner_user_id: str, payload: object, request_id: str | None = None) -> list[RemoteFile]:
    adapter = _json_object(tool.task_adapter_json)
    if adapter.get("version") != 2:
        return []
    mapping = _manifest_mapping(adapter, response=True)
    raw_items = _items(payload, mapping["items"])
    if not raw_items:
        return []
    if len(raw_items) > get_settings().file_sync_manifest_max_items:
        raise HTTPException(status_code=502, detail={"code": "FILE_MANIFEST_TOO_LARGE", "message": "文件清单项目数超过平台限制"})
    manifest_bytes = len(json.dumps(raw_items, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if manifest_bytes > get_settings().file_sync_manifest_max_bytes:
        raise HTTPException(status_code=502, detail={"code": "FILE_MANIFEST_TOO_LARGE", "message": "文件清单超过平台限制"})
    upstream = session.scalar(select(ToolUpstream).where(ToolUpstream.tool_id == tool.id))
    if not upstream:
        raise HTTPException(status_code=503, detail="工具上游配置不可用")
    endpoint = ensure_storage_endpoint(session, tool, upstream)
    task_selector = str(adapter.get("task_id_selector") or "$.job_id")
    task_upstream_id = str(_first(payload, task_selector, "") or "")
    parent = session.scalar(select(ExternalResource).where(ExternalResource.tool_id == tool.id, ExternalResource.owner_user_id == owner_user_id, ExternalResource.kind == ResourceKind.JOB.value, ExternalResource.upstream_id == task_upstream_id)) if task_upstream_id else None
    result: list[RemoteFile] = []
    for raw in raw_items:
        item, _ = upsert_manifest_file(session, tool=tool, owner_user_id=owner_user_id, endpoint=endpoint, raw=raw, mapping=mapping, parent_resource=parent, request_id=request_id)
        result.append(item)
    return result


def link_files_to_request(session: Session, items: list[RemoteFile], request_id: str) -> None:
    for item in items:
        if not item.source_gateway_request_id:
            item.source_gateway_request_id = request_id
        exists = session.scalar(select(RemoteFileLink.id).where(RemoteFileLink.remote_file_id == item.id, RemoteFileLink.gateway_request_id == request_id, RemoteFileLink.relation == "produced"))
        if not exists:
            session.add(RemoteFileLink(remote_file_id=item.id, resource_id=item.parent_resource_id, gateway_request_id=request_id, relation="produced"))


def _headers(endpoint: ToolStorageEndpointRevision, request_id: str, *, accept: str = "application/json") -> dict[str, str]:
    headers = {"accept": accept, "x-request-id": request_id}
    token = decrypt_secret(endpoint.token_ciphertext)
    if token and endpoint.auth_type == "bearer":
        headers["authorization"] = f"Bearer {token}"
    elif token and endpoint.auth_type == "header_api_key":
        headers[endpoint.token_label or "x-api-key"] = token
    return headers


def storage_headers(endpoint: ToolStorageEndpointRevision, request_id: str, *, accept: str = "application/json") -> dict[str, str]:
    return _headers(endpoint, request_id, accept=accept)


def resolve_capability(session: Session, tool: Tool, endpoint: ToolStorageEndpointRevision, capability: str) -> tuple[str, str, dict[str, Any]] | None:
    adapter = _json_object(endpoint.adapter_json) or _json_object(tool.task_adapter_json)
    routes = adapter.get("routes") if isinstance(adapter.get("routes"), dict) else {}
    route_value = routes.get(capability) if isinstance(routes, dict) else None
    if isinstance(route_value, dict):
        path = str(route_value.get("path_template") or "")
        if path:
            return str(route_value.get("method") or "GET").upper(), path, route_value
    if isinstance(route_value, str):
        published = session.get(PublishedRoute, route_value)
        if published and published.tool_id == tool.id:
            return published.method.upper(), published.upstream_path, {}
        integration = session.get(ToolIntegrationRoute, route_value)
        if integration and integration.tool_id == tool.id:
            return integration.method.upper(), integration.path_template, _json_object(integration.config_json)
    if endpoint.is_active:
        route = session.scalar(select(ToolIntegrationRoute).where(ToolIntegrationRoute.tool_id == tool.id, ToolIntegrationRoute.capability == capability, ToolIntegrationRoute.is_enabled.is_(True)))
        if route:
            return route.method.upper(), route.path_template, _json_object(route.config_json)
    return None


def render_capability_path(template: str, *, job_id: str | None = None, file_id: str | None = None, id_param: str = "job_id", file_id_param: str = "file_id") -> str:
    rendered = template
    replacements = {
        "{" + id_param + "}": quote(job_id or "", safe=""),
        "{" + file_id_param + "}": quote(file_id or "", safe=""),
        "{job_id}": quote(job_id or "", safe=""),
        "{task_id}": quote(job_id or "", safe=""),
        "{file_id}": quote(file_id or "", safe=""),
        "{artifact_id}": quote(file_id or "", safe=""),
    }
    for marker, value in replacements.items():
        rendered = rendered.replace(marker, value)
    if not rendered.startswith("/") or "{" in rendered or "}" in rendered or "?" in rendered or "#" in rendered or any(part == ".." for part in rendered.split("/")):
        raise HTTPException(status_code=503, detail={"code": "FILE_ADAPTER_INVALID", "message": "文件适配器路径不完整"})
    return rendered


async def _fetch_json(endpoint: ToolStorageEndpointRevision, method: str, path: str, request_id: str) -> object:
    timeout = httpx.Timeout(endpoint.request_timeout_seconds, connect=endpoint.connect_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, verify=endpoint.verify_tls) as client:
            response = await client.request(method, f"{endpoint.base_url}{path}", headers=_headers(endpoint, request_id))
    except httpx.ConnectError as error:
        raise HTTPException(status_code=502, detail="工具文件服务不可达") from error
    except httpx.TimeoutException as error:
        raise HTTPException(status_code=504, detail="工具文件服务响应超时") from error
    if 300 <= response.status_code < 400:
        raise HTTPException(status_code=502, detail="工具文件服务返回了不允许的重定向")
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail={"message": "工具文件服务请求失败", "details": {"upstream_status": response.status_code}})
    if len(response.content) > get_settings().file_sync_manifest_max_bytes:
        raise HTTPException(status_code=502, detail={"code": "FILE_MANIFEST_TOO_LARGE", "message": "文件清单超过平台限制"})
    try:
        return response.json()
    except ValueError as error:
        raise HTTPException(status_code=502, detail="工具文件服务返回了无效 JSON") from error


def task_storage_context(session: Session, resource: ExternalResource) -> tuple[Tool, ToolStorageEndpointRevision, dict[str, Any]]:
    tool = session.get(Tool, resource.tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="任务所属工具不存在")
    endpoint = bind_resource_storage_endpoint(session, resource)
    adapter = _json_object(endpoint.adapter_json) or _json_object(tool.task_adapter_json)
    return tool, endpoint, adapter


def task_capabilities(session: Session, resource: ExternalResource) -> dict[str, bool]:
    tool, endpoint, _ = task_storage_context(session, resource)
    result = {name: bool(resolve_capability(session, tool, endpoint, name)) for name in ("status", "logs", "manifest", "artifacts")}
    result["download"] = bool(resolve_capability(session, tool, endpoint, "bundle_download") or resolve_capability(session, tool, endpoint, "download"))
    result["files"] = bool(result["artifacts"] or result["manifest"] or result["download"])
    return result


async def fetch_task_capability_json(session: Session, resource: ExternalResource, capability: str, *, request_id: str) -> object:
    if capability not in {"status", "logs", "manifest", "artifacts"}:
        raise HTTPException(status_code=404, detail="当前工具未配置该任务能力")
    tool, endpoint, adapter = task_storage_context(session, resource)
    resolved = resolve_capability(session, tool, endpoint, capability)
    if not resolved:
        raise HTTPException(status_code=404, detail="当前工具未配置该任务能力")
    method, template, _ = resolved
    path = render_capability_path(template, job_id=resource.upstream_id, id_param=str(adapter.get("id_param") or "job_id"))
    return await _fetch_json(endpoint, method, path, request_id)


def _metadata_size(response: httpx.Response) -> int | None:
    content_range = response.headers.get("content-range", "")
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))
    content_length = response.headers.get("content-length")
    try:
        return int(content_length) if content_length is not None else None
    except ValueError:
        return None


async def _fetch_file_metadata(endpoint: ToolStorageEndpointRevision, path: str, request_id: str) -> tuple[int | None, str] | None:
    timeout = httpx.Timeout(endpoint.request_timeout_seconds, connect=endpoint.connect_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, verify=endpoint.verify_tls) as client:
        response = await client.send(client.build_request("HEAD", f"{endpoint.base_url}{path}", headers=_headers(endpoint, request_id, accept="application/octet-stream")), stream=True)
        if response.status_code in {405, 501}:
            await response.aclose()
            headers = _headers(endpoint, request_id, accept="application/octet-stream")
            headers["range"] = "bytes=0-0"
            response = await client.send(client.build_request("GET", f"{endpoint.base_url}{path}", headers=headers), stream=True)
        try:
            if response.status_code == 404:
                return None
            if 300 <= response.status_code < 400:
                raise HTTPException(status_code=502, detail="工具文件服务返回了不允许的重定向")
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail={"message": "工具文件元信息查询失败", "details": {"upstream_status": response.status_code}})
            content_type = response.headers.get("content-type", "application/octet-stream")[:255]
            if not SAFE_CONTENT_TYPE.fullmatch(content_type):
                content_type = "application/octet-stream"
            return _metadata_size(response), content_type
        finally:
            await response.aclose()


async def verify_remote_file(session: Session, item: RemoteFile, *, request_id: str) -> None:
    if item.status in {RemoteFileStatus.DELETED.value, RemoteFileStatus.EXPIRED.value, RemoteFileStatus.DELETE_PENDING.value}:
        return
    tool = session.get(Tool, item.tool_id)
    endpoint = session.get(ToolStorageEndpointRevision, item.storage_endpoint_revision_id) if item.storage_endpoint_revision_id else None
    parent = session.get(ExternalResource, item.parent_resource_id) if item.parent_resource_id else None
    if not tool or not endpoint:
        raise HTTPException(status_code=503, detail="文件存储端点不可用")
    capability = "bundle_download" if item.is_bundle else "file_download"
    resolved = resolve_capability(session, tool, endpoint, capability)
    if not resolved and item.is_bundle:
        resolved = resolve_capability(session, tool, endpoint, "download")
    if not resolved:
        raise HTTPException(status_code=409, detail={"code": "FILE_DOWNLOAD_UNSUPPORTED", "message": "当前工具未配置对应下载能力"})
    _, template, config = resolved
    adapter = _json_object(endpoint.adapter_json)
    path = render_capability_path(
        template,
        job_id=parent.upstream_id if parent else None,
        file_id=item.upstream_file_id,
        id_param=str(adapter.get("id_param") or "job_id"),
        file_id_param=str(config.get("file_id_param") or adapter.get("file_id_param") or "file_id"),
    )
    metadata = await _fetch_file_metadata(endpoint, path, request_id)
    item.last_verified_at = utc_now()
    if metadata is None:
        item.status = RemoteFileStatus.MISSING.value
        item.last_error = "上游文件不存在"
        return
    size_bytes, content_type = metadata
    item.content_type = content_type
    if size_bytes is None:
        item.status = RemoteFileStatus.PENDING.value
        item.last_error = "工具文件接口未返回可验证的文件大小"
        return
    item.size_bytes = size_bytes
    limit = min(tool.max_file_bytes or get_settings().max_remote_file_bytes, get_settings().max_remote_file_bytes)
    if size_bytes > limit:
        item.status = RemoteFileStatus.ERROR.value
        item.last_error = "文件超过工具或平台单文件上限"
        return
    item.status = RemoteFileStatus.READY.value
    item.last_error = ""


async def sync_task_bundle(session: Session, resource: ExternalResource, *, request_id: str) -> tuple[RemoteFile | None, bool]:
    tool, endpoint, adapter = task_storage_context(session, resource)
    if not (resolve_capability(session, tool, endpoint, "bundle_download") or resolve_capability(session, tool, endpoint, "download")):
        return None, False
    mapping = _manifest_mapping({"artifacts": {"id_selector": "$.id", "name_selector": "$.name", "size_selector": "$.size_bytes", "role_selector": "$.role", "visibility_selector": "$.visibility", "status_selector": "$.status", "content_type_selector": "$.content_type", "default_role": "archive", "default_visibility": "user"}})
    raw = {
        "id": f"task-bundle:{resource.upstream_id}",
        "name": str(adapter.get("bundle_file_name") or f"{tool.slug}-{resource.id}.zip"),
        "size_bytes": None,
        "role": "archive",
        "visibility": "user",
        "status": "pending",
        "content_type": "application/zip",
    }
    item, created = upsert_manifest_file(session, tool=tool, owner_user_id=resource.owner_user_id, endpoint=endpoint, raw=raw, mapping=mapping, parent_resource=resource, request_id=resource.source_request_id, is_bundle=True)
    await verify_remote_file(session, item, request_id=request_id)
    return item, created


async def sync_resource(session: Session, resource: ExternalResource, *, request_id: str) -> bool:
    tool, endpoint, adapter = task_storage_context(session, resource)
    owner = session.get(User, resource.owner_user_id)
    if not owner:
        raise HTTPException(status_code=503, detail="任务同步上下文不完整")
    id_param = str(adapter.get("id_param") or "job_id")
    terminal = {str(item).lower() for item in adapter.get("terminal_statuses", TERMINAL_TASK_STATES) if isinstance(item, str)}
    status_route = resolve_capability(session, tool, endpoint, "status")
    if status_route:
        method, template, _ = status_route
        payload = await _fetch_json(endpoint, method, render_capability_path(template, job_id=resource.upstream_id, id_param=id_param), request_id)
        status = _first(payload, str(adapter.get("status_selector") or "$.status"))
        if isinstance(status, str) and status:
            resource.status = status[:32]
            resource.updated_at = utc_now()
    artifacts_route = resolve_capability(session, tool, endpoint, "artifacts") or resolve_capability(session, tool, endpoint, "manifest")
    ready_created = False
    if artifacts_route:
        method, template, route_config = artifacts_route
        payload = await _fetch_json(endpoint, method, render_capability_path(template, job_id=resource.upstream_id, id_param=id_param), request_id)
        mapping = _manifest_mapping({**adapter, "artifacts": {**(adapter.get("artifacts") if isinstance(adapter.get("artifacts"), dict) else {}), **route_config}})
        raw_items = _items(payload, mapping["items"])
        if len(raw_items) > get_settings().file_sync_manifest_max_items:
            raise HTTPException(status_code=502, detail={"code": "FILE_MANIFEST_TOO_LARGE", "message": "文件清单项目数超过平台限制"})
        for raw in raw_items:
            item, created = upsert_manifest_file(session, tool=tool, owner_user_id=resource.owner_user_id, endpoint=endpoint, raw=raw, mapping=mapping, parent_resource=resource, request_id=None)
            ready_created = ready_created or (created and item.status == "ready" and item.visibility == "user")
    storage_route = resolve_capability(session, tool, endpoint, "storage_watermark") or resolve_capability(session, tool, endpoint, "storage")
    if storage_route:
        method, template, config = storage_route
        payload = await _fetch_json(endpoint, method, render_capability_path(template, job_id=resource.upstream_id, id_param=id_param), request_id)
        total = _first(payload, str(config.get("total_bytes_selector") or "$.total_bytes"))
        free = _first(payload, str(config.get("free_bytes_selector") or "$.free_bytes"))
        try:
            total_bytes, free_bytes = int(total), int(free)
        except (TypeError, ValueError):
            total_bytes, free_bytes = -1, -1
        if total_bytes > 0 and 0 <= free_bytes <= total_bytes:
            tool.storage_total_bytes = total_bytes
            tool.storage_free_bytes = free_bytes
            tool.storage_checked_at = utc_now()
    is_terminal = resource.status.lower() in terminal
    if is_terminal:
        bundle, bundle_created = await sync_task_bundle(session, resource, request_id=request_id)
        ready_created = ready_created or bool(bundle and bundle_created and bundle.status == RemoteFileStatus.READY.value and bundle.visibility == "user")
    if ready_created:
        action_url = f"/tasks/{resource.id}?tab=files"
        exists = session.scalar(select(Notification.id).where(Notification.user_id == resource.owner_user_id, Notification.kind == "files_ready", Notification.action_url == action_url))
        if not exists:
            add_notification(session, user_id=resource.owner_user_id, kind="files_ready", title="任务文件已可下载", body=f"{tool.name} 的任务结果已经同步完成。", action_url=action_url)
    return is_terminal


async def delete_remote_file(session: Session, item: RemoteFile, *, request_id: str) -> None:
    tool = session.get(Tool, item.tool_id)
    endpoint = session.get(ToolStorageEndpointRevision, item.storage_endpoint_revision_id)
    parent = session.get(ExternalResource, item.parent_resource_id) if item.parent_resource_id else None
    if not tool or not endpoint:
        raise HTTPException(status_code=503, detail="文件存储端点不可用")
    capability = "bundle_delete" if item.is_bundle else "file_delete"
    route = resolve_capability(session, tool, endpoint, capability) or resolve_capability(session, tool, endpoint, "delete")
    if not route:
        raise HTTPException(status_code=409, detail={"code": "FILE_DELETE_UNSUPPORTED", "message": "当前工具未配置文件删除能力"})
    method, template, config = route
    adapter = _json_object(endpoint.adapter_json)
    path = render_capability_path(
        template,
        job_id=parent.upstream_id if parent else None,
        file_id=item.upstream_file_id,
        id_param=str(adapter.get("id_param") or "job_id"),
        file_id_param=str(config.get("file_id_param") or adapter.get("file_id_param") or "file_id"),
    )
    timeout = httpx.Timeout(endpoint.request_timeout_seconds, connect=endpoint.connect_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, verify=endpoint.verify_tls) as client:
            response = await client.request(method, f"{endpoint.base_url}{path}", headers=_headers(endpoint, request_id))
    except httpx.ConnectError as error:
        raise HTTPException(status_code=502, detail="工具文件服务不可达") from error
    except httpx.TimeoutException as error:
        raise HTTPException(status_code=504, detail="工具文件删除超时") from error
    if 300 <= response.status_code < 400:
        raise HTTPException(status_code=502, detail="工具文件服务返回了不允许的重定向")
    if response.status_code not in {200, 202, 204, 404}:
        raise HTTPException(status_code=response.status_code, detail={"message": "工具文件删除失败", "details": {"upstream_status": response.status_code}})
    item.status = RemoteFileStatus.DELETED.value
    item.deleted_at = utc_now()
    item.last_error = ""


def expire_remote_files(session: Session) -> int:
    now = utc_now()
    rows = session.scalars(select(RemoteFile).where(RemoteFile.expires_at <= now, RemoteFile.status.in_(["pending", "ready", "error", "missing"]))).all()
    for item in rows:
        item.status = RemoteFileStatus.DELETE_PENDING.value
        queue_file_delete(session, item)
    tombstone_cutoff = now - timedelta(days=90)
    tombstones = session.scalars(select(RemoteFile).where(RemoteFile.deleted_at <= tombstone_cutoff, RemoteFile.status.in_(["deleted", "expired"]))).all()
    for item in tombstones:
        item.file_name = "已清理文件"
        item.content_type = "application/octet-stream"
        item.sha256 = ""
        item.metadata_json = "{}"
        item.upstream_file_id = f"purged:{item.id}"
        item.storage_endpoint_revision_id = None
        item.parent_resource_id = None
        item.source_gateway_request_id = None
        session.execute(delete(RemoteFileLink).where(RemoteFileLink.remote_file_id == item.id))
        session.execute(delete(FileSyncJob).where(FileSyncJob.remote_file_id == item.id, FileSyncJob.job_type == "verify"))
    detachable_resources = session.scalars(
        select(ExternalResource).where(
            ExternalResource.storage_endpoint_revision_id.is_not(None),
            ExternalResource.kind == ResourceKind.JOB.value,
            ExternalResource.status.in_(TERMINAL_TASK_STATES),
            ExternalResource.updated_at <= tombstone_cutoff,
            ~select(RemoteFile.id).where(RemoteFile.parent_resource_id == ExternalResource.id).exists(),
        )
    ).all()
    for resource in detachable_resources:
        resource.storage_endpoint_revision_id = None
        session.execute(delete(FileSyncJob).where(FileSyncJob.resource_id == resource.id))
    return len(rows)


def claim_sync_jobs(session: Session, *, limit: int = 20) -> tuple[str, list[FileSyncJob]]:
    token = secrets.token_urlsafe(24)
    now = utc_now()
    stale = now - timedelta(minutes=5)
    claimable = or_(
        FileSyncJob.status == "pending",
        FileSyncJob.status == "retry",
        (FileSyncJob.status == "running") & (FileSyncJob.lease_expires_at < now),
        (FileSyncJob.status == "running") & (FileSyncJob.updated_at < stale),
    )
    statement = (
        select(FileSyncJob)
        .where(
            FileSyncJob.next_attempt_at <= now,
            claimable,
        )
        .order_by(FileSyncJob.next_attempt_at, FileSyncJob.created_at)
        .limit(limit)
    )
    if session.get_bind().dialect.name != "sqlite":
        statement = statement.with_for_update(skip_locked=True)
    rows = session.scalars(statement).all()
    claimed: list[FileSyncJob] = []
    for row in rows:
        # 条件更新让第二个并发 Worker 看见已写入的租约，不能重复执行同一任务。
        result = session.execute(
            update(FileSyncJob)
            .where(
                FileSyncJob.id == row.id,
                FileSyncJob.next_attempt_at <= now,
                claimable,
            )
            .values(
                status="running",
                lease_token=token,
                lease_expires_at=now + timedelta(minutes=5),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:
            session.expire(row)
            claimed.append(row)
    session.commit()
    return token, claimed


def complete_sync_job(session: Session, job: FileSyncJob, *, terminal: bool = False) -> None:
    job.attempts = 0
    job.last_error = ""
    job.lease_token = ""
    job.lease_expires_at = None
    if job.job_type == "delete":
        job.status = "complete"
        job.next_attempt_at = utc_now()
    elif job.job_type == "verify" or terminal:
        job.status = "pending"
        job.next_attempt_at = utc_now() + timedelta(seconds=get_settings().file_verify_interval_seconds)
    else:
        job.status = "pending"
        job.next_attempt_at = utc_now() + timedelta(seconds=get_settings().file_sync_poll_seconds)


def fail_sync_job(job: FileSyncJob, error: Exception) -> None:
    job.attempts += 1
    delay = min(900, 30 * (2 ** min(job.attempts - 1, 5)))
    job.status = "retry"
    job.next_attempt_at = utc_now() + timedelta(seconds=delay)
    job.lease_token = ""
    job.lease_expires_at = None
    job.last_error = external_error_summary(error, category="FILE_SYNC_FAILED")


def file_is_downloadable(item: RemoteFile, tool: Tool, *, admin: bool) -> bool:
    if item.status != RemoteFileStatus.READY.value or tool.file_quarantined or not tool.file_access_enabled:
        return False
    expires = _aware(item.expires_at)
    if expires and expires <= utc_now():
        return False
    return admin or item.visibility == "user"


def file_is_deletable(item: RemoteFile, *, admin: bool) -> bool:
    return item.status in {"pending", "ready", "error", "missing"} and (admin or item.visibility == "user")


def file_delete_supported(session: Session, item: RemoteFile) -> bool:
    tool = session.get(Tool, item.tool_id)
    endpoint = session.get(ToolStorageEndpointRevision, item.storage_endpoint_revision_id)
    if not tool or not endpoint:
        return False
    capability = "bundle_delete" if item.is_bundle else "file_delete"
    return bool(resolve_capability(session, tool, endpoint, capability) or resolve_capability(session, tool, endpoint, "delete"))
