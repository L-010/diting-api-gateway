#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/read-production-env.sh"

MYSQL_DATABASE="$(production_env_value MYSQL_DATABASE)"
BACKUP_DIR="$(production_env_value BACKUP_DIR || true)"
RETENTION_DAYS="$(production_env_value BACKUP_RETENTION_DAYS || true)"
BACKUP_DIR="${BACKUP_DIR:-/Data/earthquake-api-gateway/backups/mysql}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
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
