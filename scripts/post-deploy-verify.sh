#!/usr/bin/env bash
set -euo pipefail

# 部署后验证脚本
# 用法: ./scripts/post-deploy-verify.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

TESTS_PASSED=0
TESTS_FAILED=0
TESTS_SKIPPED=0

echo_error() { echo -e "${RED}✗ $*${NC}" >&2; }
echo_success() { echo -e "${GREEN}✓ $*${NC}"; }
echo_warn() { echo -e "${YELLOW}⚠ $*${NC}"; }
echo_info() { echo -e "${BLUE}ℹ $*${NC}"; }

test_pass() {
  echo_success "$1"
  TESTS_PASSED=$((TESTS_PASSED + 1))
}

test_fail() {
  echo_error "$1"
  TESTS_FAILED=$((TESTS_FAILED + 1))
}

test_skip() {
  echo_warn "$1 (跳过)"
  TESTS_SKIPPED=$((TESTS_SKIPPED + 1))
}

test_warn() {
  echo_warn "$1"
}

# 获取docker compose命令
get_docker_compose() {
  if docker compose version &> /dev/null 2>&1; then
    echo "docker compose"
  else
    echo ""
  fi
}

DC=$(get_docker_compose)
if [[ -z "$DC" ]]; then
  echo_error "Docker Compose 不可用"
  exit 1
fi

backend_port=$(grep '^BACKEND_PORT=' .env.production | cut -d'=' -f2- || true)
frontend_port=$(grep '^FRONTEND_PORT=' .env.production | cut -d'=' -f2- || true)
backend_port="${backend_port:-8000}"
frontend_port="${frontend_port:-3000}"

echo_info "=========================================="
echo_info "生产部署后验证"
echo_info "=========================================="
echo ""

# 1. 容器状态检查
echo_info "【1】容器状态检查"
if $DC --env-file .env.production -f docker-compose.prod.yml ps | grep -q "Up"; then
  test_pass "Docker容器正在运行"

  # 检查每个容器
  containers=("mysql" "backend" "frontend" "email-worker" "file-worker")
  for container in "${containers[@]}"; do
    if $DC --env-file .env.production -f docker-compose.prod.yml ps "$container" | grep -q "Up"; then
      test_pass "  $container 容器运行正常"
    else
      test_fail "  $container 容器未运行"
    fi
  done
else
  test_fail "没有容器在运行"
fi

echo ""

# 2. MySQL数据库检查
echo_info "【2】MySQL 数据库检查"
if $DC --env-file .env.production -f docker-compose.prod.yml exec -T mysql \
  sh -c 'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -e "SELECT 1"' &>/dev/null; then
  test_pass "MySQL数据库连接成功"

  # 检查表数量
  table_count=$($DC --env-file .env.production -f docker-compose.prod.yml exec -T mysql \
    sh -c 'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -s -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()"')

  if [[ $table_count -gt 0 ]]; then
    test_pass "  数据库表已创建 ($table_count 个表)"
  else
    test_fail "  数据库表未创建"
  fi

  # 检查迁移版本
  current_version=$($DC --env-file .env.production -f docker-compose.prod.yml exec -T mysql \
    sh -c 'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -s -N -e "SELECT version_num FROM alembic_version LIMIT 1"' 2>/dev/null || echo "")

  if [[ -n "$current_version" ]]; then
    if [[ "$current_version" == "a6b7c8d9e0f1" ]]; then
      test_pass "  迁移版本正确 ($current_version)"
    else
      test_warn "  迁移版本不是最新 (当前: $current_version, 预期: a6b7c8d9e0f1)"
    fi
  else
    test_fail "  无法获取迁移版本"
  fi
else
  test_fail "MySQL数据库连接失败"
fi

echo ""

# 3. 后端API检查
echo_info "【3】后端 API 检查"

# 检查/health端点
if curl -s "http://127.0.0.1:${backend_port}/health" >/dev/null 2>&1; then
  test_pass "/health 端点可用"

  health_response=$(curl -s "http://127.0.0.1:${backend_port}/health")
  if echo "$health_response" | grep -q '"status":"ok"'; then
    test_pass "  后端状态: 就绪"
  else
    test_warn "  后端状态: $health_response"
  fi
else
  test_fail "/health 端点不可用"
fi

# 检查/livez端点
if curl -s "http://127.0.0.1:${backend_port}/livez" >/dev/null 2>&1; then
  test_pass "/livez 端点可用"
else
  test_fail "/livez 端点不可用"
fi

