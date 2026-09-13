# 🚀 快速参考卡片 - 部署命令速查

打印此页面，在部署时随时查看。

---

## 📌 5步快速部署

```bash
# 1️⃣ 进入数据目录（所有文件都在/Data下）
cd /Data/earthquake-api-gateway

# 2️⃣ 克隆项目
git clone https://github.com/YOUR_ORG/api-gateway.git project
cd project

# 3️⃣ 生成配置（交互式）
./scripts/gen-env-production.sh

# 4️⃣ 执行部署（自动化）
./scripts/deploy-prod.sh --build

# 5️⃣ 创建管理员
./scripts/init-admin.sh admin 'YourPassword123!'

# 6️⃣ 配置Nginx
sudo cp nginx/api-gateway.conf.example /etc/nginx/sites-available/api-gateway
sudo nano /etc/nginx/sites-available/api-gateway  # 编辑域名和证书
sudo nginx -t && sudo systemctl reload nginx
```

**完成！** 访问 https://your-domain.com

**注意**: 所有数据存储在 `/Data/earthquake-api-gateway/` 下：
- 项目代码: `/Data/earthquake-api-gateway/project/`
- MySQL数据: `/Data/earthquake-api-gateway/mysql/` (Docker卷)
- 品牌资源: `/Data/earthquake-api-gateway/brand-assets/`
- 数据备份: `/Data/earthquake-api-gateway/backups/mysql/`

---

## ⚡ 常用命令

### 查看状态
```bash
# 容器状态
docker compose --env-file .env.production -f docker-compose.prod.yml ps

# 后端日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs backend -f

# 前端日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs frontend -f

# MySQL日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs mysql
```

### 管理容器
```bash
# 重启服务
docker compose --env-file .env.production -f docker-compose.prod.yml restart

# 停止服务
docker compose --env-file .env.production -f docker-compose.prod.yml stop

# 启动服务
docker compose --env-file .env.production -f docker-compose.prod.yml up -d

# 查看特定容器日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs backend --tail 100
```

### 数据库操作
```bash
# 进入MySQL容器
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql bash

# 连接数据库
mysql -u api_user -p -h localhost -D api_gateway

# 备份数据库
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
  mysqldump -u api_user -p$(grep MYSQL_PASSWORD .env.production | cut -d= -f2) \
  api_gateway > backup-$(date +%Y%m%d_%H%M%S).sql

# 恢复数据库
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
  mysql -u api_user -p < backup-YYYYMMDD_HHMMSS.sql
```

### 进入容器
```bash
# 进入后端容器
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend bash

# 进入前端容器
docker compose --env-file .env.production -f docker-compose.prod.yml exec frontend bash

# 进入MySQL容器
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql bash
```

---

## 🔍 验证部署

```bash
# 1. 容器全部就绪
docker compose --env-file .env.production -f docker-compose.prod.yml ps | grep "Up.*healthy"

# 2. 后端响应正常
curl -I http://localhost:8000/livez
# 应返回 HTTP/1.1 200

# 3. 前端响应正常
curl -I http://localhost:3000
# 应返回 HTTP/1.1 200 或 307

# 4. Nginx配置有效
sudo nginx -t

# 5. HTTPS可访问
curl -I https://api.example.com
# 应返回 HTTP/1.1 200

# 6. 登录管理后台
# 访问 https://api.example.com 使用管理员账户
```

---

## ❌ 常见问题速解

| 问题 | 快速解决 |
|------|---------|
| 容器无法启动 | `docker logs CONTAINER_ID` 查看错误 |
| 端口被占用 | 修改 .env.production 中的端口号 |
| 前端无法连接后端 | 检查 `docker network ls` |
| 数据库连接失败 | 验证 .env.production 中的数据库凭据 |
| Nginx报错 | 运行 `sudo nginx -t` 检查配置 |
| 邮件无法发送 | 检查 .env.production 中的 SMTP 配置 |
| 磁盘满 | 检查 /Data/earthquake-api-gateway 目录大小 |

---

## 📋 部署前检查清单

- [ ] Ubuntu 20.04/22.04 LTS
- [ ] Docker 20.10+ 已安装
- [ ] Docker Compose 2.0+ 已安装
- [ ] /Data/earthquake-api-gateway 目录已创建
- [ ] TLS证书已准备（Let's Encrypt或自签名）
- [ ] 域名DNS已解析
- [ ] 项目已推送到GitHub
- [ ] .env.production 在 .gitignore 中

---

## 📊 重要参数

### 端口配置
```bash
# .env.production 中的端口配置
BACKEND_PORT=8000          # 后端API端口
FRONTEND_PORT=3000         # 前端HTTP端口
MYSQL_DATA_DIR=/Data/earthquake-api-gateway/mysql              # MySQL数据目录
BRAND_ASSET_DIR_HOST=/Data/earthquake-api-gateway/brand-assets # 品牌资源目录
```

### 关键环境变量
```bash
# 必须填充（由 gen-env-production.sh 自动生成）
APP_ENV=production
APP_SECRET_KEY=<自动生成>
APP_ENCRYPTION_KEY=<自动生成>
API_KEY_PEPPER=<自动生成>
DATABASE_URL=mysql+pymysql://api_user:password@mysql:3306/api_gateway
FRONTEND_ORIGIN=https://api.example.com
PUBLIC_GATEWAY_BASE_URL=https://api.example.com
TOMODD_BASE_URL=https://tomodd.example.com
EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1
```

