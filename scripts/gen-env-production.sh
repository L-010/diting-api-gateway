#!/usr/bin/env bash
set -Eeuo pipefail

# 生成 HTTP 直连模式的生产配置文件。

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

die() {
  echo "错误: $*" >&2
  exit 1
}

command -v openssl >/dev/null 2>&1 || die "缺少 openssl，请先安装。"
command -v python3 >/dev/null 2>&1 || die "缺少 python3，请先安装。"

if [[ -f .env.production ]]; then
  read -r -p ".env.production 已存在，是否覆盖？(y/N): " answer
  [[ "$answer" =~ ^[Yy]$ ]] || { echo "已取消。"; exit 0; }
fi

random_secret() {
  openssl rand -base64 32 | tr -d '\n'
}

random_fernet() {
  python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode(), end="")'
}

random_hex() {
  openssl rand -hex 24 | tr -d '\n'
}

validate_origin() {
  local value="$1"
  python3 - "$value" <<'PY'
from sys import argv
from urllib.parse import urlparse

value = argv[1].strip().rstrip("/")
parsed = urlparse(value)
if parsed.scheme not in {"http", "https"} or not parsed.hostname:
    raise SystemExit("地址必须是带主机名的 http/https 地址")
if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
    raise SystemExit("地址不能包含凭据、路径、查询参数或片段")
PY
}

validate_upstream_allowed() {
  python3 - "$1" "$2" <<'PY'
from sys import argv
from urllib.parse import urlparse

hostname = urlparse(argv[1]).hostname
allowed = {item.strip().lower() for item in argv[2].split(",") if item.strip()}
if not hostname or hostname.lower() not in allowed:
    raise SystemExit("TomoDD 地址的主机不在允许列表中")
PY
}

read -r -p "前端访问地址 [http://10.2.4.46:3000]: " FRONTEND_ORIGIN
FRONTEND_ORIGIN="${FRONTEND_ORIGIN:-http://10.2.4.46:3000}"
validate_origin "$FRONTEND_ORIGIN" || die "前端访问地址格式不正确。"
read -r -p "公开网关地址 [http://10.2.4.46:8000]: " PUBLIC_GATEWAY_BASE_URL
PUBLIC_GATEWAY_BASE_URL="${PUBLIC_GATEWAY_BASE_URL:-http://10.2.4.46:8000}"
validate_origin "$PUBLIC_GATEWAY_BASE_URL" || die "公开网关地址格式不正确。"
read -r -p "TomoDD 上游地址 [http://host.docker.internal:18000]: " TOMODD_BASE_URL
TOMODD_BASE_URL="${TOMODD_BASE_URL:-http://host.docker.internal:18000}"
validate_origin "$TOMODD_BASE_URL" || die "TomoDD 上游地址格式不正确。"
read -r -p "允许的上游主机 [host.docker.internal,127.0.0.1,localhost]: " ALLOWED_UPSTREAM_HOSTS
ALLOWED_UPSTREAM_HOSTS="${ALLOWED_UPSTREAM_HOSTS:-host.docker.internal,127.0.0.1,localhost}"
validate_upstream_allowed "$TOMODD_BASE_URL" "$ALLOWED_UPSTREAM_HOSTS" || die "允许的上游主机配置不匹配。"
read -r -s -p "TomoDD 上游 Token（没有可留空）: " TOMODD_UPSTREAM_TOKEN
echo

read -r -s -p "数据库用户密码（留空自动生成，仅允许字母和数字）: " MYSQL_PASSWORD
echo
MYSQL_PASSWORD="${MYSQL_PASSWORD:-$(random_hex)}"
[[ "$MYSQL_PASSWORD" =~ ^[A-Za-z0-9]+$ ]] || die "数据库用户密码只能包含字母和数字，以避免 URL 编码错误。"

read -r -s -p "MySQL root 密码（留空自动生成，仅允许字母和数字）: " MYSQL_ROOT_PASSWORD
echo
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-$(random_hex)}"
[[ "$MYSQL_ROOT_PASSWORD" =~ ^[A-Za-z0-9]+$ ]] || die "MySQL root 密码只能包含字母和数字。"
[[ "$MYSQL_PASSWORD" != "$MYSQL_ROOT_PASSWORD" ]] || die "数据库用户密码和 root 密码必须不同。"

APP_SECRET_KEY="$(random_secret)"
APP_ENCRYPTION_KEY="$(random_fernet)"
API_KEY_PEPPER="$(random_secret)"

export APP_SECRET_KEY APP_ENCRYPTION_KEY API_KEY_PEPPER
export MYSQL_PASSWORD MYSQL_ROOT_PASSWORD
export FRONTEND_ORIGIN PUBLIC_GATEWAY_BASE_URL TOMODD_BASE_URL ALLOWED_UPSTREAM_HOSTS TOMODD_UPSTREAM_TOKEN

