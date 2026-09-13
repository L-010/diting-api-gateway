#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
[[ -f .env.production ]] || { echo "缺少 .env.production" >&2; exit 1; }
source "$ROOT_DIR/scripts/read-production-env.sh"

command -v nginx >/dev/null || { echo "未安装 nginx，请先安装 nginx" >&2; exit 1; }
server_name="$(production_env_value SERVER_NAME || true)"
tls_cert_file="$(production_env_value TLS_CERT_FILE || true)"
tls_key_file="$(production_env_value TLS_KEY_FILE || true)"
[[ -n "$server_name" ]] || { echo "必须配置 SERVER_NAME" >&2; exit 1; }
[[ -r "$tls_cert_file" && -r "$tls_key_file" ]] || { echo "TLS 证书或私钥不可读" >&2; exit 1; }

rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT
escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}
escaped_server_name="$(escape_sed_replacement "$server_name")"
escaped_tls_cert_file="$(escape_sed_replacement "$tls_cert_file")"
escaped_tls_key_file="$(escape_sed_replacement "$tls_key_file")"
sed "s|api\.example\.com|${escaped_server_name}|g; s|/etc/ssl/api-gateway/fullchain.pem|${escaped_tls_cert_file}|g; s|/etc/ssl/api-gateway/privkey.pem|${escaped_tls_key_file}|g" nginx/api-gateway.conf.example > "$rendered"
sudo install -m 0644 "$rendered" /etc/nginx/sites-available/api-gateway.conf
sudo ln -sfn /etc/nginx/sites-available/api-gateway.conf /etc/nginx/sites-enabled/api-gateway.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
echo "Nginx 配置已加载: ${server_name}"
