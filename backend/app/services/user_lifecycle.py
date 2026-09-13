"""用户验证、通知和邮件 Outbox 的统一服务。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AuthActionToken, EmailOutbox, Notification, User
from .audit import log_admin_event
from .platform_settings import get_effective_platform_settings
from .security import encrypt_secret


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def hash_action_token(token: str) -> str:
    return hmac.new(
        get_settings().app_secret_key.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def issue_action_token(
    session: Session,
    *,
    user_id: str,
    purpose: str,
    target_email: str,
    lifetime: timedelta,
) -> str:
    """签发单次令牌，同时使同用途旧令牌失效。"""
    now = utc_now()
    session.execute(
        update(AuthActionToken)
        .where(
            AuthActionToken.user_id == user_id,
            AuthActionToken.purpose == purpose,
            AuthActionToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    raw_token = secrets.token_urlsafe(48)
    session.add(
        AuthActionToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=hash_action_token(raw_token),
            target_email=target_email,
            expires_at=now + lifetime,
        )
    )
    return raw_token


def get_valid_action_token(session: Session, *, token: str, purpose: str) -> AuthActionToken | None:
    row = session.scalar(
        select(AuthActionToken).where(
            AuthActionToken.token_hash == hash_action_token(token),
            AuthActionToken.purpose == purpose,
            AuthActionToken.consumed_at.is_(None),
        )
    )
    if not row:
        return None
    expires_at = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
    if expires_at <= utc_now():
        return None
    return row


def consume_action_token(row: AuthActionToken) -> None:
    row.consumed_at = utc_now()


def add_notification(
    session: Session,
    *,
    user_id: str,
    kind: str,
    title: str,
    body: str,
    action_url: str = "",
) -> Notification:
    item = Notification(user_id=user_id, kind=kind, title=title, body=body, action_url=action_url)
    session.add(item)
    return item


def queue_email(session: Session, *, recipient: str, subject: str, body_text: str) -> EmailOutbox | None:
    """邮件正文加密入库；SMTP 未配置时不制造永远无法投递的任务。"""
    if not get_effective_platform_settings(session).smtp_configured:
        return None
    item = EmailOutbox(
        recipient=recipient,
        subject=subject,
        body_ciphertext=encrypt_secret(body_text),
        status="pending",
        next_attempt_at=utc_now(),
    )
    session.add(item)
    session.flush()
    owner_user_id = session.scalar(
        select(User.id).where(or_(User.email == recipient, User.pending_email == recipient)).limit(1)
    )
    if owner_user_id:
        log_admin_event(
            session,
            admin_user_id=owner_user_id,
            action="email_queued",
            target_type="email_outbox",
            target_id=item.id,
            request_id=f"email:{item.id}",
            detail={"recipient": recipient, "subject": subject, "status": "pending"},
        )
    return item