---

## 🔐 安全要点

```bash
# 检查密钥不是默认值
grep "replace-with-" .env.production
# 应无输出

# 检查.env.production权限
stat -c "%a" .env.production
# 应显示 600

# 检查.env.production不在Git中
git log --all --full-history -- .env.production
# 应无输出
```

---

## 📞 获取帮助

| 需求 | 相关文档 |
|------|--------|
| 了解项目 | DEPLOYMENT_SUMMARY.md |
| 部署指南 | GITHUB_DEPLOYMENT.md |
| 检查清单 | DEPLOYMENT_CHECKLIST.md |
| 深度审查 | DEPLOYMENT_REVIEW.md |
| 文档导航 | DEPLOYMENT_DOCS_GUIDE.md |
| 最终报告 | FINAL_REVIEW_REPORT.md |

---

## ⏱️ 时间估计

| 任务 | 耗时 |
|------|------|
| 阅读本卡片 | 5分钟 |
| 阅读 DEPLOYMENT_SUMMARY.md | 15分钟 |
| 阅读 GITHUB_DEPLOYMENT.md | 30分钟 |
| 服务器准备 | 20分钟 |
| 生成配置 | 5分钟 |
| 执行部署 | 10分钟 |
| 验收测试 | 15分钟 |
| **总计（首次）** | **100分钟** |
| **更新部署** | **5分钟** |

---

## 🎯 部署检查点

### 部署前 (Pre-Deployment)
```
□ 代码已提交到GitHub
□ .env.production 已生成
□ TLS证书已准备
□ 域名已解析
```

### 部署中 (During Deployment)
```
□ gen-env-production.sh 执行成功
□ deploy-prod.sh 无错误
□ 所有容器已启动
□ 健康检查通过
```

### 部署后 (Post-Deployment)
```
□ 前端可访问 (https://api.example.com)
□ 后端响应正常 (curl http://localhost:8000/livez)
□ 管理员可登录
□ 数据库连接正常
□ Nginx配置生效
```

---

## 🔄 日常操作

### 查看最新日志
```bash
docker compose --env-file .env.production -f docker-compose.prod.yml logs backend -f --tail 50
```

### 重启单个服务
```bash
docker compose --env-file .env.production -f docker-compose.prod.yml restart backend
```

### 更新代码
```bash
git pull origin main
./scripts/deploy-prod.sh --build
```

### 备份数据库
```bash
./scripts/backup-mysql.sh
# 或手动
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
  mysqldump -u api_user -p$(grep MYSQL_PASSWORD .env.production | cut -d= -f2) api_gateway > backup.sql
```

---

## 🚨 紧急恢复

### 容器崩溃
```bash
# 查看错误
docker logs CONTAINER_NAME

# 重启
docker compose --env-file .env.production -f docker-compose.prod.yml restart CONTAINER_NAME
```

### 数据库故障
```bash
# 检查数据库状态
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql mysql -u root -p -e "SELECT 1;"

# 重启MySQL
docker compose --env-file .env.production -f docker-compose.prod.yml restart mysql
```

### 磁盘满
```bash
# 检查磁盘
df -h /Data/earthquake-api-gateway

# 清理Docker日志
docker container prune
docker image prune

# 清理旧备份
rm -f backup-*.sql  # 保留最近的备份
```

---

## 📈 性能监控

```bash
# 查看容器资源使用
docker stats

# 查看磁盘使用
du -sh /Data/earthquake-api-gateway/*

# 查看MySQL数据大小
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
  mysql -u root -p -e "SELECT table_schema AS 'Database', ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS 'Size in MB' FROM information_schema.tables GROUP BY table_schema;"
```

---

## 🛠️ 调试技巧

### 进入容器调试
```bash
# 进入后端容器
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend bash

# 查看Python版本和依赖
python --version
pip list

# 运行Python命令
python -c "import app; print(app.__file__)"
```

### 查看网络连接
```bash
# 列出所有网络
docker network ls

# 查看容器网络
docker network inspect api-gateway_internal

# 测试容器间连通性
docker compose --env-file .env.production -f docker-compose.prod.yml exec frontend ping backend
```

### 查看卷挂载
```bash
# 进入MySQL容器查看数据目录
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql ls -la /var/lib/mysql

# 查看品牌资源目录
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend ls -la /app/data/brand-assets
```

---

## 📞 技术支持速查

**出现问题时的第一步**：
```bash
# 1. 查看完整日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs

# 2. 查看容器状态
docker compose --env-file .env.production -f docker-compose.prod.yml ps

# 3. 查看错误日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs backend 2>&1 | grep ERROR

# 4. 查看配置
cat .env.production | grep -v "^#" | grep -v "^$"
```

---

## ✨ 部署成功标志

✅ 所有5个容器运行中（mysql, backend, frontend, email-worker, file-worker）  
✅ 前端可访问 (HTTPS 200 OK)  
✅ 后端API可访问 (/livez 返回200)  
✅ 管理员可登录  
✅ 数据库连接正常  

---

## 🎉 祝部署顺利！

按照本卡片执行操作，遇到问题查看对应文档。

**有问题？** 参考 GITHUB_DEPLOYMENT.md 的"故障排查"章节

**需要帮助？** 查看 DEPLOYMENT_DOCS_GUIDE.md 找到合适的文档

---

**打印此页面，随时参考！** 📄

最后修订：2026-09-13  
部署状态：✅ 就绪

