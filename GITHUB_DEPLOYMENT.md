# GitHub容器化部署指南

本指南说明如何将项目上传至GitHub，然后在Ubuntu服务器上进行一键Docker部署。

---

## 第一部分：上传项目到GitHub

### 1.1 在GitHub创建仓库

1. 访问 https://github.com/new
2. 填写信息：
   - **Repository name**: `api-gateway` (或你喜欢的名称)
   - **Description**: API Gateway 开发者平台 MVP
   - **Visibility**: Private（推荐）或 Public
   - 其他选项保持默认

3. **不要选择** "Initialize this repository with:" 中的任何选项

4. 点击 "Create repository"

### 1.2 在本地初始化Git并推送

在项目根目录执行：

```bash
# 进入项目目录
cd /path/to/project

# 初始化Git仓库
git init

# 验证.gitignore配置（应该已包含.env.production）
cat .gitignore  # 确保包含 .env 和 .env.* (除了.env.production.example)

# 添加所有文件
git add .

# 提交
git commit -m "Initial project commit: API Gateway MVP with Docker deployment"

# 添加GitHub远程
git remote add origin https://github.com/YOUR_USERNAME/api-gateway.git

# 重命名分支为main（GitHub默认）
git branch -M main

# 推送
git push -u origin main
```

### 1.3 验证推送成功

访问 https://github.com/YOUR_USERNAME/api-gateway 验证：
- ✅ 所有源代码已推送
- ✅ `.env.production` 不在仓库中（被.gitignore忽略）
- ✅ `backend/alembic/versions/` 包含所有迁移文件
- ✅ `docker-compose.prod.yml` 已更新（端口改为0.0.0.0）

---

## 第二部分：Ubuntu服务器部署

### 2.1 服务器前置准备

在Ubuntu 20.04/22.04 LTS服务器上执行：

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装Docker
sudo apt install -y docker.io docker-compose

# 验证Docker安装
docker --version
docker compose version

# 将当前用户加入docker组（可选，便捷但需谨慎）
sudo usermod -aG docker $USER
# 重新登录使配置生效
newgrp docker

# 创建数据目录
sudo mkdir -p /Data/api-mvp/{mysql,brand-assets,backups/mysql}

# 设置权限（允许Docker容器写入）
sudo chmod 777 /Data/api-mvp/{mysql,brand-assets,backups}
```

### 2.2 克隆项目

```bash
# 选择部署目录
cd /opt  # 或其他合适的位置

# 克隆GitHub仓库
git clone https://github.com/YOUR_USERNAME/api-gateway.git
cd api-gateway

