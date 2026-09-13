#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
COMPOSE=(docker compose --env-file .env.production -f docker-compose.prod.yml)

[[ -f .env.production ]] || { echo "缺少 .env.production" >&2; exit 1; }
"${COMPOSE[@]}" config >/dev/null
"${COMPOSE[@]}" up -d mysql
"${COMPOSE[@]}" run --rm backend python scripts/migrate.py
"${COMPOSE[@]}" run --rm backend python - <<'PY'
from pathlib import Path
import os
expected = os.getenv("EXPECTED_ALEMBIC_HEAD", "a6b7c8d9e0f1")
if expected != "a6b7c8d9e0f1":
    raise SystemExit(f"迁移 head 配置错误: {expected}")
print(f"迁移 head 配置正确: {expected}")
PY
