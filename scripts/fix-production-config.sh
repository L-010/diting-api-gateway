#!/usr/bin/env bash
set -Eeuo pipefail

# 配置修复脚本 - 自动添加缺失的生产环境配置项
# 使用方法: bash scripts/fix-production-config.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE=".env.production"

echo "🔍 检查生产环境配置文件..."

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ 错误: $ENV_FILE 文件不存在"
    echo "请先创建该文件或从 .env.production.example 复制"
    exit 1
fi

# 备份原配置
BACKUP_FILE="${ENV_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
cp "$ENV_FILE" "$BACKUP_FILE"
echo "✅ 已备份原配置到: $BACKUP_FILE"

MODIFIED=0

# 检查并添加 EXPECTED_ALEMBIC_HEAD
if ! grep -q "^EXPECTED_ALEMBIC_HEAD=" "$ENV_FILE"; then
    echo ""
    echo "📝 添加缺失的配置项: EXPECTED_ALEMBIC_HEAD"
    cat >> "$ENV_FILE" << 'EOF'

# ============================================
# 数据库迁移版本控制
# ============================================
EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1
EOF
    MODIFIED=1
else
    # 检查版本号是否正确
    CURRENT_HEAD=$(grep "^EXPECTED_ALEMBIC_HEAD=" "$ENV_FILE" | cut -d= -f2)
    if [[ "$CURRENT_HEAD" != "a6b7c8d9e0f1" ]]; then
        echo ""
        echo "⚠️  更新 EXPECTED_ALEMBIC_HEAD: $CURRENT_HEAD -> a6b7c8d9e0f1"
        sed -i.tmp 's/^EXPECTED_ALEMBIC_HEAD=.*/EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1/' "$ENV_FILE"
        rm -f "${ENV_FILE}.tmp"
        MODIFIED=1
    fi
fi

# 检查并添加 Docker Compose 配置
if ! grep -q "^MYSQL_DATA_DIR=" "$ENV_FILE"; then
    echo ""
    echo "📝 添加缺失的配置项: Docker Compose 目录配置"
    cat >> "$ENV_FILE" << 'EOF'

# ============================================
# Docker Compose 配置
# ============================================
MYSQL_DATA_DIR=/Data/earthquake-api-gateway/mysql
BRAND_ASSET_DIR_HOST=/Data/earthquake-api-gateway/brand-assets
BACKUP_DIR=/Data/earthquake-api-gateway/backups/mysql
BACKEND_PORT=8000
FRONTEND_PORT=3000
EOF
    MODIFIED=1
fi

if [[ $MODIFIED -eq 0 ]]; then
    echo ""
    echo "✅ 配置文件已是最新，无需修改"
else
    echo ""
    echo "✅ 配置文件已更新"
fi

echo ""
echo "🔧 创建必要的目录..."

MYSQL_DIR="/Data/earthquake-api-gateway/mysql"
BRAND_DIR="/Data/earthquake-api-gateway/brand-assets"
BACKUP_DIR="/Data/earthquake-api-gateway/backups/mysql"

for dir in "$MYSQL_DIR" "$BRAND_DIR" "$BACKUP_DIR"; do
    if [[ ! -d "$dir" ]]; then
        if [[ "$(id -u)" -eq 0 ]]; then
            mkdir -p "$dir"
            echo "  ✓ 创建目录: $dir"
        elif command -v sudo >/dev/null; then
            sudo mkdir -p "$dir"
            echo "  ✓ 创建目录: $dir (使用 sudo)"
        else
            echo "  ⚠️  无法创建目录: $dir (需要 root 或 sudo 权限)"
        fi
    else
        echo "  ✓ 目录已存在: $dir"
    fi
done

echo ""
echo "🔧 设置品牌资源目录权限 (UID:GID = 10001:10001)..."

if [[ -d "$BRAND_DIR" ]]; then
    if [[ "$(id -u)" -eq 0 ]]; then
        chown -R 10001:10001 "$BRAND_DIR"
        echo "  ✓ 权限设置成功"
    elif command -v sudo >/dev/null; then
        sudo chown -R 10001:10001 "$BRAND_DIR"
        echo "  ✓ 权限设置成功 (使用 sudo)"
    else
        echo "  ⚠️  无法设置权限 (需要 root 或 sudo 权限)"
        echo "     请手动执行: sudo chown -R 10001:10001 $BRAND_DIR"
    fi
fi

echo ""
echo "═══════════════════════════════════════════════════════"
echo "✅ 配置修复完成！"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "📋 关键配置项验证:"
echo "-----------------------------------------------------------"
grep "^EXPECTED_ALEMBIC_HEAD=" "$ENV_FILE" || echo "  ❌ EXPECTED_ALEMBIC_HEAD 未配置"
grep "^MYSQL_DATA_DIR=" "$ENV_FILE" || echo "  ⚠️  MYSQL_DATA_DIR 未配置"
grep "^APP_ENV=" "$ENV_FILE" || echo "  ❌ APP_ENV 未配置"
grep "^DATABASE_URL=" "$ENV_FILE" || echo "  ❌ DATABASE_URL 未配置"
echo "-----------------------------------------------------------"

echo ""
echo "🚀 下一步操作:"
echo "   bash scripts/deploy-prod.sh"
echo ""
