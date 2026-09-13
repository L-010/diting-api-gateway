#!/usr/bin/env bash
set -euo pipefail

# 生成安全的生产环境配置文件
# 用法: ./scripts/gen-env-production.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo_error() { echo -e "${RED}✗ $*${NC}" >&2; }
echo_success() { echo -e "${GREEN}✓ $*${NC}"; }
echo_info() { echo -e "${BLUE}ℹ $*${NC}"; }
echo_warn() { echo -e "${YELLOW}⚠ $*${NC}"; }

# 检查依赖
check_dependencies() {
  local deps=("openssl" "python3")
  for cmd in "${deps[@]}"; do
    if ! command -v "$cmd" &> /dev/null; then
      echo_error "缺少依赖: $cmd"
      exit 1
    fi
  done
}

# URL编码函数
urlencode() {
  python3 -c "import urllib.parse; print(urllib.parse.quote('$1', safe=''))"
}

# 生成随机字符串
gen_random_b64() {
  local length="${1:-32}"
  openssl rand -base64 "$length" | tr -d '\n'
}

# 生成Fernet密钥
gen_fernet_key() {
  python3 << 'EOF'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
EOF
}

# 生成APP_SECRET_KEY (32字节)
gen_app_secret_key() {
  openssl rand -base64 32 | tr -d '\n'
}

# 生成API_KEY_PEPPER (24字节)
gen_api_key_pepper() {
  openssl rand -base64 24 | tr -d '\n'
}

check_dependencies

echo_info "=========================================="
echo_info "生产环境配置文件生成工具"
echo_info "=========================================="
echo ""

# 检查模板文件
if [[ ! -f .env.production.example ]]; then
  echo_error ".env.production.example 文件不存在"
  exit 1
fi

# 检查目标文件是否已存在
if [[ -f .env.production ]]; then
  echo_warn ".env.production 已存在"
  read -p "是否覆盖? (y/N): " -r
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo_info "已取消"
    exit 0
  fi
fi

echo_info "开始生成必要的密钥和配置..."
echo ""

# 生成密钥
echo_info "[1/4] 生成加密密钥..."
APP_SECRET_KEY=$(gen_app_secret_key)
echo_success "  APP_SECRET_KEY: ${APP_SECRET_KEY:0:16}..."

APP_ENCRYPTION_KEY=$(gen_fernet_key)
echo_success "  APP_ENCRYPTION_KEY: ${APP_ENCRYPTION_KEY:0:16}..."

API_KEY_PEPPER=$(gen_api_key_pepper)
echo_success "  API_KEY_PEPPER: ${API_KEY_PEPPER:0:16}..."

# 数据库配置
echo ""
echo_info "[2/4] 数据库配置..."
read -p "数据库名称 [api_gateway]: " MYSQL_DATABASE
MYSQL_DATABASE="${MYSQL_DATABASE:-api_gateway}"

read -p "数据库用户 [api_user]: " MYSQL_USER
MYSQL_USER="${MYSQL_USER:-api_user}"

read -sp "数据库用户密码: " MYSQL_PASSWORD
echo ""
if [[ -z "$MYSQL_PASSWORD" ]]; then
  MYSQL_PASSWORD=$(gen_random_b64 16)
  echo_info "已生成随机密码"
fi

read -sp "MySQL root密码: " MYSQL_ROOT_PASSWORD
echo ""
if [[ -z "$MYSQL_ROOT_PASSWORD" ]]; then
  MYSQL_ROOT_PASSWORD=$(gen_random_b64 16)
  echo_info "已生成随机root密码"
fi

# URL编码密码
MYSQL_PASSWORD_ENCODED=$(urlencode "$MYSQL_PASSWORD")
DATABASE_URL="mysql+pymysql://${MYSQL_USER}:${MYSQL_PASSWORD_ENCODED}@mysql:3306/${MYSQL_DATABASE}?charset=utf8mb4"

