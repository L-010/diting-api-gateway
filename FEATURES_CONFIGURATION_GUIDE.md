# 📧 邮件功能 & 文件同步配置指南

**项目**: 地震局API部署  
**日期**: 2026年9月13日  
**内容**: 邮件服务、文件同步功能的完整配置说明

---

## ✅ 项目功能完整清单

### 🎯 核心功能

| 功能模块 | 状态 | 实现方式 | 位置 |
|---------|------|--------|------|
| **邮件服务** | ✅ 已集成 | Email Worker (独立容器) | docker-compose.prod.yml |
| **文件同步** | ✅ 已集成 | File Worker (独立容器) | docker-compose.prod.yml |
| **后端API** | ✅ 已集成 | FastAPI (Python) | backend/app/ |
| **前端UI** | ✅ 已集成 | Next.js (TypeScript) | frontend/ |
| **数据库** | ✅ 已集成 | MySQL 8.0 (Docker) | /Data/earthquake-api-gateway/mysql |
| **Nginx反向代理** | ✅ 已集成 | Nginx (Docker) | nginx/ |

---

## 📧 邮件功能配置详解

### 1️⃣ Email Worker 服务

#### Docker容器配置
```yaml
email-worker:
  image: api-gateway-backend:local
  restart: unless-stopped
  env_file: .env.production
  environment:
    DATABASE_URL: ${DATABASE_URL}
    PYTHONPATH: /app/backend
  command: ["python", "-m", "app.email_worker", "--interval", "5"]
  depends_on:
    mysql:
      condition: service_healthy
  healthcheck:
    test: ["CMD", "python", "-c", "import app.email_worker; print('ok')"]
    interval: 30s
    timeout: 5s
    retries: 3
```

**说明**:
- `--interval 5`: 每5秒检查一次待发送邮件队列
- `depends_on mysql`: 需要等待MySQL启动完成
- `healthcheck`: 定期检查worker是否运行正常

#### 2️⃣ SMTP 配置（.env.production）

```bash
# SMTP服务器配置
SMTP_HOST=smtp.example.com              # 邮件服务器地址
SMTP_PORT=587                            # SMTP端口 (587: TLS, 465: SSL)
SMTP_USERNAME=your-email@example.com    # 邮箱账户
SMTP_PASSWORD=your-app-password         # 应用密码（不是邮箱密码）
SMTP_FROM=noreply@example.com           # 发件人地址
SMTP_USE_TLS=true                       # 使用TLS加密 (587端口时设为true)
SMTP_TIMEOUT_SECONDS=10                 # SMTP超时时间

# 邮件功能配置
EMAIL_OUTBOX_MAX_ATTEMPTS=5             # 最大重试次数
EMAIL_TEST_MODE=false                   # 测试模式（仅开发/测试环境）
```

#### 3️⃣ 常见邮箱服务配置

**Gmail**
```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password          # 需要生成应用密码
SMTP_USE_TLS=true
SMTP_FROM=your-email@gmail.com
```

**163邮箱**
```bash
SMTP_HOST=smtp.163.com
SMTP_PORT=465                            # 163推荐使用465
SMTP_USERNAME=your-email@163.com
SMTP_PASSWORD=your-authorization-code    # 授权码（不是邮箱密码）
SMTP_FROM=your-email@163.com
SMTP_USE_TLS=false                       # 465端口不需要TLS
```

**企业邮箱（阿里企业邮箱）**
```bash
SMTP_HOST=smtp.qiye.aliyun.com
SMTP_PORT=465
SMTP_USERNAME=your-email@company.com
SMTP_PASSWORD=your-password
SMTP_FROM=your-email@company.com
SMTP_USE_TLS=false
```

#### 4️⃣ 邮件功能工作流程

```
用户操作 (API调用)
    ↓
后端API接收请求
    ↓
将邮件写入数据库 (email_outbox 表)
    ↓
Email Worker 定期轮询 (每5秒)
    ↓
发现待发送邮件
    ↓
通过SMTP发送邮件
    ↓
更新数据库状态 (已发送/失败)
    ↓
如果失败，自动重试 (最多5次)
```

#### 5️⃣ 验证邮件功能

```bash
# 1. 检查email-worker容器是否运行
docker compose --env-file .env.production -f docker-compose.prod.yml ps | grep email-worker

# 2. 查看email-worker日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs email-worker -f

# 3. 检查数据库中的邮件队列
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
  mysql -u api_user -p$(grep MYSQL_PASSWORD .env.production | cut -d= -f2) -D api_gateway \
  -e "SELECT id, recipient, subject, status, created_at FROM email_outbox LIMIT 10;"

# 4. 测试发送邮件（在后端容器中）
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend \
  python -c "from app.services.email_delivery import send_test_email; send_test_email('your-email@example.com')"
```

---

## 📁 文件同步功能配置详解

### 1️⃣ File Worker 服务

