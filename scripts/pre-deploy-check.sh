#!/usr/bin/env bash
set -euo pipefail

# 部署前检查脚本
# 用法: ./scripts/pre-deploy-check.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

CHECKS_PASSED=0
CHECKS_FAILED=0
CHECKS_WARNING=0

echo_error() { echo -e "${RED}✗ $*${NC}" >&2; }
echo_success() { echo -e "${GREEN}✓ $*${NC}"; }
echo_warn() { echo -e "${YELLOW}⚠ $*${NC}"; }
echo_info() { echo -e "${BLUE}ℹ $*${NC}"; }

check_pass() {
  echo_success "$1"
  CHECKS_PASSED=$((CHECKS_PASSED + 1))
}

check_fail() {
  echo_error "$1"
  CHECKS_FAILED=$((CHECKS_FAILED + 1))
}

check_warn() {
  echo_warn "$1"
  CHECKS_WARNING=$((CHECKS_WARNING + 1))
}

echo_info "=========================================="
echo_info "生产部署前检查"
echo_info "=========================================="
echo ""

# 1. 环境文件检查
echo_info "【1】环境文件检查"
if [[ ! -f .env.production ]]; then
  check_fail "缺少 .env.production 文件"
else
  check_pass ".env.production 文件存在"

  # 检查占位符
  if grep -qi "replace-with-\|changeme\|example\.test\|api\.example\.com" .env.production; then
    check_fail "配置文件包含占位符值"
  else
    check_pass "没有发现占位符值"
  fi

  # 检查关键变量
  vars=("APP_ENV" "APP_SECRET_KEY" "APP_ENCRYPTION_KEY" "API_KEY_PEPPER" "DATABASE_URL" "FRONTEND_ORIGIN" "PUBLIC_GATEWAY_BASE_URL" "EXPECTED_ALEMBIC_HEAD" "SECURE_COOKIES")
  for var in "${vars[@]}"; do
    if grep -q "^${var}=" .env.production; then
      check_pass "✓ 变量 $var 已配置"
    else
      check_fail "缺少变量 $var"
    fi
  done

  # 检查APP_ENV=production
  if grep -q "^APP_ENV=production$" .env.production; then
    check_pass "✓ APP_ENV = production"
  else
    check_fail "APP_ENV 必须为 production"
  fi

  # 检查EMAIL_TEST_MODE=false
  if grep -q "^EMAIL_TEST_MODE=false$" .env.production; then
    check_pass "✓ EMAIL_TEST_MODE = false"
  else
    check_fail "EMAIL_TEST_MODE 必须为 false"
  fi
fi

echo ""

# 2. Docker检查
echo_info "【2】Docker 环境检查"
if command -v docker &> /dev/null; then
  check_pass "✓ Docker 已安装"
  DOCKER_VERSION=$(docker --version | cut -d' ' -f3 | cut -d',' -f1)
  echo_info "  版本: $DOCKER_VERSION"
else
  check_fail "Docker 未安装或不在PATH中"
fi

if docker compose version &> /dev/null 2>&1; then
  check_pass "✓ Docker Compose 已安装"
  DC_VERSION=$(docker compose version --short 2>/dev/null || echo "2.x+")
  echo_info "  版本: $DC_VERSION"
else
  check_fail "Docker Compose 未安装"
fi

echo ""

# 3. 配置文件检查
echo_info "【3】项目文件检查"
required_files=("docker-compose.prod.yml" "backend/Dockerfile" "frontend/Dockerfile" "scripts/deploy-prod.sh" "scripts/migrate.py" "alembic.ini")
for file in "${required_files[@]}"; do
  if [[ -f "$file" ]]; then
    check_pass "✓ $file 存在"
  else
    check_fail "缺少 $file"
  fi
done

echo ""

# 4. 数据库密码验证
echo_info "【4】数据库配置验证"
if grep -q "^DATABASE_URL=mysql" .env.production; then
  check_pass "✓ DATABASE_URL 使用 MySQL 协议"
else
  check_fail "DATABASE_URL 必须使用 mysql+pymysql:// 协议"
fi

# 检查EXPECTED_ALEMBIC_HEAD
if grep -q "^EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1$" .env.production; then
  check_pass "✓ EXPECTED_ALEMBIC_HEAD = a6b7c8d9e0f1"
else
  check_fail "EXPECTED_ALEMBIC_HEAD 必须为 a6b7c8d9e0f1"
fi

secure_cookies=$(grep '^SECURE_COOKIES=' .env.production | cut -d'=' -f2- || true)
if [[ "$secure_cookies" == "false" ]]; then
  check_pass "✓ HTTP 模式 Cookie 配置正确"
