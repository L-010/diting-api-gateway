# Ubuntu Docker MySQL 生产部署指南

> **文档版本**: 1.0  
> **最后更新**: 2026-09-13  
> **适用系统**: Ubuntu 20.04 LTS / 22.04 LTS  
> **项目**: API网关开发者平台 MVP

## 快速开始

### 在Windows上准备（仅首次）

```bash
# 1. 验证项目完整性
./scripts/pre-deploy-check.sh

# 2. 生成生产配置（首次部署）
./scripts/gen-env-production.sh

# 3. 将项目上传到Ubuntu服务器
scp -r . ubuntu@server:/Data/earthquake-api-gateway/project/
```

### 在Ubuntu服务器上部署

```bash
# 1. 登录服务器
ssh ubuntu@server
cd /Data/earthquake-api-gateway/project

# 2. 创建必要的目录
sudo mkdir -p /Data/earthquake-api-gateway/{mysql,backups}
sudo chown -R 999:999 /Data/earthquake-api-gateway/mysql  # mysql容器用户
sudo mkdir -p /etc/ssl/api-gateway
sudo chown -R root:root /etc/ssl/api-gateway

# 3. 上传TLS证书（如果未包含）
scp /path/to/fullchain.pem ubuntu@server:/etc/ssl/api-gateway/
scp /path/to/privkey.pem ubuntu@server:/etc/ssl/api-gateway/
sudo chmod 644 /etc/ssl/api-gateway/*.pem

# 4. 执行部署脚本
./scripts/deploy-prod.sh --build

# 5. 初始化管理员账户（部署完成后）
./scripts/init-admin.sh

# 6. 验证部署成功
./scripts/post-deploy-verify.sh
```

## 详细部署步骤

### 第一步：服务器准备

#### 系统要求

```bash
# 检查系统版本
cat /etc/os-release

# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装Docker和Docker Compose
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker ubuntu

# 验证安装
docker --version
docker compose version
```

#### 创建部署目录结构

```bash
# 创建主目录
sudo mkdir -p /Data/earthquake-api-gateway/project
sudo chown ubuntu:ubuntu /Data/earthquake-api-gateway/project

# 创建数据卷目录
sudo mkdir -p /Data/earthquake-api-gateway/{mysql,backups,brand-assets}

# 设置权限
sudo chown 999:999 /Data/earthquake-api-gateway/mysql      # MySQL容器用户
sudo chmod 700 /Data/earthquake-api-gateway/mysql
sudo chown 10001:10001 /Data/earthquake-api-gateway/brand-assets  # App容器用户
sudo chmod 700 /Data/earthquake-api-gateway/brand-assets

# 创建TLS证书目录
sudo mkdir -p /etc/ssl/api-gateway
sudo chmod 700 /etc/ssl/api-gateway
```

#### 上传TLS证书

```bash
# 在本地执行
scp /path/to/fullchain.pem ubuntu@server:/tmp/
scp /path/to/privkey.pem ubuntu@server:/tmp/

# 在服务器上执行
sudo mv /tmp/fullchain.pem /etc/ssl/api-gateway/
sudo mv /tmp/privkey.pem /etc/ssl/api-gateway/
sudo chmod 644 /etc/ssl/api-gateway/fullchain.pem
sudo chmod 640 /etc/ssl/api-gateway/privkey.pem
sudo chown root:docker /etc/ssl/api-gateway/privkey.pem
```

### 第二步：部署前配置

#### 上传项目文件

```bash
# 在Windows上
cd 地震局API部署
tar czf earthquake-api-gateway.tar.gz .
scp earthquake-api-gateway.tar.gz ubuntu@server:/opt/

# 在Ubuntu上
cd /opt
sudo tar xzf earthquake-api-gateway.tar.gz -C /Data/earthquake-api-gateway --strip-components=1
cd /Data/earthquake-api-gateway/project
```

#### 生成和配置.env.production

```bash
cd /Data/earthquake-api-gateway/project

# 方法1：使用交互式脚本（推荐）
bash scripts/gen-env-production.sh

# 方法2：从模板手动配置
cp .env.production.example .env.production
nano .env.production

# 验证配置
bash scripts/pre-deploy-check.sh
```

#### 关键配置项说明

