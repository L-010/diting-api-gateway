"""将 Gateway 调用记录转换为管理员和用户可安全查看的结构。"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ApiEndpoint, ApiKey, GatewayRequest, User


def _json_list(value: str) -> list[dict[str, str]]:
    try:
        loaded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    result: list[dict[str, str]] = []
    for item in loaded[:100]:
        if not isinstance(item, dict):
            continue
        result.append({"key": str(item.get("key", ""))[:128], "value": str(item.get("value", ""))[:200]})
    return result


def failure_stage(item: GatewayRequest) -> str:
    if item.status_code < 400:
        return "completed"
    if item.status_code == 429:
        return "rate_limit"
    code = (item.error_code or "").upper()
    if any(part in code for part in ("KEY", "AUTH", "ACCOUNT", "SCOPE", "FORBIDDEN")):
        return "access_control"
    if any(part in code for part in ("ROUTE", "TOOL", "ENDPOINT", "METHOD")):
        return "routing"
    if item.upstream_status_code is not None or "UPSTREAM" in code:
        return "upstream"
    return "gateway"


def gateway_call_views(session: Session, rows: list[GatewayRequest], *, admin: bool) -> list[dict[str, object]]:
    user_ids = {item.user_id for item in rows if item.user_id}
    api_key_ids = {item.api_key_id for item in rows if item.api_key_id}
    endpoint_ids = {item.endpoint_id for item in rows if item.endpoint_id}
    users = {item.id: item for item in session.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
    api_keys = {item.id: item for item in session.scalars(select(ApiKey).where(ApiKey.id.in_(api_key_ids))).all()} if api_key_ids else {}
    endpoints = {item.id: item for item in session.scalars(select(ApiEndpoint).where(ApiEndpoint.id.in_(endpoint_ids))).all()} if endpoint_ids else {}
    views: list[dict[str, object]] = []
    for item in rows:
        user = users.get(item.user_id or "")
        api_key = api_keys.get(item.api_key_id or "")
        endpoint = endpoints.get(item.endpoint_id or "")
        view: dict[str, object] = {
            "id": item.id,
            "request_id": item.id,
            "user_id": item.user_id,
            "username": user.username if user else "",
            "api_key_id": item.api_key_id,
            "api_key_prefix": api_key.prefix if api_key else "",
            "tool_id": item.tool_id,
            "tool_slug": item.tool_slug,
            "endpoint_id": item.endpoint_id,
            "endpoint_summary": endpoint.summary if endpoint else "",
            "operation_id": endpoint.operation_id if endpoint else None,
            "method": item.method,
            "path": item.path,
            "status_code": item.status_code,
            "upstream_status_code": item.upstream_status_code,
            "duration_ms": item.duration_ms,
            "request_bytes": item.request_bytes,
            "response_bytes": item.response_bytes,
            "error_code": item.error_code,
            "failure_stage": failure_stage(item),
            "query": _json_list(item.redacted_query_json),
            "created_at": item.created_at,
            "upstream_path": item.upstream_path if admin else "",
            "client_ip_fingerprint": item.client_ip_hash[:12] if admin else "",
            "user_agent_fingerprint": item.user_agent_hash[:12] if admin else "",
        }
        views.append(view)
    return views


def gateway_call_view(session: Session, row: GatewayRequest, *, admin: bool) -> dict[str, object]:
    return gateway_call_views(session, [row], admin=admin)[0]
