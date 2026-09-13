"""生产目标环境预检；本脚本不会修改数据库、文件或外部服务。"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from cryptography.fernet import Fernet


REQUIRED = (
    "APP_ENV",
    "APP_SECRET_KEY",
    "APP_ENCRYPTION_KEY",
    "API_KEY_PEPPER",
    "DATABASE_URL",
    "ALLOWED_UPSTREAM_HOSTS",
    "TOMODD_BASE_URL",
    "FRONTEND_ORIGIN",
)
PLACEHOLDER_MARKERS = ("replace-with-", "changeme", "example.test")


def _status(name: str, ok: bool, reason: str) -> bool:
    print(f"{name}={'ok' if ok else 'failed'} reason={reason}")
    return ok


def main() -> int:
    """要求显式目标环境标记，并以非零状态拒绝不完整配置。"""

    checks: list[bool] = []
    checks.append(_status("target_marker", os.getenv("RELEASE_TARGET_ENV") == "production", "RELEASE_TARGET_ENV"))
    checks.append(
        _status(
            "execution_ack",
            os.getenv("RELEASE_VALIDATION_ACK") == "I_UNDERSTAND_TARGET_ENV",
            "RELEASE_VALIDATION_ACK",
        )
    )
    for name in REQUIRED:
        value = os.getenv(name, "").strip()
        present = bool(value) and not any(marker in value.lower() for marker in PLACEHOLDER_MARKERS)
        checks.append(_status(name, present, "present" if present else "missing_or_placeholder"))

    checks.append(_status("app_env_value", os.getenv("APP_ENV", "").strip().lower() == "production", "APP_ENV=production"))

    secret_key = os.getenv("APP_SECRET_KEY", "")
    pepper = os.getenv("API_KEY_PEPPER", "")
    checks.append(_status("secret_lengths", len(secret_key) >= 32 and len(pepper) >= 24, "minimum_lengths"))
    try:
        Fernet(os.getenv("APP_ENCRYPTION_KEY", "").encode("utf-8"))
        encryption_ok = True
    except Exception:
        encryption_ok = False
    checks.append(_status("encryption_key_format", encryption_ok, "fernet"))

    database_url = os.getenv("DATABASE_URL", "").strip()
    parsed_database = urlparse(database_url)
    sqlite_exception = os.getenv("PRODUCTION_SQLITE_EXCEPTION_APPROVED", "").lower() == "true"
    database_is_supported = bool(parsed_database.scheme) and (
        parsed_database.scheme not in {"sqlite", "sqlite+aiosqlite"} or sqlite_exception
    )
    checks.append(
        _status(
            "database_policy",
            database_is_supported,
            "non_sqlite_or_explicit_single_instance_exception" if database_is_supported else "sqlite_requires_approved_exception",
        )
    )

    upstream_hosts = {item.strip().lower() for item in os.getenv("ALLOWED_UPSTREAM_HOSTS", "").split(",") if item.strip()}
    tomodd_host = urlparse(os.getenv("TOMODD_BASE_URL", "")).hostname
    checks.append(_status("tomodd_host_policy", bool(tomodd_host and tomodd_host.lower() in upstream_hosts), "allowlisted"))
    checks.append(_status("email_test_mode", os.getenv("EMAIL_TEST_MODE", "false").lower() != "true", "disabled_in_production"))

    workers_raw = os.getenv("AGW_WORKERS", "1")
    workers_ok = bool(re.fullmatch(r"[1-8]", workers_raw))
    if parsed_database.scheme.startswith("sqlite") and sqlite_exception:
        workers_ok = workers_ok and workers_raw == "1" and os.getenv("AGW_MULTI_INSTANCE", "false").lower() == "false"
    checks.append(_status("worker_policy", workers_ok, "bounded_and_compatible" if workers_ok else "invalid_or_incompatible"))

    frontend = os.getenv("FRONTEND_ORIGIN", "").strip()
    frontend_ok = bool(urlparse(frontend).scheme in {"http", "https"} and urlparse(frontend).hostname)
    checks.append(_status("frontend_origin", frontend_ok, "absolute_url" if frontend_ok else "invalid_url"))

    print(f"preflight={'passed' if all(checks) else 'failed'} checks={len(checks)}")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