python3 - <<'PY'
from pathlib import Path
import os

values = {
    "APP_ENV": "production",
    "APP_SECRET_KEY": os.environ["APP_SECRET_KEY"],
    "APP_ENCRYPTION_KEY": os.environ["APP_ENCRYPTION_KEY"],
    "API_KEY_PEPPER": os.environ["API_KEY_PEPPER"],
    "MYSQL_DATABASE": "api_gateway",
    "MYSQL_USER": "api_user",
    "MYSQL_PASSWORD": os.environ["MYSQL_PASSWORD"],
    "MYSQL_ROOT_PASSWORD": os.environ["MYSQL_ROOT_PASSWORD"],
    "DATABASE_URL": f"mysql+pymysql://api_user:{os.environ['MYSQL_PASSWORD']}@mysql:3306/api_gateway?charset=utf8mb4",
    "ALLOWED_UPSTREAM_HOSTS": os.environ["ALLOWED_UPSTREAM_HOSTS"],
    "TOMODD_BASE_URL": os.environ["TOMODD_BASE_URL"],
    "TOMODD_UPSTREAM_TOKEN": os.environ["TOMODD_UPSTREAM_TOKEN"],
    "MAX_UPLOAD_BYTES": "104857600",
    "MAX_DOWNLOAD_BYTES": "1073741824",
    "MAX_REMOTE_FILE_BYTES": "53687091200",
    "FILE_DOWNLOAD_CONCURRENCY_PER_USER": "4",
    "FILE_DOWNLOAD_CONCURRENCY_PER_TOOL": "16",
    "FILE_DOWNLOAD_AUTHORIZATION_MINUTES": "5",
    "FILE_DOWNLOAD_LEASE_SECONDS": "180",
    "FILE_SYNC_POLL_SECONDS": "15",
    "FILE_VERIFY_INTERVAL_SECONDS": "86400",
    "STORAGE_WATERMARK_MAX_AGE_SECONDS": "300",
    "FILE_SYNC_MANIFEST_MAX_BYTES": "1048576",
    "FILE_SYNC_MANIFEST_MAX_ITEMS": "1000",
    "BRAND_ASSET_DIR": "/app/data/brand-assets",
    "FRONTEND_ORIGIN": os.environ["FRONTEND_ORIGIN"],
    "PUBLIC_GATEWAY_BASE_URL": os.environ["PUBLIC_GATEWAY_BASE_URL"],
    "SESSION_COOKIE_NAME": "agw_session",
    "SECURE_COOKIES": "false",
    "ACCESS_TOKEN_MINUTES": "480",
    "LOGIN_PROTECTION_ENABLED": "true",
    "LOGIN_FAILURE_LIMIT": "5",
    "LOCK_MINUTES": "15",
    "CALL_LOG_RETENTION_DAYS": "90",
    "SMTP_HOST": "",
    "SMTP_PORT": "587",
    "SMTP_USERNAME": "",
    "SMTP_PASSWORD": "",
    "SMTP_FROM": "",
    "SMTP_USE_TLS": "true",
    "SMTP_TIMEOUT_SECONDS": "10",
    "EMAIL_OUTBOX_MAX_ATTEMPTS": "5",
    "SUPPORT_CONTACT": "",
    "EMAIL_TEST_MODE": "false",
    "EXPECTED_ALEMBIC_HEAD": "a6b7c8d9e0f1",
    "MYSQL_DATA_DIR": "/Data/earthquake-api-gateway/mysql",
    "BRAND_ASSET_DIR_HOST": "/Data/earthquake-api-gateway/brand-assets",
    "BACKUP_DIR": "/Data/earthquake-api-gateway/backups/mysql",
    "BACKUP_RETENTION_DAYS": "14",
    "BACKEND_PORT": "8000",
    "FRONTEND_PORT": "3000",
    "COMPOSE_PROJECT_NAME": "earthquake-api-gateway",
}

def quote(value: str) -> str:
    simple = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/,@%+?=-"
    if value == "" or any(ch not in simple for ch in value):
        return "'" + value.replace("'", "'\\''") + "'"
    return value

content = "# 由 scripts/gen-env-production.sh 生成，请勿提交到 Git。\n"
content += "\n".join(f"{key}={quote(value)}" for key, value in values.items()) + "\n"
temporary = Path(".env.production.tmp")
temporary.write_text(content, encoding="utf-8", newline="\n")
temporary.replace(Path(".env.production"))
PY

chmod 600 .env.production
echo "已生成 .env.production（权限 600）。"
echo "当前为 HTTP 直连模式；配置 HTTPS 后请将 SECURE_COOKIES 改为 true。"