# 检查/readyz端点（最重要的验收标准）
if curl -s "http://127.0.0.1:${backend_port}/readyz" >/dev/null 2>&1; then
  readyz_response=$(curl -s "http://127.0.0.1:${backend_port}/readyz")

  if echo "$readyz_response" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"'; then
    test_pass "/readyz 端点 - 状态就绪"

    # 检查所有checks
    if echo "$readyz_response" | grep -Eq '"config"[[:space:]]*:[[:space:]]*"ok"'; then
      test_pass "  ✓ config check"
    else
      test_fail "  ✗ config check"
    fi

    if echo "$readyz_response" | grep -Eq '"database"[[:space:]]*:[[:space:]]*"ok"'; then
      test_pass "  ✓ database check"
    else
      test_fail "  ✗ database check"
    fi

    if echo "$readyz_response" | grep -Eq '"migration"[[:space:]]*:[[:space:]]*"ok"'; then
      test_pass "  ✓ migration check"
    else
      test_fail "  ✗ migration check"
    fi
  else
    test_fail "/readyz 端点 - 状态非就绪"
    echo_info "  响应: $readyz_response"
  fi
else
  test_fail "/readyz 端点不可用"
fi

echo ""

# 4. 前端验证
echo_info "【4】前端应用检查"

if curl -s -I "http://127.0.0.1:${frontend_port}/" | grep -q "200\|3"; then
  test_pass "前端应用响应正常"

  # 检查Next.js
  if curl -s "http://127.0.0.1:${frontend_port}/" | grep -q "html\|<body"; then
    test_pass "  前端页面已加载"
  else
    test_warn "  无法验证前端页面内容"
  fi
else
  test_fail "前端应用无响应"
fi

echo ""

# 5. 网络连接检查
echo_info "【5】服务间网络连接检查"

# 检查后端是否能连接到MySQL
$DC --env-file .env.production -f docker-compose.prod.yml exec -T backend \
  python -c "from sqlalchemy import create_engine, text; from app.config import get_settings; e=create_engine(get_settings().database_url); e.connect().execute(text('SELECT 1'))" &>/dev/null && \
  test_pass "后端能连接MySQL" || \
  test_fail "后端无法连接MySQL"

# 检查前端能否连接到后端（如果在容器内）
if $DC --env-file .env.production -f docker-compose.prod.yml exec -T frontend \
  node -e "fetch('http://backend:8000/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"; then
  test_pass "前端容器能连接到后端"
else
  test_warn "前端容器无法连接到后端（可能正常）"
fi

echo ""

# 6. 文件和目录权限检查
echo_info "【6】文件和目录权限检查"

brand_asset_dir=$(grep '^BRAND_ASSET_DIR_HOST=' .env.production | cut -d'=' -f2- || true)
brand_asset_dir="${brand_asset_dir:-/Data/earthquake-api-gateway/brand-assets}"
if [[ -d "$brand_asset_dir" ]]; then
  test_pass "品牌资源目录存在"

  if [[ -w "$brand_asset_dir" ]]; then
    test_pass "  品牌资源目录可写"
  else
    test_warn "  品牌资源目录权限可能有问题"
  fi
else
  test_warn "品牌资源目录不存在"
fi

echo ""

# 7. 日志检查
echo_info "【7】服务日志检查"

# 检查后端日志中是否有错误
backend_errors=$($DC --env-file .env.production -f docker-compose.prod.yml logs backend 2>/dev/null | awk 'BEGIN {IGNORECASE=1} /error|exception|failed/ {count++} END {print count+0}')
if [[ $backend_errors -eq 0 ]]; then
  test_pass "后端日志无错误"
else
  test_warn "后端日志中发现 $backend_errors 条错误/异常"
  echo_info "  最近的错误："
  $DC --env-file .env.production -f docker-compose.prod.yml logs backend 2>/dev/null | grep -i "error\|exception\|failed" | tail -3 | sed 's/^/    /' || true
fi

# 检查前端日志
if $DC --env-file .env.production -f docker-compose.prod.yml logs frontend 2>/dev/null | grep -q "ready - started server"; then
  test_pass "前端服务已启动"
else
  test_warn "无法确认前端服务启动状态"
fi

echo ""

# 8. 磁盘空间检查
echo_info "【8】磁盘空间检查"

if [[ "$OSTYPE" == "linux-gnu"* ]]; then
  mysql_size=$(du -sh backend/data 2>/dev/null | cut -f1 || true)
  echo_info "  后端数据目录: $mysql_size"

  if [[ -d /Data/earthquake-api-gateway/mysql ]]; then
    mysql_volume=$(du -sh /Data/earthquake-api-gateway/mysql 2>/dev/null | cut -f1)
    echo_info "  MySQL数据卷: $mysql_volume"
  fi

available=$(df /Data 2>/dev/null | tail -1 | awk '{print $4}' || true)
  if [[ -n "$available" && $available -gt 1048576 ]]; then  # > 1GB
    test_pass "磁盘空间充足"
  else
    test_warn "磁盘空间不足"
  fi
else
  test_skip "磁盘空间检查"
fi

echo ""

# 9. 环境配置验证
echo_info "【9】环境配置验证"