#### Docker容器配置
```yaml
file-worker:
  image: api-gateway-backend:local
  restart: unless-stopped
  env_file: .env.production
  environment:
    DATABASE_URL: ${DATABASE_URL}
    PYTHONPATH: /app/backend
  command: ["python", "-m", "app.file_worker"]
  depends_on:
    mysql:
      condition: service_healthy
  healthcheck:
    test: ["CMD", "python", "-c", "import app.file_worker; print('ok')"]
    interval: 30s
    timeout: 5s
    retries: 3
```

**说明**:
- 持续运行的后台服务
- 依赖MySQL数据库
- 自动监测和处理文件同步任务

#### 2️⃣ 文件同步配置（.env.production）

```bash
# 远程文件存储配置
REMOTE_FILES_STORAGE_TYPE=local         # local / s3 / oss (本地存储/S3/阿里云)
REMOTE_FILES_LOCAL_PATH=/Data/earthquake-api-gateway/brand-assets  # 本地存储路径

# 如果使用S3存储
# AWS_S3_BUCKET=your-bucket
# AWS_S3_REGION=us-east-1
# AWS_ACCESS_KEY_ID=your-key-id
# AWS_SECRET_ACCESS_KEY=your-secret-key

# 如果使用阿里云OSS
# OSS_ENDPOINT=oss-cn-beijing.aliyuncs.com
# OSS_BUCKET=your-bucket
# OSS_ACCESS_KEY_ID=your-key-id
# OSS_ACCESS_KEY_SECRET=your-secret-key

# 文件同步配置
FILE_SYNC_BATCH_SIZE=10                 # 每批处理的文件数量
FILE_SYNC_TIMEOUT_SECONDS=30            # 单个文件同步超时时间
FILE_SYNC_MAX_RETRIES=3                 # 失败重试次数
REMOTE_FILE_EXPIRY_DAYS=90              # 文件过期天数
```

#### 3️⃣ 支持的存储方式

| 存储方式 | 特点 | 适用场景 |
|---------|------|--------|
| **本地存储** | 简单、无成本 | 小规模、单服务器 |
| **AWS S3** | 高可用、可扩展 | 企业应用 |
| **阿里云OSS** | 国内加速、便宜 | 国内用户 |
| **MinIO** | 自托管S3兼容 | 私有云部署 |

**推荐**: 小规模使用本地存储，大规模使用云存储

#### 4️⃣ 文件同步工作流程

```
用户上传文件 (API调用)
    ↓
文件存储到本地临时目录
    ↓
在数据库创建同步任务 (file_sync_jobs 表)
    ↓
File Worker 持续监听
    ↓
获取待处理的同步任务
    ↓
执行文件同步 (验证、处理、上传)
    ↓
更新任务状态 (成功/失败)
    ↓
如果成功，清理临时文件
    ↓
如果失败，自动重试 (最多3次)
```

#### 5️⃣ 品牌资源存储

项目中的品牌资源和用户上传文件存储在：
```
/Data/earthquake-api-gateway/brand-assets/
```

这是一个Docker卷，持久化存储文件数据。

#### 6️⃣ 验证文件同步功能

```bash
# 1. 检查file-worker容器是否运行
docker compose --env-file .env.production -f docker-compose.prod.yml ps | grep file-worker

# 2. 查看file-worker日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs file-worker -f

# 3. 检查同步任务队列
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
  mysql -u api_user -p$(grep MYSQL_PASSWORD .env.production | cut -d= -f2) -D api_gateway \
  -e "SELECT id, source_path, status, created_at FROM file_sync_jobs LIMIT 10;"

# 4. 检查存储目录
ls -lah /Data/earthquake-api-gateway/brand-assets/

# 5. 查看文件大小
du -sh /Data/earthquake-api-gateway/brand-assets/
```

---

## 🔧 完整的 Docker 服务编排

```
启动顺序:
1. MySQL 启动 ✓
   ↓
2. 等待MySQL健康检查通过
   ↓
3. Backend 启动 ✓
   ├─ 依赖: MySQL (健康)
   ├─ 运行: FastAPI服务
   ├─ 端口: 8000
   └─ 功能: API接口、邮件入库、文件同步入库
   ↓
4. Frontend 启动 ✓
   ├─ 依赖: Backend (健康)
   ├─ 运行: Next.js服务
   ├─ 端口: 3000
   └─ 功能: 用户界面
   ↓
5. Email Worker 启动 ✓
   ├─ 依赖: MySQL (健康)
   ├─ 运行: 邮件发送worker
   ├─ 功能: 从队列发送邮件
   └─ 重试: 失败自动重试
   ↓
6. File Worker 启动 ✓
   ├─ 依赖: MySQL (健康)
   ├─ 运行: 文件同步worker
   └─ 功能: 处理文件同步任务

所有服务网络隔离:
├─ internal bridge network
└─ 不暴露到公网
```

---

## 📊 部署配置检查清单

### 邮件功能检查清单
- [ ] .env.production 中填写了SMTP_HOST
- [ ] .env.production 中填写了SMTP_PORT
- [ ] .env.production 中填写了SMTP_USERNAME
- [ ] .env.production 中填写了SMTP_PASSWORD
- [ ] .env.production 中填写了SMTP_FROM
- [ ] SMTP_USE_TLS 设置正确 (根据端口)
- [ ] EMAIL_OUTBOX_MAX_ATTEMPTS >= 3
- [ ] EMAIL_TEST_MODE = false (生产环境)
- [ ] email-worker 容器已启动
- [ ] email-worker 健康检查通过

