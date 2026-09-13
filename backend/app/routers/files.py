"""用户和管理员的远程文件目录、下载与生命周期接口。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from ..config import get_settings
from ..database import SessionLocal, get_db
from ..models import (
    ExternalResource,
    ApiEndpoint,
    FileDownloadAuthorization,
    FileDownloadEvent,
    FileDownloadSlot,
    FileSyncJob,
    FileWorkerHeartbeat,
    GatewayRequest,
    RemoteFile,
    RemoteFileLink,
    RemoteFileStatus,
    ResourceKind,
    Role,
    Tool,
    ToolIntegrationRoute,
    ToolStorageEndpointRevision,
    User,
    UserRole,
)
from ..schemas import AdminTaskDetailView, FileAdminActionRequest, FileDeleteRequest, FileDownloadAuthorizationView, FileDownloadEventPage, FileDownloadEventView, RemoteFilePage, RemoteFileView, ToolFileAccessUpdateRequest, ToolIntegrationRouteUpdateRequest, ToolIntegrationRouteView, ToolStorageEndpointRevisionView
from ..services.audit import log_admin_event
from ..services.remote_files import (
    effective_user_quota,
    fetch_task_capability_json,
    file_is_deletable,
    file_delete_supported,
    file_is_downloadable,
    queue_file_delete,
    queue_resource_sync,
    remote_file_usage,
    render_capability_path,
    resolve_capability,
    storage_headers,
    task_capabilities,
    task_storage_context,
)
from ..services.user_lifecycle import utc_now
from .deps import get_user_roles, request_id, require_admin, require_not_password_change, validate_csrf


router = APIRouter(tags=["文件与制品"])


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _parse_time(value: str | None, label: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"{label}时间格式不正确") from error
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _file_view(session: Session, item: RemoteFile, *, admin: bool) -> RemoteFileView:
    tool = session.get(Tool, item.tool_id)
    owner = session.get(User, item.owner_user_id)
    parent = session.get(ExternalResource, item.parent_resource_id) if item.parent_resource_id else None
    if not tool:
        raise HTTPException(status_code=500, detail="文件所属工具记录缺失")
    return RemoteFileView(
        id=item.id,
        tool_id=tool.id,
        tool_slug=tool.slug,
        tool_name=tool.name,
        owner_user_id=item.owner_user_id,
        owner_username=owner.username if admin and owner else "",
        file_name=item.file_name or "元信息待同步",
        role=item.role,
        visibility=item.visibility,
        status=item.status,
        content_type=item.content_type,
        size_bytes=item.size_bytes,
        sha256=item.sha256,
        is_bundle=item.is_bundle,
        source_request_id=item.source_gateway_request_id,
        parent_resource_id=item.parent_resource_id,
        parent_kind=parent.kind if parent else None,
        parent_platform_id=parent.id if parent else None,
        expires_at=item.expires_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        downloadable=file_is_downloadable(item, tool, admin=admin),
        deletable=file_is_deletable(item, admin=admin) and file_delete_supported(session, item),
    )


def _file_conditions(
    *,
    user_id: str | None,
    admin: bool,
    tool_slug: str | None,
    role: str | None,
    visibility: str | None,
    status_filter: str | None,
    search: str | None,
    source_request_id: str | None,
    task_id: str | None,
    start: datetime | None,
    end: datetime | None,
) -> list[object]:
    conditions: list[object] = []
    if user_id:
        conditions.append(RemoteFile.owner_user_id == user_id)
    if not admin:
        conditions.append(RemoteFile.visibility == "user")
    if tool_slug:
        conditions.append(Tool.slug == tool_slug.strip())
    if role:
        conditions.append(RemoteFile.role == role.strip())
    if visibility:
        conditions.append(RemoteFile.visibility == visibility.strip())
    if status_filter:
        conditions.append(RemoteFile.status == status_filter.strip())
    if search:
        conditions.append(RemoteFile.file_name.ilike(f"%{search.strip()}%"))
    if source_request_id:
        conditions.append(or_(RemoteFile.source_gateway_request_id == source_request_id.strip(), RemoteFileLink.gateway_request_id == source_request_id.strip()))
    if task_id:
        conditions.append(or_(RemoteFile.parent_resource_id == task_id, RemoteFileLink.resource_id == task_id))
    if start:
        conditions.append(RemoteFile.created_at >= start)
    if end:
        conditions.append(RemoteFile.created_at <= end)
    return conditions


def _file_page(
    session: Session,
    *,
    user: User | None,
    admin: bool,
    tool_slug: str | None,
    role: str | None,
    visibility: str | None,
    status_filter: str | None,
    search: str | None,
    source_request_id: str | None,
    task_id: str | None,
    start: datetime | None,
    end: datetime | None,
    page: int,
    page_size: int,
) -> RemoteFilePage:
    conditions = _file_conditions(user_id=user.id if user else None, admin=admin, tool_slug=tool_slug, role=role, visibility=visibility, status_filter=status_filter, search=search, source_request_id=source_request_id, task_id=task_id, start=start, end=end)
    base = select(RemoteFile).join(Tool, Tool.id == RemoteFile.tool_id).outerjoin(RemoteFileLink, RemoteFileLink.remote_file_id == RemoteFile.id).where(*conditions).distinct()
    count = select(func.count(func.distinct(RemoteFile.id))).select_from(RemoteFile).join(Tool, Tool.id == RemoteFile.tool_id).outerjoin(RemoteFileLink, RemoteFileLink.remote_file_id == RemoteFile.id).where(*conditions)
    total = int(session.scalar(count) or 0)
    rows = session.scalars(base.order_by(RemoteFile.created_at.desc(), RemoteFile.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    used = remote_file_usage(session, user.id) if user else int(session.scalar(select(func.coalesce(func.sum(RemoteFile.size_bytes), 0)).where(RemoteFile.status.notin_(["deleted", "expired", "missing"]))) or 0)
    quota = effective_user_quota(session, user) if user else 0
    return RemoteFilePage(items=[_file_view(session, item, admin=admin) for item in rows], total=total, page=page, page_size=page_size, has_more=page * page_size < total, used_bytes=used, quota_bytes=quota)


@router.get("/api/me/files", response_model=RemoteFilePage)
def list_my_files(
    tool_slug: str | None = Query(default=None, max_length=64),
    role: str | None = Query(default=None, max_length=32),
    status_filter: str | None = Query(default=None, alias="status", max_length=24),
    search: str | None = Query(default=None, max_length=255),
    source_request_id: str | None = Query(default=None, max_length=36),
    task_id: str | None = Query(default=None, max_length=36),
    start: str | None = Query(default=None, max_length=40),
    end: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=10, le=100),
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> RemoteFilePage:
    start_time, end_time = _parse_time(start, "开始"), _parse_time(end, "结束")
    if start_time and end_time and start_time > end_time:
        raise HTTPException(status_code=422, detail="开始时间不能晚于结束时间")
    return _file_page(session, user=user, admin=False, tool_slug=tool_slug, role=role, visibility="user", status_filter=status_filter, search=search, source_request_id=source_request_id, task_id=task_id, start=start_time, end=end_time, page=page, page_size=page_size)


@router.get("/api/me/files/{file_id}", response_model=RemoteFileView)
def get_my_file(file_id: str, user: User = Depends(require_not_password_change), session: Session = Depends(get_db)) -> RemoteFileView:
    item = session.scalar(select(RemoteFile).where(RemoteFile.id == file_id, RemoteFile.owner_user_id == user.id, RemoteFile.visibility == "user"))
    if not item:
        raise HTTPException(status_code=404, detail="文件不存在")
    return _file_view(session, item, admin=False)


@router.get("/api/me/calls/{source_request_id}/files", response_model=list[RemoteFileView])
def list_my_call_files(source_request_id: str, user: User = Depends(require_not_password_change), session: Session = Depends(get_db)) -> list[RemoteFileView]:
    call = session.scalar(select(GatewayRequest).where(GatewayRequest.id == source_request_id, GatewayRequest.user_id == user.id))
    if not call:
        raise HTTPException(status_code=404, detail="调用记录不存在")
    rows = session.scalars(select(RemoteFile).outerjoin(RemoteFileLink, RemoteFileLink.remote_file_id == RemoteFile.id).where(RemoteFile.owner_user_id == user.id, RemoteFile.visibility == "user", or_(RemoteFile.source_gateway_request_id == source_request_id, RemoteFileLink.gateway_request_id == source_request_id)).distinct().order_by(RemoteFile.created_at)).all()
    return [_file_view(session, item, admin=False) for item in rows]


@router.get("/api/admin/calls/{source_request_id}/files", response_model=list[RemoteFileView])
def list_admin_call_files(source_request_id: str, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> list[RemoteFileView]:
    call = session.get(GatewayRequest, source_request_id)
    if not call:
        raise HTTPException(status_code=404, detail="调用记录不存在")
    rows = session.scalars(select(RemoteFile).outerjoin(RemoteFileLink, RemoteFileLink.remote_file_id == RemoteFile.id).where(or_(RemoteFile.source_gateway_request_id == source_request_id, RemoteFileLink.gateway_request_id == source_request_id)).distinct().order_by(RemoteFile.created_at)).all()
    return [_file_view(session, item, admin=True) for item in rows]


@router.get("/api/me/tasks/{task_id}/files", response_model=list[RemoteFileView])
def list_my_task_files(task_id: str, user: User = Depends(require_not_password_change), session: Session = Depends(get_db)) -> list[RemoteFileView]:
    task = session.scalar(select(ExternalResource).where(ExternalResource.id == task_id, ExternalResource.owner_user_id == user.id, ExternalResource.kind == ResourceKind.JOB.value))
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    rows = session.scalars(select(RemoteFile).outerjoin(RemoteFileLink, RemoteFileLink.remote_file_id == RemoteFile.id).where(RemoteFile.owner_user_id == user.id, RemoteFile.visibility == "user", or_(RemoteFile.parent_resource_id == task_id, RemoteFileLink.resource_id == task_id)).distinct().order_by(RemoteFile.created_at)).all()
    return [_file_view(session, item, admin=False) for item in rows]


@router.get("/api/admin/tasks/{task_id}/files", response_model=list[RemoteFileView])
def list_admin_task_files(task_id: str, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> list[RemoteFileView]:
    task = session.scalar(select(ExternalResource).where(ExternalResource.id == task_id, ExternalResource.kind == ResourceKind.JOB.value))
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    rows = session.scalars(
        select(RemoteFile)
        .outerjoin(RemoteFileLink, RemoteFileLink.remote_file_id == RemoteFile.id)
        .where(or_(RemoteFile.parent_resource_id == task_id, RemoteFileLink.resource_id == task_id))
        .distinct()
        .order_by(RemoteFile.created_at)
    ).all()
    return [_file_view(session, item, admin=True) for item in rows]


def _create_download_authorization(session: Session, item: RemoteFile, actor: User, *, admin: bool) -> FileDownloadAuthorizationView:
    tool = session.get(Tool, item.tool_id)
    if not tool or not file_is_downloadable(item, tool, admin=admin):
        expires = _aware(item.expires_at)
        if expires and expires <= utc_now():
            raise HTTPException(status_code=410, detail="文件已过期")
        raise HTTPException(status_code=409, detail="文件当前不可下载")
    expires_at = utc_now() + timedelta(minutes=get_settings().file_download_authorization_minutes)
    authorization = FileDownloadAuthorization(remote_file_id=item.id, actor_user_id=actor.id, is_admin=admin, expires_at=expires_at)
    session.add(authorization)
    session.flush()
    return FileDownloadAuthorizationView(authorization_id=authorization.id, download_url=f"/api/file-downloads/{authorization.id}", expires_at=expires_at)


@router.post("/api/me/files/{file_id}/download-authorizations", response_model=FileDownloadAuthorizationView)
def authorize_my_file_download(file_id: str, request: Request, user: User = Depends(require_not_password_change), session: Session = Depends(get_db)) -> FileDownloadAuthorizationView:
    validate_csrf(request)
    item = session.scalar(select(RemoteFile).where(RemoteFile.id == file_id, RemoteFile.owner_user_id == user.id, RemoteFile.visibility == "user"))
    if not item:
        raise HTTPException(status_code=404, detail="文件不存在")
    result = _create_download_authorization(session, item, user, admin=False)
    session.commit()
    return result


@router.post("/api/admin/files/{file_id}/download-authorizations", response_model=FileDownloadAuthorizationView)
def authorize_admin_file_download(file_id: str, request: Request, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> FileDownloadAuthorizationView:
    validate_csrf(request)
    item = session.get(RemoteFile, file_id)
    if not item:
        raise HTTPException(status_code=404, detail="文件不存在")
    result = _create_download_authorization(session, item, admin, admin=True)
    log_admin_event(session, admin_user_id=admin.id, action="admin_file_download_authorized", target_type="remote_file", target_id=item.id, request_id=request_id(request), detail={"owner_user_id": item.owner_user_id, "file_name": item.file_name, "size_bytes": item.size_bytes})
    session.commit()
    return result


def _claim_download_scope_slot(session: Session, event_id: str, *, scope_type: str, scope_id: str, limit: int, expires_at: datetime) -> bool:
    for slot_number in range(limit):
        try:
            with session.begin_nested():
                session.add(FileDownloadSlot(event_id=event_id, scope_type=scope_type, scope_id=scope_id, slot_number=slot_number, expires_at=expires_at))
                session.flush()
            return True
        except IntegrityError:
            continue
    return False


def _reserve_download_event(session: Session, *, authorization: FileDownloadAuthorization, item: RemoteFile, actor: User, request_value: str) -> FileDownloadEvent:
    now = utc_now()
    expired_event_ids = session.scalars(select(FileDownloadSlot.event_id).where(FileDownloadSlot.expires_at <= now).distinct()).all()
    if expired_event_ids:
        session.execute(
            update(FileDownloadEvent)
            .where(FileDownloadEvent.id.in_(expired_event_ids), FileDownloadEvent.status == "started")
            .values(status="interrupted", error_code="DOWNLOAD_LEASE_EXPIRED", completed_at=now)
        )
        session.execute(delete(FileDownloadSlot).where(FileDownloadSlot.expires_at <= now))
    event = FileDownloadEvent(
        authorization_id=authorization.id,
        remote_file_id=item.id,
        actor_user_id=actor.id,
        owner_user_id=item.owner_user_id,
        request_id=request_value,
        is_admin=authorization.is_admin,
        status="started",
    )
    session.add(event)
    session.flush()
    expires_at = now + timedelta(seconds=get_settings().file_download_lease_seconds)
    if not _claim_download_scope_slot(session, event.id, scope_type="actor", scope_id=actor.id, limit=get_settings().file_download_concurrency_per_user, expires_at=expires_at):
        session.delete(event)
        session.commit()
        raise HTTPException(status_code=429, detail={"code": "FILE_DOWNLOAD_CONCURRENCY_EXCEEDED", "message": "同时下载数量已达到账号上限"})
    if not _claim_download_scope_slot(session, event.id, scope_type="tool", scope_id=item.tool_id, limit=get_settings().file_download_concurrency_per_tool, expires_at=expires_at):
        session.execute(delete(FileDownloadSlot).where(FileDownloadSlot.event_id == event.id))
        session.delete(event)
        session.commit()
        raise HTTPException(status_code=429, detail={"code": "TOOL_DOWNLOAD_CONCURRENCY_EXCEEDED", "message": "当前工具下载繁忙，请稍后重试"})
    session.commit()
    return event


def _touch_download_event(event_id: str, *, bytes_sent: int) -> None:
    with SessionLocal() as session:
        expires_at = utc_now() + timedelta(seconds=get_settings().file_download_lease_seconds)
        session.execute(update(FileDownloadEvent).where(FileDownloadEvent.id == event_id, FileDownloadEvent.status == "started").values(bytes_sent=bytes_sent))
        session.execute(update(FileDownloadSlot).where(FileDownloadSlot.event_id == event_id).values(expires_at=expires_at))
        session.commit()


def _finish_download_event(event_id: str, *, status: str, bytes_sent: int, error_code: str = "") -> None:
    with SessionLocal() as session:
        event = session.get(FileDownloadEvent, event_id)
        if event and event.completed_at is None:
            event.status = status
            event.bytes_sent = bytes_sent
            event.error_code = error_code
            event.completed_at = utc_now()
            session.execute(delete(FileDownloadSlot).where(FileDownloadSlot.event_id == event_id))
            if event.is_admin:
                action = "admin_file_download_completed" if status == "completed" else "admin_file_download_interrupted" if status == "interrupted" else "admin_file_download_failed"
                log_admin_event(
                    session,
                    admin_user_id=event.actor_user_id,
                    action=action,
                    target_type="remote_file",
                    target_id=event.remote_file_id,
                    request_id=event.request_id,
                    detail={"owner_user_id": event.owner_user_id, "bytes_sent": bytes_sent, "error_code": error_code},
                )
            session.commit()


@router.api_route("/api/file-downloads/{authorization_id}", methods=["GET", "HEAD"], response_model=None)
async def stream_authorized_file(
    authorization_id: str,
    request: Request,
    actor: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> Response | StreamingResponse:
    authorization = session.scalar(select(FileDownloadAuthorization).where(FileDownloadAuthorization.id == authorization_id, FileDownloadAuthorization.actor_user_id == actor.id))
    if not authorization or (_aware(authorization.expires_at) or utc_now()) <= utc_now():
        raise HTTPException(status_code=404, detail="下载授权不存在或已过期")
    if authorization.is_admin and "admin" not in get_user_roles(session, actor.id):
        raise HTTPException(status_code=403, detail="管理员权限已失效")
    item = session.get(RemoteFile, authorization.remote_file_id)
    if not item or (not authorization.is_admin and (item.owner_user_id != actor.id or item.visibility != "user")):
        raise HTTPException(status_code=404, detail="文件不存在")
    tool = session.get(Tool, item.tool_id)
    endpoint = session.get(ToolStorageEndpointRevision, item.storage_endpoint_revision_id)
    parent = session.get(ExternalResource, item.parent_resource_id) if item.parent_resource_id else None
    if not tool or not endpoint:
        raise HTTPException(status_code=503, detail="文件存储端点不可用")
    if not file_is_downloadable(item, tool, admin=authorization.is_admin):
        expires = _aware(item.expires_at)
        if expires and expires <= utc_now():
            raise HTTPException(status_code=410, detail="文件已过期")
        raise HTTPException(status_code=409, detail="文件当前不可下载")
    limit = min(tool.max_file_bytes or get_settings().max_remote_file_bytes, get_settings().max_remote_file_bytes)
    if item.size_bytes is None:
        raise HTTPException(status_code=409, detail={"code": "FILE_METADATA_PENDING", "message": "文件大小尚未完成校验"})
    if item.size_bytes > limit:
        raise HTTPException(status_code=413, detail={"code": "FILE_TOO_LARGE", "message": "文件超过工具或平台代理上限"})
    capability = "download" if item.is_bundle else "file_download"
    resolved = resolve_capability(session, tool, endpoint, capability)
    if not resolved and item.is_bundle:
        resolved = resolve_capability(session, tool, endpoint, "bundle_download")
    if not resolved:
        raise HTTPException(status_code=409, detail="当前工具未配置对应下载能力")
    method, template, config = resolved
    adapter = json.loads(endpoint.adapter_json or "{}")
    path = render_capability_path(template, job_id=parent.upstream_id if parent else None, file_id=item.upstream_file_id, id_param=str(adapter.get("id_param") or "job_id"), file_id_param=str(config.get("file_id_param") or adapter.get("file_id_param") or "file_id"))
    headers = storage_headers(endpoint, request_id(request), accept=request.headers.get("accept", "application/octet-stream"))
    for name in ("range", "if-range"):
        if request.headers.get(name):
            headers[name] = request.headers[name]
    event = _reserve_download_event(session, authorization=authorization, item=item, actor=actor, request_value=request_id(request))
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=endpoint.connect_timeout_seconds), follow_redirects=False, verify=endpoint.verify_tls)
    try:
        upstream = await client.send(client.build_request("HEAD" if request.method == "HEAD" else method, f"{endpoint.base_url}{path}", headers=headers), stream=True)
    except httpx.ConnectError as error:
        await client.aclose()
        _finish_download_event(event.id, status="failed", bytes_sent=0, error_code="FILE_UPSTREAM_UNAVAILABLE")
        raise HTTPException(status_code=502, detail="工具文件服务不可达") from error
    except httpx.TimeoutException as error:
        await client.aclose()
        _finish_download_event(event.id, status="failed", bytes_sent=0, error_code="FILE_UPSTREAM_TIMEOUT")
        raise HTTPException(status_code=504, detail="工具文件服务响应超时") from error
    if 300 <= upstream.status_code < 400:
        await upstream.aclose(); await client.aclose()
        _finish_download_event(event.id, status="failed", bytes_sent=0, error_code="FILE_REDIRECT_BLOCKED")
        raise HTTPException(status_code=502, detail="工具文件服务返回了不允许的重定向")
    if upstream.status_code == 404:
        await upstream.aclose(); await client.aclose()
        item.status = RemoteFileStatus.MISSING.value; item.last_error = "上游文件不存在"; session.commit()
        _finish_download_event(event.id, status="failed", bytes_sent=0, error_code="FILE_MISSING")
        raise HTTPException(status_code=410, detail="文件已被工具服务器清理")
    if upstream.status_code >= 400:
        code = upstream.status_code
        await upstream.aclose(); await client.aclose()
        _finish_download_event(event.id, status="failed", bytes_sent=0, error_code="FILE_UPSTREAM_ERROR")
        raise HTTPException(status_code=502, detail={"message": "工具文件下载失败", "details": {"upstream_status": code}})
    safe_headers = {
        "content-disposition": f"attachment; filename*=UTF-8''{quote(item.file_name or 'download.bin')}",
        "content-type": item.content_type or "application/octet-stream",
        "cache-control": "private, no-store",
        "x-content-type-options": "nosniff",
        "x-request-id": request_id(request),
        "referrer-policy": "no-referrer",
    }
    for name in ("content-length", "content-range", "accept-ranges", "etag", "last-modified"):
        if upstream.headers.get(name):
            safe_headers[name] = upstream.headers[name]
    upstream_size: int | None = None
    content_range = upstream.headers.get("content-range", "")
    range_total = content_range.rsplit("/", 1)[-1] if "/" in content_range else ""
    try:
        upstream_size = int(range_total) if range_total.isdigit() else int(upstream.headers["content-length"]) if upstream.headers.get("content-length") else None
    except ValueError:
        upstream_size = None
    if upstream_size is not None and upstream_size > limit:
        await upstream.aclose(); await client.aclose()
        _finish_download_event(event.id, status="failed", bytes_sent=0, error_code="FILE_TOO_LARGE")
        raise HTTPException(status_code=413, detail={"code": "FILE_TOO_LARGE", "message": "工具服务器返回的文件超过平台代理上限"})
    if request.method == "HEAD":
        await upstream.aclose(); await client.aclose()
        _finish_download_event(event.id, status="completed", bytes_sent=0)
        return Response(status_code=upstream.status_code, headers=safe_headers)
    async def body() -> AsyncIterator[bytes]:
        sent = 0
        final_status, final_error = "completed", ""
        last_touch = time.monotonic()
        try:
            async for chunk in upstream.aiter_bytes():
                sent += len(chunk)
                if sent > limit:
                    final_status, final_error = "failed", "FILE_TOO_LARGE"
                    raise RuntimeError("文件流超过已校验的平台限制")
                yield chunk
                if time.monotonic() - last_touch >= max(30, get_settings().file_download_lease_seconds // 2):
                    _touch_download_event(event.id, bytes_sent=sent)
                    last_touch = time.monotonic()
        except asyncio.CancelledError:
            final_status, final_error = "interrupted", "CLIENT_DISCONNECTED"
            raise
        except httpx.HTTPError:
            final_status, final_error = "failed", "FILE_STREAM_INTERRUPTED"
            raise
        finally:
            await upstream.aclose(); await client.aclose()
            _finish_download_event(event.id, status=final_status, bytes_sent=sent, error_code=final_error)

    return StreamingResponse(body(), status_code=upstream.status_code, headers=safe_headers)


@router.post("/api/me/files/{file_id}/delete", status_code=202)
def delete_my_file(file_id: str, payload: FileDeleteRequest, request: Request, user: User = Depends(require_not_password_change), session: Session = Depends(get_db)) -> dict[str, object]:
    validate_csrf(request)
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="必须确认删除")
    item = session.scalar(select(RemoteFile).where(RemoteFile.id == file_id, RemoteFile.owner_user_id == user.id, RemoteFile.visibility == "user"))
    if not item:
        raise HTTPException(status_code=404, detail="文件不存在")
    if not file_is_deletable(item, admin=False):
        raise HTTPException(status_code=409, detail="文件当前不能删除")
    if not file_delete_supported(session, item):
        raise HTTPException(status_code=409, detail="当前工具不支持受控文件删除")
    if item.parent_resource_id:
        parent = session.get(ExternalResource, item.parent_resource_id)
        if parent and parent.status.lower() not in {"succeeded", "completed", "failed", "cancelled", "canceled"}:
            raise HTTPException(status_code=409, detail="运行中的任务仍依赖该文件")
    active_download = session.scalar(
        select(FileDownloadEvent.id)
        .join(FileDownloadSlot, FileDownloadSlot.event_id == FileDownloadEvent.id)
        .where(FileDownloadEvent.remote_file_id == item.id, FileDownloadEvent.status == "started", FileDownloadSlot.expires_at > utc_now())
        .limit(1)
    )
    if active_download:
        raise HTTPException(status_code=409, detail="文件仍在下载中，请等待下载结束后再删除")
    linked_resources = int(session.scalar(select(func.count(func.distinct(RemoteFileLink.resource_id))).where(RemoteFileLink.remote_file_id == item.id, RemoteFileLink.resource_id.is_not(None))) or 0)
    if linked_resources > 1:
        raise HTTPException(status_code=409, detail="文件仍被多个资源引用，不能直接删除")
    item.status = RemoteFileStatus.DELETE_PENDING.value
    queue_file_delete(session, item)
    session.commit()
    return {"accepted": True, "file_id": item.id, "status": item.status}


@router.get("/api/admin/files", response_model=RemoteFilePage)
def list_admin_files(
    user_id: str | None = Query(default=None, max_length=36), tool_slug: str | None = Query(default=None, max_length=64), role: str | None = Query(default=None, max_length=32), visibility: str | None = Query(default=None, pattern="^(user|admin|internal)$"), status_filter: str | None = Query(default=None, alias="status", max_length=24), search: str | None = Query(default=None, max_length=255), source_request_id: str | None = Query(default=None, max_length=36), task_id: str | None = Query(default=None, max_length=36), start: str | None = Query(default=None, max_length=40), end: str | None = Query(default=None, max_length=40), page: int = Query(default=1, ge=1), page_size: int = Query(default=30, ge=10, le=100), admin: User = Depends(require_admin), session: Session = Depends(get_db),
) -> RemoteFilePage:
    owner = session.get(User, user_id) if user_id else None
    if user_id and not owner:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _file_page(session, user=owner, admin=True, tool_slug=tool_slug, role=role, visibility=visibility, status_filter=status_filter, search=search, source_request_id=source_request_id, task_id=task_id, start=_parse_time(start, "开始"), end=_parse_time(end, "结束"), page=page, page_size=page_size)


@router.get("/api/admin/users/{user_id}/files", response_model=RemoteFilePage)
def list_admin_user_files(user_id: str, page: int = Query(default=1, ge=1), page_size: int = Query(default=30, ge=10, le=100), admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> RemoteFilePage:
    owner = session.get(User, user_id)
    if not owner:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _file_page(session, user=owner, admin=True, tool_slug=None, role=None, visibility=None, status_filter=None, search=None, source_request_id=None, task_id=None, start=None, end=None, page=page, page_size=page_size)


@router.get("/api/admin/tasks")
def list_admin_tasks(
    user_id: str | None = Query(default=None, max_length=36),
    tool_slug: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=32),
    task_id: str | None = Query(default=None, max_length=36),
    source_request_id: str | None = Query(default=None, max_length=36),
    start: str | None = Query(default=None, max_length=40),
    end: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=10, le=100),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    start_time, end_time = _parse_time(start, "开始"), _parse_time(end, "结束")
    if start_time and end_time and start_time > end_time:
        raise HTTPException(status_code=422, detail="开始时间不能晚于结束时间")
    conditions: list[object] = [ExternalResource.kind == ResourceKind.JOB.value]
    if user_id: conditions.append(ExternalResource.owner_user_id == user_id)
    if tool_slug: conditions.append(Tool.slug == tool_slug)
    if status: conditions.append(ExternalResource.status == status)
    if task_id: conditions.append(ExternalResource.id == task_id)
    if source_request_id: conditions.append(ExternalResource.source_request_id == source_request_id)
    if start_time: conditions.append(ExternalResource.created_at >= start_time)
    if end_time: conditions.append(ExternalResource.created_at <= end_time)
    total = int(session.scalar(select(func.count(ExternalResource.id)).join(Tool, Tool.id == ExternalResource.tool_id).where(*conditions)) or 0)
    rows = session.execute(select(ExternalResource, Tool, User).join(Tool, Tool.id == ExternalResource.tool_id).join(User, User.id == ExternalResource.owner_user_id).where(*conditions).order_by(ExternalResource.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for resource, tool, owner in rows:
        capabilities = task_capabilities(session, resource)
        items.append({
            "id": resource.id,
            "tool_id": tool.id,
            "tool_slug": tool.slug,
            "tool_name": tool.name,
            "owner_user_id": owner.id,
            "owner_username": owner.username,
            "upstream_id": resource.upstream_id,
            "source_request_id": resource.source_request_id,
            "status": resource.status,
            "capabilities": {
                **capabilities,
                "download_all": capabilities["download"],
            },
            "created_at": resource.created_at,
            "updated_at": resource.updated_at,
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size, "has_more": page * page_size < total}


@router.get("/api/admin/tasks/{task_id}", response_model=AdminTaskDetailView)
def get_admin_task(task_id: str, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> AdminTaskDetailView:
    task = session.scalar(select(ExternalResource).where(ExternalResource.id == task_id, ExternalResource.kind == ResourceKind.JOB.value))
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    tool, endpoint, adapter = task_storage_context(session, task)
    owner = session.get(User, task.owner_user_id)
    if not owner:
        raise HTTPException(status_code=500, detail="任务所有者记录缺失")
    terminal_statuses = {str(value).lower() for value in adapter.get("terminal_statuses", ["succeeded", "failed", "cancelled"]) if isinstance(value, str)}
    try:
        metadata = json.loads(task.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return AdminTaskDetailView(
        id=task.id,
        tool_slug=tool.slug,
        tool_name=tool.name,
        upstream_id=task.upstream_id,
        status=task.status,
        created_at=task.created_at,
        updated_at=task.updated_at,
        metadata=metadata if isinstance(metadata, dict) else {},
        capabilities=task_capabilities(session, task),
        is_terminal=task.status.lower() in terminal_statuses,
        upstream=None,
        owner_user_id=owner.id,
        owner_username=owner.username,
        source_request_id=task.source_request_id,
        storage_endpoint_revision=endpoint.revision,
    )


@router.get("/api/admin/tasks/{task_id}/capabilities/{capability}")
async def get_admin_task_capability(
    task_id: str,
    capability: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
    current_request_id: str = Depends(request_id),
) -> object:
    if capability not in {"status", "logs", "manifest", "artifacts"}:
        raise HTTPException(status_code=404, detail="未知任务能力")
    task = session.scalar(select(ExternalResource).where(ExternalResource.id == task_id, ExternalResource.kind == ResourceKind.JOB.value))
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return await fetch_task_capability_json(session, task, capability, request_id=current_request_id)


@router.get("/api/admin/file-download-events", response_model=FileDownloadEventPage)
def list_file_download_events(
    owner_user_id: str | None = Query(default=None, max_length=36),
    actor_user_id: str | None = Query(default=None, max_length=36),
    tool_slug: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=24),
    file_id: str | None = Query(default=None, max_length=36),
    download_request_id: str | None = Query(default=None, alias="request_id", max_length=36),
    start: str | None = Query(default=None, max_length=40),
    end: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=10, le=100),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> FileDownloadEventPage:
    start_time, end_time = _parse_time(start, "开始"), _parse_time(end, "结束")
    if start_time and end_time and start_time > end_time:
        raise HTTPException(status_code=422, detail="开始时间不能晚于结束时间")
    conditions: list[object] = []
    if owner_user_id: conditions.append(FileDownloadEvent.owner_user_id == owner_user_id)
    if actor_user_id: conditions.append(FileDownloadEvent.actor_user_id == actor_user_id)
    if tool_slug: conditions.append(Tool.slug == tool_slug.strip())
    if status: conditions.append(FileDownloadEvent.status == status.strip())
    if file_id: conditions.append(FileDownloadEvent.remote_file_id == file_id)
    if download_request_id: conditions.append(FileDownloadEvent.request_id == download_request_id.strip())
    if start_time: conditions.append(FileDownloadEvent.started_at >= start_time)
    if end_time: conditions.append(FileDownloadEvent.started_at <= end_time)
    actor_alias = aliased(User)
    owner_alias = aliased(User)
    base = (
        select(FileDownloadEvent, RemoteFile, Tool, actor_alias, owner_alias)
        .join(RemoteFile, RemoteFile.id == FileDownloadEvent.remote_file_id)
        .join(Tool, Tool.id == RemoteFile.tool_id)
        .join(actor_alias, actor_alias.id == FileDownloadEvent.actor_user_id)
        .join(owner_alias, owner_alias.id == FileDownloadEvent.owner_user_id)
        .where(*conditions)
    )
    count = (
        select(func.count(FileDownloadEvent.id))
        .select_from(FileDownloadEvent)
        .join(RemoteFile, RemoteFile.id == FileDownloadEvent.remote_file_id)
        .join(Tool, Tool.id == RemoteFile.tool_id)
        .where(*conditions)
    )
    total = int(session.scalar(count) or 0)
    rows = session.execute(base.order_by(FileDownloadEvent.started_at.desc(), FileDownloadEvent.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    items = [
        FileDownloadEventView(
            id=event.id,
            remote_file_id=item.id,
            file_name=item.file_name,
            tool_id=tool.id,
            tool_slug=tool.slug,
            actor_user_id=actor_row.id,
            actor_username=actor_row.username,
            owner_user_id=owner_row.id,
            owner_username=owner_row.username,
            request_id=event.request_id,
            is_admin=event.is_admin,
            status=event.status,
            bytes_sent=event.bytes_sent,
            error_code=event.error_code,
            started_at=event.started_at,
            completed_at=event.completed_at,
        )
        for event, item, tool, actor_row, owner_row in rows
    ]
    return FileDownloadEventPage(items=items, total=total, page=page, page_size=page_size, has_more=page * page_size < total)


@router.post("/api/admin/files/{file_id}/quarantine")
def quarantine_file(file_id: str, payload: FileAdminActionRequest, request: Request, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> dict[str, object]:
    validate_csrf(request)
    item = session.get(RemoteFile, file_id)
    if not item: raise HTTPException(status_code=404, detail="文件不存在")
    item.status = RemoteFileStatus.QUARANTINED.value
    item.last_error = (payload.reason or "管理员安全隔离")[:1_000]
    log_admin_event(session, admin_user_id=admin.id, action="remote_file_quarantined", target_type="remote_file", target_id=item.id, request_id=request_id(request), detail={"owner_user_id": item.owner_user_id, "reason": payload.reason})
    session.commit()
    return {"ok": True, "file_id": item.id, "status": item.status}


@router.post("/api/admin/tasks/{task_id}/sync", status_code=202)
def retry_task_file_sync(task_id: str, request: Request, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> dict[str, object]:
    validate_csrf(request)
    task = session.scalar(select(ExternalResource).where(ExternalResource.id == task_id, ExternalResource.kind == ResourceKind.JOB.value))
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    queue_resource_sync(session, task)
    log_admin_event(session, admin_user_id=admin.id, action="task_file_sync_retried", target_type="external_resource", target_id=task.id, request_id=request_id(request), detail={"owner_user_id": task.owner_user_id, "tool_id": task.tool_id})
    session.commit()
    return {"accepted": True, "task_id": task.id}


@router.post("/api/admin/files/{file_id}/retry-sync", status_code=202)
def retry_file_sync(file_id: str, payload: FileAdminActionRequest, request: Request, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> dict[str, object]:
    validate_csrf(request)
    item = session.get(RemoteFile, file_id)
    if not item:
        raise HTTPException(status_code=404, detail="文件不存在")
    task = session.get(ExternalResource, item.parent_resource_id) if item.parent_resource_id else None
    if not task or task.kind != ResourceKind.JOB.value:
        raise HTTPException(status_code=409, detail="该文件没有可重新同步的任务来源")
    item.status = RemoteFileStatus.PENDING.value
    item.last_error = ""
    queue_resource_sync(session, task)
    log_admin_event(session, admin_user_id=admin.id, action="remote_file_sync_retried", target_type="remote_file", target_id=item.id, request_id=request_id(request), detail={"task_id": task.id, "owner_user_id": item.owner_user_id, "reason": payload.reason})
    session.commit()
    return {"accepted": True, "file_id": item.id, "status": item.status}


@router.get("/api/admin/files/metrics")
def file_metrics(admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> dict[str, object]:
    now = utc_now()
    tools = session.scalars(select(Tool).order_by(Tool.name)).all()
    storage_watermarks = []
    for tool in tools:
        ratio = (tool.storage_free_bytes / tool.storage_total_bytes) if tool.storage_total_bytes and tool.storage_free_bytes is not None else None
        checked_at = _aware(tool.storage_checked_at)
        stale = bool(checked_at and checked_at < now - timedelta(seconds=get_settings().storage_watermark_max_age_seconds))
        level = "stale" if stale else "critical" if ratio is not None and ratio < 0.05 else "warning" if ratio is not None and ratio < 0.15 else "ok" if ratio is not None else "unknown"
        storage_watermarks.append({"tool_id": tool.id, "tool_slug": tool.slug, "tool_name": tool.name, "total_bytes": tool.storage_total_bytes, "free_bytes": tool.storage_free_bytes, "free_ratio": ratio, "checked_at": tool.storage_checked_at, "level": level})
    worker_required = bool(session.scalar(select(func.count(ToolIntegrationRoute.id)).where(ToolIntegrationRoute.is_enabled.is_(True), ToolIntegrationRoute.capability.in_(["status", "artifacts", "manifest", "file_download", "download", "delete", "head", "storage"]))))
    active_worker_cutoff = now - timedelta(seconds=30)
    worker_rows = session.scalars(select(FileWorkerHeartbeat).where(FileWorkerHeartbeat.last_seen_at >= active_worker_cutoff).order_by(FileWorkerHeartbeat.last_seen_at.desc())).all()
    return {
        "total_files": int(session.scalar(select(func.count(RemoteFile.id))) or 0),
        "ready_files": int(session.scalar(select(func.count(RemoteFile.id)).where(RemoteFile.status == "ready")) or 0),
        "logical_bytes": int(session.scalar(select(func.coalesce(func.sum(RemoteFile.size_bytes), 0)).where(RemoteFile.status.notin_(["deleted", "expired", "missing"]))) or 0),
        "sync_backlog": int(session.scalar(select(func.count(FileSyncJob.id)).where(FileSyncJob.status.in_(["pending", "retry"]))) or 0),
        "sync_failures": int(session.scalar(select(func.count(FileSyncJob.id)).where(FileSyncJob.status == "retry", FileSyncJob.attempts > 0)) or 0),
        "active_downloads": int(session.scalar(select(func.count(func.distinct(FileDownloadSlot.event_id))).where(FileDownloadSlot.expires_at > now)) or 0),
        "worker_required": worker_required,
        "worker_status": "healthy" if worker_rows else "offline" if worker_required else "not_required",
        "active_workers": len(worker_rows),
        "workers": [{"instance_id": item.instance_id, "process_id": item.process_id, "started_at": item.started_at, "last_seen_at": item.last_seen_at, "processed_jobs": item.processed_jobs, "last_error": item.last_error} for item in worker_rows],
        "storage_watermarks": storage_watermarks,
    }


def _mask_endpoint_url(value: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(value)
    host = parsed.hostname or ""
    if not host:
        return "未配置"
    masked_host = host if len(host) <= 6 else f"{host[:3]}***{host[-3:]}"
    return f"{parsed.scheme}://{masked_host}{f':{parsed.port}' if parsed.port else ''}"


@router.get("/api/admin/tools/{tool_id}/storage-endpoints", response_model=list[ToolStorageEndpointRevisionView])
def list_tool_storage_endpoints(tool_id: str, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> list[ToolStorageEndpointRevisionView]:
    if not session.get(Tool, tool_id):
        raise HTTPException(status_code=404, detail="工具不存在")
    rows = session.scalars(select(ToolStorageEndpointRevision).where(ToolStorageEndpointRevision.tool_id == tool_id).order_by(ToolStorageEndpointRevision.revision.desc())).all()
    result: list[ToolStorageEndpointRevisionView] = []
    for row in rows:
        file_count = int(session.scalar(select(func.count(RemoteFile.id)).where(RemoteFile.storage_endpoint_revision_id == row.id)) or 0)
        task_count = int(session.scalar(select(func.count(ExternalResource.id)).where(ExternalResource.storage_endpoint_revision_id == row.id)) or 0)
        pending_delete_count = int(session.scalar(select(func.count(RemoteFile.id)).where(RemoteFile.storage_endpoint_revision_id == row.id, RemoteFile.status == RemoteFileStatus.DELETE_PENDING.value)) or 0)
        result.append(ToolStorageEndpointRevisionView(id=row.id, tool_id=row.tool_id, revision=row.revision, base_url_masked=_mask_endpoint_url(row.base_url), auth_type=row.auth_type, token_configured=bool(row.token_ciphertext), verify_tls=row.verify_tls, is_active=row.is_active, file_count=file_count, task_count=task_count, pending_delete_count=pending_delete_count, created_at=row.created_at, retired_at=row.retired_at))
    return result


@router.get("/api/admin/tools/{tool_id}/integration-routes", response_model=list[ToolIntegrationRouteView])
def list_tool_integration_routes(tool_id: str, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> list[ToolIntegrationRouteView]:
    if not session.get(Tool, tool_id):
        raise HTTPException(status_code=404, detail="工具不存在")
    rows = session.scalars(select(ToolIntegrationRoute).where(ToolIntegrationRoute.tool_id == tool_id).order_by(ToolIntegrationRoute.capability)).all()
    return [ToolIntegrationRouteView(id=row.id, tool_id=row.tool_id, endpoint_id=row.endpoint_id, capability=row.capability, method=row.method, path_template=row.path_template, config=json.loads(row.config_json or "{}"), is_enabled=row.is_enabled, created_at=row.created_at, updated_at=row.updated_at) for row in rows]


@router.delete("/api/admin/tools/{tool_id}/storage-endpoints/{endpoint_id}", status_code=204)
def remove_retired_storage_endpoint(tool_id: str, endpoint_id: str, payload: FileAdminActionRequest, request: Request, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> Response:
    validate_csrf(request)
    endpoint = session.scalar(select(ToolStorageEndpointRevision).where(ToolStorageEndpointRevision.id == endpoint_id, ToolStorageEndpointRevision.tool_id == tool_id))
    if not endpoint:
        raise HTTPException(status_code=404, detail="历史存储端点不存在")
    if endpoint.is_active:
        raise HTTPException(status_code=409, detail="当前活动存储端点不能移除")
    file_count = int(session.scalar(select(func.count(RemoteFile.id)).where(RemoteFile.storage_endpoint_revision_id == endpoint.id)) or 0)
    task_count = int(session.scalar(select(func.count(ExternalResource.id)).where(ExternalResource.storage_endpoint_revision_id == endpoint.id)) or 0)
    if file_count or task_count:
        raise HTTPException(status_code=409, detail="该端点仍有关联任务、文件或未完成清理的墓碑，不能移除")
    log_admin_event(session, admin_user_id=admin.id, action="tool_storage_endpoint_removed", target_type="tool_storage_endpoint", target_id=endpoint.id, request_id=request_id(request), detail={"tool_id": tool_id, "revision": endpoint.revision, "reason": payload.reason})
    session.delete(endpoint)
    session.commit()
    return Response(status_code=204)


@router.put("/api/admin/tools/{tool_id}/integration-routes/{capability}", response_model=ToolIntegrationRouteView)
def update_tool_integration_route(tool_id: str, capability: str, payload: ToolIntegrationRouteUpdateRequest, request: Request, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> ToolIntegrationRouteView:
    validate_csrf(request)
    if capability != payload.capability:
        raise HTTPException(status_code=422, detail="路径能力与请求能力不一致")
    tool = session.get(Tool, tool_id)
    endpoint = session.get(ApiEndpoint, payload.endpoint_id)
    if not tool or not endpoint or endpoint.tool_id != tool_id:
        raise HTTPException(status_code=404, detail="工具或同工具接口不存在")
    if endpoint.method not in {"GET", "POST", "DELETE", "HEAD"}:
        raise HTTPException(status_code=422, detail="该接口方法不能用于内部文件能力")
    if capability in {"status", "manifest", "artifacts", "file_download", "bundle_download", "storage_watermark"} and endpoint.method not in {"GET", "HEAD"}:
        raise HTTPException(status_code=422, detail="读取类能力只能引用 GET 或 HEAD 接口")
    if capability in {"file_delete", "bundle_delete"} and endpoint.method not in {"DELETE", "POST"}:
        raise HTTPException(status_code=422, detail="删除能力只能引用 DELETE 或 POST 接口")
    for key, value in payload.config.items():
        if key.endswith("selector") and isinstance(value, str):
            try:
                from jsonpath_ng import parse as parse_jsonpath
                parse_jsonpath(value)
            except Exception as error:
                raise HTTPException(status_code=422, detail=f"{key} 不是有效 JSONPath") from error
    row = session.scalar(select(ToolIntegrationRoute).where(ToolIntegrationRoute.tool_id == tool_id, ToolIntegrationRoute.capability == capability))
    if not row:
        row = ToolIntegrationRoute(tool_id=tool_id, capability=capability)
        session.add(row)
    row.endpoint_id = endpoint.id
    row.method = endpoint.method
    row.path_template = endpoint.upstream_path
    row.config_json = json.dumps(payload.config, ensure_ascii=False, separators=(",", ":"))
    row.is_enabled = payload.is_enabled
    log_admin_event(session, admin_user_id=admin.id, action="tool_integration_route_updated", target_type="tool", target_id=tool_id, request_id=request_id(request), detail={"capability": capability, "endpoint_id": endpoint.id, "enabled": payload.is_enabled, "reason": payload.reason})
    session.commit()
    session.refresh(row)
    return ToolIntegrationRouteView(id=row.id, tool_id=row.tool_id, endpoint_id=row.endpoint_id, capability=row.capability, method=row.method, path_template=row.path_template, config=json.loads(row.config_json or "{}"), is_enabled=row.is_enabled, created_at=row.created_at, updated_at=row.updated_at)


@router.patch("/api/admin/tools/{tool_id}/file-access")
def update_tool_file_access(tool_id: str, payload: ToolFileAccessUpdateRequest, request: Request, admin: User = Depends(require_admin), session: Session = Depends(get_db)) -> dict[str, object]:
    validate_csrf(request)
    tool = session.get(Tool, tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")
    previous = {"file_access_enabled": tool.file_access_enabled, "file_quarantined": tool.file_quarantined}
    tool.file_access_enabled = payload.file_access_enabled
    tool.file_quarantined = payload.file_quarantined
    log_admin_event(session, admin_user_id=admin.id, action="tool_file_access_updated", target_type="tool", target_id=tool.id, request_id=request_id(request), detail={"previous": previous, "file_access_enabled": tool.file_access_enabled, "file_quarantined": tool.file_quarantined, "reason": payload.reason})
    session.commit()
    return {"tool_id": tool.id, "file_access_enabled": tool.file_access_enabled, "file_quarantined": tool.file_quarantined}
