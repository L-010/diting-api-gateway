"""集中管理应用配置，禁止在业务代码中直接读取环境变量。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """运行时配置。开发环境也必须使用显式密钥。"""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_secret_key: str = ""
    app_encryption_key: str = ""
    api_key_pepper: str = ""
    database_url: str = "sqlite:///./data/api_gateway.db"
    allowed_upstream_hosts: str = "127.0.0.1,localhost"
    tomodd_base_url: str = "http://127.0.0.1:18000"
    tomodd_upstream_token: str = ""
    max_upload_bytes: int = Field(default=104_857_600, ge=1_048_576)
    max_download_bytes: int = Field(default=1_073_741_824, ge=1_048_576)
    max_remote_file_bytes: int = Field(default=53_687_091_200, ge=1_048_576)
    file_download_concurrency_per_user: int = Field(default=4, ge=1, le=64)
    file_download_concurrency_per_tool: int = Field(default=16, ge=1, le=256)
    file_download_authorization_minutes: int = Field(default=5, ge=1, le=30)
    file_download_lease_seconds: int = Field(default=180, ge=60, le=3_600)
    file_sync_poll_seconds: int = Field(default=15, ge=5, le=300)
    file_verify_interval_seconds: int = Field(default=86_400, ge=300, le=604_800)
    storage_watermark_max_age_seconds: int = Field(default=300, ge=30, le=86_400)
    file_sync_manifest_max_bytes: int = Field(default=1_048_576, ge=65_536, le=10_485_760)
    file_sync_manifest_max_items: int = Field(default=1_000, ge=1, le=10_000)
    session_cookie_name: str = "agw_session"
    frontend_origin: str = "http://127.0.0.1:3000"
    public_gateway_base_url: str = ""
    brand_asset_dir: str = "./data/brand-assets"
    access_token_minutes: int = Field(default=480, ge=5, le=1_440)
    login_protection_enabled: bool = True
    login_failure_limit: int = Field(default=5, ge=3, le=20)
    lock_minutes: int = Field(default=15, ge=1, le=120)
    call_log_retention_days: int = Field(default=90, ge=1, le=3_650)
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65_535)
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    smtp_timeout_seconds: int = Field(default=10, ge=1, le=60)
    email_outbox_max_attempts: int = Field(default=5, ge=1, le=20)
    support_contact: str = ""
    email_test_mode: bool = False

    @field_validator("tomodd_base_url")
    @classmethod
    def validate_tomodd_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("TOMODD_BASE_URL 必须是 http 或 https URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("TOMODD_BASE_URL 不能包含凭据、查询参数或片段")
        return value.rstrip("/")

    @field_validator("public_gateway_base_url")
    @classmethod
    def validate_public_gateway_base_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned:
            return ""
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("PUBLIC_GATEWAY_BASE_URL 必须是 http 或 https 根地址")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("PUBLIC_GATEWAY_BASE_URL 不能包含凭据、路径、查询参数或片段")
        return cleaned

    @property
    def upstream_hosts(self) -> set[str]:
        return {item.strip().lower() for item in self.allowed_upstream_hosts.split(",") if item.strip()}

    @property
    def smtp_configured(self) -> bool:
        """公开注册依赖可真实投递的 SMTP 配置。"""
        return bool(self.smtp_host.strip() and self.smtp_from.strip())

    def validate_runtime(self) -> None:
        """在启动阶段拒绝不安全或不完整的配置。"""
        missing = [
            name
            for name, value in {
                "APP_SECRET_KEY": self.app_secret_key,
                "APP_ENCRYPTION_KEY": self.app_encryption_key,
                "API_KEY_PEPPER": self.api_key_pepper,
            }.items()
            if not value or value.startswith("replace-with-")
        ]
        if missing:
            raise RuntimeError(f"缺少必要安全配置: {', '.join(missing)}")
        if len(self.app_secret_key) < 32 or len(self.api_key_pepper) < 24:
            raise RuntimeError("APP_SECRET_KEY 和 API_KEY_PEPPER 长度不足")
        if self.email_test_mode and self.app_env not in {"development", "test"}:
            raise RuntimeError("EMAIL_TEST_MODE 只能在 development 或 test 环境使用")
        try:
            Fernet(self.app_encryption_key.encode("utf-8"))
        except Exception as error:
            raise RuntimeError("APP_ENCRYPTION_KEY 不是有效 Fernet 密钥") from error
        hostname = urlparse(self.tomodd_base_url).hostname
        if not hostname or hostname.lower() not in self.upstream_hosts:
            raise RuntimeError("TOMODD_BASE_URL 主机不在 ALLOWED_UPSTREAM_HOSTS 白名单中")
        database_path = self.sqlite_path
        if database_path:
            database_path.parent.mkdir(parents=True, exist_ok=True)
        Path(self.brand_asset_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        path = Path(self.database_url.removeprefix(prefix))
        return path if path.is_absolute() else ROOT_DIR / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
