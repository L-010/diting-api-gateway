#!/usr/bin/env bash
set -euo pipefail

# 创建管理员账户脚本（Ubuntu版本）
# 用法: ./scripts/init-admin.sh [用户名] [密码]
# 或交互式: ./scripts/init-admin.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo_error() { echo -e "${RED}✗ $*${NC}" >&2; }
echo_success() { echo -e "${GREEN}✓ $*${NC}"; }
echo_info() { echo -e "${YELLOW}ℹ $*${NC}"; }

# 检查.env.production是否存在
if [[ ! -f .env.production ]]; then
  echo_error ".env.production 文件不存在"
  exit 1
fi

# 参数处理
USERNAME="${1:-}"
PASSWORD="${2:-}"

# 如果未提供参数，使用交互式输入
if [[ -z "$USERNAME" ]]; then
  read -p "请输入管理员用户名 [admin]: " USERNAME
  USERNAME="${USERNAME:-admin}"
fi

if [[ -z "$PASSWORD" ]]; then
  while true; do
    read -sp "请输入管理员密码: " PASSWORD
    echo
    if [[ -z "$PASSWORD" ]]; then
      echo_error "密码不能为空"
      continue
    fi
    read -sp "确认密码: " PASSWORD_CONFIRM
    echo
    if [[ "$PASSWORD" == "$PASSWORD_CONFIRM" ]]; then
      break
    else
      echo_error "两次输入的密码不一致，请重试"
    fi
  done
fi

# 验证密码要求
validate_password() {
  local pwd="$1"
  local user="$2"

  if [[ ${#pwd} -lt 12 ]]; then
    echo_error "密码长度至少为 12 个字符"
    return 1
  fi

  if ! echo "$pwd" | grep -q '[a-z]'; then
    echo_error "密码必须包含小写字母"
    return 1
  fi

  if ! echo "$pwd" | grep -q '[A-Z]'; then
    echo_error "密码必须包含大写字母"
    return 1
  fi

  if ! echo "$pwd" | grep -q '[0-9]'; then
    echo_error "密码必须包含数字"
    return 1
  fi

  if echo "$pwd" | grep -iq "$user"; then
    echo_error "密码不能包含用户名"
    return 1
  fi

  return 0
}

# 验证密码
if ! validate_password "$PASSWORD" "$USERNAME"; then
  echo_error ""
  echo_error "密码验证失败。密码要求："
  echo_error "  • 至少12个字符"
  echo_error "  • 包含大小写字母"
  echo_error "  • 包含数字"
  echo_error "  • 不能包含用户名"
  exit 1
fi

echo_info "准备创建管理员账户..."
echo_info "用户名: $USERNAME"

# 检查 Docker Compose v2 是否可用
if ! command -v docker &> /dev/null || ! docker compose version &> /dev/null 2>&1; then
  echo_error "未找到可用的 Docker Compose v2 命令"
  exit 1
fi

# 执行seed_admin.py
echo_info "正在初始化管理员账户..."

if docker compose --env-file .env.production -f docker-compose.prod.yml exec -T backend \
  env BOOTSTRAP_ADMIN_USERNAME="$USERNAME" BOOTSTRAP_ADMIN_PASSWORD="$PASSWORD" \
  python scripts/seed_admin.py; then
  echo_success "管理员账户创建成功！"
  echo_info "用户名: $USERNAME"
  echo_info "现在可以使用此账户登录管理后台"
  exit 0
else
  echo_error "管理员账户创建失败"
  echo_error "请检查后端容器是否正常运行: docker compose --env-file .env.production -f docker-compose.prod.yml logs backend"
  exit 1
fi
