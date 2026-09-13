"""使用标准 SMTP 投递数据库 Outbox，业务事务与邮件失败相互隔离。"""

from __future__ import annotations

import logging
import smtplib
from datetime import timedelta
from email.message import EmailMessage

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import EmailOutbox, User
from ..redaction import external_error_summary
from .audit import log_admin_event
from .platform_settings import EffectivePlatformSettings, get_effective_platform_settings
from .security import decrypt_secret, encrypt_secret
from .user_lifecycle import utc_now


logger = logging.getLogger("api_gateway.email_delivery")


def _delivery_error_summary(error: Exception, stage: str) -> str:
    """保存异常类型和协议阶段，不保存第三方错误正文。"""

    return f"{external_error_summary(error, category='SMTP_DELIVERY_FAILED')}:{stage}"


def _set_smtp_stage(error: Exception, stage: str) -> None:
    """给异常附加 SMTP 阶段，保留原异常类型以兼容现有错误码。"""

    try:
        setattr(error, "smtp_stage", stage)
    except Exception:
        # 极少数第三方异常可能不允许动态属性，不能影响失败收敛。
        pass


def _connect_smtp(settings: EffectivePlatformSettings) -> smtplib.SMTP:
    """建立并认证 SMTP 连接，同时记录失败发生的协议阶段。

    465 端口是隐式 TLS，不能再发送 STARTTLS；其它端口按配置选择
    STARTTLS 或明文。构造 SMTP 客户端本身就会等待服务端欢迎语，因此
    该步骤也必须纳入异常捕获，便于区分网络/服务端横幅超时与认证失败。
    """

    client: smtplib.SMTP | None = None
    stage = "connect"
    mode = "implicit_tls" if settings.smtp_port == 465 else "starttls" if settings.smtp_use_tls else "plain"
    try:
        if settings.smtp_port == 465:
            client = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds)
        else:
            client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds)
        stage = "ehlo"
        client.ehlo()
        if settings.smtp_port != 465 and settings.smtp_use_tls:
            stage = "starttls"
            client.starttls()
            stage = "ehlo_tls"
            client.ehlo()
        if settings.smtp_username:
            stage = "auth"
            client.login(settings.smtp_username, settings.smtp_password)
        return client
    except Exception as error:
        _set_smtp_stage(error, stage)
        logger.warning(
            "smtp_connection_failed",
            extra={
                "event": "smtp_connection",
                "smtp_host": settings.smtp_host,
                "smtp_port": settings.smtp_port,
                "smtp_mode": mode,
                "smtp_stage": stage,
                "error_code": f"SMTP_CONNECTION_FAILED:{type(error).__name__}",
            },
        )
        if client is not None:
            try:
                client.close()
            except Exception:
                # 服务器已断开时 close 可能再次抛错，不能覆盖原始故障。
                pass
        raise


def test_smtp_connection(session: Session) -> None:
    settings = get_effective_platform_settings(session)
    if not settings.smtp_configured:
        raise ValueError("邮件服务未启用或配置不完整")
    with _connect_smtp(settings) as client:
        code, _ = client.noop()
        if code >= 400:
            raise OSError(f"SMTP NOOP 返回状态码 {code}")


def _claim_due_email(session: Session) -> EmailOutbox | None:
    """原子领取一封到期邮件，避免多个 Worker 同时发送同一条记录。"""
    now = utc_now()
    lease_cutoff = now - timedelta(minutes=5)
    candidate = session.scalar(
        select(EmailOutbox)
        .where(
            EmailOutbox.next_attempt_at <= now,
            or_(
                EmailOutbox.status.in_(["pending", "retry"]),
                (EmailOutbox.status == "sending") & (EmailOutbox.updated_at < lease_cutoff),
            ),
            EmailOutbox.attempts < get_settings().email_outbox_max_attempts,
        )
        .order_by(EmailOutbox.created_at)
        .limit(1)
    )
    if not candidate:
        return None
    result = session.execute(
        update(EmailOutbox)
        .where(
            EmailOutbox.id == candidate.id,
            EmailOutbox.next_attempt_at <= now,
            or_(
                EmailOutbox.status.in_(["pending", "retry"]),
                (EmailOutbox.status == "sending") & (EmailOutbox.updated_at < lease_cutoff),
            ),
        )
        .values(status="sending", updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if not result.rowcount:
        session.rollback()
        return None
    session.commit()
    session.refresh(candidate)
    return candidate


def deliver_due_email(session: Session) -> bool:
    runtime_settings = get_settings()
    email_settings = get_effective_platform_settings(session)
    if not email_settings.smtp_configured:
        return False
    now = utc_now()
    item = _claim_due_email(session)
    if not item:
        return False

    stage = "prepare_message"
    try:
        message = EmailMessage()
        message["From"] = email_settings.smtp_from_header
        message["To"] = item.recipient
        message["Subject"] = item.subject
        stage = "decrypt_body"
        message.set_content(decrypt_secret(item.body_ciphertext))
        stage = "send"
        with _connect_smtp(email_settings) as client:
            client.send_message(message)
    except Exception as error:
        stage = getattr(error, "smtp_stage", stage)
        item.attempts += 1
        item.last_error = _delivery_error_summary(error, stage)
        if item.attempts >= runtime_settings.email_outbox_max_attempts:
            item.status = "failed"
        else:
            item.status = "retry"
            item.next_attempt_at = now + timedelta(minutes=min(60, 2 ** item.attempts))
        owner_user_id = session.scalar(
            select(User.id).where(or_(User.email == item.recipient, User.pending_email == item.recipient)).limit(1)
        )
        if owner_user_id:
            log_admin_event(
                session,
                admin_user_id=owner_user_id,
                action="email_delivery_failed",
                target_type="email_outbox",
                target_id=item.id,
                request_id=f"email:{item.id}",
                detail={"recipient": item.recipient, "subject": item.subject, "status": item.status, "attempts": item.attempts, "error_code": item.last_error, "smtp_stage": stage},
            )
        session.commit()
        logger.warning(
            "smtp_delivery_failed",
            extra={
                "event": "smtp_delivery",
                "outbox_id": item.id,
                "smtp_stage": stage,
                "status": item.status,
                "attempts": item.attempts,
                "error_code": item.last_error,
            },
        )
        return True

    item.status = "sent"
    item.attempts += 1
    item.sent_at = now
    item.last_error = ""
    item.body_ciphertext = encrypt_secret("邮件已投递，正文已清除。")
    owner_user_id = session.scalar(
        select(User.id).where(or_(User.email == item.recipient, User.pending_email == item.recipient)).limit(1)
    )
    if owner_user_id:
        log_admin_event(
            session,
            admin_user_id=owner_user_id,
            action="email_delivered",
            target_type="email_outbox",
            target_id=item.id,
            request_id=f"email:{item.id}",
            detail={"recipient": item.recipient, "subject": item.subject, "status": "sent", "attempts": item.attempts},
        )
    session.commit()
    return True
