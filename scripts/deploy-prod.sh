#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/read-production-env.sh"
COMPOSE=(docker compose --env-file .env.production -f docker-compose.prod.yml)
ACTION="up"
BUILD=0
if [[ "${1:-}" == "--build" ]]; then BUILD=1; elif [[ "${1:-}" == "--status" ]]; then ACTION=status; elif [[ "${1:-}" == "--stop" ]]; then ACTION=stop; elif [[ "${1:-}" == "--logs" ]]; then ACTION=logs; fi

require_command() { command -v "$1" >/dev/null || { echo "缺少命令: $1" >&2; exit 1; }; }
require_command docker
require_command curl
docker compose version >/dev/null

if [[ "$ACTION" == status ]]; then "${COMPOSE[@]}" ps; exit 0; fi
if [[ "$ACTION" == stop ]]; then "${COMPOSE[@]}" stop; exit 0; fi
if [[ "$ACTION" == logs ]]; then "${COMPOSE[@]}" logs --tail=100 "${2:-backend}"; exit 0; fi

[[ -f .env.production ]] || { echo "请先复制并填写 .env.production" >&2; exit 1; }
grep -Eiq '^APP_ENV=production([[:space:]]*)$' .env.production || { echo "APP_ENV 必须为 production" >&2; exit 1; }
grep -Eiq '^EMAIL_TEST_MODE=false([[:space:]]*)$' .env.production || { echo "EMAIL_TEST_MODE 必须为 false" >&2; exit 1; }
grep -Eiq '^DATABASE_URL=mysql\+pymysql://' .env.production || { echo "DATABASE_URL 必须使用 MySQL" >&2; exit 1; }
if grep -Eiq 'replace-with-|changeme|example\.test|api\.example\.com' .env.production; then echo "环境文件仍包含占位值" >&2; exit 1; fi

expected_head="$(production_env_value EXPECTED_ALEMBIC_HEAD || true)"
frontend_origin="$(production_env_value FRONTEND_ORIGIN || true)"
public_gateway_base_url="$(production_env_value PUBLIC_GATEWAY_BASE_URL || true)"
tls_cert_file="$(production_env_value TLS_CERT_FILE || true)"
tls_key_file="$(production_env_value TLS_KEY_FILE || true)"
mysql_data_dir="$(production_env_value MYSQL_DATA_DIR || true)"
brand_asset_dir_host="$(production_env_value BRAND_ASSET_DIR_HOST || true)"
backup_dir="$(production_env_value BACKUP_DIR || true)"
backend_port="$(production_env_value BACKEND_PORT || true)"
frontend_port="$(production_env_value FRONTEND_PORT || true)"
secure_cookies="$(production_env_value SECURE_COOKIES || true)"
[[ "$expected_head" == "a6b7c8d9e0f1" ]] || { echo "EXPECTED_ALEMBIC_HEAD 必须为 a6b7c8d9e0f1" >&2; exit 1; }
[[ -n "$frontend_origin" && -n "$public_gateway_base_url" ]] || { echo "必须配置正式域名" >&2; exit 1; }
backend_port="${backend_port:-8000}"
frontend_port="${frontend_port:-3000}"
[[ "$backend_port" =~ ^[0-9]+$ && "$backend_port" -ge 1024 && "$backend_port" -le 65535 ]] || {
  echo "BACKEND_PORT 必须是 1024 到 65535 之间的整数" >&2
  exit 1
}
[[ "$frontend_port" =~ ^[0-9]+$ && "$frontend_port" -ge 1024 && "$frontend_port" -le 65535 ]] || {
  echo "FRONTEND_PORT 必须是 1024 到 65535 之间的整数" >&2
  exit 1
}
[[ "$secure_cookies" == "true" || "$secure_cookies" == "false" ]] || {
  echo "SECURE_COOKIES 必须为 true 或 false" >&2
  exit 1
}
# TLS 检查（HTTP 部署时两个值都留空）
if [[ -n "$tls_cert_file" || -n "$tls_key_file" ]]; then
  [[ -n "$tls_cert_file" && -n "$tls_key_file" ]] || { echo "TLS_CERT_FILE 和 TLS_KEY_FILE 必须同时配置" >&2; exit 1; }
  [[ -r "$tls_cert_file" && -r "$tls_key_file" ]] || { echo "TLS 证书或私钥不可读" >&2; exit 1; }
fi
if [[ -z "$tls_cert_file" && "$secure_cookies" != "false" ]]; then
  echo "HTTP 直连模式必须设置 SECURE_COOKIES=false" >&2
  exit 1
fi
if [[ -n "$tls_cert_file" && "$secure_cookies" != "true" ]]; then
  echo "HTTPS 模式必须设置 SECURE_COOKIES=true" >&2
  exit 1
fi
mysql_data_dir="${mysql_data_dir:-/Data/earthquake-api-gateway/mysql}"
brand_asset_dir_host="${brand_asset_dir_host:-/Data/earthquake-api-gateway/brand-assets}"
backup_dir="${backup_dir:-/Data/earthquake-api-gateway/backups/mysql}"
if ! mkdir -p "$mysql_data_dir" "$brand_asset_dir_host" "$backup_dir" 2>/dev/null; then
  command -v sudo >/dev/null || { echo "无法创建数据目录，请使用 sudo 或调整目录权限" >&2; exit 1; }
  sudo mkdir -p "$mysql_data_dir" "$brand_asset_dir_host" "$backup_dir"
fi
if [[ "$(id -u)" -eq 0 ]]; then
  chown -R 999:999 "$mysql_data_dir"
  chown -R 10001:10001 "$brand_asset_dir_host"
  chown -R 0:0 "$backup_dir"
elif command -v sudo >/dev/null; then
  sudo chown -R 999:999 "$mysql_data_dir"
  sudo chown -R 10001:10001 "$brand_asset_dir_host"
  sudo chown -R "$(id -u):$(id -g)" "$backup_dir"
else
  echo "无法设置品牌资源目录权限，请以 root 或具备 sudo 权限的账号运行" >&2
  exit 1
fi
"${COMPOSE[@]}" config >/dev/null
if [[ "$BUILD" == 1 ]]; then "${COMPOSE[@]}" build --pull; else "${COMPOSE[@]}" build; fi
"${COMPOSE[@]}" up -d mysql
"${COMPOSE[@]}" run --rm backend python scripts/migrate.py
"${COMPOSE[@]}" up -d backend frontend email-worker file-worker
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${backend_port}/livez" >/dev/null; then break; fi
  sleep 2
done
curl -fsS "http://127.0.0.1:${backend_port}/readyz" >/dev/null
"${COMPOSE[@]}" ps
if [[ -n "$tls_cert_file" ]]; then
  echo "部署完成。请确认宿主机 Nginx 已加载正式 HTTPS 配置。"
else
  echo "HTTP 直连部署完成。正式上线前请配置 HTTPS、Nginx，并将 SECURE_COOKIES 改为 true。"
fi
