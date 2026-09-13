#!/usr/bin/env bash
# 自动化部署脚本 - 从GitHub拉取代码并部署
# 位置: scripts/git-deploy.sh
# 用途: 自动化从GitHub拉取最新代码并执行Docker部署

set -euo pipefail

# ==================== 配置 ====================
PROJECT_DIR="${PROJECT_DIR:-/Data/earthquake-api-gateway/project}"
LOG_FILE="${DEPLOY_LOG_FILE:-/tmp/api-gateway-deploy.log}"
BACKUP_DIR="/Data/earthquake-api-gateway/backups"
SLACK_WEBHOOK="${SLACK_WEBHOOK_URL:-}"
GITHUB_REPO="${GITHUB_REPO:-origin}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
MAX_BACKUPS=7

# ==================== 颜色输出 ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ==================== 函数定义 ====================

log() {
  local level="$1"
  shift
  local message="$*"
  local timestamp=$(date +'%Y-%m-%d %H:%M:%S')

  case "$level" in
    INFO)
      echo -e "${BLUE}[${timestamp}]${NC} ${message}" | tee -a "$LOG_FILE"
      ;;
    SUCCESS)
      echo -e "${GREEN}[${timestamp}] ✅ ${message}${NC}" | tee -a "$LOG_FILE"
      ;;
    WARNING)
      echo -e "${YELLOW}[${timestamp}] ⚠️  ${message}${NC}" | tee -a "$LOG_FILE"
      ;;
    ERROR)
      echo -e "${RED}[${timestamp}] ❌ ${message}${NC}" | tee -a "$LOG_FILE"
      ;;
  esac
}

notify_slack() {
  local message="$1"
  local status="${2:-success}"

  if [[ -z "$SLACK_WEBHOOK" ]]; then
    return
  fi

  local color="good"
  [[ "$status" == "error" ]] && color="danger"
  [[ "$status" == "warning" ]] && color="warning"

  curl -s -X POST "$SLACK_WEBHOOK" \
    -H 'Content-Type: application/json' \
    -d "{
      \"attachments\": [
        {
          \"color\": \"$color\",
          \"title\": \"API Gateway Deployment\",
          \"text\": \"$message\",
          \"ts\": $(date +%s)
        }
      ]
    }" || true
}

cleanup_old_backups() {
  log INFO "清理旧备份文件..."

  if [[ ! -d "$BACKUP_DIR/mysql" ]]; then
    return
  fi

  # 列出按修改时间排序的备份文件
  local backup_count=$(find "$BACKUP_DIR/mysql" -name "*.sql.gz" -type f | wc -l)

  if [[ $backup_count -gt $MAX_BACKUPS ]]; then
    log WARNING "备份数量($backup_count)超过限制($MAX_BACKUPS)，删除旧备份..."
    find "$BACKUP_DIR/mysql" -name "*.sql.gz" -type f -printf '%T@ %p\n' | \
      sort -rn | \
      tail -n +$((MAX_BACKUPS + 1)) | \
      cut -d' ' -f2- | \
      xargs -r rm -f
    log SUCCESS "旧备份已清理"
  fi
}

check_prerequisites() {
  log INFO "=== 检查前置条件 ==="

  # 检查项目目录
  if [[ ! -d "$PROJECT_DIR" ]]; then
    log ERROR "项目目录不存在: $PROJECT_DIR"
    return 1
  fi

  # 检查Git
  if ! command -v git &> /dev/null; then
    log ERROR "git 命令未找到"
    return 1
  fi

  # 检查Docker
  if ! command -v docker &> /dev/null; then
    log ERROR "docker 命令未找到"
    return 1
  fi

  # 检查 Docker Compose v2
  if ! docker compose version &> /dev/null 2>&1; then
    log ERROR "Docker Compose v2 命令未找到"
    return 1
  fi

  # 检查.env.production
  if [[ ! -f "$PROJECT_DIR/.env.production" ]]; then
    log ERROR ".env.production 文件不存在"
    return 1
  fi

  log SUCCESS "所有前置条件已满足"
  return 0
}