echo_success "  数据库: $MYSQL_DATABASE"
echo_success "  用户: $MYSQL_USER"

# 网络和域名配置
echo ""
echo_info "[3/4] 网络和域名配置..."
read -p "服务器域名 (如: api.example.com): " SERVER_NAME
if [[ -z "$SERVER_NAME" ]]; then
  echo_error "域名不能为空"
  exit 1
fi

FRONTEND_ORIGIN="https://${SERVER_NAME}"
PUBLIC_GATEWAY_BASE_URL="https://${SERVER_NAME}"

echo_success "  服务器域名: $SERVER_NAME"
echo_success "  前端源: $FRONTEND_ORIGIN"

read -p "TLS证书路径 [/etc/ssl/api-gateway/fullchain.pem]: " TLS_CERT_FILE
TLS_CERT_FILE="${TLS_CERT_FILE:-/etc/ssl/api-gateway/fullchain.pem}"

read -p "TLS私钥路径 [/etc/ssl/api-gateway/privkey.pem]: " TLS_KEY_FILE
TLS_KEY_FILE="${TLS_KEY_FILE:-/etc/ssl/api-gateway/privkey.pem}"

echo_success "  TLS证书: $TLS_CERT_FILE"

# 上游服务配置
echo ""
echo_info "[4/4] 上游服务配置..."
read -p "TOMODD上游地址 (如: https://tomodd.example.com): " TOMODD_BASE_URL
if [[ -z "$TOMODD_BASE_URL" ]]; then
  TOMODD_BASE_URL="https://replace-with-approved-upstream"
  echo_warn "  已使用占位值，需要手动修改"
fi

read -sp "TOMODD上游Token: " TOMODD_UPSTREAM_TOKEN
echo ""

# 可选：SMTP配置
echo ""
read -p "是否配置SMTP邮箱? (y/N): " -r
if [[ $REPLY =~ ^[Yy]$ ]]; then
  read -p "SMTP主机: " SMTP_HOST
  read -p "SMTP端口 [587]: " SMTP_PORT
  SMTP_PORT="${SMTP_PORT:-587}"
  read -p "SMTP用户: " SMTP_USERNAME
  read -sp "SMTP密码: " SMTP_PASSWORD
  echo ""
  read -p "发件人地址: " SMTP_FROM
else
  SMTP_HOST=""
  SMTP_PORT="587"
  SMTP_USERNAME=""
  SMTP_PASSWORD=""
  SMTP_FROM=""
fi

# 生成.env.production
echo ""
echo_info "生成 .env.production..."

cp .env.production.example .env.production.tmp

# 替换密钥
sed -i "s|^APP_SECRET_KEY=.*|APP_SECRET_KEY=${APP_SECRET_KEY}|" .env.production.tmp
sed -i "s|^APP_ENCRYPTION_KEY=.*|APP_ENCRYPTION_KEY=${APP_ENCRYPTION_KEY}|" .env.production.tmp
sed -i "s|^API_KEY_PEPPER=.*|API_KEY_PEPPER=${API_KEY_PEPPER}|" .env.production.tmp

# 替换数据库配置
sed -i "s|^MYSQL_DATABASE=.*|MYSQL_DATABASE=${MYSQL_DATABASE}|" .env.production.tmp
sed -i "s|^MYSQL_USER=.*|MYSQL_USER=${MYSQL_USER}|" .env.production.tmp
sed -i "s|^MYSQL_PASSWORD=.*|MYSQL_PASSWORD=${MYSQL_PASSWORD}|" .env.production.tmp
sed -i "s|^MYSQL_ROOT_PASSWORD=.*|MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD}|" .env.production.tmp
sed -i "s|^DATABASE_URL=.*|DATABASE_URL=${DATABASE_URL}|" .env.production.tmp

