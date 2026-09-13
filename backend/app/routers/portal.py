"""用户门户数据接口，仅返回当前用户拥有的审计与任务资源。"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import AdminAuditEvent, ApiEndpoint, ApiKey, EndpointStatus, ExternalResource, GatewayRequest, KeyStatus, Notification, PublishedRoute, ResourceKind, Tool, ToolIntegrationRoute, ToolStorageEndpointRevision, ToolUpstream, User
from ..schemas import EndpointView, GatewayCallPage, GatewayCallView, NotificationPage, NotificationView, TaskDetailView, TaskPage, TaskView, UserEventPage, UserEventView
from ..services.call_history import gateway_call_view, gateway_call_views
from ..services.gateway_proxy import (
    api_key_is_expired,
    gateway_error_payload,
    safe_download_disposition,
    upstream_headers,
)
from ..services.platform_settings import get_effective_platform_settings
from ..services.remote_files import fetch_task_capability_json, task_capabilities, task_storage_context
from ..services.user_lifecycle import add_notification, utc_now
from .deps import get_user_roles, request_id, require_not_password_change, validate_csrf


router = APIRouter(prefix="/api/me", tags=["用户门户"])


ERROR_CODE_GUIDE = [
    {"http_status": 401, "code": "UNAUTHORIZED", "message": "未提供、已禁用或已过期的 API Key；或 Key 所属账号不可调用。"},
    {"http_status": 403, "code": "FORBIDDEN", "message": "账号未审批、已拒绝、权限不足或 CSRF/Origin 校验失败。"},
    {"http_status": 404, "code": "NOT_FOUND", "message": "接口未发布、资源不归属当前用户，或平台刻意隐藏无权资源。"},
    {"http_status": 413, "code": "PAYLOAD_TOO_LARGE", "message": "上传或下载内容超过平台限制。"},
    {"http_status": 415, "code": "UNSUPPORTED_MEDIA_TYPE", "message": "Content-Type 不在已发布接口允许范围内。"},
    {"http_status": 422, "code": "VALIDATION_ERROR", "message": "请求参数、JSON 请求体或 OpenAPI/Swagger 文档不合法。"},
    {"http_status": 429, "code": "RATE_LIMITED", "message": "触发登录或 Gateway 调用限流。"},
    {"http_status": 429, "code": "USER_RATE_LIMITED", "message": "当前用户跨全部 Key 和工具的每分钟请求数已达到管理员设置的上限。"},
    {"http_status": 502, "code": "BAD_GATEWAY", "message": "上游服务不可达、返回非法重定向或响应格式异常。"},
    {"http_status": 503, "code": "SERVICE_UNAVAILABLE", "message": "工具未启用或上游配置不可用。"},
    {"http_status": 504, "code": "UPSTREAM_TIMEOUT", "message": "上游服务响应超时。"},
]


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_array(value: str) -> list[Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _published_route_policy(route: PublishedRoute, rate_limit_per_minute: int) -> dict[str, object]:
    return {
        "route_version": route.route_version,
        "allowed_content_types": _json_list(route.allowed_content_types_json),
        "max_request_bytes": route.max_request_bytes,
        "max_response_bytes": route.max_response_bytes,
        "request_timeout_seconds": route.request_timeout_seconds,
        "allow_stream_upload": route.allow_stream_upload,
        "allow_stream_download": route.allow_stream_download,
        "access_mode": route.access_mode,
        "resource_policy": _json_object(route.resource_policy_json),
        "policy_version": route.policy_version,
        "require_idempotency_key": route.require_idempotency_key,
        "allow_retry": route.allow_retry,
        "risk_level": route.risk_level,
        "rate_limit_per_minute": rate_limit_per_minute,
        "storage_action": route.storage_action,
    }


def _developer_endpoint_doc(tool: Tool, endpoint: ApiEndpoint, route: PublishedRoute, rate_limit_per_minute: int) -> dict[str, object]:
    """返回给普通开发者的脱敏接口文档视图，只展示 Gateway 契约。"""
    return {
        "id": endpoint.id,
        "tool_slug": tool.slug,
        "tool_name": tool.name,
        "method": endpoint.method,
        "gateway_path": route.gateway_path,
        "operation_id": endpoint.operation_id,
        "summary": endpoint.summary or endpoint.operation_id or "",
        "description": endpoint.public_description or endpoint.summary or "",
        "content_types": _json_list(endpoint.content_types),
        "parameters": _json_array(endpoint.parameters_json),
        "request_body": _json_object(endpoint.request_body_json),
        "responses": _json_object(endpoint.responses_json),
        "route_policy": _published_route_policy(route, rate_limit_per_minute),
    }


def _task_adapter(tool: Tool) -> dict[str, Any]:
    value = _json_object(tool.task_adapter_json)
    return value if value.get("version") in {1, 2} and isinstance(value.get("routes"), dict) else {}


def _task_capabilities(session: Session, tool: Tool, adapter: dict[str, Any]) -> dict[str, bool]:
    routes = adapter.get("routes") if isinstance(adapter.get("routes"), dict) else {}
    result: dict[str, bool] = {}
    for name in ("status", "logs", "manifest", "artifacts", "download"):
        route_id = routes.get(name) if isinstance(routes, dict) else None
        route = session.get(PublishedRoute, route_id) if isinstance(route_id, str) else None
        integration = session.get(ToolIntegrationRoute, route_id) if isinstance(route_id, str) else None
        result[name] = bool((route and route.tool_id == tool.id and route.is_enabled and route.method == "GET") or (integration and integration.tool_id == tool.id and integration.is_enabled and integration.method == "GET"))
    return result


def _task_view(
    item: ExternalResource,
    tool: Tool,
    capabilities: dict[str, bool],
    adapter: dict[str, Any],
    upstream: dict[str, object] | None = None,
) -> TaskDetailView:
    terminal_statuses = {
        str(value).lower()
        for value in adapter.get("terminal_statuses", ["succeeded", "failed", "cancelled"])
        if isinstance(value, str)
    }
    return TaskDetailView(
        id=item.id,
        tool_slug=tool.slug,
        tool_name=tool.name,
        upstream_id=item.upstream_id,
        status=item.status,
        created_at=item.created_at,
        updated_at=item.updated_at,
        metadata=json.loads(item.metadata_json),
        capabilities=capabilities,
        is_terminal=item.status.lower() in terminal_statuses,
        upstream=upstream,
    )


def _owned_task_context(session: Session, user_id: str, task_id: str) -> tuple[ExternalResource, Tool, ToolStorageEndpointRevision, dict[str, Any], dict[str, bool]]:
    resource = session.scalar(
        select(ExternalResource).where(
            ExternalResource.id == task_id,
            ExternalResource.owner_user_id == user_id,
            ExternalResource.kind == ResourceKind.JOB.value,
        )
    )
    if not resource:
        raise HTTPException(status_code=404, detail="任务不存在")
    tool = session.get(Tool, resource.tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="任务所属工具不存在")
    _, endpoint, adapter = task_storage_context(session, resource)
    return resource, tool, endpoint, adapter, task_capabilities(session, resource)


def get_owned_job(session: Session, tool_id: str, user_id: str, job_id: str) -> ExternalResource:
    """保留给资源归属测试和旧内部调用的精确查询，不执行任何工具特例。"""
    resource = session.scalar(
        select(ExternalResource).where(
            ExternalResource.tool_id == tool_id,
            ExternalResource.owner_user_id == user_id,
            ExternalResource.kind == ResourceKind.JOB.value,
            ExternalResource.upstream_id == job_id,
        )
    )
    if not resource:
        raise HTTPException(status_code=404, detail="任务不存在")
    return resource


def _task_route(session: Session, tool: Tool, adapter: dict[str, Any], capability: str) -> PublishedRoute | ToolIntegrationRoute:
    routes = adapter.get("routes") if isinstance(adapter.get("routes"), dict) else {}
    route_id = routes.get(capability) if isinstance(routes, dict) else None
    route = session.get(PublishedRoute, route_id) if isinstance(route_id, str) else None
    integration = session.get(ToolIntegrationRoute, route_id) if isinstance(route_id, str) else None
    selected = route or integration
    if not selected or selected.tool_id != tool.id or not selected.is_enabled or selected.method != "GET":
        raise HTTPException(status_code=404, detail="当前工具未配置该任务能力")
    return selected


def _task_upstream_path(route: PublishedRoute | ToolIntegrationRoute, adapter: dict[str, Any], upstream_id: str) -> str:
    id_param = str(adapter.get("id_param") or "job_id")
    marker = "{" + id_param + "}"
    path_template = route.upstream_path if isinstance(route, PublishedRoute) else route.path_template
    if marker not in path_template:
        raise HTTPException(status_code=503, detail="任务适配器路径参数配置不完整")
    rendered = path_template.replace(marker, quote(upstream_id, safe=""))
    if "{" in rendered or "}" in rendered:
        raise HTTPException(status_code=503, detail="任务适配器包含未解析路径参数")
    return rendered


async def _fetch_task_json(upstream: ToolUpstream, path: str, current_request_id: str) -> object:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(upstream.request_timeout_seconds, connect=upstream.connect_timeout_seconds), follow_redirects=False) as client:
            response = await client.get(f"{upstream.base_url}{path}", headers=upstream_headers(upstream, current_request_id))
    except httpx.ConnectError as error:
        raise HTTPException(status_code=502, detail="专业服务不可达") from error
    except httpx.TimeoutException as error:
        raise HTTPException(status_code=504, detail="专业服务响应超时") from error
    if 300 <= response.status_code < 400:
        raise HTTPException(status_code=502, detail="专业服务返回了不允许的重定向")
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail={"message": "专业服务请求失败", "details": {"upstream_status": response.status_code}})
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        return {"content_type": content_type or "text/plain", "text": response.text[:20_000]}
    try:
        return response.json()
    except ValueError as error:
        raise HTTPException(status_code=502, detail="上游返回了无效 JSON") from error


async def _stream_task_download(upstream: ToolUpstream, path: str, current_request_id: str) -> JSONResponse | StreamingResponse:
    client = httpx.AsyncClient(timeout=httpx.Timeout(upstream.request_timeout_seconds, connect=upstream.connect_timeout_seconds), follow_redirects=False)
    try:
        response = await client.send(
            client.build_request("GET", f"{upstream.base_url}{path}", headers=upstream_headers(upstream, current_request_id, accept="application/zip")),
            stream=True,
        )
    except httpx.ConnectError as error:
        await client.aclose()
        raise HTTPException(status_code=502, detail="专业服务不可达") from error
    except httpx.TimeoutException as error:
        await client.aclose()
        raise HTTPException(status_code=504, detail="专业服务响应超时") from error
    if 300 <= response.status_code < 400:
        await response.aclose()
        await client.aclose()
        return JSONResponse(gateway_error_payload("UPSTREAM_REDIRECT_BLOCKED", "专业服务返回了不允许的重定向", current_request_id), status_code=502)
    if response.status_code >= 400:
        await response.aread()
        await response.aclose()
        await client.aclose()
        return JSONResponse(
            gateway_error_payload("UPSTREAM_ERROR", "专业服务请求失败", current_request_id, {"upstream_status": response.status_code}),
            status_code=response.status_code,
        )
    content_length = response.headers.get("content-length")
    try:
        if content_length and int(content_length) > get_settings().max_download_bytes:
            await response.aclose()
            await client.aclose()
            return JSONResponse(gateway_error_payload("UPSTREAM_RESPONSE_TOO_LARGE", "上游响应超过平台下载限制", current_request_id), status_code=413)
    except ValueError:
        pass

    async def body() -> AsyncIterator[bytes]:
        received = 0
        try:
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > get_settings().max_download_bytes:
                    raise HTTPException(status_code=413, detail="上游响应超过平台下载限制")
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        body(),
        status_code=response.status_code,
        headers={
            "x-request-id": current_request_id,
            "content-type": response.headers.get("content-type", "application/octet-stream"),
            "content-disposition": safe_download_disposition(response.headers.get("content-disposition")),
        },
    )


# 仅保留内部测试与旧调用兼容，实际任务端点已完全使用通用适配器。
stream_tomodd_download = _stream_task_download


def _parse_call_datetime(value: str | None, field_name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"{field_name} 时间格式不正确") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _call_conditions(
    user_id: str,
    *,
    call_request_id: str | None = None,
    tool_slug: str | None = None,
    status_code: int | None = None,
    result: str | None = None,
    method: str | None = None,
    api_key_id: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[object]:
    conditions: list[object] = [GatewayRequest.user_id == user_id]
    if call_request_id:
        conditions.append(GatewayRequest.id == call_request_id.strip())
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
    if start_time:
        conditions.append(GatewayRequest.created_at >= start_time)
    if end_time:
        conditions.append(GatewayRequest.created_at <= end_time)
    return conditions


@router.get("/overview")
def get_user_overview(
    window: str = Query(default="7d", pattern="^(24h|7d|30d)$"),
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    now = utc_now()
    delta = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}[window]
    started_at = now - delta
    conditions = [GatewayRequest.user_id == user.id, GatewayRequest.created_at >= started_at, GatewayRequest.created_at <= now]
    totals = session.execute(
        select(
            func.count(GatewayRequest.id),
            func.sum(case((GatewayRequest.status_code < 400, 1), else_=0)),
            func.sum(case((GatewayRequest.status_code >= 400, 1), else_=0)),
            func.sum(case((GatewayRequest.status_code == 429, 1), else_=0)),
            func.coalesce(func.sum(GatewayRequest.request_bytes), 0),
            func.coalesce(func.sum(GatewayRequest.response_bytes), 0),
        ).where(*conditions)
    ).one()
    total = int(totals[0] or 0)
    success = int(totals[1] or 0)
    p95 = 0
    if total:
        p95_index = max(0, math.ceil(total * 0.95) - 1)
        p95 = int(
            session.scalar(
                select(GatewayRequest.duration_ms)
                .where(*conditions)
                .order_by(GatewayRequest.duration_ms)
                .offset(p95_index)
                .limit(1)
            )
            or 0
        )
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        bucket = func.strftime("%Y-%m-%dT%H:00:00Z" if window == "24h" else "%Y-%m-%dT00:00:00Z", GatewayRequest.created_at)
    elif dialect in {"mysql", "mariadb"}:
        bucket = func.date_format(GatewayRequest.created_at, "%Y-%m-%dT%H:00:00Z" if window == "24h" else "%Y-%m-%dT00:00:00Z")
    else:
        bucket = func.date_trunc("hour" if window == "24h" else "day", GatewayRequest.created_at)
    trend_rows = session.execute(
        select(
            bucket.label("bucket"),
            func.count(GatewayRequest.id),
            func.sum(case((GatewayRequest.status_code < 400, 1), else_=0)),
        )
        .where(*conditions)
        .group_by(bucket)
        .order_by(bucket)
    ).all()
    active_keys = session.scalars(
        select(ApiKey).where(
            ApiKey.user_id == user.id,
            ApiKey.status == KeyStatus.ACTIVE.value,
            or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
        )
    ).all()
    expiring_keys = [
        item
        for item in active_keys
        if item.expires_at
        and (item.expires_at.replace(tzinfo=timezone.utc) if item.expires_at.tzinfo is None else item.expires_at) <= now + timedelta(days=14)
    ]
    running_tasks = int(
        session.scalar(
            select(func.count(ExternalResource.id)).where(
                ExternalResource.owner_user_id == user.id,
                ExternalResource.kind == ResourceKind.JOB.value,
                ExternalResource.status.notin_(["succeeded", "failed", "cancelled"]),
            )
        )
        or 0
    )
    return {
        "window": window,
        "window_start": started_at,
        "window_end": now,
        "metrics": {
            "total": total,
            "success": success,
            "failed": int(totals[2] or 0),
            "rate_limited": int(totals[3] or 0),
            "success_rate": round(success * 100 / total, 2) if total else 0.0,
            "p95_duration_ms": p95,
            "request_bytes": int(totals[4] or 0),
            "response_bytes": int(totals[5] or 0),
        },
        "active_key_count": len(active_keys),
        "expiring_key_count": len(expiring_keys),
        "running_task_count": running_tasks,
        "retention_days": get_settings().call_log_retention_days,
        "trend": [
            {"bucket": str(row[0]), "total": int(row[1]), "success": int(row[2] or 0)} for row in trend_rows
        ],
    }


@router.get("/calls", response_model=list[GatewayCallView] | GatewayCallPage)
def list_my_calls(
    call_request_id: str | None = Query(default=None, alias="request_id", max_length=64),
    tool_slug: str | None = Query(default=None, max_length=64),
    status_code: int | None = Query(default=None, ge=100, le=599),
    result: str | None = Query(default=None, pattern="^(success|failed|rate_limited)$"),
    method: str | None = Query(default=None, pattern="^(GET|POST|PUT|PATCH|DELETE|HEAD)$"),
    api_key_id: str | None = Query(default=None, max_length=36),
    start: str | None = Query(default=None, max_length=40),
    end: str | None = Query(default=None, max_length=40),
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    user: User = Depends(require_not_password_change), session: Session = Depends(get_db)
) -> list[dict[str, object]] | dict[str, object]:
    start_time = _parse_call_datetime(start, "开始")
    end_time = _parse_call_datetime(end, "结束")
    if start_time and end_time and start_time > end_time:
        raise HTTPException(status_code=422, detail="开始时间不能晚于结束时间")
    conditions = _call_conditions(
        user.id,
        call_request_id=call_request_id,
        tool_slug=tool_slug,
        status_code=status_code,
        result=result,
        method=method,
        api_key_id=api_key_id,
        start_time=start_time,
        end_time=end_time,
    )
    statement = select(GatewayRequest).where(*conditions).order_by(GatewayRequest.created_at.desc(), GatewayRequest.id.desc())
    if page is None:
        rows = session.scalars(statement.limit(100)).all()
    else:
        rows = session.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
    items = gateway_call_views(session, list(rows), admin=False)
    if page is None:
        return items
    total = int(session.scalar(select(func.count(GatewayRequest.id)).where(*conditions)) or 0)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
    }


def _csv_safe(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


@router.get("/calls/export.csv", response_model=None)
def export_my_calls(
    call_request_id: str | None = Query(default=None, alias="request_id", max_length=64),
    tool_slug: str | None = Query(default=None, max_length=64),
    status_code: int | None = Query(default=None, ge=100, le=599),
    result: str | None = Query(default=None, pattern="^(success|failed|rate_limited)$"),
    method: str | None = Query(default=None, pattern="^(GET|POST|PUT|PATCH|DELETE|HEAD)$"),
    api_key_id: str | None = Query(default=None, max_length=36),
    start: str | None = Query(default=None, max_length=40),
    end: str | None = Query(default=None, max_length=40),
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> Response:
    end_time = _parse_call_datetime(end, "结束") or utc_now()
    start_time = _parse_call_datetime(start, "开始") or end_time - timedelta(days=7)
    if start_time > end_time:
        raise HTTPException(status_code=422, detail="开始时间不能晚于结束时间")
    if end_time - start_time > timedelta(days=31):
        raise HTTPException(status_code=422, detail="单次导出时间范围不能超过 31 天")
    conditions = _call_conditions(
        user.id,
        call_request_id=call_request_id,
        tool_slug=tool_slug,
        status_code=status_code,
        result=result,
        method=method,
        api_key_id=api_key_id,
        start_time=start_time,
        end_time=end_time,
    )
    rows = session.scalars(
        select(GatewayRequest)
        .where(*conditions)
        .order_by(GatewayRequest.created_at.desc(), GatewayRequest.id.desc())
        .limit(50_001)
    ).all()
    if len(rows) > 50_000:
        raise HTTPException(status_code=422, detail="匹配记录超过 50,000 行，请缩小筛选范围")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["request_id", "time", "tool", "method", "path", "status", "duration_ms", "request_bytes", "response_bytes", "error_code", "api_key_prefix"])
    key_prefixes = {
        key.id: key.prefix
        for key in session.scalars(select(ApiKey).where(ApiKey.user_id == user.id)).all()
    }
    for row in rows:
        writer.writerow(
            [
                _csv_safe(row.id),
                _csv_safe(row.created_at.isoformat()),
                _csv_safe(row.tool_slug),
                _csv_safe(row.method),
                _csv_safe(row.path),
                row.status_code,
                row.duration_ms,
                row.request_bytes,
                row.response_bytes,
                _csv_safe(row.error_code),
                _csv_safe(key_prefixes.get(row.api_key_id or "", "")),
            ]
        )
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gateway-calls.csv"'},
    )


@router.get("/calls/{call_request_id}", response_model=GatewayCallView)
def get_my_call(
    call_request_id: str,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    row = session.scalar(select(GatewayRequest).where(GatewayRequest.id == call_request_id, GatewayRequest.user_id == user.id))
    if not row:
        raise HTTPException(status_code=404, detail="调用记录不存在")
    return gateway_call_view(session, row, admin=False)


def _ensure_key_expiry_notifications(session: Session, user: User) -> None:
    now = utc_now()
    keys = session.scalars(
        select(ApiKey).where(
            ApiKey.user_id == user.id,
            ApiKey.status == KeyStatus.ACTIVE.value,
            ApiKey.expires_at.is_not(None),
            ApiKey.expires_at > now,
            ApiKey.expires_at <= now + timedelta(days=14),
        )
    ).all()
    changed = False
    for key in keys:
        action_url = f"/api-keys?focus={key.id}"
        exists = session.scalar(
            select(Notification.id).where(
                Notification.user_id == user.id,
                Notification.kind == "key_expiry",
                Notification.action_url == action_url,
            )
        )
        if exists:
            continue
        expiry = key.expires_at.replace(tzinfo=timezone.utc) if key.expires_at and key.expires_at.tzinfo is None else key.expires_at
        days = max(1, math.ceil(((expiry or now) - now).total_seconds() / 86_400))
        add_notification(
            session,
            user_id=user.id,
            kind="key_expiry",
            title="API Key 即将过期",
            body=f"{key.label}（{key.prefix}...）将在约 {days} 天后过期，请提前完成轮换。",
            action_url=action_url,
        )
        changed = True
    if changed:
        session.commit()


@router.get("/notifications", response_model=NotificationPage)
def list_notifications(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=10, le=100),
    unread_only: bool = False,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> NotificationPage:
    _ensure_key_expiry_notifications(session, user)
    conditions: list[object] = [Notification.user_id == user.id]
    if unread_only:
        conditions.append(Notification.read_at.is_(None))
    total = int(session.scalar(select(func.count(Notification.id)).where(*conditions)) or 0)
    unread_count = int(
        session.scalar(
            select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.read_at.is_(None))
        )
        or 0
    )
    rows = session.scalars(
        select(Notification)
        .where(*conditions)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return NotificationPage(
        items=[NotificationView.model_validate(item, from_attributes=True) for item in rows],
        unread_count=unread_count,
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )


@router.patch("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    request: Request,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> dict[str, bool]:
    validate_csrf(request)
    item = session.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id))
    if not item:
        raise HTTPException(status_code=404, detail="通知不存在")
    if item.read_at is None:
        item.read_at = utc_now()
        session.commit()
    return {"ok": True}


@router.patch("/notifications/read-all")
def mark_all_notifications_read(
    request: Request,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> dict[str, bool]:
    validate_csrf(request)
    session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=utc_now())
    )
    session.commit()
    return {"ok": True}


@router.get("/events", response_model=UserEventPage)
def list_my_events(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=10, le=100),
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> UserEventPage:
    key_ids = list(session.scalars(select(ApiKey.id).where(ApiKey.user_id == user.id)).all())
    ownership = [
        (AdminAuditEvent.target_type == "user") & (AdminAuditEvent.target_id == user.id),
        AdminAuditEvent.admin_user_id == user.id,
    ]
    if key_ids:
        ownership.append((AdminAuditEvent.target_type == "api_key") & (AdminAuditEvent.target_id.in_(key_ids)))
    condition = or_(*ownership)
    total = int(session.scalar(select(func.count(AdminAuditEvent.id)).where(condition)) or 0)
    rows = session.scalars(
        select(AdminAuditEvent)
        .where(condition)
        .order_by(AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    actor_ids = {row.admin_user_id for row in rows}
    actors = {
        actor.id: actor.username
        for actor in session.scalars(select(User).where(User.id.in_(actor_ids))).all()
    }
    allowed_detail_keys = {"username", "reason", "label", "prefix", "key_prefix", "display_name", "account_locked"}
    items: list[UserEventView] = []
    for row in rows:
        raw_detail = _json_object(row.detail_json)
        detail = {key: raw_detail[key] for key in allowed_detail_keys if key in raw_detail}
        items.append(
            UserEventView(
                id=row.id,
                action=row.action,
                actor_user_id=row.admin_user_id,
                actor_username=actors.get(row.admin_user_id, "未知账号"),
                target_type=row.target_type,
                target_id=row.target_id,
                request_id=row.request_id,
                detail=detail,
                created_at=row.created_at,
            )
        )
    return UserEventPage(items=items, total=total, page=page, page_size=page_size, has_more=page * page_size < total)


@router.get("/quickstart")
def get_developer_quickstart(
    user: User = Depends(require_not_password_change), session: Session = Depends(get_db)
) -> dict[str, object]:
    """返回用户程序集成所需的安全聚合信息，不暴露真实上游地址或上游 Token。"""
    keys = session.scalars(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())).all()
    active_keys = [item for item in keys if item.status == KeyStatus.ACTIVE.value and not api_key_is_expired(item)]

    route_conditions = [Tool.is_enabled.is_(True), PublishedRoute.is_enabled.is_(True), ApiEndpoint.status == EndpointStatus.PUBLISHED.value]
    if "admin" not in get_user_roles(session, user.id):
        route_conditions.append(PublishedRoute.access_mode != "admin_only")
    rows = session.execute(
        select(Tool, ApiEndpoint, PublishedRoute)
        .join(PublishedRoute, PublishedRoute.tool_id == Tool.id)
        .join(ApiEndpoint, ApiEndpoint.id == PublishedRoute.endpoint_id)
        .where(*route_conditions)
        .order_by(Tool.slug, ApiEndpoint.method, PublishedRoute.gateway_path)
    ).all()

    tool_summaries: dict[str, dict[str, object]] = {}
    sample: dict[str, object] | None = None
    sample_row = next((row for row in rows if row[1].method == "GET"), rows[0] if rows else None)
    for tool, endpoint, route in rows:
        summary = tool_summaries.setdefault(
            tool.slug,
            {
                "slug": tool.slug,
                "name": tool.name,
                "description": tool.description,
                "published_endpoint_count": 0,
            },
        )
        summary["published_endpoint_count"] = int(summary["published_endpoint_count"]) + 1
        if sample_row and tool.id == sample_row[0].id and endpoint.id == sample_row[1].id:
            sample = {
                "tool_slug": tool.slug,
                "tool_name": tool.name,
                "method": endpoint.method,
                "gateway_path": route.gateway_path,
                "summary": endpoint.summary or endpoint.operation_id or "",
                "operation_id": endpoint.operation_id,
                "content_types": _json_list(endpoint.content_types),
            }

    platform_settings = get_effective_platform_settings(session)
    return {
        "gateway_base_url": platform_settings.public_gateway_base_url,
        "gateway_base_path": "/gateway",
        "gateway_pattern": "/gateway/{tool_slug}{gateway_path}",
        "auth_header": "X-API-Key",
        "active_key_count": len(active_keys),
        "active_key_prefixes": [item.prefix for item in active_keys],
        "published_tool_count": len(tool_summaries),
        "published_endpoint_count": len(rows),
        "sample_endpoint": sample,
        "tools": list(tool_summaries.values()),
        "error_codes": ERROR_CODE_GUIDE,
    }


@router.get("/api-docs")
def get_developer_api_docs(
    user: User = Depends(require_not_password_change), session: Session = Depends(get_db)
) -> dict[str, object]:
    """聚合全部已发布工具文档；普通用户只看到统一 Gateway 契约。"""
    route_conditions = [Tool.is_enabled.is_(True), PublishedRoute.is_enabled.is_(True), ApiEndpoint.status == EndpointStatus.PUBLISHED.value]
    if "admin" not in get_user_roles(session, user.id):
        route_conditions.append(PublishedRoute.access_mode != "admin_only")
    rows = session.execute(
        select(Tool, ApiEndpoint, PublishedRoute)
        .join(PublishedRoute, PublishedRoute.tool_id == Tool.id)
        .join(ApiEndpoint, ApiEndpoint.id == PublishedRoute.endpoint_id)
        .where(*route_conditions)
        .order_by(Tool.name, Tool.slug, PublishedRoute.gateway_path, ApiEndpoint.method)
    ).all()
    tools: dict[str, dict[str, object]] = {}
    rate_limits: dict[str, int] = {}
    total_endpoints = 0
    for tool, endpoint, route in rows:
        item = tools.setdefault(
            tool.slug,
            {
                "id": tool.id,
                "slug": tool.slug,
                "name": tool.name,
                "description": tool.description,
                "gateway_base_path": f"/gateway/{tool.slug}",
                "endpoints": [],
            },
        )
        endpoints = item["endpoints"]
        if isinstance(endpoints, list):
            if tool.id not in rate_limits:
                rate_limits[tool.id] = int(
                    session.scalar(select(ToolUpstream.rate_limit_per_minute).where(ToolUpstream.tool_id == tool.id)) or 0
                )
            endpoints.append(_developer_endpoint_doc(tool, endpoint, route, rate_limits[tool.id]))
            total_endpoints += 1
    platform_settings = get_effective_platform_settings(session)
    return {
        "gateway_base_url": platform_settings.public_gateway_base_url,
        "gateway_pattern": "/gateway/{tool_slug}{gateway_path}",
        "auth_header": "X-API-Key",
        "tools": list(tools.values()),
        "total_tools": len(tools),
        "total_endpoints": total_endpoints,
        "error_codes": ERROR_CODE_GUIDE,
    }


@router.get("/tasks", response_model=TaskPage)
def list_my_tasks(
    tool_slug: str | None = Query(default=None, max_length=64),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    search: str | None = Query(default=None, max_length=128),
    start: str | None = Query(default=None, max_length=40),
    end: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=10, le=100),
    user: User = Depends(require_not_password_change), session: Session = Depends(get_db)
) -> TaskPage:
    start_time = _parse_call_datetime(start, "开始")
    end_time = _parse_call_datetime(end, "结束")
    if start_time and end_time and start_time > end_time:
        raise HTTPException(status_code=422, detail="开始时间不能晚于结束时间")
    conditions: list[object] = [ExternalResource.owner_user_id == user.id, ExternalResource.kind == ResourceKind.JOB.value]
    if tool_slug:
        conditions.append(Tool.slug == tool_slug.strip())
    if status_filter:
        conditions.append(ExternalResource.status == status_filter.strip())
    if search:
        conditions.append(ExternalResource.upstream_id.ilike(f"%{search.strip()}%"))
    if start_time:
        conditions.append(ExternalResource.created_at >= start_time)
    if end_time:
        conditions.append(ExternalResource.created_at <= end_time)
    statement = (
        select(ExternalResource, Tool)
        .join(Tool, Tool.id == ExternalResource.tool_id)
        .where(*conditions)
        .order_by(ExternalResource.updated_at.desc(), ExternalResource.id.desc())
    )
    total = int(
        session.scalar(
            select(func.count(ExternalResource.id))
            .join(Tool, Tool.id == ExternalResource.tool_id)
            .where(*conditions)
        )
        or 0
    )
    rows = session.execute(statement.offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for resource, tool in rows:
        _, _, adapter = task_storage_context(session, resource)
        items.append(_task_view(resource, tool, task_capabilities(session, resource), adapter))
    return TaskPage(items=items, total=total, page=page, page_size=page_size, has_more=page * page_size < total)


@router.get("/tasks/{task_id}", response_model=TaskDetailView)
def get_my_task(
    task_id: str,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> TaskDetailView:
    resource, tool, _, adapter, capabilities = _owned_task_context(session, user.id, task_id)
    # 页面详情只读取平台记录；上游轮询由文件同步进程负责，避免上游故障阻断历史文件访问。
    return _task_view(resource, tool, capabilities, adapter)


@router.get("/tasks/{task_id}/logs")
async def get_my_task_logs(
    task_id: str,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
    current_request_id: str = Depends(request_id),
) -> object:
    resource, _, _, _, _ = _owned_task_context(session, user.id, task_id)
    return await fetch_task_capability_json(session, resource, "logs", request_id=current_request_id)


@router.get("/tasks/{task_id}/manifest")
async def get_my_task_manifest(
    task_id: str,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
    current_request_id: str = Depends(request_id),
) -> object:
    resource, _, _, _, _ = _owned_task_context(session, user.id, task_id)
    return await fetch_task_capability_json(session, resource, "manifest", request_id=current_request_id)


@router.get("/tasks/{task_id}/artifacts")
async def get_my_task_artifacts(
    task_id: str,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
    current_request_id: str = Depends(request_id),
) -> object:
    resource, _, _, _, _ = _owned_task_context(session, user.id, task_id)
    return await fetch_task_capability_json(session, resource, "artifacts", request_id=current_request_id)


@router.get("/tasks/{task_id}/download", response_model=None)
async def download_my_task_zip(
    task_id: str,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
    current_request_id: str = Depends(request_id),
) -> Response:
    _owned_task_context(session, user.id, task_id)
    raise HTTPException(status_code=410, detail={"code": "LEGACY_TASK_DOWNLOAD_RETIRED", "message": "整包结果已纳入文件与制品，请在任务文件标签中创建下载授权"})


@router.get("/tools")
def list_user_tools(
    user: User = Depends(require_not_password_change), session: Session = Depends(get_db)
) -> list[dict[str, object]]:
    conditions = [Tool.is_enabled.is_(True), PublishedRoute.is_enabled.is_(True), ApiEndpoint.status == EndpointStatus.PUBLISHED.value]
    if "admin" not in get_user_roles(session, user.id):
        conditions.append(PublishedRoute.access_mode != "admin_only")
    rows = session.scalars(
        select(Tool)
        .join(PublishedRoute, PublishedRoute.tool_id == Tool.id)
        .join(ApiEndpoint, ApiEndpoint.id == PublishedRoute.endpoint_id)
        .where(*conditions)
        .distinct()
        .order_by(Tool.name)
    ).all()
    return [{"id": item.id, "slug": item.slug, "name": item.name, "description": item.description} for item in rows]


@router.get("/tools/{tool_slug}/endpoints", response_model=list[EndpointView])
def list_user_tool_endpoints(
    tool_slug: str,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> list[EndpointView]:
    tool = session.scalar(select(Tool).where(Tool.slug == tool_slug, Tool.is_enabled.is_(True)))
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")
    conditions = [PublishedRoute.tool_id == tool.id, PublishedRoute.is_enabled.is_(True), ApiEndpoint.status == EndpointStatus.PUBLISHED.value]
    if "admin" not in get_user_roles(session, user.id):
        conditions.append(PublishedRoute.access_mode != "admin_only")
    rows = session.execute(
        select(ApiEndpoint, PublishedRoute)
        .join(PublishedRoute, PublishedRoute.endpoint_id == ApiEndpoint.id)
        .where(*conditions)
        .order_by(ApiEndpoint.upstream_path, ApiEndpoint.method)
    ).all()
    return [
        EndpointView(
            id=endpoint.id,
            method=endpoint.method,
            upstream_path=route.gateway_path,
            gateway_path=route.gateway_path,
            operation_id=endpoint.operation_id,
            summary=endpoint.summary,
            content_types=json.loads(endpoint.content_types),
            parameters=json.loads(endpoint.parameters_json),
            request_body=json.loads(endpoint.request_body_json),
            responses=json.loads(endpoint.responses_json),
            public_description=endpoint.public_description,
            route_policy={
                "method": route.method,
                "route_version": route.route_version,
                "gateway_path": route.gateway_path,
                "max_request_bytes": route.max_request_bytes,
                "max_response_bytes": route.max_response_bytes,
                "request_timeout_seconds": route.request_timeout_seconds,
                "allow_stream_upload": route.allow_stream_upload,
                "allow_stream_download": route.allow_stream_download,
                "access_mode": route.access_mode,
                "resource_policy": _json_object(route.resource_policy_json),
                "policy_version": route.policy_version,
                "require_idempotency_key": route.require_idempotency_key,
                "allow_retry": route.allow_retry,
                "risk_level": route.risk_level,
            },
            access_policy={"access_mode": route.access_mode, "rules": _json_object(route.resource_policy_json), "confirmed": True, "complete": True},
            published=True,
            enabled=route.is_enabled,
        )
        for endpoint, route in rows
    ]
