"""平台 API Key 鉴权后的受控 Gateway 路由。"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ApiKey, ApprovalStatus, KeyStatus, Tool, ToolStatus, ToolUpstream, User
from ..services.rate_limit import consume_fixed_window, consume_user_fixed_window
from ..services.security import hash_api_key
from ..services.gateway_proxy import api_key_is_expired, proxy_gateway_request, record_gateway_request
from .deps import get_user_roles, request_id


router = APIRouter(tags=["Gateway"])


def _gateway_detail(code: str, message: str, details: dict[str, object] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"code": code, "message": message}
    if details:
        payload["details"] = details
    return payload


def _status_error_code(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        413: "PAYLOAD_TOO_LARGE",
        415: "UNSUPPORTED_MEDIA_TYPE",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        502: "UPSTREAM_UNAVAILABLE",
        503: "SERVICE_UNAVAILABLE",
        504: "UPSTREAM_TIMEOUT",
    }.get(status_code, "REQUEST_FAILED")


def _error_code_from_exception(error: HTTPException) -> str:
    if isinstance(error.detail, dict):
        return str(error.detail.get("code") or _status_error_code(error.status_code))
    return _status_error_code(error.status_code)


def _request_bytes(request: Request) -> int:
    try:
        return max(int(request.headers.get("content-length", "0") or "0"), 0)
    except ValueError:
        return 0


def _record_entry_failure(
    session: Session,
    *,
    current_request_id: str,
    request: Request,
    tool_slug: str,
    path: str,
    status_code: int,
    error_code: str,
    user_id: str | None = None,
    api_key_id: str | None = None,
    tool_id: str | None = None,
    started: float,
) -> None:
    record_gateway_request(
        session,
        request_id=current_request_id,
        user_id=user_id,
        api_key_id=api_key_id,
        tool_id=tool_id,
        tool_slug=tool_slug,
        method=request.method,
        path=f"/{path}",
        status_code=status_code,
        duration_ms=int((time.perf_counter() - started) * 1000),
        request_bytes=_request_bytes(request),
        error_code=error_code,
    )
    session.commit()


def lookup_api_key(session: Session, secret: str | None) -> tuple[ApiKey | None, User | None]:
    if not secret or not secret.startswith("agw_"):
        return None, None
    api_key = session.scalar(select(ApiKey).where(ApiKey.secret_hash == hash_api_key(secret)))
    user = session.get(User, api_key.user_id) if api_key else None
    return api_key, user


def authenticate_api_key(session: Session, secret: str | None) -> tuple[ApiKey, User]:
    if not secret or not secret.startswith("agw_"):
        raise HTTPException(status_code=401, detail=_gateway_detail("MISSING_API_KEY", "缺少有效的平台 API Key"))
    api_key, user = lookup_api_key(session, secret)
    if not api_key or api_key.status != KeyStatus.ACTIVE.value or api_key_is_expired(api_key):
        raise HTTPException(status_code=401, detail=_gateway_detail("API_KEY_UNAVAILABLE", "API Key 无效、禁用或已过期"))
    if not user or user.approval_status != ApprovalStatus.APPROVED.value or not user.is_active or user.must_change_password:
        raise HTTPException(status_code=401, detail=_gateway_detail("API_KEY_ACCOUNT_UNAVAILABLE", "API Key 所属账号不可调用"))
    api_key.last_used_at = datetime.now(timezone.utc)
    return api_key, user


def api_key_allows_tool(api_key: ApiKey, tool_slug: str) -> bool:
    scopes = {item.strip() for item in api_key.scopes.split(",") if item.strip()}
    return (
        "*" in scopes
        or f"tool:{tool_slug}:*" in scopes
        or f"{tool_slug}:*" in scopes
        or (tool_slug == "tomodd" and "tomodd:*" in scopes)
    )


@router.api_route("/gateway/{tool_slug}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def tool_gateway(
    tool_slug: str,
    path: str,
    request: Request,
    x_api_key: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    started = time.perf_counter()
    current_request_id = request_id(request)
    try:
        api_key, user = authenticate_api_key(session, x_api_key)
    except HTTPException as error:
        candidate_key, candidate_user = lookup_api_key(session, x_api_key)
        _record_entry_failure(
            session,
            current_request_id=current_request_id,
            request=request,
            tool_slug=tool_slug,
            path=path,
            status_code=error.status_code,
            error_code=_error_code_from_exception(error),
            user_id=candidate_user.id if candidate_user else None,
            api_key_id=candidate_key.id if candidate_key else None,
            started=started,
        )
        raise
    tool = session.scalar(select(Tool).where(Tool.slug == tool_slug, Tool.is_enabled.is_(True)))
    if not tool or tool.status == ToolStatus.DISABLED.value:
        _record_entry_failure(
            session,
            current_request_id=current_request_id,
            request=request,
            tool_slug=tool_slug,
            path=path,
            status_code=503,
            error_code="TOOL_UNAVAILABLE",
            user_id=user.id,
            api_key_id=api_key.id,
            tool_id=tool.id if tool else None,
            started=started,
        )
        raise HTTPException(status_code=503, detail=_gateway_detail("TOOL_UNAVAILABLE", "工具尚未配置或已停用"))
    upstream = session.scalar(select(ToolUpstream).where(ToolUpstream.tool_id == tool.id))
    if not upstream:
        _record_entry_failure(
            session,
            current_request_id=current_request_id,
            request=request,
            tool_slug=tool.slug,
            path=path,
            status_code=503,
            error_code="UPSTREAM_CONFIG_UNAVAILABLE",
            user_id=user.id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            started=started,
        )
        raise HTTPException(status_code=503, detail=_gateway_detail("UPSTREAM_CONFIG_UNAVAILABLE", "工具上游配置不可用"))
    if not api_key_allows_tool(api_key, tool_slug):
        _record_entry_failure(
            session,
            current_request_id=current_request_id,
            request=request,
            tool_slug=tool.slug,
            path=path,
            status_code=403,
            error_code="API_KEY_SCOPE_DENIED",
            user_id=user.id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            started=started,
        )
        raise HTTPException(status_code=403, detail=_gateway_detail("API_KEY_SCOPE_DENIED", "API Key 不具备该工具调用权限"))
    try:
        consume_user_fixed_window(session, user.id, user.rate_limit_per_minute)
    except HTTPException as error:
        _record_entry_failure(
            session,
            current_request_id=current_request_id,
            request=request,
            tool_slug=tool.slug,
            path=path,
            status_code=429,
            error_code="USER_RATE_LIMITED",
            user_id=user.id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            started=started,
        )
        raise HTTPException(status_code=429, detail=_gateway_detail("USER_RATE_LIMITED", "用户已超过每分钟请求上限")) from error
    try:
        consume_fixed_window(session, api_key.id, tool.id, upstream.rate_limit_per_minute)
    except HTTPException as error:
        _record_entry_failure(
            session,
            current_request_id=current_request_id,
            request=request,
            tool_slug=tool.slug,
            path=path,
            status_code=429,
            error_code="RATE_LIMITED",
            user_id=user.id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            started=started,
        )
        raise HTTPException(status_code=429, detail=_gateway_detail("RATE_LIMITED", "API Key 已超过每分钟调用配额")) from error
    return await proxy_gateway_request(
        request=request,
        session=session,
        tool=tool,
        upstream=upstream,
        api_key=api_key,
        user_id=user.id,
        is_admin="admin" in get_user_roles(session, user.id),
        request_id=current_request_id,
        upstream_base_url=upstream.base_url,
    )
