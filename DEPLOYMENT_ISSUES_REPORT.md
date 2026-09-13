# 🚨 部署配置问题检查报告

**检查时间**: 2026-09-13  
**服务器**: 10.2.210.10  
**检查结果**: 发现 **3 个关键问题** 需要立即修复

---

## ❌ 问题 1: EXPECTED_ALEMBIC_HEAD 配置错误（致命）

### 问题描述
`.env.production` 文件中 **缺少** `EXPECTED_ALEMBIC_HEAD` 配置项，导致部署脚本无法通过数据库迁移验证。

### 实际影响
- `scripts/deploy-prod.sh` 第 35 行会失败退出
- 错误信息: `EXPECTED_ALEMBIC_HEAD 必须为 f9a0b1c2d3e4`
- **部署会在数据库迁移前就终止**

### 根本原因
通过分析迁移文件链，实际最新的 migration head 是 **`a6b7c8d9e0f1`**，而不是 `f9a0b1c2d3e4`。

迁移链顺序:
```
f9a0b1c2d3e4 (reconcile_endpoint_route_governance)
  ↓
a1b2c3d4e5f6 (gateway_idempotency_records)
  ↓
a2b3c4d5e6f7 (user_username_key)
  ↓
a3b4c5d6e7f8 (unicode_username_key)
  ↓
a4b5c6d7e8f9 (mysql_document_longtext)
  ↓
a5b6c7d8e9f0 (mysql_datetime_precision)
  ↓
a6b7c8d9e0f1 (site_brand_settings) ← **最新版本**
```

### 修复方案
在 `.env.production` 文件中添加:
```bash
EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1
```

---

## ⚠️ 问题 2: 缺少可选的 Docker Compose 配置项

### 问题描述
`.env.production` 缺少以下 Docker Compose 需要的配置项（这些有默认值，但建议显式配置）:

#### 缺少的配置项:
1. **MYSQL_DATA_DIR** - MySQL 数据持久化目录
2. **BRAND_ASSET_DIR_HOST** - 品牌资源宿主机目录
3. **BACKUP_DIR** - 备份目录
4. **BACKEND_PORT** - 后端端口（默认 8000）
5. **FRONTEND_PORT** - 前端端口（默认 3000）

### 实际影响
- 部署脚本会使用硬编码的默认值（第 41-43 行）
- 如果目录权限有问题，部署可能失败

### 修复方案
在 `.env.production` 文件中添加:
```bash
# Docker Compose 配置
MYSQL_DATA_DIR=/Data/earthquake-api-gateway/mysql
BRAND_ASSET_DIR_HOST=/Data/earthquake-api-gateway/brand-assets
BACKUP_DIR=/Data/earthquake-api-gateway/backups/mysql
BACKEND_PORT=8000
FRONTEND_PORT=3000
```

---

## ⚠️ 问题 3: SERVER_DEPLOY_COMMANDS.md 中的部署命令错误

### 问题描述
部署指南文件第 18 行的 Git 仓库名称不匹配:
- 文档中写的: `git clone https://github.com/L-010/diting-api-gateway`
- 实际目录名: `earthquake-api-gateway`

### 实际影响
- 如果按照文档执行，克隆的目录名会是 `diting-api-gateway`
- 后续所有的 `cd /Data/earthquake-api-gateway` 命令都会失败

### 修复方案
需要确认实际的 GitHub 仓库名称，然后：
1. 要么将仓库名改为 `earthquake-api-gateway`
2. 要么克隆后手动重命名目录

---

## ✅ 已正确配置的项目

1. ✅ 安全密钥配置正确（APP_SECRET_KEY, APP_ENCRYPTION_KEY, API_KEY_PEPPER）
2. ✅ 数据库配置正确（MySQL 连接字符串和凭据）
3. ✅ 前端和后端 URL 配置正确
4. ✅ 文件上传下载限制配置完整
5. ✅ 会话和认证配置完整
6. ✅ EMAIL_TEST_MODE 正确设置为 false

---

## 📝 立即执行的修复步骤

### 第一步：更新本地 .env.production 文件

在你的本地项目目录执行：

```bash
cd c:/Users/LiuTao/Documents/chatgpt/地震局API部署

# 备份现有文件
cp .env.production .env.production.backup

# 添加缺失的配置项
cat >> .env.production << 'EOF'

# ============================================
# 数据库迁移版本控制
# ============================================
EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1

# ============================================
# Docker Compose 配置
# ============================================
MYSQL_DATA_DIR=/Data/earthquake-api-gateway/mysql
BRAND_ASSET_DIR_HOST=/Data/earthquake-api-gateway/brand-assets
BACKUP_DIR=/Data/earthquake-api-gateway/backups/mysql
BACKEND_PORT=8000
FRONTEND_PORT=3000
EOF
```

### 第二步：更新服务器上的配置文件

选项 A - 如果你还没有上传 .env.production：
```bash
# 在本地上传更新后的文件
scp .env.production yaozhx@10.2.210.10:/Data/earthquake-api-gateway/
```

选项 B - 如果已经在服务器上，直接在服务器编辑：
```bash
ssh yaozhx@10.2.210.10

cd /Data/earthquake-api-gateway

# 添加缺失配置
cat >> .env.production << 'EOF'

EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1
MYSQL_DATA_DIR=/Data/earthquake-api-gateway/mysql
BRAND_ASSET_DIR_HOST=/Data/earthquake-api-gateway/brand-assets
BACKUP_DIR=/Data/earthquake-api-gateway/backups/mysql
BACKEND_PORT=8000
FRONTEND_PORT=3000
EOF
```

### 第三步：验证配置文件

```bash
# 检查必要的配置项是否存在
grep EXPECTED_ALEMBIC_HEAD .env.production
grep MYSQL_DATA_DIR .env.production

# 查看完整配置
cat .env.production
```

### 第四步：重新执行部署

```bash
cd /Data/earthquake-api-gateway
bash scripts/deploy-prod.sh
```

---

## 🔍 如何判断当前遇到的是哪个问题

请分享你在服务器上遇到的具体错误信息，我可以帮你精准定位：

1. 如果错误提示 `EXPECTED_ALEMBIC_HEAD 必须为 f9a0b1c2d3e4` → **问题 1**
2. 如果提示 `mkdir` 或权限错误 → **问题 2**
3. 如果 `cd /Data/earthquake-api-gateway` 失败 → **问题 3**
4. 如果数据库迁移失败，提示找不到 migration → **问题 1**

---

## 📋 完整的正确配置清单

部署成功需要确保：
- [x] `.env.production` 包含所有必需的配置项
- [x] `EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1`（最新迁移版本）
- [x] 数据库密码与环境变量一致
- [x] 目录路径 `/Data/earthquake-api-gateway` 存在
- [x] Docker 和 Docker Compose 已安装
- [x] 服务器防火墙开放 3000 和 8000 端口
