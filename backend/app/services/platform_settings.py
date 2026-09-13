"""读取和初始化数据库平台设置，并兼容未迁移前的环境变量配置。"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import formataddr

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import PlatformSettings
from .security import decrypt_secret, encrypt_secret


@dataclass(frozen=True)
class EffectivePlatformSettings:
    smtp_enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from_email: str
    smtp_from_name: str
    smtp_use_tls: bool
    smtp_timeout_seconds: int
    default_user_rate_limit_per_minute: int
    default_user_storage_quota_bytes: int
    default_file_retention_days: int
    public_gateway_base_url: str
    gateway_source: str
    source: str
    site_name: str
    site_subtitle: str
    registration_enabled: bool
    brand_image_url: str

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_enabled and self.smtp_host.strip() and self.smtp_from_email.strip())

    @property
    def smtp_from_header(self) -> str:
        return formataddr((self.smtp_from_name.strip(), self.smtp_from_email.strip()))


def get_effective_platform_settings(session: Session) -> EffectivePlatformSettings:
    row = session.get(PlatformSettings, 1)
    runtime = get_settings()
    if row is None:
        return EffectivePlatformSettings(
            smtp_enabled=runtime.smtp_configured,
            smtp_host=runtime.smtp_host.strip(),
            smtp_port=runtime.smtp_port,
            smtp_username=runtime.smtp_username.strip(),
            smtp_password=runtime.smtp_password,
            smtp_from_email=runtime.smtp_from.strip(),
            smtp_from_name="API Gateway",
            smtp_use_tls=runtime.smtp_use_tls,
            smtp_timeout_seconds=runtime.smtp_timeout_seconds,
            default_user_rate_limit_per_minute=0,
            default_user_storage_quota_bytes=107_374_182_400,
            default_file_retention_days=90,
            public_gateway_base_url=runtime.public_gateway_base_url,
            gateway_source="environment" if runtime.public_gateway_base_url else "browser",
            source="environment",
            site_name="API Gateway",
            site_subtitle="开发者统一工作台",
            registration_enabled=True,
            brand_image_url="",
        )
    return EffectivePlatformSettings(
        smtp_enabled=row.smtp_enabled,
        smtp_host=row.smtp_host.strip(),
        smtp_port=row.smtp_port,
        smtp_username=row.smtp_username.strip(),
        smtp_password=decrypt_secret(row.smtp_password_ciphertext),
        smtp_from_email=row.smtp_from_email.strip(),
        smtp_from_name=row.smtp_from_name.strip(),
        smtp_use_tls=row.smtp_use_tls,
        smtp_timeout_seconds=runtime.smtp_timeout_seconds,
        default_user_rate_limit_per_minute=row.default_user_rate_limit_per_minute,
        default_user_storage_quota_bytes=row.default_user_storage_quota_bytes,
        default_file_retention_days=row.default_file_retention_days,
        public_gateway_base_url=(row.public_gateway_base_url.strip() or runtime.public_gateway_base_url),
        gateway_source="database" if row.public_gateway_base_url.strip() else "environment" if runtime.public_gateway_base_url else "browser",
        source="database",
        site_name=row.site_name.strip() or "API Gateway",
        site_subtitle=row.site_subtitle.strip() or "开发者统一工作台",
        registration_enabled=row.registration_enabled,
        brand_image_url=row.brand_image_url.strip(),
    )


def ensure_platform_settings(session: Session, *, updated_by_user_id: str) -> PlatformSettings:
    row = session.get(PlatformSettings, 1)
    if row is not None:
        row.updated_by_user_id = updated_by_user_id
        return row
    current = get_effective_platform_settings(session)
    row = PlatformSettings(
        id=1,
        smtp_enabled=current.smtp_enabled,
        smtp_host=current.smtp_host,
        smtp_port=current.smtp_port,
        smtp_username=current.smtp_username,
        smtp_password_ciphertext=encrypt_secret(current.smtp_password),
        smtp_from_email=current.smtp_from_email,
        smtp_from_name=current.smtp_from_name,
        smtp_use_tls=current.smtp_use_tls,
        default_user_rate_limit_per_minute=current.default_user_rate_limit_per_minute,
        default_user_storage_quota_bytes=current.default_user_storage_quota_bytes,
        default_file_retention_days=current.default_file_retention_days,
        # 创建数据库设置行时保留环境变量回退，避免保存邮箱设置意外固化网关地址。
        public_gateway_base_url="",
        site_name=current.site_name,
        site_subtitle=current.site_subtitle,
        registration_enabled=current.registration_enabled,
        brand_image_url=current.brand_image_url,
        updated_by_user_id=updated_by_user_id,
    )
    session.add(row)
    session.flush()
    return row


def default_user_rate_limit(session: Session) -> int:
    return get_effective_platform_settings(session).default_user_rate_limit_per_minute
