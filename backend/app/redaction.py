"""外部依赖错误的最小脱敏工具。"""

from __future__ import annotations

import re


_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(?:authorization|api[_-]?key|x[_-]?api[_-]?key|token|secret|password|passwd|cookie|set-cookie)\s*(?:=|:)\s*(?:(?:bearer|basic)\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_CREDENTIAL_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@[^\s,;]+")


def redact_sensitive_text(value: object, *, limit: int = 1_000) -> str:
    """清理异常文本中的常见凭据格式，供日志和持久化错误摘要共用。"""

    text = str(value or "")
    text = _CREDENTIAL_URL.sub("[已脱敏连接地址]", text)
    text = _SENSITIVE_VALUE.sub("[已脱敏]", text)
    return text[:limit]


def external_error_summary(error: Exception, *, category: str) -> str:
    """外部服务失败只保存类别和异常类型，避免依赖方错误正文进入持久化数据。"""

    return f"{category}:{type(error).__name__}"