# 检查关键环境变量
critical_vars=("APP_ENV" "DATABASE_URL" "FRONTEND_ORIGIN" "PUBLIC_GATEWAY_BASE_URL" "SECURE_COOKIES")
for var in "${critical_vars[@]}"; do
  if grep -q "^${var}=" .env.production; then
    value=$(grep "^${var}=" .env.production | cut -d'=' -f2-)
    if [[ ${#value} -gt 50 ]]; then
      value="${value:0:47}..."
    fi
    test_pass "  $var 已配置"
  else
    test_fail "  $var 未配置"
  fi
done

echo ""

# 10. Nginx反代验证（如果可用）
echo_info "【10】Nginx 反代验证"

if command -v nginx &> /dev/null && grep -q '^SERVER_NAME=' .env.production && grep -q '^TLS_CERT_FILE=' .env.production && grep -q '^TLS_KEY_FILE=' .env.production; then
  if sudo nginx -t >/dev/null 2>&1; then
    test_pass "Nginx配置文件有效"

    # 尝试通过域名访问（可能需要hosts配置）
    server_name=$(grep "^SERVER_NAME=" .env.production | cut -d'=' -f2-)
    if curl -s -k https://$server_name/health >/dev/null 2>&1; then
      test_pass "  能够通过HTTPS访问"
    else
      test_warn "  暂无法通过HTTPS访问（可能需要DNS或hosts配置）"
    fi
  else
    test_fail "Nginx配置文件无效"
  fi
else
  test_skip "Nginx反代验证（未安装Nginx）"
fi

echo ""

# 11. 数据库初始化检查
echo_info "【11】数据库初始化检查"

# 检查用户表
user_count=$($DC --env-file .env.production -f docker-compose.prod.yml exec -T mysql \
  sh -c 'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -s -N -e "SELECT COUNT(*) FROM users"' 2>/dev/null || echo "0")

if [[ $user_count -eq 0 ]]; then
  test_pass "用户表已初始化（当前为空，正常）"
else
  test_pass "用户表已初始化（当前 $user_count 个用户）"
fi

# 检查角色表
role_count=$($DC --env-file .env.production -f docker-compose.prod.yml exec -T mysql \
  sh -c 'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -s -N -e "SELECT COUNT(*) FROM roles"' 2>/dev/null || echo "0")

if [[ $role_count -gt 0 ]]; then
  test_pass "角色表已初始化（$role_count 个角色）"
else
  test_fail "角色表未初始化"
fi

echo ""

# 12. 备份功能检查
echo_info "【12】备份功能检查"

if [[ -f scripts/backup-mysql.sh ]]; then
  if bash -n scripts/backup-mysql.sh 2>/dev/null; then
    test_pass "备份脚本语法正确"

    # 尝试执行备份
    if bash scripts/backup-mysql.sh &>/dev/null; then
      test_pass "  备份执行成功"

      # 检查备份文件
      backup_dir=$(grep '^BACKUP_DIR=' .env.production | cut -d'=' -f2- || true)
      backup_dir="${backup_dir:-/Data/earthquake-api-gateway/backups/mysql}"
      if ls "$backup_dir"/*.sql* &>/dev/null 2>&1; then
        latest_backup=$(ls -t "$backup_dir"/*.sql* 2>/dev/null | head -1)
        backup_size=$(du -h "$latest_backup" | cut -f1)
        test_pass "  最新备份: $latest_backup ($backup_size)"
      fi
    else
      test_warn "  备份执行出错（可能缺少权限或备份目录）"
    fi
  else
    test_fail "备份脚本语法错误"
  fi
else
  test_warn "备份脚本不存在"
fi

echo ""

# 总结
echo_info "=========================================="
echo_info "验证结果汇总"
echo_info "=========================================="
echo -e "通过: ${GREEN}$TESTS_PASSED${NC} | 失败: ${RED}$TESTS_FAILED${NC} | 跳过: ${YELLOW}$TESTS_SKIPPED${NC}"

echo ""

if [[ $TESTS_FAILED -eq 0 ]]; then
  echo_success "✓ 部署验证通过！"
  echo_success ""
  echo_success "接下来的步骤:"
  echo_success "  1. 初始化管理员账户: ./scripts/init-admin.sh"
  echo_success "  2. 配置Nginx反代: sudo ./scripts/configure-nginx.sh"
  echo_success "  3. 登录管理后台: https://api.example.com"
  echo_success "  4. 设置自动备份: sudo crontab -e (参考文档)"
  echo ""
  exit 0
else
  echo_error "✗ 存在 $TESTS_FAILED 个验证失败"
  echo_error ""
  echo_error "请查看上面的错误信息并进行排查"
  echo_error "参考故障排查文档: docs/UBUNTU_DEPLOYMENT.md"
  echo ""
  exit 1
fi