# 验证关键文件存在
ls -la scripts/*.sh              # 查看脚本
ls -la docker-compose.prod.yml   # Docker Compose配置
ls -la .env.production.example   # 配置模板
```

### 2.3 准备TLS证书

**选项A：使用Let's Encrypt（推荐）**

```bash
# 安装Certbot
sudo apt install -y certbot python3-certbot-nginx

# 获取证书（替换your-domain.com）
sudo certbot certonly --standalone -d your-domain.com

# 证书路径（Certbot会输出）
# 通常在: /etc/letsencrypt/live/your-domain.com/
```

**选项B：使用自签名证书（测试用）**

```bash
sudo mkdir -p /etc/ssl/api-gateway

sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/ssl/api-gateway/privkey.pem \
  -out /etc/ssl/api-gateway/fullchain.pem \
  -subj "/CN=your-domain.com"

sudo chmod 644 /etc/ssl/api-gateway/*.pem
```

### 2.4 生成生产环境配置

```bash
# 执行配置生成脚本（交互式）
./scripts/gen-env-production.sh

# 脚本会提示输入：
# 1. 数据库名称（默认：api_gateway）
# 2. 数据库用户（默认：api_user）
# 3. 数据库密码（可自动生成）
# 4. MySQL root密码（可自动生成）
# 5. 服务器域名（例：api.example.com）
# 6. TLS证书路径（例：/etc/letsencrypt/live/api.example.com/fullchain.pem）
# 7. TLS私钥路径（例：/etc/letsencrypt/live/api.example.com/privkey.pem）
# 8. TOMODD上游地址
# 9. TOMODD上游Token
# 10. SMTP配置（可选）

# 验证配置生成
cat .env.production | head -20
```

**关键输入值示例**：
```
数据库名称: api_gateway
数据库用户: api_user
数据库密码: GeneratedRandomPassword123!
MySQL root密码: GeneratedRootPassword456!
服务器域名: api.example.com
TLS证书: /etc/letsencrypt/live/api.example.com/fullchain.pem
TLS私钥: /etc/letsencrypt/live/api.example.com/privkey.pem
TOMODD地址: https://tomodd.example.com
TOMODD Token: your-upstream-token-here
```

### 2.5 执行完整部署

```bash
# 构建并启动所有容器
./scripts/deploy-prod.sh --build

# 脚本执行流程：
# 1. 配置前检查
# 2. 目录创建
# 3. Docker build（可能耗时5-10分钟）
# 4. MySQL启动
# 5. 数据库迁移
# 6. 后端/前端/worker启动
# 7. 健康检查
# 8. 输出容器状态

# 等待脚本完成（通常10-15分钟）
```

**正常输出示例**：
```
✓ MySQL 已启动
✓ 后端容器已启动
✓ 前端容器已启动
✓ Email worker已启动
✓ File worker已启动

容器状态:
CONTAINER ID   IMAGE                          STATUS
abc123...      api-gateway-backend:local      Up 2 minutes (healthy)
def456...      api-gateway-frontend:local     Up 1 minute (healthy)
ghi789...      mysql:8.0                      Up 5 minutes (healthy)
...

部署完成。请确认宿主机 Nginx 已加载 nginx/api-gateway.conf.example 的正式配置。
```

### 2.6 创建管理员账户

```bash
# 创建管理员（首次部署必须）
./scripts/init-admin.sh

# 交互式输入：
# 请输入管理员用户名 [admin]: admin
# 请输入管理员密码: YourPassword123!
# 确认密码: YourPassword123!

# 或使用命令行参数
./scripts/init-admin.sh admin 'YourPassword123!'
```

**密码要求**：
- 至少12个字符
- 包含大写字母
- 包含小写字母
- 包含数字
- 不能包含用户名

### 2.7 配置Nginx反向代理

```bash
# 复制Nginx配置模板
sudo cp nginx/api-gateway.conf.example /etc/nginx/sites-available/api-gateway

# 编辑配置文件（替换域名和证书路径）
sudo nano /etc/nginx/sites-available/api-gateway

# 修改以下内容：
# - server_name api.example.com  -> 改为你的域名
# - ssl_certificate /etc/ssl/api-gateway/fullchain.pem  -> 改为实际路径
# - ssl_certificate_key /etc/ssl/api-gateway/privkey.pem  -> 改为实际路径

# 启用配置（如果使用sites-enabled）
sudo ln -s /etc/nginx/sites-available/api-gateway /etc/nginx/sites-enabled/

# 测试Nginx配置
sudo nginx -t

# 输出应该显示：
# nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
# nginx: configuration will be successful

# 重载Nginx
sudo systemctl reload nginx

# 或启动Nginx（如果还未启动）
sudo systemctl start nginx
sudo systemctl enable nginx  # 开机自启
```

### 2.8 验证部署

```bash
# 1. 检查容器状态
docker compose --env-file .env.production -f docker-compose.prod.yml ps

# 2. 检查后端健康
curl -I http://localhost:8000/livez

# 3. 检查数据库连接
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
  mysql -u api_user -p$(grep MYSQL_PASSWORD .env.production | cut -d= -f2) \
  -e "SELECT 1 as connected;"

# 4. 检查日志（如有问题）
docker compose --env-file .env.production -f docker-compose.prod.yml logs backend

# 5. 测试HTTPS访问（等待DNS生效）
curl -I https://api.example.com

# 6. 登录管理后台
# 访问 https://api.example.com
# 使用刚创建的管理员账户登录
```

---

## 第三部分：后续操作

### 3.1 查看运行状态

```bash
# 查看所有容器
docker compose --env-file .env.production -f docker-compose.prod.yml ps

# 查看特定服务日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs backend --tail 50
docker compose --env-file .env.production -f docker-compose.prod.yml logs frontend --tail 50

# 实时跟踪日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f backend
```

### 3.2 更新代码部署

当代码有更新时：

```bash
# 进入项目目录
cd /opt/api-gateway

# 拉取最新代码
git pull origin main

# 重新构建并部署
./scripts/deploy-prod.sh --build

# 或只重启不重建
docker compose --env-file .env.production -f docker-compose.prod.yml restart
```

### 3.3 备份数据库

```bash
# 执行备份脚本
./scripts/backup-mysql.sh

# 或手动备份
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
  mysqldump -u api_user -p$(grep MYSQL_PASSWORD .env.production | cut -d= -f2) \
  api_gateway > backup-$(date +%Y%m%d_%H%M%S).sql
```

### 3.4 停止服务

```bash
# 优雅停止（保留数据）
docker compose --env-file .env.production -f docker-compose.prod.yml stop

# 完全移除容器（保留数据）
docker compose --env-file .env.production -f docker-compose.prod.yml down

# 移除一切包括数据（谨慎！）
docker compose --env-file .env.production -f docker-compose.prod.yml down -v
```

### 3.5 扩展容量

```bash
# 增加后端worker数量
# 编辑 .env.production：AGW_WORKERS=4

# 重启后端
docker compose --env-file .env.production -f docker-compose.prod.yml restart backend

# 扩展MySQL存储（修改宿主机数据目录大小）
# 如果使用LVM或云盘，按照宿主机文档扩展
```

---

## 故障排查

### 问题1：部署失败 "缺少 .env.production"

```bash
# 确保已运行配置生成脚本
./scripts/gen-env-production.sh

# 验证文件存在
ls -la .env.production
```

### 问题2：后端容器持续重启

```bash
# 查看错误日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs backend

# 常见原因：
# - DATABASE_URL不正确
# - APP_SECRET_KEY/APP_ENCRYPTION_KEY未设置
# - 依赖缺失

# 进入容器调试
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend bash
```

### 问题3：前端无法连接后端

```bash
# 检查网络连通性
docker compose --env-file .env.production -f docker-compose.prod.yml exec frontend \
  curl -I http://backend:8000/health

# 检查前端环境变量
docker compose --env-file .env.production -f docker-compose.prod.yml exec frontend \
  printenv | grep BACKEND
```

### 问题4：MySQL连接失败

```bash
# 检查MySQL容器健康
docker compose --env-file .env.production -f docker-compose.prod.yml ps | grep mysql

# 查看MySQL日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs mysql

# 验证凭据
cat .env.production | grep MYSQL

# 手动测试连接
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
  mysql -h 127.0.0.1 -u api_user -p
```

### 问题5：端口已被占用

```bash
# 查看占用8000/3000的进程
sudo lsof -i :8000
sudo lsof -i :3000

# 修改端口（编辑.env.production）
BACKEND_PORT=8001
FRONTEND_PORT=3001

# 重启容器
docker compose --env-file .env.production -f docker-compose.prod.yml restart
```

---

## 安全建议

### 环境变量安全

```bash
# ✅ 正确：保护.env.production文件
chmod 600 .env.production

# ✅ 正确：不提交到版本控制
cat .gitignore | grep -E "^\.env"

# ❌ 错误：不要将密钥硬编码在Dockerfile
# ❌ 错误：不要在日志中打印敏感信息
```

### 密钥轮换

```bash
# 定期更换密钥
# 1. 备份当前配置
cp .env.production .env.production.backup

# 2. 编辑密钥部分
nano .env.production

# 3. 重启容器应用新密钥
docker compose --env-file .env.production -f docker-compose.prod.yml restart backend
```

### 网络安全

```bash
# ✅ 只暴露必要的端口给外网（通过Nginx）
# ✅ MySQL不直接暴露到公网
# ✅ 使用防火墙限制访问
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable
```

---

## 性能优化

### 1. 调整worker进程数

```bash
# 编辑.env.production
AGW_WORKERS=4  # 默认2，根据CPU核心数调整

# 重启后端
docker compose --env-file .env.production -f docker-compose.prod.yml restart backend
```

### 2. 增加MySQL连接池

```bash
# 这部分已在backend代码中自动处理
# pool_pre_ping: True (连接检查)
# pool_recycle: 1800 (30分钟轮换)
```

### 3. Nginx缓存优化

编辑 `/etc/nginx/sites-available/api-gateway`，添加：

```nginx
# 静态资源缓存
location ~* \.(?:js|css|woff|woff2|png|jpg|jpeg|gif|svg)$ {
    expires 30d;
    add_header Cache-Control "public, immutable";
}

# API不缓存
location /api/ {
    proxy_no_cache 1;
    proxy_cache_bypass 1;
}
```

---

## 一键快速参考

### 最小化部署流程

```bash
# 1️⃣ 服务器初始化（仅首次）
sudo apt update && sudo apt install -y docker.io docker-compose

# 2️⃣ 克隆项目
cd /opt && git clone https://github.com/YOUR_USERNAME/api-gateway.git && cd api-gateway

# 3️⃣ 生成配置（交互式，5分钟）
./scripts/gen-env-production.sh

# 4️⃣ 部署（自动化，10分钟）
./scripts/deploy-prod.sh --build

# 5️⃣ 初始化管理员
./scripts/init-admin.sh admin 'YourPassword123!'

# 6️⃣ 配置Nginx
sudo cp nginx/api-gateway.conf.example /etc/nginx/sites-available/api-gateway
# 编辑域名和证书路径
sudo nginx -t && sudo systemctl reload nginx

# 完成！访问 https://your-domain.com
```

---

## 总结

✅ **项目已完全支持GitHub到服务器的自动化部署**

- **上传GitHub**：`git push` 即可
- **服务器部署**：运行4个脚本即可完成
- **首次部署**：约20-30分钟（包括TLS准备）
- **更新部署**：约5分钟（仅pull+重启）

**关键要点**：
1. 配置文件（.env.production）绝不提交
2. MySQL数据完全隔离，易于备份
3. 使用Let's Encrypt获取免费TLS证书
4. 所有脚本都有详细的错误检查和输出

**遇到问题**：查看"故障排查"章节或查看容器日志

