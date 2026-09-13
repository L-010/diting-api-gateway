"""审计记录只保留必要元数据，禁止写入请求正文和秘密。"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from ..models import AdminAuditEvent
from ..redaction import redact_sensitive_text


SENSITIVE_KEY_PARTS = ("authorization", "api_key", "apikey", "x-api-key", "token", "secret", "password", "bearer", "cookie", "set-cookie")
MAX_AUDIT_STRING_LENGTH = 1_000


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value[:MAX_AUDIT_STRING_LENGTH]
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value[:MAX_AUDIT_STRING_LENGTH]
    safe_pairs = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        safe_pairs.append((key, "***" if _is_sensitive_key(key) else item[:200]))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(safe_pairs), ""))


def sanitize_audit_detail(value: Any) -> Any:
    """递归脱敏审计详情，防止未来新调用点误写入凭据。"""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        cleaned = redact_sensitive_text(_redact_url(value))
        if cleaned.lower().startswith(("bearer ", "basic ")):
            return "***"
        return cleaned[:MAX_AUDIT_STRING_LENGTH]
    if isinstance(value, list | tuple | set):
        return [sanitize_audit_detail(item) for item in list(value)[:100]]
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:100]:
            key = str(raw_key)
            sanitized[key] = "***" if _is_sensitive_key(key) else sanitize_audit_detail(raw_value)
        return sanitized
    return str(value)[:MAX_AUDIT_STRING_LENGTH]


def log_admin_event(
    session: Session,
    *,
    admin_user_id: str,
    action: str,
    target_type: str,
    target_id: str | None,
    request_id: str,
    detail: dict[str, object] | None = None,
) -> None:
    session.add(
        AdminAuditEvent(
            admin_user_id=admin_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id,
            detail_json=json.dumps(sanitize_audit_detail(detail or {}), ensure_ascii=False, separators=(",", ":")),
        )
    )