# 替换网络配置
sed -i "s|^FRONTEND_ORIGIN=.*|FRONTEND_ORIGIN=${FRONTEND_ORIGIN}|" .env.production.tmp
sed -i "s|^PUBLIC_GATEWAY_BASE_URL=.*|PUBLIC_GATEWAY_BASE_URL=${PUBLIC_GATEWAY_BASE_URL}|" .env.production.tmp
sed -i "s|^SERVER_NAME=.*|SERVER_NAME=${SERVER_NAME}|" .env.production.tmp
sed -i "s|^TLS_CERT_FILE=.*|TLS_CERT_FILE=${TLS_CERT_FILE}|" .env.production.tmp
sed -i "s|^TLS_KEY_FILE=.*|TLS_KEY_FILE=${TLS_KEY_FILE}|" .env.production.tmp

# 替换上游服务配置
sed -i "s|^TOMODD_BASE_URL=.*|TOMODD_BASE_URL=${TOMODD_BASE_URL}|" .env.production.tmp
sed -i "s|^TOMODD_UPSTREAM_TOKEN=.*|TOMODD_UPSTREAM_TOKEN=${TOMODD_UPSTREAM_TOKEN}|" .env.production.tmp

# 替换SMTP配置
if [[ -n "$SMTP_HOST" ]]; then
  sed -i "s|^SMTP_HOST=.*|SMTP_HOST=${SMTP_HOST}|" .env.production.tmp
  sed -i "s|^SMTP_PORT=.*|SMTP_PORT=${SMTP_PORT}|" .env.production.tmp
  sed -i "s|^SMTP_USERNAME=.*|SMTP_USERNAME=${SMTP_USERNAME}|" .env.production.tmp
  sed -i "s|^SMTP_PASSWORD=.*|SMTP_PASSWORD=${SMTP_PASSWORD}|" .env.production.tmp
  sed -i "s|^SMTP_FROM=.*|SMTP_FROM=${SMTP_FROM}|" .env.production.tmp
fi

# 确保APP_ENV和EMAIL_TEST_MODE正确
sed -i "s|^APP_ENV=.*|APP_ENV=production|" .env.production.tmp
sed -i "s|^EMAIL_TEST_MODE=.*|EMAIL_TEST_MODE=false|" .env.production.tmp
sed -i "s|^EXPECTED_ALEMBIC_HEAD=.*|EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1|" .env.production.tmp

# 移动文件
mv .env.production.tmp .env.production
chmod 600 .env.production

echo_success ".env.production 已生成"
echo ""
echo_warn "重要信息:"
echo_warn "  • 此文件包含敏感信息，请不要提交到版本控制"
echo_warn "  • 文件权限已设置为 600（仅所有者可读写）"
echo_warn "  • 请验证以下配置后再进行部署："
echo_warn ""

# 显示验证清单
echo_info "配置验证清单:"
echo_info "  ✓ APP_ENV = production"
echo_info "  ✓ EMAIL_TEST_MODE = false"
echo_info "  ✓ DATABASE_URL 使用 MySQL"
echo_info "  ✓ EXPECTED_ALEMBIC_HEAD = a6b7c8d9e0f1"
echo ""

# 验证是否包含占位符
if grep -qi "replace-with-\|changeme\|example\.test\|api\.example\.com" .env.production; then
  echo_error "配置文件仍包含占位符值，请手动修改"
  exit 1
fi

# 检查TLS证书
if [[ ! -r "$TLS_CERT_FILE" ]] || [[ ! -r "$TLS_KEY_FILE" ]]; then
  echo_warn "TLS证书文件不可读，请确保证书已上传到服务器"
  echo_warn "  证书路径: $TLS_CERT_FILE"
  echo_warn "  私钥路径: $TLS_KEY_FILE"
fi

echo_success ""
echo_success "✓ .env.production 生成完成！"
echo_success "接下来请执行："
echo_success "  ./scripts/deploy-prod.sh --build"
echo ""