### 文件同步检查清单
- [ ] /Data/earthquake-api-gateway/brand-assets/ 目录已创建
- [ ] 目录权限已设置 (chmod 777)
- [ ] .env.production 中的存储类型已配置
- [ ] 如果使用云存储，AccessKey已配置
- [ ] file-worker 容器已启动
- [ ] file-worker 健康检查通过
- [ ] 数据库中的 file_sync_jobs 表已创建

---

## 🚀 快速启用指南

### 启用邮件功能（3步）

**第1步**: 获取邮箱授权码
```
以163邮箱为例:
1. 登录 https://mail.163.com
2. 进入 设置 > POP3/SMTP/IMAP
3. 启用 SMTP 服务
4. 生成授权码并保存
```

**第2步**: 配置 .env.production
```bash
SMTP_HOST=smtp.163.com
SMTP_PORT=465
SMTP_USERNAME=your-email@163.com
SMTP_PASSWORD=your-authorization-code  # 刚才生成的授权码
SMTP_FROM=your-email@163.com
SMTP_USE_TLS=false
```

**第3步**: 重启email-worker
```bash
docker compose --env-file .env.production -f docker-compose.prod.yml restart email-worker
```

### 启用文件同步功能（2步）

**第1步**: 创建存储目录（已在部署脚本中完成）
```bash
sudo mkdir -p /Data/earthquake-api-gateway/brand-assets
sudo chmod 777 /Data/earthquake-api-gateway/brand-assets
```

**第2步**: 配置 .env.production
```bash
REMOTE_FILES_STORAGE_TYPE=local
REMOTE_FILES_LOCAL_PATH=/Data/earthquake-api-gateway/brand-assets
FILE_SYNC_BATCH_SIZE=10
FILE_SYNC_TIMEOUT_SECONDS=30
FILE_SYNC_MAX_RETRIES=3
REMOTE_FILE_EXPIRY_DAYS=90
```

---

## 📈 监控和维护

### 实时监控

```bash
# 监控所有Worker
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f email-worker file-worker

# 监控邮件队列
watch -n 5 'docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql mysql -u api_user -p$(grep MYSQL_PASSWORD .env.production | cut -d= -f2) -D api_gateway -e "SELECT COUNT(*) as pending_emails FROM email_outbox WHERE status='\''pending'\''"'

# 监控文件同步任务
watch -n 5 'docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql mysql -u api_user -p$(grep MYSQL_PASSWORD .env.production | cut -d= -f2) -D api_gateway -e "SELECT COUNT(*) as pending_files FROM file_sync_jobs WHERE status='\''pending'\''"'
```

### 故障排查

**问题**: 邮件未发送
```bash
# 1. 检查worker是否运行
docker compose --env-file .env.production -f docker-compose.prod.yml ps | grep email-worker

# 2. 查看错误日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs email-worker --tail 100

# 3. 检查SMTP配置
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend \
  python -c "from app.config import settings; print(f'SMTP_HOST: {settings.smtp_host}')"

# 4. 测试SMTP连接
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend \
  python -c "import smtplib; s=smtplib.SMTP('smtp.163.com', 587); print('OK')"
```

**问题**: 文件同步失败
```bash
# 1. 检查worker是否运行
docker compose --env-file .env.production -f docker-compose.prod.yml ps | grep file-worker

# 2. 查看错误日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs file-worker --tail 100

# 3. 检查存储目录权限
ls -ld /Data/earthquake-api-gateway/brand-assets/

# 4. 检查磁盘空间
df -h /Data/earthquake-api-gateway/
```

---

## ✅ 功能状态总结

| 功能 | 状态 | 配置位置 | 容器 | 启动顺序 |
|------|------|--------|------|--------|
| **邮件服务** | ✅ 就绪 | .env.production (SMTP_*) | email-worker | 第5个 |
| **文件同步** | ✅ 就绪 | .env.production (REMOTE_FILES_*) | file-worker | 第6个 |
| **后端API** | ✅ 就绪 | docker-compose.prod.yml | backend | 第3个 |
| **前端UI** | ✅ 就绪 | docker-compose.prod.yml | frontend | 第4个 |
| **数据库** | ✅ 就绪 | docker-compose.prod.yml | mysql | 第1个 |

---

## 🎯 部署后的下一步

1. ✅ 项目已清理完毕
2. ✅ 所有功能已集成
3. ✅ 所有容器已配置
4. ✅ 部署脚本已就绪

**现在可以**:
```bash
# 推送到GitHub
git push -u origin main

# 在Ubuntu服务器部署
cd /Data/earthquake-api-gateway/project
./scripts/gen-env-production.sh  # 配置邮件和文件同步
./scripts/deploy-prod.sh --build
```

---

**所有功能都已配置完毕，可以立即部署！** 🚀

