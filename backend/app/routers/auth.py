"""浏览器身份认证、公开注册申请和用户 API Key 管理。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import ApiKey, ApprovalStatus, AuthActionToken, KeyStatus, Role, RoleName, User, UserRole
from ..username import normalize_username_key
from ..schemas import (
    ApiKeyCreated,
    ApiKeyCreateRequest,
    ApiKeyView,
    ChangePasswordRequest,
    EmailAddressRequest,
    LoginRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenRequest,
    UserView,
)
from ..services.audit import log_admin_event
from ..services.email_templates import email_change, password_reset, registration_verification
from ..services.login_limit import consume_login_attempt
from ..services.platform_settings import default_user_rate_limit, get_effective_platform_settings
from ..services.security import (
    DUMMY_PASSWORD_HASH,
    create_api_key,
    create_csrf_token,
    create_session_token,
    hash_password,
    validate_password,
    verify_password,
)
from ..services.gateway_proxy import api_key_is_expired
from ..services.user_lifecycle import (
    add_notification,
    consume_action_token,
    get_valid_action_token,
    issue_action_token,
    queue_email,
    utc_now,
)
from .deps import get_current_user, get_user_roles, request_id, require_not_password_change, validate_csrf


router = APIRouter(prefix="/api", tags=["认证与用户"])


def _action_token_in_cooldown(session: Session, *, user_id: str, purpose: str) -> bool:
    """同类邮件一分钟内只入队一次，接口仍返回统一成功结果。"""

    latest = session.scalar(
        select(AuthActionToken.created_at)
        .where(AuthActionToken.user_id == user_id, AuthActionToken.purpose == purpose)
        .order_by(AuthActionToken.created_at.desc())
        .limit(1)
    )
    if latest is None:
        return False
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return latest > utc_now() - timedelta(seconds=60)


def user_view(session: Session, user: User) -> UserView:
    return UserView(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        email_verified=user.email_verified_at is not None,
        roles=get_user_roles(session, user.id),
        approval_status=user.approval_status,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
    )


def api_key_view(key: ApiKey) -> ApiKeyView:
    scopes = [item.strip() for item in key.scopes.split(",") if item.strip()]
    expired = api_key_is_expired(key)
    can_enable = (
        key.status == KeyStatus.DISABLED.value
        and not expired
        and key.disabled_by_user_id == key.user_id
        and key.disable_reason == "用户主动禁用"
    )
    if key.status == KeyStatus.REVOKED.value:
        rotation_hint = "该 Key 已撤销且不可恢复；请生成新 Key 并更新用户程序配置。"
    elif expired:
        rotation_hint = "该 Key 已过期；请生成新 Key 并更新用户程序配置。"
    elif can_enable:
        rotation_hint = "该 Key 由你主动禁用；确认仍可信后可以重新启用。"
    elif key.status == KeyStatus.DISABLED.value:
        rotation_hint = "该 Key 由管理员禁用；如需恢复，请联系管理员处理。"
    elif key.last_used_at is None:
        rotation_hint = "该 Key 尚未使用；上线前建议先用快速接入示例做一次最小调用。"
    else:
        rotation_hint = "如怀疑泄露，请先创建新 Key、切换用户程序，再禁用旧 Key。"
    return ApiKeyView(
        id=key.id,
        label=key.label,
        prefix=key.prefix,
        status=key.status,
        scopes=scopes,
        expires_at=key.expires_at,
        last_used_at=key.last_used_at,
        disabled_at=key.disabled_at,
        disabled_by_user_id=key.disabled_by_user_id,
        disable_reason=key.disable_reason,
        can_enable=can_enable,
        is_expired=expired,
        rotation_hint=rotation_hint,
        created_at=key.created_at,
    )


def _ensure_user_role(session: Session) -> Role:
    role = session.scalar(select(Role).where(Role.name == RoleName.USER.value))
    if role:
        return role
    role = Role(name=RoleName.USER.value, description="普通平台用户")
    session.add(role)
    session.flush()
    return role


def _raise_account_state(user: User) -> None:
    if user.email and user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "EMAIL_NOT_VERIFIED", "message": "请先完成邮箱验证"},
        )
    if user.approval_status == ApprovalStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PENDING_APPROVAL", "message": "账号注册申请正在等待管理员审批"},
        )
    if user.approval_status == ApprovalStatus.REJECTED.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "REGISTRATION_REJECTED",
                "message": "账号注册申请已被拒绝",
                "details": {"reason": user.rejection_reason} if user.rejection_reason else None,
            },
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ACCOUNT_DISABLED",
                "message": "账号已停用，请联系管理员",
                "details": {"reason": user.disable_reason} if user.disable_reason else None,
            },
        )


def set_session_cookies(response: Response, user: User) -> None:
    """写入 HttpOnly 会话 Cookie 和双提交 CSRF Cookie。"""
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        create_session_token(user.id, user.must_change_password, user.session_version),
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        max_age=settings.access_token_minutes * 60,
        path="/",
    )
    response.set_cookie(
        "agw_csrf",
        create_csrf_token(),
        httponly=False,
        samesite="lax",
        secure=settings.secure_cookies,
        max_age=settings.access_token_minutes * 60,
        path="/",
    )


@router.post("/auth/register", response_model=UserView, status_code=201)
def register(payload: RegisterRequest, request: Request, session: Session = Depends(get_db)) -> UserView:
    settings = get_settings()
    effective_settings = get_effective_platform_settings(session)
    if not effective_settings.registration_enabled or not effective_settings.smtp_configured:
        raise HTTPException(
            status_code=503,
            detail={"code": "REGISTRATION_DISABLED", "message": "当前未配置邮件服务，公开注册暂不可用"},
        )
    username = payload.username.strip()
    email = payload.email.strip().lower() if payload.email else None
    if settings.email_test_mode and not email:
        email = f"{username.lower()}@example.test"
    if not email:
        raise HTTPException(status_code=422, detail="公开注册必须填写邮箱")
    if session.scalar(select(User.id).where(User.username_key == normalize_username_key(username))):
        raise HTTPException(status_code=409, detail="用户名已存在")
    if email and session.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise HTTPException(status_code=409, detail="邮箱已存在")
    try:
        validate_password(payload.password, username)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    role = _ensure_user_role(session)
    now = datetime.now(timezone.utc)
    user = User(
        username=username,
        email=email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        is_active=False,
        approval_status=ApprovalStatus.PENDING.value,
        registered_at=now,
        registration_note=payload.registration_note.strip(),
        rate_limit_per_minute=default_user_rate_limit(session),
        must_change_password=False,
        email_verified_at=now if settings.email_test_mode else None,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "REGISTRATION_CONFLICT", "message": "用户名或邮箱已存在"},
        ) from error
    session.add(UserRole(user_id=user.id, role_id=role.id))
    token = issue_action_token(
        session,
        user_id=user.id,
        purpose="verify_registration",
        target_email=email,
        lifetime=timedelta(hours=24),
    )
    verification_url = f"{settings.frontend_origin.rstrip('/')}/verify-email?token={token}"
    content = registration_verification(user.display_name or user.username, verification_url, brand_name=get_effective_platform_settings(session).site_name)
    queue_email(
        session,
        recipient=email,
        subject=content.subject,
        body_text=content.body_text,
    )
    log_admin_event(
        session,
        admin_user_id=user.id,
        action="user_registered",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={"username": user.username, "email": user.email or "", "registration_note": user.registration_note},
    )
    session.commit()
    return user_view(session, user)


@router.post("/auth/verify-email")
def verify_registration_email(
    payload: TokenRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> dict[str, bool]:
    token = get_valid_action_token(session, token=payload.token, purpose="verify_registration")
    if not token:
        raise HTTPException(status_code=422, detail="验证链接无效或已过期")
    user = session.get(User, token.user_id)
    if not user or user.email != token.target_email:
        raise HTTPException(status_code=422, detail="验证链接与当前账号不匹配")
    user.email_verified_at = utc_now()
    consume_action_token(token)
    log_admin_event(
        session,
        admin_user_id=user.id,
        action="email_verified",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={"email": user.email or ""},
    )
    session.commit()
    return {"ok": True}


@router.post("/auth/resend-verification", status_code=202)
def resend_verification(payload: EmailAddressRequest, session: Session = Depends(get_db)) -> dict[str, bool]:
    settings = get_settings()
    if not get_effective_platform_settings(session).smtp_configured:
        raise HTTPException(status_code=503, detail="邮件服务未配置")
    user = session.scalar(select(User).where(func.lower(User.email) == payload.email))
    if (
        user
        and user.email_verified_at is None
        and user.approval_status == ApprovalStatus.PENDING.value
        and not _action_token_in_cooldown(session, user_id=user.id, purpose="verify_registration")
    ):
        token = issue_action_token(
            session,
            user_id=user.id,
            purpose="verify_registration",
            target_email=payload.email,
            lifetime=timedelta(hours=24),
        )
        action_url = f"{settings.frontend_origin.rstrip('/')}/verify-email?token={token}"
        content = registration_verification(user.display_name or user.username, action_url, resent=True, brand_name=get_effective_platform_settings(session).site_name)
        queue_email(
            session,
            recipient=payload.email,
            subject=content.subject,
            body_text=content.body_text,
        )
        session.commit()
    return {"ok": True}


@router.post("/auth/forgot-password", status_code=202)
def forgot_password(payload: EmailAddressRequest, session: Session = Depends(get_db)) -> dict[str, bool]:
    settings = get_settings()
    if not get_effective_platform_settings(session).smtp_configured:
        raise HTTPException(status_code=503, detail="邮件服务未配置，请联系管理员重置密码")
    user = session.scalar(select(User).where(func.lower(User.email) == payload.email))
    if (
        user
        and user.email_verified_at is not None
        and user.approval_status == ApprovalStatus.APPROVED.value
        and not _action_token_in_cooldown(session, user_id=user.id, purpose="reset_password")
    ):
        token = issue_action_token(
            session,
            user_id=user.id,
            purpose="reset_password",
            target_email=payload.email,
            lifetime=timedelta(minutes=30),
        )
        action_url = f"{settings.frontend_origin.rstrip('/')}/reset-password?token={token}"
        content = password_reset(user.display_name or user.username, action_url, brand_name=get_effective_platform_settings(session).site_name)
        queue_email(
            session,
            recipient=payload.email,
            subject=content.subject,
            body_text=content.body_text,
        )
        session.commit()
    return {"ok": True}


@router.post("/auth/reset-password")
def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> dict[str, bool]:
    token = get_valid_action_token(session, token=payload.token, purpose="reset_password")
    if not token:
        raise HTTPException(status_code=422, detail="重置链接无效或已过期")
    user = session.get(User, token.user_id)
    if not user:
        raise HTTPException(status_code=422, detail="重置链接无效或已过期")
    try:
        validate_password(payload.new_password, user.username)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.password_changed_at = utc_now()
    user.session_version += 1
    consume_action_token(token)
    add_notification(
        session,
        user_id=user.id,
        kind="security",
        title="密码已通过找回流程重置",
        body="若非本人操作，请立即联系管理员停用账号。",
        action_url="/account",
    )
    log_admin_event(
        session,
        admin_user_id=user.id,
        action="password_reset",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={},
    )
    session.commit()
    return {"ok": True}


@router.post("/auth/login", response_model=UserView)
def login(payload: LoginRequest, request: Request, response: Response, session: Session = Depends(get_db)) -> UserView:
    username = payload.username.strip()
    consume_login_attempt(request, username)
    user = session.scalar(select(User).where(User.username_key == normalize_username_key(username)))
    now = datetime.now(timezone.utc)
    password_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
    is_valid_password = verify_password(payload.password, password_hash)
    if not user or not is_valid_password:
        if user:
            user.failed_login_count += 1
            locked = False
            if get_settings().login_protection_enabled and user.failed_login_count >= get_settings().login_failure_limit:
                user.locked_until = now + timedelta(minutes=get_settings().lock_minutes)
                user.failed_login_count = 0
                locked = True
            log_admin_event(
                session,
                admin_user_id=user.id,
                action="user_login_failed",
                target_type="user",
                target_id=user.id,
                request_id=request_id(request),
                detail={"reason": "invalid_credentials", "account_locked": locked},
            )
            session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if get_settings().login_protection_enabled and user.locked_until:
        locked_until = user.locked_until.replace(tzinfo=timezone.utc) if user.locked_until.tzinfo is None else user.locked_until
        if locked_until > now:
            log_admin_event(
                session,
                admin_user_id=user.id,
                action="user_login_failed",
                target_type="user",
                target_id=user.id,
                request_id=request_id(request),
                detail={"reason": "account_locked", "account_locked": True},
            )
            session.commit()
            raise HTTPException(status_code=status.HTTP_423_LOCKED, detail="账号暂时锁定")
    try:
        _raise_account_state(user)
    except HTTPException as error:
        log_admin_event(
            session,
            admin_user_id=user.id,
            action="user_login_failed",
            target_type="user",
            target_id=user.id,
            request_id=request_id(request),
            detail={"reason": "account_state", "status_code": error.status_code},
        )
        session.commit()
        raise
    user.failed_login_count = 0
    user.locked_until = None
    log_admin_event(
        session,
        admin_user_id=user.id,
        action="user_logged_in",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={},
    )
    session.commit()
    set_session_cookies(response, user)
    return user_view(session, user)


@router.post("/auth/logout")
def logout(
    response: Response,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, bool]:
    validate_csrf(request)
    user.session_version += 1
    log_admin_event(
        session,
        admin_user_id=user.id,
        action="user_logged_out",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={},
    )
    session.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    response.delete_cookie("agw_csrf", path="/")
    return {"ok": True}


@router.get("/me", response_model=UserView)
def me(user: User = Depends(get_current_user), session: Session = Depends(get_db)) -> UserView:
    return user_view(session, user)


@router.patch("/me/profile", response_model=UserView)
def update_profile(
    payload: ProfileUpdateRequest,
    request: Request,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> UserView:
    validate_csrf(request)
    user.display_name = payload.display_name
    log_admin_event(
        session,
        admin_user_id=user.id,
        action="profile_updated",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={"display_name": user.display_name or ""},
    )
    session.commit()
    return user_view(session, user)


@router.post("/me/change-email", status_code=202)
def request_email_change(
    payload: EmailAddressRequest,
    request: Request,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> dict[str, bool]:
    validate_csrf(request)
    settings = get_settings()
    if not get_effective_platform_settings(session).smtp_configured:
        raise HTTPException(status_code=503, detail="邮件服务未配置，暂时无法更换邮箱")
    if payload.email == (user.email or "").lower():
        raise HTTPException(status_code=409, detail="新邮箱与当前邮箱相同")
    if session.scalar(select(User.id).where(func.lower(User.email) == payload.email, User.id != user.id)):
        raise HTTPException(status_code=409, detail="邮箱已被其它账号使用")
    user.pending_email = payload.email
    token = issue_action_token(
        session,
        user_id=user.id,
        purpose="change_email",
        target_email=payload.email,
        lifetime=timedelta(hours=24),
    )
    action_url = f"{settings.frontend_origin.rstrip('/')}/verify-email?purpose=change&token={token}"
    content = email_change(user.display_name or user.username, action_url, brand_name=get_effective_platform_settings(session).site_name)
    queue_email(
        session,
        recipient=payload.email,
        subject=content.subject,
        body_text=content.body_text,
    )
    session.commit()
    return {"ok": True}


@router.post("/auth/verify-email-change")
def verify_email_change(payload: TokenRequest, request: Request, session: Session = Depends(get_db)) -> dict[str, bool]:
    token = get_valid_action_token(session, token=payload.token, purpose="change_email")
    if not token:
        raise HTTPException(status_code=422, detail="邮箱变更链接无效或已过期")
    user = session.get(User, token.user_id)
    if not user or user.pending_email != token.target_email:
        raise HTTPException(status_code=422, detail="邮箱变更链接与当前账号不匹配")
    if session.scalar(select(User.id).where(func.lower(User.email) == token.target_email, User.id != user.id)):
        raise HTTPException(status_code=409, detail="邮箱已被其它账号使用")
    old_email = user.email or ""
    user.email = token.target_email
    user.pending_email = None
    user.email_verified_at = utc_now()
    consume_action_token(token)
    add_notification(
        session,
        user_id=user.id,
        kind="security",
        title="登录邮箱已更新",
        body="后续安全邮件将发送到新邮箱。",
        action_url="/account",
    )
    log_admin_event(
        session,
        admin_user_id=user.id,
        action="email_changed",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={"old_email": old_email, "new_email": user.email},
    )
    session.commit()
    return {"ok": True}


@router.post("/auth/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, bool]:
    validate_csrf(request)
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码错误")
    try:
        validate_password(payload.new_password, user.username)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.password_changed_at = datetime.now(timezone.utc)
    user.session_version += 1
    add_notification(
        session,
        user_id=user.id,
        kind="security",
        title="账号密码已更新",
        body="全部旧登录状态已失效。",
        action_url="/account",
    )
    log_admin_event(
        session,
        admin_user_id=user.id,
        action="password_changed",
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        detail={},
    )
    session.commit()
    set_session_cookies(response, user)
    return {"ok": True}


@router.get("/me/api-keys", response_model=list[ApiKeyView])
def list_api_keys(user: User = Depends(require_not_password_change), session: Session = Depends(get_db)) -> list[ApiKeyView]:
    keys = session.scalars(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())).all()
    return [api_key_view(key) for key in keys]


@router.post("/me/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_user_api_key(
    payload: ApiKeyCreateRequest,
    request: Request,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> ApiKeyCreated:
    validate_csrf(request)
    if payload.expires_at is not None and ("expires_in_days" in payload.model_fields_set or payload.permanent):
        raise HTTPException(status_code=422, detail="expires_at 不能与 expires_in_days 或 permanent 同时使用")
    if payload.permanent:
        expires_at = None
    elif payload.expires_at is not None:
        expires_at = payload.expires_at
    else:
        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days or 90)
    if expires_at and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="过期时间必须晚于当前时间")
    secret, prefix, secret_hash = create_api_key()
    key = ApiKey(
        user_id=user.id,
        label=payload.label.strip(),
        prefix=prefix,
        secret_hash=secret_hash,
        scopes="*",
        status=KeyStatus.ACTIVE.value,
        expires_at=expires_at,
    )
    session.add(key)
    session.flush()
    log_admin_event(
        session,
        admin_user_id=user.id,
        action="api_key_created",
        target_type="api_key",
        target_id=key.id,
        request_id=request_id(request),
        detail={"user_id": user.id, "prefix": prefix, "scopes": ["*"], "label": key.label},
    )
    session.commit()
    session.refresh(key)
    return ApiKeyCreated(**api_key_view(key).model_dump(), secret=secret)


@router.patch("/me/api-keys/{key_id}/disable", response_model=ApiKeyView)
def disable_user_api_key(
    key_id: str,
    request: Request,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> ApiKeyView:
    validate_csrf(request)
    key = session.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id))
    if not key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    if key.status == KeyStatus.REVOKED.value:
        raise HTTPException(status_code=409, detail="已撤销的 API Key 不可禁用")
    if key.status != KeyStatus.ACTIVE.value or api_key_is_expired(key):
        raise HTTPException(status_code=409, detail="API Key 已不可用，请刷新列表")
    key.status = KeyStatus.DISABLED.value
    key.disabled_at = datetime.now(timezone.utc)
    key.disabled_by_user_id = user.id
    key.disable_reason = "用户主动禁用"
    log_admin_event(
        session,
        admin_user_id=user.id,
        action="api_key_disabled",
        target_type="api_key",
        target_id=key.id,
        request_id=request_id(request),
        detail={"user_id": user.id, "prefix": key.prefix, "key_prefix": key.prefix},
    )
    session.commit()
    return api_key_view(key)


@router.patch("/me/api-keys/{key_id}/enable", response_model=ApiKeyView)
def enable_user_api_key(
    key_id: str,
    request: Request,
    user: User = Depends(require_not_password_change),
    session: Session = Depends(get_db),
) -> ApiKeyView:
    """恢复用户自己主动禁用且仍在有效期内的 API Key。"""

    validate_csrf(request)
    key = session.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id))
    if not key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    if key.status == KeyStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail="API Key 已启用，请刷新列表")
    if key.status == KeyStatus.REVOKED.value:
        raise HTTPException(status_code=409, detail="已撤销的 API Key 不可恢复")
    if api_key_is_expired(key):
        raise HTTPException(status_code=409, detail="已过期的 API Key 不可恢复，请重新生成")
    if key.status != KeyStatus.DISABLED.value:
        raise HTTPException(status_code=409, detail="该 API Key 当前状态不可恢复")
    if key.disabled_by_user_id != user.id or key.disable_reason != "用户主动禁用":
        raise HTTPException(status_code=403, detail="该 API Key 由管理员禁用，请联系管理员处理")

    previous = {
        "user_id": user.id,
        "prefix": key.prefix,
        "disabled_at": key.disabled_at.isoformat() if key.disabled_at else None,
        "disabled_by_user_id": key.disabled_by_user_id,
        "disable_reason": key.disable_reason,
    }
    key.status = KeyStatus.ACTIVE.value
    key.disabled_at = None
    key.disabled_by_user_id = None
    key.disable_reason = ""
    log_admin_event(
        session,
        admin_user_id=user.id,
        action="api_key_enabled",
        target_type="api_key",
        target_id=key.id,
        request_id=request_id(request),
        detail=previous,
    )
    session.commit()
    return api_key_view(key)
