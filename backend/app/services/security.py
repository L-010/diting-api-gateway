"""密码、会话和密钥处理。所有秘密只保存不可逆哈希或密文。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet
from pwdlib import PasswordHash

from ..config import get_settings


PASSWORD_HASHER = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("GatewayDummyPassword2026!")


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return PASSWORD_HASHER.verify(password, password_hash)


def validate_password(password: str, username: str | None = None) -> None:
    if len(password) < 12:
        raise ValueError("密码长度至少为 12 个字符")
    if not any(char.islower() for char in password) or not any(char.isupper() for char in password):
        raise ValueError("密码必须同时包含大小写字母")
    if not any(char.isdigit() for char in password):
        raise ValueError("密码必须包含数字")
    if username and username.lower() in password.lower():
        raise ValueError("密码不能包含用户名")


def create_session_token(user_id: str, must_change_password: bool, session_version: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "mcp": must_change_password,
        "sv": session_version,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm="HS256")


def decode_session_token(token: str) -> dict[str, object]:
    return jwt.decode(token, get_settings().app_secret_key, algorithms=["HS256"])


def create_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _api_key_hash(secret: str) -> str:
    return hmac.new(
        get_settings().api_key_pepper.encode("utf-8"),
        secret.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_api_key() -> tuple[str, str, str]:
    secret = f"agw_{secrets.token_urlsafe(32)}"
    return secret, secret[:16], _api_key_hash(secret)


def hash_api_key(secret: str) -> str:
    return _api_key_hash(secret)


def hash_idempotency_key(value: str) -> str:
    """使用服务端 pepper 计算幂等键摘要，不在数据库保存原始请求头。"""
    return hmac.new(
        get_settings().api_key_pepper.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return Fernet(get_settings().app_encryption_key.encode("utf-8")).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    return Fernet(get_settings().app_encryption_key.encode("utf-8")).decrypt(value.encode("utf-8")).decode("utf-8")
