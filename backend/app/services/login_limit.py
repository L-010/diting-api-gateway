"""登录入口的轻量固定窗口限流。

首版本地 MVP 不引入 Redis，也不把匿名失败用户名写入数据库；这里用进程内
固定窗口同时限制客户端 IP 和规范化用户名，避免未知账号被无限暴力探测。
部署到多进程或多实例时，可把本模块替换为 Redis/网关层限流实现。
"""

from __future__ import annotations

import time

from fastapi import HTTPException, Request

from ..config import get_settings


WINDOW_SECONDS = 60
_LOGIN_BUCKETS: dict[str, tuple[int, int]] = {}


def _normalized_username(username: str) -> str:
    return username.strip().lower() or "anonymous"


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def _bucket_count(key: str, window_start: int) -> int:
    stored_window, count = _LOGIN_BUCKETS.get(key, (window_start, 0))
    return count if stored_window == window_start else 0


def _write_bucket(key: str, window_start: int, count: int) -> None:
    _LOGIN_BUCKETS[key] = (window_start, count)


def consume_login_attempt(request: Request, username: str) -> None:
    """对登录请求做匿名/IP 双维度限流，不记录密码或明文请求体。"""
    if not get_settings().login_protection_enabled:
        return
    now = int(time.time())
    limit = get_settings().login_failure_limit
    window_start = now - (now % WINDOW_SECONDS)
    keys = [f"ip:{_client_ip(request)}", f"user:{_normalized_username(username)}"]
    for key in keys:
        if _bucket_count(key, window_start) >= limit:
            raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    for key in keys:
        _write_bucket(key, window_start, _bucket_count(key, window_start) + 1)


def reset_login_limit_state() -> None:
    """测试专用：清理进程内限流桶。"""
    _LOGIN_BUCKETS.clear()
