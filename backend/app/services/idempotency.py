"""Gateway 幂等记录的持久化、指纹和结果重放。"""

from __future__ import annotations

import base64
import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from fastapi import HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ApiKey, IdempotencyRecord, PublishedRoute
from .security import decrypt_secret, encrypt_secret, hash_idempotency_key


IDEMPOTENCY_TTL = timedelta(hours=24)
PROCESSING_TIMEOUT = timedelta(minutes=5)
MAX_CACHED_RESPONSE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class IdempotencyClaim:
    record: IdempotencyRecord
    owner: bool


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def request_fingerprint(request: Request, route: PublishedRoute, body: bytes) -> str:
    """只对方法、路由、查询、内容类型和请求体摘要做指纹，不落原文。"""
    items_method = getattr(request.query_params, "multi_items", None)
    query_items = items_method() if callable(items_method) else getattr(request.query_params, "items", lambda: [])()
    query = sorted((str(key), str(value)) for key, value in query_items)
    payload = {
        "method": request.method.upper(),
        "route_id": route.id,
        "query": query,
        "content_type": (request.headers.get("content-type") or "").lower(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_length": len(body),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _scope_query(session: Session, *, user_id: str, api_key_id: str, route_id: str, key_hash: str):
    return select(IdempotencyRecord).where(
        IdempotencyRecord.user_id == user_id,
        IdempotencyRecord.api_key_id == api_key_id,
        IdempotencyRecord.published_route_id == route_id,
        IdempotencyRecord.key_hash == key_hash,
    )


def claim_idempotency_record(
    session: Session,
    *,
    user_id: str,
    api_key: ApiKey,
    route: PublishedRoute,
    raw_key: str,
    fingerprint: str,
) -> IdempotencyClaim:
    """通过唯一约束抢占幂等记录；并发冲突只能读取已有记录。"""
    key_hash = hash_idempotency_key(raw_key)
    now = datetime.now(timezone.utc)
    existing = session.scalar(_scope_query(session, user_id=user_id, api_key_id=api_key.id, route_id=route.id, key_hash=key_hash))
    if existing and _as_utc(existing.expires_at) <= now:
        session.delete(existing)
        session.flush()
        existing = None
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=422, detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": "相同 Idempotency-Key 的请求内容不一致"})
        return IdempotencyClaim(existing, owner=False)

    record = IdempotencyRecord(
        user_id=user_id,
        api_key_id=api_key.id,
        published_route_id=route.id,
        key_hash=key_hash,
        request_fingerprint=fingerprint,
        status="processing",
        expires_at=now + IDEMPOTENCY_TTL,
    )
    try:
        with session.begin_nested():
            session.add(record)
            session.flush()
    except IntegrityError:
        existing = session.scalar(_scope_query(session, user_id=user_id, api_key_id=api_key.id, route_id=route.id, key_hash=key_hash))
        if not existing:
            raise HTTPException(status_code=503, detail={"code": "IDEMPOTENCY_STORE_UNAVAILABLE", "message": "幂等记录暂时不可用"})
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=422, detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": "相同 Idempotency-Key 的请求内容不一致"})
        return IdempotencyClaim(existing, owner=False)
    return IdempotencyClaim(record, owner=True)


async def wait_for_idempotency_result(session: Session, record: IdempotencyRecord, timeout_seconds: float = 2.0) -> IdempotencyRecord:
    """短暂等待并发持有者完成；超时后由重放函数返回受控冲突。"""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while record.status == "processing" and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)
        session.expire(record)
        session.refresh(record)
    if record.status == "processing" and datetime.now(timezone.utc) - _as_utc(record.updated_at) >= PROCESSING_TIMEOUT:
        mark_idempotency_unavailable(session, record)
        session.commit()
    return record


def response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """只允许安全响应头进入重放记录。"""
    allowed = {"content-type", "content-disposition"}
    return {key.lower(): value for key, value in headers.items() if key.lower() in allowed and value}


def save_idempotency_result(session: Session, record: IdempotencyRecord, *, status_code: int, body: bytes, headers: Mapping[str, str]) -> None:
    """加密保存有限大小的结果；超大结果重复请求改为失败关闭。"""
    record.status = "completed"
    record.response_status_code = status_code
    record.response_headers_json = json.dumps(response_headers(headers), ensure_ascii=False, separators=(",", ":"))
    if len(body) <= MAX_CACHED_RESPONSE_BYTES:
        record.response_body_ciphertext = encrypt_secret(base64.b64encode(body).decode("ascii"))
    else:
        record.response_body_ciphertext = ""
    record.updated_at = datetime.now(timezone.utc)
    session.flush()


def mark_idempotency_unavailable(session: Session, record: IdempotencyRecord) -> None:
    """失败或超限结果不允许重放时结束占用，后续请求得到明确冲突而非永久处理中。"""
    record.status = "failed"
    record.response_status_code = None
    record.response_body_ciphertext = ""
    record.response_headers_json = "{}"
    record.updated_at = datetime.now(timezone.utc)
    session.flush()


def replay_idempotency_result(record: IdempotencyRecord) -> Response:
    if record.status == "processing":
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_IN_PROGRESS", "message": "相同 Idempotency-Key 的请求仍在处理"})
    if record.status == "failed" or not record.response_body_ciphertext or record.response_status_code is None:
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_RESULT_UNAVAILABLE", "message": "首次结果不可安全重放，请使用新的请求键"})
    try:
        body = base64.b64decode(decrypt_secret(record.response_body_ciphertext), validate=True)
        headers = json.loads(record.response_headers_json or "{}")
    except Exception as error:
        raise HTTPException(status_code=503, detail={"code": "IDEMPOTENCY_STORE_UNAVAILABLE", "message": "幂等结果暂时不可读取"}) from error
    safe_headers = headers if isinstance(headers, dict) else {}
    return Response(content=body, status_code=record.response_status_code, headers={str(key): str(value) for key, value in safe_headers.items()})
