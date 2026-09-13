"""结构化日志与运维观测辅助函数。

本地 MVP 先用标准库输出 JSON 日志，避免引入额外日志依赖；后续接入
OpenTelemetry/Prometheus 时可以在这里替换导出器，业务代码不需要大改。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import ApiKey, GatewayRequest
from ..redaction import redact_sensitive_text


_RESERVED_LOG_RECORD_KEYS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


def _json_safe(value: Any) -> Any:
    """将日志扩展字段转换成 JSON 可序列化的安全值。"""

    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return redact_sensitive_text(value)


class JsonLogFormatter(logging.Formatter):
    """输出单行 JSON，便于本地 grep 和后续日志平台采集。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_sensitive_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_KEYS or key.startswith("_"):
                continue
            payload[key] = _json_safe(value)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_json_logging() -> None:
    """幂等配置根日志器，统一输出结构化 JSON。"""

    root_logger = logging.getLogger()
    if getattr(root_logger, "_api_gateway_json_logging", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)
    setattr(root_logger, "_api_gateway_json_logging", True)


gateway_logger = logging.getLogger("api_gateway.gateway")
http_logger = logging.getLogger("api_gateway.http")


def log_gateway_request_event(session: Session, record: GatewayRequest) -> None:
    """记录一次 Gateway 数据面调用的脱敏结构化日志。"""

    api_key_prefix = ""
    if record.api_key_id:
        api_key = session.get(ApiKey, record.api_key_id)
        api_key_prefix = api_key.prefix if api_key else ""
    gateway_logger.info(
        "gateway_request_recorded",
        extra={
            "event": "gateway_request",
            "request_id": record.id,
            "user_id": record.user_id,
            "api_key_prefix": api_key_prefix,
            "tool_slug": record.tool_slug,
            "route_id": record.published_route_id,
            "endpoint_id": record.endpoint_id,
            "method": record.method,
            "gateway_path": record.path,
            "upstream_path": record.upstream_path,
            "status_code": record.status_code,
            "upstream_status_code": record.upstream_status_code,
            "error_code": record.error_code,
            "duration_ms": record.duration_ms,
            "request_bytes": record.request_bytes,
            "response_bytes": record.response_bytes,
        },
    )
