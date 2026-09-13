"""SQLite 开发限流器；接口可在部署时替换为 Redis 实现。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import RateLimitBucket, UserRateLimitBucket


def _consume_sqlite_window(session: Session, model, values: dict[str, object], index_columns: list[str], limit: int, message: str) -> bool:
    """SQLite 用单条 UPSERT 计数，避免两个进程同时建桶时唯一约束异常穿透。"""

    statement = sqlite_insert(model).values(**values).on_conflict_do_update(
        index_elements=index_columns,
        set_={"request_count": model.request_count + 1},
        where=model.request_count < limit,
    )
    result = session.execute(statement)
    if result.rowcount:
        return True
    raise HTTPException(status_code=429, detail=message)


def _consume_mysql_window(session: Session, model, values: dict[str, object], filters: list[object], limit: int, message: str) -> None:
    """MySQL 先原子尝试建桶，再锁定已存在的桶完成限额判断。"""

    primary_key = next(iter(model.__table__.primary_key.columns))
    statement = mysql_insert(model).values(**values).on_duplicate_key_update(**{primary_key.name: primary_key})
    result = session.execute(statement)
    if result.rowcount == 1:
        return
    bucket = session.scalar(select(model).where(*filters).with_for_update())
    if bucket is None:
        raise RuntimeError("限流桶创建后无法读取")
    if bucket.request_count >= limit:
        raise HTTPException(status_code=429, detail=message)
    bucket.request_count += 1


def consume_fixed_window(session: Session, api_key_id: str, tool_id: str, limit: int) -> None:
    now = datetime.now(timezone.utc)
    window_start = now.replace(second=0, microsecond=0)
    if session.get_bind().dialect.name == "sqlite":
        _consume_sqlite_window(
            session,
            RateLimitBucket,
            {"api_key_id": api_key_id, "tool_id": tool_id, "window_start": window_start, "request_count": 1},
            ["api_key_id", "tool_id", "window_start"],
            limit,
            "API Key 已超过每分钟调用配额",
        )
        return
    filters = [
        RateLimitBucket.api_key_id == api_key_id,
        RateLimitBucket.tool_id == tool_id,
        RateLimitBucket.window_start == window_start,
    ]
    if session.get_bind().dialect.name in {"mysql", "mariadb"}:
        _consume_mysql_window(
            session,
            RateLimitBucket,
            {"api_key_id": api_key_id, "tool_id": tool_id, "window_start": window_start, "request_count": 1},
            filters,
            limit,
            "API Key 已超过每分钟调用配额",
        )
        return
    bucket = session.scalar(
        select(RateLimitBucket).where(*filters).with_for_update()
    )
    if not bucket:
        session.add(RateLimitBucket(api_key_id=api_key_id, tool_id=tool_id, window_start=window_start, request_count=1))
        session.flush()
        return
    if bucket.request_count >= limit:
        raise HTTPException(status_code=429, detail="API Key 已超过每分钟调用配额")
    bucket.request_count += 1


def consume_user_fixed_window(session: Session, user_id: str, limit: int) -> None:
    """按用户聚合全部 Key 和工具的请求；0 表示不增加用户级限制。"""
    if limit <= 0:
        return
    now = datetime.now(timezone.utc)
    window_start = now.replace(second=0, microsecond=0)
    if session.get_bind().dialect.name == "sqlite":
        _consume_sqlite_window(
            session,
            UserRateLimitBucket,
            {"user_id": user_id, "window_start": window_start, "request_count": 1},
            ["user_id", "window_start"],
            limit,
            "用户已超过每分钟请求上限",
        )
        return
    filters = [UserRateLimitBucket.user_id == user_id, UserRateLimitBucket.window_start == window_start]
    if session.get_bind().dialect.name in {"mysql", "mariadb"}:
        _consume_mysql_window(
            session,
            UserRateLimitBucket,
            {"user_id": user_id, "window_start": window_start, "request_count": 1},
            filters,
            limit,
            "用户已超过每分钟请求上限",
        )
        return
    bucket = session.scalar(
        select(UserRateLimitBucket).where(*filters).with_for_update()
    )
    if not bucket:
        session.add(UserRateLimitBucket(user_id=user_id, window_start=window_start, request_count=1))
        session.flush()
        return
    if bucket.request_count >= limit:
        raise HTTPException(status_code=429, detail="用户已超过每分钟请求上限")
    bucket.request_count += 1
