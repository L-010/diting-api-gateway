"""统一 Gateway 上游代理、流量策略和审计。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import ApiEndpoint, ApiKey, GatewayRequest, PublishedRoute, Tool, ToolUpstream, ToolUpstreamInstance, User
from .observability import log_gateway_request_event
from .remote_files import capture_response_files, ensure_write_capacity, link_files_to_request
from .resource_policies import execute_post_actions, execute_pre_checks, filter_owned_list
from .security import decrypt_secret
from .idempotency import (
    claim_idempotency_record,
    mark_idempotency_unavailable,
    replay_idempotency_result,
    request_fingerprint,
    save_idempotency_result,
    wait_for_idempotency_result,
)


TEMPLATE_PARAM_PATTERN = re.compile(r"\{([^/{}]+)\}")
TEXT_BODY_LIMIT = 8_192
SENSITIVE_QUERY_KEYS = ("authorization", "api_key", "apikey", "x-api-key", "token", "secret", "password", "bearer", "cookie")
INSTANCE_5XX_FAILURE_THRESHOLD = 3


def mask_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or "隐藏"
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{hostname}{port}"


def validate_upstream_url(url: str) -> str:
    parsed = urlparse(url)
    settings = get_settings()
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("上游地址必须是 http 或 https URL")
    if parsed.hostname.lower() not in settings.upstream_hosts:
        raise ValueError("上游地址不在允许主机白名单中")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("上游地址必须是纯主机根地址")
    return url.rstrip("/")


def route_match_params(template: str, path: str) -> dict[str, str] | None:
    """匹配 OpenAPI 风格路径模板，并提取实际路径参数。"""
    pattern_parts: list[str] = ["^"]
    cursor = 0
    for match in TEMPLATE_PARAM_PATTERN.finditer(template):
        pattern_parts.append(re.escape(template[cursor : match.start()]))
        pattern_parts.append(f"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    pattern_parts.append(re.escape(template[cursor:]))
    pattern_parts.append("$")
    matched = re.match("".join(pattern_parts), path)
    return matched.groupdict() if matched else None


def route_matches(template: str, path: str) -> bool:
    return route_match_params(template, path) is not None


def render_upstream_path(template: str, params: dict[str, str]) -> str:
    """把已发布路由模板中的路径参数安全回填到上游路径。"""
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise HTTPException(status_code=404, detail="接口路径参数不匹配")
        return quote(params[name], safe="")

    return TEMPLATE_PARAM_PATTERN.sub(replace, template)


def get_published_route(session: Session, tool_id: str, method: str, path: str) -> tuple[PublishedRoute, dict[str, str]] | None:
    candidates = session.scalars(
        select(PublishedRoute)
        .join(ApiEndpoint, ApiEndpoint.id == PublishedRoute.endpoint_id)
        .where(
            PublishedRoute.tool_id == tool_id,
            PublishedRoute.method == method.upper(),
            PublishedRoute.is_enabled.is_(True),
            ApiEndpoint.status == "published",
        )
    ).all()
    # 路径匹配必须优先选择更具体的静态路由，避免参数路由吞掉同名静态路径。
    candidates.sort(key=lambda item: (len(TEMPLATE_PARAM_PATTERN.findall(item.gateway_path)), -len(item.gateway_path)))
    for route in candidates:
        params = route_match_params(route.gateway_path, path)
        if params is not None:
            return route, params
    return None


def record_gateway_request(
    session: Session,
    *,
    request_id: str,
    user_id: str | None,
    api_key_id: str | None,
    tool_id: str | None,
    method: str,
    path: str,
    status_code: int,
    duration_ms: int,
    endpoint_id: str | None = None,
    published_route_id: str | None = None,
    upstream_instance: ToolUpstreamInstance | None = None,
    tool_slug: str = "",
    upstream_path: str = "",
    redacted_query_json: str = "{}",
    upstream_status_code: int | None = None,
    request_bytes: int = 0,
    response_bytes: int = 0,
    error_code: str | None = None,
    client_ip_hash: str = "",
    user_agent_hash: str = "",
) -> None:
    row = GatewayRequest(
        id=request_id,
        user_id=user_id,
        api_key_id=api_key_id,
        tool_id=tool_id,
        endpoint_id=endpoint_id,
        published_route_id=published_route_id,
        upstream_instance_id=upstream_instance.id if upstream_instance else None,
        upstream_environment=upstream_instance.environment_name if upstream_instance else "",
        upstream_instance_name=upstream_instance.instance_name if upstream_instance else "",
        tool_slug=tool_slug,
        method=method,
        path=path,
        upstream_path=upstream_path,
        redacted_query_json=redacted_query_json,
        status_code=status_code,
        upstream_status_code=upstream_status_code,
        duration_ms=duration_ms,
        request_bytes=request_bytes,
        response_bytes=response_bytes,
        error_code=error_code,
        client_ip_hash=client_ip_hash,
        user_agent_hash=user_agent_hash,
    )
    session.add(row)
    log_gateway_request_event(session, row)


async def bounded_request_stream(request: Request, limit: int) -> AsyncIterator[bytes]:
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise HTTPException(status_code=413, detail="上传内容超过平台限制")
        yield chunk


async def read_bounded_request_body(request: Request, limit: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=413, detail="上传内容超过平台限制")
    return bytes(body)


def media_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _route_allowed_content_types(session: Session, route: PublishedRoute) -> set[str]:
    allowed = {media_type(item) for item in _json_list(route.allowed_content_types_json) if media_type(item)}
    if allowed:
        return allowed
    endpoint = session.get(ApiEndpoint, route.endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="接口发布记录不完整")
    return {media_type(item) for item in json.loads(endpoint.content_types or "[]") if isinstance(item, str) and media_type(item)}


def _route_request_limit(route: PublishedRoute) -> int:
    return route.max_request_bytes or get_settings().max_upload_bytes


def _route_response_limit(route: PublishedRoute) -> int:
    return route.max_response_bytes or get_settings().max_download_bytes


def _route_timeout_seconds(route: PublishedRoute, upstream: ToolUpstream) -> int:
    return route.request_timeout_seconds or upstream.request_timeout_seconds


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


def ensure_content_type_allowed(session: Session, route: PublishedRoute, request: Request) -> None:
    allowed = _route_allowed_content_types(session, route)
    if not allowed:
        return
    received = media_type(request.headers.get("content-type"))
    if received not in allowed:
        raise HTTPException(status_code=415, detail=f"Content-Type 不受支持，仅允许: {', '.join(sorted(allowed))}")


def ensure_declared_upload_size(request: Request, route: PublishedRoute) -> None:
    value = request.headers.get("content-length")
    if not value:
        return
    try:
        size = int(value)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Content-Length 不合法") from error
    if size < 0:
        raise HTTPException(status_code=400, detail="Content-Length 不合法")
    if size > _route_request_limit(route):
        raise HTTPException(status_code=413, detail="上传内容超过平台限制")


def ensure_idempotency_policy(route: PublishedRoute, request: Request) -> str | None:
    if not route.require_idempotency_key or request.method.upper() not in {"POST", "DELETE"}:
        return None
    value = (request.headers.get("idempotency-key") or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="该接口要求提供 Idempotency-Key 请求头")
    if len(value) > 255 or any(ord(char) < 0x21 or ord(char) > 0x7E for char in value):
        raise HTTPException(status_code=422, detail="Idempotency-Key 格式不合法")
    if route.allow_stream_download:
        raise HTTPException(status_code=422, detail="要求幂等的接口不允许流式响应")
    return value


def mark_gateway_stream_failure(session: Session, request_id: str, *, status_code: int, error_code: str, response_bytes: int) -> None:
    row = session.get(GatewayRequest, request_id)
    if not row:
        return
    row.status_code = status_code
    row.error_code = error_code
    row.response_bytes = response_bytes
    session.commit()


def safe_download_disposition(value: str | None) -> str:
    """移除路径、控制字符与不可信扩展信息，只回传安全下载文件名。"""
    fallback = "download.bin"
    if not value:
        return f"attachment; filename={fallback}"
    value = value.replace("\r", "\n").split("\n", 1)[0]
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^;\"]+)", value, flags=re.IGNORECASE)
    if not match:
        return f"attachment; filename={fallback}"
    filename = unquote(match.group(1)).replace("\\", "/").split("/")[-1]
    filename = re.sub(r"[\x00-\x1f\x7f\"']", "", filename).strip(". ")[:180]
    if not filename:
        filename = fallback
    return f"attachment; filename*=UTF-8''{quote(filename)}"


def gateway_error_payload(code: str, message: str, request_id: str, details: dict[str, object] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"code": code, "message": message, "request_id": request_id}
    if details:
        payload["details"] = details
    return payload


def _safe_int(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(parsed, 0)


def _hash_text(value: str | None) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _request_client_ip(request: Request) -> str:
    client = getattr(request, "client", None)
    host = getattr(client, "host", "") if client else ""
    return str(host or "")


def _redacted_query_json(request: Request) -> str:
    items_method = getattr(request.query_params, "multi_items", None)
    pairs = items_method() if callable(items_method) else getattr(request.query_params, "items", lambda: [])()
    safe_items: list[dict[str, str]] = []
    for key, value in list(pairs)[:100]:
        normalized = str(key).lower().replace("-", "_")
        safe_items.append(
            {
                "key": str(key)[:128],
                "value": "***" if any(part in normalized for part in SENSITIVE_QUERY_KEYS) else str(value)[:200],
            }
        )
    return json.dumps(safe_items, ensure_ascii=False, separators=(",", ":"))


def _error_code_for_status(status_code: int) -> str:
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
        504: "UPSTREAM_TIMEOUT",
    }.get(status_code, "REQUEST_FAILED")


def record_gateway_failure(
    session: Session,
    *,
    request_id: str,
    user_id: str,
    api_key_id: str,
    tool_id: str,
    method: str,
    path: str,
    started: float,
    status_code: int,
    error_code: str,
    tool_slug: str = "",
    request: Request | None = None,
    route: PublishedRoute | None = None,
    upstream_instance: ToolUpstreamInstance | None = None,
    upstream_path: str = "",
    upstream_status_code: int | None = None,
    request_bytes: int = 0,
    response_bytes: int = 0,
) -> None:
    declared_request_bytes = _safe_int(request.headers.get("content-length")) if request else request_bytes
    record_gateway_request(
        session,
        request_id=request_id,
        user_id=user_id,
        api_key_id=api_key_id,
        tool_id=tool_id,
        endpoint_id=route.endpoint_id if route else None,
        published_route_id=route.id if route else None,
        upstream_instance=upstream_instance,
        tool_slug=tool_slug,
        method=method,
        path=path,
        upstream_path=upstream_path or (route.upstream_path if route else ""),
        redacted_query_json=_redacted_query_json(request) if request else "{}",
        status_code=status_code,
        upstream_status_code=upstream_status_code,
        duration_ms=int((time.perf_counter() - started) * 1000),
        request_bytes=request_bytes or declared_request_bytes,
        response_bytes=response_bytes,
        error_code=error_code,
        client_ip_hash=_hash_text(_request_client_ip(request)) if request else "",
        user_agent_hash=_hash_text(request.headers.get("user-agent")) if request else "",
    )
    session.commit()


def raise_gateway_failure(
    session: Session,
    *,
    request_id: str,
    user_id: str,
    api_key_id: str,
    tool_id: str,
    method: str,
    path: str,
    started: float,
    status_code: int,
    detail: str,
    error_code: str | None = None,
    tool_slug: str = "",
    request: Request | None = None,
    route: PublishedRoute | None = None,
    upstream_instance: ToolUpstreamInstance | None = None,
    upstream_path: str = "",
) -> None:
    code = error_code or _error_code_for_status(status_code)
    record_gateway_failure(
        session,
        request_id=request_id,
        user_id=user_id,
        api_key_id=api_key_id,
        tool_id=tool_id,
        tool_slug=tool_slug,
        method=method,
        path=path,
        started=started,
        status_code=status_code,
        error_code=code,
        request=request,
        route=route,
        upstream_instance=upstream_instance,
        upstream_path=upstream_path,
    )
    raise HTTPException(status_code=status_code, detail=detail)


def upstream_headers(upstream: ToolUpstream, request_id: str, accept: str = "application/json", content_type: str | None = None) -> dict[str, str]:
    headers = {"x-request-id": request_id, "accept": accept}
    if content_type:
        headers["content-type"] = content_type
    token = decrypt_secret(upstream.token_ciphertext)
    if token and upstream.auth_type == "bearer":
        headers["authorization"] = f"Bearer {token}"
    elif token and upstream.auth_type == "header_api_key":
        header_name = upstream.token_label or "X-Upstream-API-Key"
        headers[header_name] = token
    return headers


def update_gateway_upstream_instance_state(
    instance: ToolUpstreamInstance | None,
    *,
    upstream_status_code: int | None,
    error_code: str | None = None,
    network_failure: bool = False,
) -> None:
    """兼容历史调用记录字段：存在实例对象时同步其健康状态。"""
    if not instance:
        return
    checked_at = datetime.now(timezone.utc)
    instance.last_health_status_code = upstream_status_code
    instance.last_health_checked_at = checked_at
    if network_failure or error_code in {"UPSTREAM_UNAVAILABLE", "UPSTREAM_TIMEOUT"}:
        instance.failure_count += 1
        instance.health_status = "unavailable"
        instance.last_error = error_code or "NETWORK_FAILURE"
    elif upstream_status_code is not None and upstream_status_code >= 500:
        instance.failure_count += 1
        instance.health_status = "unavailable" if instance.failure_count >= INSTANCE_5XX_FAILURE_THRESHOLD else "degraded"
        instance.last_error = f"UPSTREAM_{upstream_status_code}"
    else:
        instance.failure_count = 0
        instance.health_status = "ok"
        instance.last_error = ""
    instance.updated_at = checked_at


async def proxy_gateway_request(
    *,
    request: Request,
    session: Session,
    tool: Tool,
    upstream: ToolUpstream,
    api_key: ApiKey,
    user_id: str,
    is_admin: bool = False,
    request_id: str,
    upstream_base_url: str | None = None,
    upstream_instance: ToolUpstreamInstance | None = None,
) -> JSONResponse | StreamingResponse:
    """按已发布策略执行鉴权、资源隔离并转发上游请求。"""
    started = time.perf_counter()
    gateway_path = "/" + request.path_params["path"]
    route_match = get_published_route(session, tool.id, request.method, gateway_path)
    if not route_match:
        raise_gateway_failure(
            session,
            request_id=request_id,
            user_id=user_id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            tool_slug=tool.slug,
            method=request.method,
            path=gateway_path,
            started=started,
            status_code=404,
            detail="接口未发布或不存在",
            request=request,
        )
    route, path_params = route_match
    rendered_upstream_path = render_upstream_path(route.upstream_path, path_params)
    declared_request_bytes = _safe_int(request.headers.get("content-length"))
    buffered_body: bytes | None = None
    idempotency_key: str | None = None
    idempotency_record = None
    try:
        if route.access_mode == "admin_only" and not is_admin:
            raise HTTPException(status_code=403, detail="该接口仅允许管理员调用")
        ensure_content_type_allowed(session, route, request)
        ensure_declared_upload_size(request, route)
        idempotency_key = ensure_idempotency_policy(route, request)
        if route.storage_action in {"upload", "upload_file", "create_task", "produce_files"}:
            owner = session.get(User, user_id)
            if not owner:
                raise HTTPException(status_code=401, detail="用户不存在")
            try:
                expected_bytes = int(request.headers.get("content-length", "0") or 0)
            except ValueError:
                expected_bytes = 0
            ensure_write_capacity(session, owner, tool, expected_bytes=expected_bytes if route.storage_action in {"upload", "upload_file"} else 0)
        request_content_type = request.headers.get("content-type")
        request_media_type = media_type(request_content_type)
        upload_limit = _route_request_limit(route)
        if idempotency_key and request.method.upper() in {"POST", "DELETE"}:
            # 幂等指纹必须覆盖完整请求体，不能对允许流式上传的请求只记录长度。
            buffered_body = await read_bounded_request_body(request, upload_limit)
        elif request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and request_media_type == "application/json":
            buffered_body = await read_bounded_request_body(request, upload_limit)
        elif request.method.upper() not in {"GET", "HEAD"} and not route.allow_stream_upload:
            buffered_body = await read_bounded_request_body(request, upload_limit)
        request_json: object = {}
        if buffered_body is not None and request_media_type == "application/json":
            try:
                request_json = json.loads(buffered_body or b"{}")
            except json.JSONDecodeError as error:
                raise HTTPException(status_code=422, detail="JSON 请求体不合法") from error
        execute_pre_checks(
            session,
            route=route,
            user_id=user_id,
            path_params=path_params,
            query_params=request.query_params,
            request_json=request_json,
        )
        if idempotency_key:
            fingerprint = request_fingerprint(request, route, buffered_body or b"")
            claim = claim_idempotency_record(
                session,
                user_id=user_id,
                api_key=api_key,
                route=route,
                raw_key=idempotency_key,
                fingerprint=fingerprint,
            )
            idempotency_record = claim.record
            if not claim.owner:
                await wait_for_idempotency_result(session, idempotency_record)
                return replay_idempotency_result(idempotency_record)
            # 先提交 processing 状态，其他并发请求才能观察到唯一占用者。
            session.commit()
    except HTTPException as error:
        record_gateway_failure(
            session,
            request_id=request_id,
            user_id=user_id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            tool_slug=tool.slug,
            method=request.method,
            path=gateway_path,
            started=started,
            status_code=error.status_code,
            error_code=_error_code_for_status(error.status_code),
            request=request,
            route=route,
            upstream_path=rendered_upstream_path,
            request_bytes=len(buffered_body) if buffered_body is not None else declared_request_bytes,
        )
        raise

    headers = upstream_headers(
        upstream,
        request_id,
        accept=request.headers.get("accept", "application/json"),
        content_type=request.headers.get("content-type"),
    )
    if idempotency_key:
        headers["idempotency-key"] = idempotency_key
    target_url = f"{(upstream_base_url or upstream.base_url).rstrip('/')}{rendered_upstream_path}"
    content: bytes | AsyncIterator[bytes] | None
    if request.method.upper() in {"GET", "HEAD"}:
        content = None
    elif buffered_body is not None:
        content = buffered_body
    else:
        content = bounded_request_stream(request, _route_request_limit(route))
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(_route_timeout_seconds(route, upstream), connect=upstream.connect_timeout_seconds),
        follow_redirects=False,
        verify=upstream.verify_tls,
    )
    can_replay_request = content is None or isinstance(content, bytes)
    retry_attempts = upstream.retry_count if route.allow_retry and can_replay_request else 0
    try:
        for attempt in range(retry_attempts + 1):
            upstream_request = client.build_request(
                request.method,
                target_url,
                params=request.query_params,
                headers=headers,
                content=content,
            )
            try:
                response = await client.send(upstream_request, stream=True)
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt >= retry_attempts:
                    raise
                await asyncio.sleep(min(0.1 * (2**attempt), 1.0))
                continue
            if response.status_code not in {502, 503, 504} or attempt >= retry_attempts:
                break
            await response.aclose()
            await asyncio.sleep(min(0.1 * (2**attempt), 1.0))
    except httpx.ConnectError as error:
        await client.aclose()
        update_gateway_upstream_instance_state(upstream_instance, upstream_status_code=None, error_code="UPSTREAM_UNAVAILABLE", network_failure=True)
        record_gateway_failure(
            session,
            request_id=request_id,
            user_id=user_id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            tool_slug=tool.slug,
            method=request.method,
            path=gateway_path,
            started=started,
            status_code=502,
            error_code="UPSTREAM_UNAVAILABLE",
            request=request,
            route=route,
            upstream_instance=upstream_instance,
            upstream_path=rendered_upstream_path,
            request_bytes=len(buffered_body) if buffered_body is not None else declared_request_bytes,
        )
        if idempotency_record:
            payload = gateway_error_payload("UPSTREAM_UNAVAILABLE", "专业服务不可达", request_id)
            save_idempotency_result(
                session,
                idempotency_record,
                status_code=502,
                body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
            session.commit()
        raise HTTPException(status_code=502, detail="专业服务不可达") from error
    except httpx.TimeoutException as error:
        await client.aclose()
        update_gateway_upstream_instance_state(upstream_instance, upstream_status_code=None, error_code="UPSTREAM_TIMEOUT", network_failure=True)
        record_gateway_failure(
            session,
            request_id=request_id,
            user_id=user_id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            tool_slug=tool.slug,
            method=request.method,
            path=gateway_path,
            started=started,
            status_code=504,
            error_code="UPSTREAM_TIMEOUT",
            request=request,
            route=route,
            upstream_instance=upstream_instance,
            upstream_path=rendered_upstream_path,
            request_bytes=len(buffered_body) if buffered_body is not None else declared_request_bytes,
        )
        if idempotency_record:
            payload = gateway_error_payload("UPSTREAM_TIMEOUT", "专业服务响应超时", request_id)
            save_idempotency_result(
                session,
                idempotency_record,
                status_code=504,
                body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
            session.commit()
        raise HTTPException(status_code=504, detail="专业服务响应超时") from error

    duration_ms = int((time.perf_counter() - started) * 1000)
    content_type = response.headers.get("content-type", "")
    content_length = response.headers.get("content-length")
    try:
        response_too_large = bool(content_length and int(content_length) > _route_response_limit(route))
    except ValueError:
        response_too_large = False
    if response_too_large:
        await response.aclose()
        await client.aclose()
        update_gateway_upstream_instance_state(upstream_instance, upstream_status_code=response.status_code)
        if idempotency_record:
            mark_idempotency_unavailable(session, idempotency_record)
            session.commit()
        raise_gateway_failure(
            session,
            request_id=request_id,
            user_id=user_id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            tool_slug=tool.slug,
            method=request.method,
            path=gateway_path,
            started=started,
            status_code=413,
            detail="上游响应超过平台下载限制",
            error_code="UPSTREAM_RESPONSE_TOO_LARGE",
            request=request,
            route=route,
            upstream_instance=upstream_instance,
            upstream_path=rendered_upstream_path,
        )
    if 300 <= response.status_code < 400:
        await response.aclose()
        await client.aclose()
        update_gateway_upstream_instance_state(upstream_instance, upstream_status_code=response.status_code)
        status_code = 502
        payload = gateway_error_payload("UPSTREAM_REDIRECT_BLOCKED", "专业服务返回了不允许的重定向", request_id)
        record_gateway_request(
            session,
            request_id=request_id,
            user_id=user_id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            endpoint_id=route.endpoint_id,
            published_route_id=route.id,
            upstream_instance=upstream_instance,
            tool_slug=tool.slug,
            method=request.method,
            path=gateway_path,
            upstream_path=rendered_upstream_path,
            redacted_query_json=_redacted_query_json(request),
            status_code=status_code,
            upstream_status_code=response.status_code,
            duration_ms=duration_ms,
            request_bytes=len(buffered_body) if buffered_body is not None else declared_request_bytes,
            response_bytes=0,
            error_code="UPSTREAM_REDIRECT_BLOCKED",
            client_ip_hash=_hash_text(_request_client_ip(request)),
            user_agent_hash=_hash_text(request.headers.get("user-agent")),
        )
        if idempotency_record:
            save_idempotency_result(
                session,
                idempotency_record,
                status_code=status_code,
                body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
        session.commit()
        return JSONResponse(payload, status_code=status_code, headers={"x-request-id": request_id})
    is_json = "application/json" in content_type
    if is_json:
        captured_files = []
        update_gateway_upstream_instance_state(upstream_instance, upstream_status_code=response.status_code)
        body = await response.aread()
        await response.aclose()
        await client.aclose()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = gateway_error_payload("UPSTREAM_INVALID_JSON", "上游返回了无效 JSON", request_id)
            status_code = 502
            error_code = "UPSTREAM_INVALID_JSON"
        else:
            status_code = response.status_code
            error_code = None
            if 200 <= status_code < 300:
                try:
                    with session.begin_nested():
                        payload = filter_owned_list(session, route=route, user_id=user_id, payload=payload)
                        execute_post_actions(
                            session,
                            route=route,
                            user_id=user_id,
                            path_params=path_params,
                            query_params=request.query_params,
                            request_json=request_json,
                            response_json=payload,
                            response_headers=dict(response.headers),
                            source_request_id=request_id,
                        )
                        captured_files = capture_response_files(session, tool=tool, owner_user_id=user_id, payload=payload)
                        session.flush()
                except HTTPException as error:
                    payload = gateway_error_payload("RESOURCE_POLICY_FAILED", "资源归属策略执行失败", request_id, {"reason": error.detail})
                    status_code = 502
                    error_code = "RESOURCE_POLICY_FAILED"
            elif isinstance(payload, dict):
                error_code = "UPSTREAM_ERROR"
                payload = gateway_error_payload("UPSTREAM_ERROR", "专业服务请求失败", request_id, {"upstream_status": status_code})
        record_gateway_request(
            session,
            request_id=request_id,
            user_id=user_id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            endpoint_id=route.endpoint_id,
            published_route_id=route.id,
            upstream_instance=upstream_instance,
            tool_slug=tool.slug,
            method=request.method,
            path=gateway_path,
            upstream_path=rendered_upstream_path,
            redacted_query_json=_redacted_query_json(request),
            status_code=status_code,
            upstream_status_code=response.status_code,
            duration_ms=duration_ms,
            request_bytes=len(buffered_body) if buffered_body is not None else declared_request_bytes,
            response_bytes=len(body),
            error_code=error_code,
            client_ip_hash=_hash_text(_request_client_ip(request)),
            user_agent_hash=_hash_text(request.headers.get("user-agent")),
        )
        if status_code < 300 and captured_files:
            link_files_to_request(session, captured_files, request_id)
        if idempotency_record:
            save_idempotency_result(
                session,
                idempotency_record,
                status_code=status_code,
                body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
        session.commit()
        return JSONResponse(payload, status_code=status_code, headers={"x-request-id": request_id})

    if 200 <= response.status_code < 300:
        try:
            with session.begin_nested():
                filter_owned_list(session, route=route, user_id=user_id, payload={})
                execute_post_actions(
                    session,
                    route=route,
                    user_id=user_id,
                    path_params=path_params,
                    query_params=request.query_params,
                    request_json=request_json,
                    response_json={},
                    response_headers=dict(response.headers),
                    source_request_id=request_id,
                )
                session.flush()
        except HTTPException as error:
            await response.aclose()
            await client.aclose()
            payload = gateway_error_payload("RESOURCE_POLICY_FAILED", "资源归属策略执行失败", request_id, {"reason": error.detail})
            record_gateway_request(
                session,
                request_id=request_id,
                user_id=user_id,
                api_key_id=api_key.id,
                tool_id=tool.id,
                endpoint_id=route.endpoint_id,
                published_route_id=route.id,
                upstream_instance=upstream_instance,
                tool_slug=tool.slug,
                method=request.method,
                path=gateway_path,
                upstream_path=rendered_upstream_path,
                redacted_query_json=_redacted_query_json(request),
                status_code=502,
                upstream_status_code=response.status_code,
                duration_ms=duration_ms,
                request_bytes=len(buffered_body) if buffered_body is not None else declared_request_bytes,
                response_bytes=0,
                error_code="RESOURCE_POLICY_FAILED",
                client_ip_hash=_hash_text(_request_client_ip(request)),
                user_agent_hash=_hash_text(request.headers.get("user-agent")),
            )
            if idempotency_record:
                save_idempotency_result(
                    session,
                    idempotency_record,
                    status_code=502,
                    body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                    headers={"content-type": "application/json"},
                )
            session.commit()
            return JSONResponse(payload, status_code=502, headers={"x-request-id": request_id})

    if response.status_code >= 400:
        body = await response.aread()
        await response.aclose()
        await client.aclose()
        payload = gateway_error_payload("UPSTREAM_ERROR", "专业服务请求失败", request_id, {"upstream_status": response.status_code})
        update_gateway_upstream_instance_state(upstream_instance, upstream_status_code=response.status_code, error_code="UPSTREAM_ERROR")
        record_gateway_request(
            session,
            request_id=request_id,
            user_id=user_id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            endpoint_id=route.endpoint_id,
            published_route_id=route.id,
            upstream_instance=upstream_instance,
            tool_slug=tool.slug,
            method=request.method,
            path=gateway_path,
            upstream_path=rendered_upstream_path,
            redacted_query_json=_redacted_query_json(request),
            status_code=response.status_code,
            upstream_status_code=response.status_code,
            duration_ms=duration_ms,
            request_bytes=len(buffered_body) if buffered_body is not None else declared_request_bytes,
            response_bytes=len(body),
            error_code="UPSTREAM_ERROR",
            client_ip_hash=_hash_text(_request_client_ip(request)),
            user_agent_hash=_hash_text(request.headers.get("user-agent")),
        )
        if idempotency_record:
            save_idempotency_result(
                session,
                idempotency_record,
                status_code=response.status_code,
                body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
        session.commit()
        return JSONResponse(payload, status_code=response.status_code, headers={"x-request-id": request_id})

    response_limit = _route_response_limit(route)

    if not route.allow_stream_download:
        received_body = bytearray()
        try:
            async for chunk in response.aiter_bytes():
                received_body.extend(chunk)
                if len(received_body) > response_limit:
                    await response.aclose()
                    await client.aclose()
                    update_gateway_upstream_instance_state(upstream_instance, upstream_status_code=response.status_code)
                    raise_gateway_failure(
                        session,
                        request_id=request_id,
                        user_id=user_id,
                        api_key_id=api_key.id,
                        tool_id=tool.id,
                        tool_slug=tool.slug,
                        method=request.method,
                        path=gateway_path,
                        started=started,
                        status_code=413,
                        detail="上游响应超过平台下载限制",
                        error_code="UPSTREAM_RESPONSE_TOO_LARGE",
                        request=request,
                        route=route,
                        upstream_instance=upstream_instance,
                        upstream_path=rendered_upstream_path,
                    )
        except httpx.HTTPError as error:
            await response.aclose()
            await client.aclose()
            update_gateway_upstream_instance_state(upstream_instance, upstream_status_code=response.status_code, error_code="UPSTREAM_STREAM_INTERRUPTED")
            raise_gateway_failure(
                session,
                request_id=request_id,
                user_id=user_id,
                api_key_id=api_key.id,
                tool_id=tool.id,
                tool_slug=tool.slug,
                method=request.method,
                path=gateway_path,
                started=started,
                status_code=502,
                detail="上游响应流中断",
                error_code="UPSTREAM_STREAM_INTERRUPTED",
                request=request,
                route=route,
                upstream_instance=upstream_instance,
                upstream_path=rendered_upstream_path,
            )
            raise HTTPException(status_code=502, detail="上游响应流中断") from error
        await response.aclose()
        await client.aclose()
        update_gateway_upstream_instance_state(upstream_instance, upstream_status_code=response.status_code)
        record_gateway_request(
            session,
            request_id=request_id,
            user_id=user_id,
            api_key_id=api_key.id,
            tool_id=tool.id,
            endpoint_id=route.endpoint_id,
            published_route_id=route.id,
            upstream_instance=upstream_instance,
            tool_slug=tool.slug,
            method=request.method,
            path=gateway_path,
            upstream_path=rendered_upstream_path,
            redacted_query_json=_redacted_query_json(request),
            status_code=response.status_code,
            upstream_status_code=response.status_code,
            duration_ms=duration_ms,
            request_bytes=len(buffered_body) if buffered_body is not None else declared_request_bytes,
            response_bytes=len(received_body),
            client_ip_hash=_hash_text(_request_client_ip(request)),
            user_agent_hash=_hash_text(request.headers.get("user-agent")),
        )
        session.commit()
        body_bytes = bytes(received_body)
        if idempotency_record:
            save_idempotency_result(
                session,
                idempotency_record,
                status_code=response.status_code,
                body=body_bytes,
                headers={"content-type": content_type or "application/octet-stream", "content-disposition": safe_download_disposition(response.headers.get("content-disposition"))},
            )
            session.commit()
        return Response(
            content=body_bytes,
            status_code=response.status_code,
            media_type=content_type or "application/octet-stream",
            headers={
                "x-request-id": request_id,
                "content-disposition": safe_download_disposition(response.headers.get("content-disposition")),
            },
        )

    async def stream_body() -> AsyncIterator[bytes]:
        received = 0
        try:
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > response_limit:
                    mark_gateway_stream_failure(
                        session,
                        request_id,
                        status_code=413,
                        error_code="UPSTREAM_RESPONSE_TOO_LARGE",
                        response_bytes=received,
                    )
                    raise HTTPException(status_code=413, detail="上游响应超过平台下载限制")
                yield chunk
        except httpx.HTTPError:
            mark_gateway_stream_failure(
                session,
                request_id,
                status_code=502,
                error_code="UPSTREAM_STREAM_INTERRUPTED",
                response_bytes=received,
            )
            raise
        finally:
            await response.aclose()
            await client.aclose()

    update_gateway_upstream_instance_state(upstream_instance, upstream_status_code=response.status_code)
    record_gateway_request(
        session,
        request_id=request_id,
        user_id=user_id,
        api_key_id=api_key.id,
        tool_id=tool.id,
        endpoint_id=route.endpoint_id,
        published_route_id=route.id,
        upstream_instance=upstream_instance,
        tool_slug=tool.slug,
        method=request.method,
        path=gateway_path,
        upstream_path=rendered_upstream_path,
        redacted_query_json=_redacted_query_json(request),
        status_code=response.status_code,
        upstream_status_code=response.status_code,
        duration_ms=duration_ms,
        request_bytes=len(buffered_body) if buffered_body is not None else declared_request_bytes,
        response_bytes=_safe_int(content_length),
        client_ip_hash=_hash_text(_request_client_ip(request)),
        user_agent_hash=_hash_text(request.headers.get("user-agent")),
    )
    session.commit()
    safe_headers = {"x-request-id": request_id, "content-type": content_type}
    safe_headers["content-disposition"] = safe_download_disposition(response.headers.get("content-disposition"))
    if idempotency_record:
        # 流式响应在开始发送后无法原子保存完整结果，策略入口已提前拒绝此配置。
        idempotency_record.response_status_code = response.status_code
        idempotency_record.status = "completed"
        idempotency_record.response_headers_json = json.dumps({"content-type": content_type}, ensure_ascii=False)
        idempotency_record.response_body_ciphertext = ""
        session.commit()
    return StreamingResponse(stream_body(), status_code=response.status_code, headers=safe_headers)


def api_key_is_expired(api_key: ApiKey) -> bool:
    if not api_key.expires_at:
        return False
    expires_at = api_key.expires_at.replace(tzinfo=timezone.utc) if api_key.expires_at.tzinfo is None else api_key.expires_at
    return expires_at <= datetime.now(timezone.utc)