```env
# 应用配置
APP_ENV=production                          # 必须
EMAIL_TEST_MODE=false                       # 必须
EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1          # 必须

# 数据库（MySQL）
DATABASE_URL=mysql+pymysql://api_user:password@mysql:3306/api_gateway?charset=utf8mb4
MYSQL_USER=api_user
MYSQL_PASSWORD=<强密码>                     # 至少12个字符，包含大小写和数字
MYSQL_ROOT_PASSWORD=<强密码>

# 网络和SSL
FRONTEND_ORIGIN=https://api.example.com     # 必须与域名一致
PUBLIC_GATEWAY_BASE_URL=https://api.example.com
SERVER_NAME=api.example.com
TLS_CERT_FILE=/etc/ssl/api-gateway/fullchain.pem
TLS_KEY_FILE=/etc/ssl/api-gateway/privkey.pem

# 上游服务
TOMODD_BASE_URL=https://tomodd.upstream.com
TOMODD_UPSTREAM_TOKEN=<token>

# 加密密钥（由gen-env-production.sh自动生成）
APP_SECRET_KEY=<32字节base64>
APP_ENCRYPTION_KEY=<Fernet密钥>
API_KEY_PEPPER=<24字节base64>

# SMTP（可选）
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=noreply@example.com
SMTP_PASSWORD=<密码>
SMTP_FROM=noreply@example.com
```

### 第三步：执行部署

```bash
cd /Data/earthquake-api-gateway/project

# 完整部署（包括构建镜像）
bash scripts/deploy-prod.sh --build

# 仅启动（镜像已存在）
bash scripts/deploy-prod.sh

# 查看部署进度
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f

# 等待所有服务就绪（约3-5分钟）
```

#### 部署过程中发生的事情

1. **前置检查** (30秒)
   - 验证 `.env.production` 配置
   - 检查依赖和文件完整性
   - 验证TLS证书

2. **构建镜像** (5-10分钟，首次)
   - 编译后端Python镜像
   - 编译前端Node.js镜像
   - 构建缓存后续部署更快

3. **启动服务** (2-3分钟)
   - MySQL容器启动并初始化数据库
   - 执行数据库迁移（Alembic）
   - 后端API容器启动
   - 前端应用容器启动
   - 异步Worker容器启动

4. **健康检查** (30-60秒)
   - 等待所有服务响应
   - 验证数据库迁移版本
   - 确保所有endpoints就绪

### 第四步：初始化管理员

```bash
# 方法1：交互式（推荐）
bash scripts/init-admin.sh

# 方法2：命令行参数
bash scripts/init-admin.sh admin "Admin@12345678"

# 密码要求
# - 至少12个字符
# - 包含大小写字母和数字
# - 不能包含用户名
```

### 第五步：验证部署

```bash
# 查看容器状态
docker compose --env-file .env.production -f docker-compose.prod.yml ps

# 应该显示所有容器为 Up 状态

# 测试后端API
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/readyz

# 应返回: {"status":"ready","checks":{"config":"ok","database":"ok","migration":"ok"}}

# 测试前端
curl -I http://127.0.0.1:3000/

# 检查数据库
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
  mysql -u api_user -p$(grep MYSQL_PASSWORD .env.production | cut -d= -f2) api_gateway \
  -e "SELECT VERSION(); SHOW TABLES; SELECT COUNT(*) as user_count FROM users;"

# 测试通过Nginx（需DNS或hosts配置）
curl https://api.example.com/health
```

## 配置Nginx反代（可选但推荐）

```bash
# 1. 安装Nginx
sudo apt install nginx -y

# 2. 配置反代
sudo ./scripts/configure-nginx.sh

# 3. 验证配置
sudo nginx -t

# 4. 重启Nginx
sudo systemctl restart nginx

# 5. 验证HTTPS
curl -k https://api.example.com/health
```

## 定期维护

### 设置自动备份

```bash
# 编辑crontab
sudo crontab -e

# 添加以下任务
# 每天凌晨2:15备份MySQL
15 2 * * * cd /Data/earthquake-api-gateway/project && ./scripts/backup-mysql.sh >> /var/log/api-gateway-backup.log 2>&1

# 每天凌晨2:45执行清理任务
45 2 * * * cd /Data/earthquake-api-gateway/project && \
  docker compose --env-file .env.production -f docker-compose.prod.yml run --rm backend \
  python -m app.maintenance cleanup >> /var/log/api-gateway-cleanup.log 2>&1
```

### 查看日志