fetch_and_checkout() {
  log INFO "=== 拉取GitHub代码 ==="

  cd "$PROJECT_DIR"

  # 获取最新代码
  log INFO "从 $GITHUB_REPO 拉取 $DEPLOY_BRANCH 分支..."
  git fetch "$GITHUB_REPO" "$DEPLOY_BRANCH" || {
    log ERROR "Git fetch 失败"
    return 1
  }

  # 检查当前分支是否有未提交的更改
  if ! git diff-index --quiet HEAD --; then
    log WARNING "工作目录有未提交的更改"
    git status
  fi

  # 切换到最新代码
  log INFO "切换到本地 $DEPLOY_BRANCH 分支..."
  if git show-ref --verify --quiet "refs/heads/$DEPLOY_BRANCH"; then
    git checkout "$DEPLOY_BRANCH"
  else
    git checkout -b "$DEPLOY_BRANCH" "$GITHUB_REPO/$DEPLOY_BRANCH"
  fi || {
    log ERROR "Git checkout 失败"
    return 1
  }
  git pull --ff-only "$GITHUB_REPO" "$DEPLOY_BRANCH" || {
    log ERROR "Git pull 失败"
    return 1
  }

  # 获取当前提交哈希
  local current_commit=$(git rev-parse --short HEAD)
  log SUCCESS "已拉取最新代码 (commit: $current_commit)"

  return 0
}

run_pre_deploy_check() {
  log INFO "=== 运行部署前检查 ==="

  cd "$PROJECT_DIR"

  if [[ ! -f "scripts/pre-deploy-check.sh" ]]; then
    log WARNING "部署前检查脚本不存在，跳过"
    return 0
  fi

  if bash scripts/pre-deploy-check.sh; then
    log SUCCESS "部署前检查通过"
    return 0
  else
    log ERROR "部署前检查失败"
    return 1
  fi
}

backup_database() {
  log INFO "=== 备份数据库 ==="

  cd "$PROJECT_DIR"

  if [[ ! -f "scripts/backup-mysql.sh" ]]; then
    log WARNING "备份脚本不存在，跳过"
    return 0
  fi

  if bash scripts/backup-mysql.sh; then
    log SUCCESS "数据库备份成功"
    cleanup_old_backups
    return 0
  else
    log WARNING "数据库备份失败，但继续部署"
    return 0
  fi
}

deploy() {
  log INFO "=== 执行Docker部署 ==="

  cd "$PROJECT_DIR"

  # 检查是否需要重建镜像
  local build_flag=""
  if [[ "${1:-}" == "--build" ]]; then
    build_flag="--build"
    log INFO "将重建Docker镜像"
  fi

  if bash scripts/deploy-prod.sh $build_flag; then
    log SUCCESS "Docker部署成功"
    return 0
  else
    log ERROR "Docker部署失败"
    return 1
  fi
}

verify_deployment() {
  log INFO "=== 验证部署 ==="

  cd "$PROJECT_DIR"

  if [[ ! -f "scripts/post-deploy-verify.sh" ]]; then
    log WARNING "部署后验证脚本不存在，跳过"
    return 0
  fi

  if bash scripts/post-deploy-verify.sh; then
    log SUCCESS "部署验证通过"
    return 0
  else
    log ERROR "部署验证失败"
    return 1
  fi
}

# ==================== 主流程 ====================

main() {
  log INFO "╔════════════════════════════════════════╗"
  log INFO "║   开始自动化部署流程                   ║"
  log INFO "╚════════════════════════════════════════╝"

  # 检查前置条件
  if ! check_prerequisites; then
    log ERROR "前置条件检查失败"
    notify_slack "❌ 部署失败: 前置条件检查未通过" error
    exit 1
  fi

  # 拉取代码
  if ! fetch_and_checkout; then
    log ERROR "代码拉取失败"
    notify_slack "❌ 部署失败: Git 代码拉取失败" error
    exit 1
  fi

  # 运行部署前检查
  if ! run_pre_deploy_check; then
    log ERROR "部署前检查失败"
    notify_slack "❌ 部署失败: 部署前检查未通过" error
    exit 1
  fi

  # 备份数据库
  if ! backup_database; then
    log ERROR "数据库备份失败"
    notify_slack "⚠️  部署警告: 数据库备份失败" warning
    # 但继续部署
  fi

  # 检查是否需要重建
  local build_flag=""
  if [[ "${1:-}" == "--build" ]]; then
    build_flag="--build"
  fi

  # 执行部署
  if ! deploy $build_flag; then
    log ERROR "Docker部署失败"
    notify_slack "❌ 部署失败: Docker 部署出错" error
    exit 1
  fi

  # 验证部署
  if ! verify_deployment; then
    log ERROR "部署验证发现问题"
    notify_slack "❌ 部署完成但验证失败" error
    exit 1
  fi

  log INFO "╔════════════════════════════════════════╗"
  log INFO "║   ✅ 部署流程完成                      ║"
  log INFO "╚════════════════════════════════════════╝"

  notify_slack "✅ API Gateway 部署成功" success
  exit 0
}

# 捕获错误
trap 'log ERROR "部署流程中断"; notify_slack "❌ 部署流程中断" error; exit 1' ERR

# 运行主流程
main "$@"
