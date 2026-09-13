"""路由依赖：身份、角色、请求 ID 与 CSRF 校验。"""

from __future__ import annotations

from datetime import datetime, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import ApprovalStatus, Role, User, UserRole
from ..services.security import decode_session_token


def request_id(request: Request) -> str:
    return request.state.request_id


def get_user_roles(session: Session, user_id: str) -> list[str]:
    statement = select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
    return list(session.scalars(statement).all())


def get_current_user(request: Request, session: Session = Depends(get_db)) -> User:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = decode_session_token(token)
        user_id = str(payload["sub"])
        session_version = int(payload["sv"])
    except (jwt.InvalidTokenError, KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效") from error
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
    if user.approval_status != ApprovalStatus.APPROVED.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号尚未审批通过")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")
    if user.session_version != session_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效")
    if user.locked_until:
        locked_until = user.locked_until.replace(tzinfo=timezone.utc) if user.locked_until.tzinfo is None else user.locked_until
        if locked_until > datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_423_LOCKED, detail="账号暂时锁定")
    return user


def require_not_password_change(user: User = Depends(get_current_user)) -> User:
    if user.must_change_password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="请先修改临时密码")
    return user


def require_admin(user: User = Depends(require_not_password_change), session: Session = Depends(get_db)) -> User:
    if "admin" not in get_user_roles(session, user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def validate_csrf(request: Request) -> None:
    """同源前端必须回传双提交 CSRF 值，同时限制 Origin。"""
    origin = request.headers.get("origin")
    settings = get_settings()
    if origin and origin.rstrip("/") != settings.frontend_origin.rstrip("/"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="来源校验失败")
    cookie_value = request.cookies.get("agw_csrf")
    header_value = request.headers.get("x-csrf-token")
    if not cookie_value or not header_value or cookie_value != header_value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")