```bash
# 实时日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f

# 特定服务的日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs backend
docker compose --env-file .env.production -f docker-compose.prod.yml logs frontend

# 查看上次n行
docker compose --env-file .env.production -f docker-compose.prod.yml logs backend --tail 100
```

### 检查服务状态

```bash
# 查看资源占用
docker stats

# 查看容器详情
docker ps -a

# 查看网络
docker network ls
```

### 停止和重启

```bash
# 优雅停止（保留数据）
docker compose --env-file .env.production -f docker-compose.prod.yml stop

# 启动服务
docker compose --env-file .env.production -f docker-compose.prod.yml up -d

# 重启特定服务
docker compose --env-file .env.production -f docker-compose.prod.yml restart backend

# 删除容器（保留数据卷）
docker compose --env-file .env.production -f docker-compose.prod.yml down

# 删除所有（包括数据卷）- 谨慎使用！
docker compose --env-file .env.production -f docker-compose.prod.yml down -v
```

## 故障排查

### 问题1：后端无法连接到MySQL

**症状**：`/readyz` 返回 `"database": "failed"`

**排查步骤**：
```bash
# 1. 检查MySQL容器状态
docker compose --env-file .env.production -f docker-compose.prod.yml ps mysql

# 2. 查看MySQL日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs mysql | tail -50

# 3. 验证DATABASE_URL格式
grep DATABASE_URL .env.production

# 4. 手动测试连接
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend \
  python -c "
from sqlalchemy import create_engine, text
from app.config import get_settings
settings = get_settings()
engine = create_engine(settings.database_url)
with engine.connect() as conn:
    result = conn.execute(text('SELECT 1'))
    print('✓ 数据库连接成功')
  "

# 5. 检查网络连接
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend \
  bash -c "nc -zv mysql 3306"
```

**常见原因和解决方案**：
- **密码URL编码错误**：检查 `@` 等特殊字符是否正确编码
- **MySQL未就绪**：等待MySQL容器完全启动（查看健康检查）
- **网络隔离**：检查docker-compose网络配置
- **权限问题**：验证MySQL用户和数据库权限

### 问题2：数据库迁移失败

**症状**：`/readyz` 返回 `"migration": "failed"`

**排查步骤**：
```bash
# 1. 检查当前迁移状态
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend \
  python -m alembic current

# 2. 查看迁移历史
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend \
  python -m alembic history

# 3. 查看具体错误
docker compose --env-file .env.production -f docker-compose.prod.yml logs backend | grep -i "alembic\|migration\|error"

# 4. 尝试重新运行迁移（如果需要）
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend \
  python scripts/migrate.py
```

**常见原因和解决方案**：
- **版本冲突**：确保 `EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1`
- **数据冲突**：检查用户名重复（运行 `migration_precheck.py`）
- **MySQL兼容性**：检查MySQL 8.0特定的迁移是否失败

### 问题3：前端无法连接到后端

**症状**：前端加载失败，控制台显示CORS或连接错误

**排查步骤**：
```bash
# 1. 检查BACKEND_ORIGIN配置
docker compose --env-file .env.production -f docker-compose.prod.yml exec frontend \
  cat /app/.env.local | grep BACKEND_ORIGIN

# 2. 验证后端是否可达
docker compose --env-file .env.production -f docker-compose.prod.yml exec frontend \
  curl -v http://backend:8000/health

# 3. 检查前端日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs frontend | tail -50

# 4. 检查Docker网络
docker network inspect $(docker compose --env-file .env.production -f docker-compose.prod.yml network ls -q)
```

**常见原因和解决方案**：
- **BACKEND_ORIGIN错误**：应该是 `http://backend:8000`（内部）或 `https://api.example.com`（外部）
- **后端未就绪**：检查后端容器状态和日志
- **网络配置错误**：验证 `docker-compose.prod.yml` 中的 `depends_on` 和健康检查

### 问题4：TLS证书错误

**症状**：Nginx返回 `SSL_ERROR_RX_RECORD_TOO_LONG` 或证书不被信任

**排查步骤**：
```bash
# 1. 检查证书文件
ls -la /etc/ssl/api-gateway/

# 2. 验证证书格式
openssl x509 -in /etc/ssl/api-gateway/fullchain.pem -text -noout

# 3. 验证私钥
openssl rsa -in /etc/ssl/api-gateway/privkey.pem -check

# 4. 检查Nginx配置
sudo nginx -t

# 5. 查看Nginx错误日志
sudo tail -50 /var/log/nginx/error.log

# 6. 重启Nginx
sudo systemctl restart nginx
```