elif [[ "$secure_cookies" == "true" ]]; then
  check_warn "SECURE_COOKIES=true：仅适用于已配置 HTTPS 的环境"
else
  check_fail "SECURE_COOKIES 必须为 true 或 false"
fi

echo ""

# 5. TLS证书检查
echo_info "【5】TLS 证书检查"
cert_file=$(grep "^TLS_CERT_FILE=" .env.production | cut -d'=' -f2- || true)
key_file=$(grep "^TLS_KEY_FILE=" .env.production | cut -d'=' -f2- || true)

if [[ -z "$cert_file" ]]; then
  check_warn "未配置 TLS_CERT_FILE"
elif [[ -r "$cert_file" ]]; then
  check_pass "✓ 证书文件可读: $cert_file"
else
  check_warn "证书文件不可读或不存在: $cert_file（当前 HTTP 模式可跳过）"
fi

if [[ -z "$key_file" ]]; then
  check_warn "未配置 TLS_KEY_FILE"
elif [[ -r "$key_file" ]]; then
  check_pass "✓ 私钥文件可读: $key_file"
else
  check_warn "私钥文件不可读或不存在: $key_file（当前 HTTP 模式可跳过）"
fi

echo ""

# 6. bash脚本兼容性检查
echo_info "【6】脚本兼容性检查"
bash_scripts=("scripts/deploy-prod.sh" "scripts/backup-mysql.sh" "scripts/migrate-prod.sh" "scripts/init-admin.sh" "scripts/gen-env-production.sh")
for script in "${bash_scripts[@]}"; do
  if [[ -f "$script" ]]; then
    if bash -n "$script" 2>/dev/null; then
      check_pass "✓ $script 语法正确"
    else
      check_warn "$script 语法检查失败（可能需要在Ubuntu上运行）"
    fi
  fi
done

echo ""

# 7. 品牌资源目录检查
echo_info "【7】品牌资源目录检查"
if [[ -d backend/data/brand-assets ]]; then
  check_pass "✓ 品牌资源目录存在"
else
  check_warn "品牌资源目录不存在（将在部署时自动创建）"
fi

echo ""

# 8. Python依赖检查
echo_info "【8】Python 依赖检查"
if [[ -f backend/requirements.txt ]]; then
  check_pass "✓ requirements.txt 存在"
  # 检查关键依赖
  key_deps=("fastapi" "sqlalchemy" "PyMySQL" "alembic" "pydantic" "cryptography")
  for dep in "${key_deps[@]}"; do
    if grep -q "^$dep" backend/requirements.txt; then
      check_pass "  ✓ $dep"
    else
      check_fail "  ✗ 缺少依赖: $dep"
    fi
  done
else
  check_fail "缺少 backend/requirements.txt"
fi

echo ""

# 9. Node.js依赖检查
echo_info "【9】Node.js 依赖检查"
if [[ -f frontend/package.json ]]; then
  check_pass "✓ package.json 存在"
  if grep -q '"next":' frontend/package.json; then
    check_pass "  ✓ Next.js"
  else
    check_fail "  ✗ 缺少 Next.js"
  fi
else
  check_fail "缺少 frontend/package.json"
fi

echo ""

# 10. 权限检查
echo_info "【10】文件权限检查"
if [[ -x scripts/deploy-prod.sh ]]; then
  check_pass "✓ deploy-prod.sh 可执行"
else
  check_warn "deploy-prod.sh 不可执行（将尝试使用 bash 执行）"
fi

echo ""

# 11. 磁盘空间检查（如果在Ubuntu上）
echo_info "【11】系统资源检查"
# 检查是否在Linux上
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
  available_space=$(df /Data 2>/dev/null | tail -1 | awk '{print $4}' || true)
  if [[ -n "$available_space" && $available_space -gt 52428800 ]]; then  # > 50GB
    check_pass "✓ /Data 分区可用空间充足 ($(( available_space / 1048576 ))GB)"
  else
    check_warn "⚠ /Data 分区可用空间不足 50GB"
  fi
else
  echo_info "  (在 Windows/Mac 上部分检查跳过)"
fi

echo ""

# 总结
echo_info "=========================================="
echo_info "检查结果汇总"
echo_info "=========================================="
echo -e "通过: ${GREEN}$CHECKS_PASSED${NC} | 失败: ${RED}$CHECKS_FAILED${NC} | 警告: ${YELLOW}$CHECKS_WARNING${NC}"

if [[ $CHECKS_FAILED -eq 0 ]]; then
  echo ""
  echo_success "✓ 所有关键检查已通过"
  echo_success "可以继续执行部署: bash scripts/deploy-prod.sh --build"
  exit 0
else
  echo ""
  echo_error "✗ 存在 $CHECKS_FAILED 个检查失败，请修复后重试"
  exit 1
fi
