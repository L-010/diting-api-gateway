#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/read-production-env.sh"

command -v docker >/dev/null 2>&1 || { echo "缺少 docker 命令" >&2; exit 1; }
command -v gzip >/dev/null 2>&1 || { echo "缺少 gzip 命令" >&2; exit 1; }
command -v sha256sum >/dev/null 2>&1 || { echo "缺少 sha256sum 命令" >&2; exit 1; }

MYSQL_DATABASE="$(production_env_value MYSQL_DATABASE)"
BACKUP_DIR="$(production_env_value BACKUP_DIR || true)"
RETENTION_DAYS="$(production_env_value BACKUP_RETENTION_DAYS || true)"
BACKUP_DIR="${BACKUP_DIR:-/Data/earthquake-api-gateway/backups/mysql}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
[[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || { echo "BACKUP_RETENTION_DAYS 必须是非负整数" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIR/${MYSQL_DATABASE}_${timestamp}.sql.gz"
tmp="${target}.tmp"

cleanup() { rm -f "$tmp"; }
trap cleanup EXIT

docker compose --env-file .env.production -f docker-compose.prod.yml exec -T mysql \
  sh -c 'exec mysqldump --single-transaction --routines --triggers --events --hex-blob -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' \
  | gzip -9 > "$tmp"
test -s "$tmp"
mv "$tmp" "$target"
sha256sum "$target" > "${target}.sha256"
find "$BACKUP_DIR" -type f -name "*.sql.gz" -mtime "+$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -type f -name "*.sql.gz.sha256" -mtime "+$RETENTION_DAYS" -delete
echo "备份完成: $target"