**常见原因和解决方案**：
- **证书过期**：检查有效期 `openssl x509 -enddate -noout`
- **证书路径错误**：确保.env中的路径正确且文件可读
- **Nginx权限**：确保Nginx用户可以读取私钥

### 问题5：磁盘空间不足

**症状**：容器无法启动或数据卷写入失败

**排查步骤**：
```bash
# 1. 检查磁盘使用情况
df -h /Data

# 2. 检查MySQL数据卷大小
du -sh /Data/earthquake-api-gateway/mysql

# 3. 检查备份大小
du -sh /Data/earthquake-api-gateway/backups

# 4. 清理old backups
ls -ltr /Data/earthquake-api-gateway/backups/mysql/ | tail -5
# 手动删除超过14天的备份
find /Data/earthquake-api-gateway/backups -mtime +14 -delete
```

**解决方案**：
- **扩展分区**：在云平台中增加磁盘空间
- **清理备份**：删除旧备份文件
- **迁移数据**：移动大型日志或备份到其他位置

## 备份和恢复

### 创建备份

```bash
# 手动备份
cd /Data/earthquake-api-gateway/project
bash scripts/backup-mysql.sh

# 查看备份
ls -lh /Data/earthquake-api-gateway/backups/mysql/

# 备份说明
# - 每个备份文件名: api_gateway_YYYYMMDD_HHMMSS.sql
# - 保留14天的备份
# - 自动压缩为gzip格式
```

### 恢复数据

```bash
# 1. 检查可用备份
ls -lh /Data/earthquake-api-gateway/backups/mysql/

# 2. 恢复备份（假设备份文件为 api_gateway_20260913_020000.sql.gz）
gunzip < /Data/earthquake-api-gateway/backups/mysql/api_gateway_20260913_020000.sql.gz | \
  docker compose --env-file .env.production -f docker-compose.prod.yml exec -T mysql \
  mysql -u api_user -p$(grep MYSQL_PASSWORD .env.production | cut -d= -f2) api_gateway

# 3. 验证恢复
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
  mysql -u api_user -p$(grep MYSQL_PASSWORD .env.production | cut -d= -f2) api_gateway \
  -e "SELECT COUNT(*) FROM users;"
```

## 更新部署

### 更新代码

```bash
cd /Data/earthquake-api-gateway/project

# 1. 拉取最新代码
git pull

# 2. 检查变更
git diff

# 3. 重新构建镜像
bash scripts/deploy-prod.sh --build

# 4. 执行数据库迁移（如果有新迁移）
docker compose --env-file .env.production -f docker-compose.prod.yml run --rm backend \
  python scripts/migrate.py

# 5. 重启服务
docker compose --env-file .env.production -f docker-compose.prod.yml down
docker compose --env-file .env.production -f docker-compose.prod.yml up -d
```

## 性能优化建议

1. **增加后端workers**
   ```bash
   # 编辑docker-compose.prod.yml中backend服务的CMD
   # 改为: --workers 4 (取决于CPU核心数)
   ```

2. **启用Redis缓存**
   ```bash
   # 在docker-compose.prod.yml中添加redis服务
   # 在.env中配置REDIS_URL
   ```

3. **配置CDN**
   - 将前端资源（JS、CSS、图片）上传到CDN
   - 在next.config.ts中配置assetPrefix

4. **监控和告警**
   ```bash
   # 使用Prometheus + Grafana
   # 或者集成到现有监控系统
   ```

## 安全建议

1. **定期更新**
   - 每月检查依赖更新
   - 及时安装安全补丁
   - 定期更新Docker镜像基础版本

2. **密钥管理**
   - 使用强密码和密钥
   - 定期轮换敏感密钥
   - 使用专业的密钥管理系统（如HashiCorp Vault）

3. **网络安全**
   - 配置防火墙规则
   - 限制不必要的端口暴露
   - 使用VPN或私有网络

4. **日志和审计**
   - 定期检查日志
   - 启用审计功能
   - 配置日志聚合和分析

## 联系支持

如遇到问题：
1. 检查日志：`docker compose logs -f`
2. 参考故障排查部分
3. 查看项目文档：`docs/DOCKER_PRODUCTION.md`
4. 提交issue包含：错误日志、环境信息、复现步骤
