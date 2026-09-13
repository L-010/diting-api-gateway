# 快速部署参考卡

## 部署流程概览

```
Windows准备  →  上传到Ubuntu  →  配置环境  →  执行部署  →  验收  →  上线
```

## 在Windows上的准备（15分钟）

```bash
# 1. 验证项目
bash scripts/pre-deploy-check.sh

# 2. 生成配置
bash scripts/gen-env-production.sh

# 3. 打包上传
tar czf api-mvp.tar.gz .
# scp上传到Ubuntu
```

## 在Ubuntu上的部署（30-45分钟）

```bash
# 1. 系统准备
sudo apt update && sudo apt upgrade -y
curl -fsSL https://get.docker.com | bash
sudo usermod -aG docker ubuntu
docker --version  # 验证

# 2. 目录准备
sudo mkdir -p /Data/api-mvp/{mysql,backups,brand-assets}
sudo chown 999:999 /Data/api-mvp/mysql
sudo chown 10001:10001 /Data/api-mvp/brand-assets
sudo mkdir -p /etc/ssl/api-gateway

# 3. 上传证书
scp /path/to/cert.pem ubuntu@server:/tmp/
scp /path/to/key.pem ubuntu@server:/tmp/
sudo mv /tmp/cert.pem /etc/ssl/api-gateway/fullchain.pem
sudo mv /tmp/key.pem /etc/ssl/api-gateway/privkey.pem

# 4. 解压项目
cd /opt
tar xzf api-mvp.tar.gz -C api-mvp

# 5. 检查和部署
cd /opt/api-mvp
bash scripts/pre-deploy-check.sh
bash scripts/deploy-prod.sh --build

# 6. 验证
bash scripts/post-deploy-verify.sh

# 7. 初始化管理员
bash scripts/init-admin.sh

# 8. 配置Nginx（可选）
sudo bash scripts/configure-nginx.sh
```

## 关键命令速查

### 查看状态

```bash
# 所有容器
docker compose --env-file .env.production -f docker-compose.prod.yml ps

# 实时日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f

# 后端日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs backend

# 资源占用
docker stats
```

### 常用操作

```bash
# 停止服务
docker compose --env-file .env.production -f docker-compose.prod.yml stop

# 启动服务
docker compose --env-file .env.production -f docker-compose.prod.yml up -d

# 重启特定容器
docker compose --env-file .env.production -f docker-compose.prod.yml restart backend

# 进入容器
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend bash

# 查看数据库
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql mysql -u root -p api_gateway
```

### 故障排查

```bash
# 检查后端健康
curl http://127.0.0.1:8000/readyz

# 检查MySQL连接
docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
  mysql -u api_user -p mysql_password -e "SELECT 1"

# 查看详细日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs backend --tail 200
```

## 配置检查清单

- [ ] APP_ENV = production
- [ ] EMAIL_TEST_MODE = false
- [ ] DATABASE_URL = mysql+pymysql://...
- [ ] MYSQL_PASSWORD已设置
- [ ] FRONTEND_ORIGIN = https://domain.com
- [ ] PUBLIC_GATEWAY_BASE_URL = https://domain.com
- [ ] SERVER_NAME = domain.com
- [ ] TLS_CERT_FILE和TLS_KEY_FILE路径正确
- [ ] TOMODD_BASE_URL和TOKEN已配置
- [ ] 没有占位符值（replace-with-, changeme等）
- [ ] EXPECTED_ALEMBIC_HEAD = a6b7c8d9e0f1

## 部署后必做

1. **验证部署**
   ```bash
   bash scripts/post-deploy-verify.sh
   ```

2. **创建管理员**
   ```bash
   bash scripts/init-admin.sh
   ```

3. **测试访问**
   ```bash
   curl https://api.example.com/health
   ```

4. **设置备份**
   ```bash
   sudo crontab -e
   # 添加备份任务
   15 2 * * * cd /opt/api-mvp && ./scripts/backup-mysql.sh
   ```

## 常见问题快速解决

| 问题 | 解决方案 |
|-----|--------|
| 后端无法连接MySQL | 检查DATABASE_URL、MYSQL_PASSWORD，查看MySQL日志 |
| 迁移失败 | 检查EXPECTED_ALEMBIC_HEAD，验证用户名冲突 |
| 前端无法访问 | 检查BACKEND_ORIGIN，验证后端容器状态 |
| TLS证书错误 | 检查证书路径、格式、权限 |
| 磁盘满 | 清理old backups: `find /Data/api-mvp/backups -mtime +14 -delete` |

## 文件位置速查

| 文件 | 位置 |
|-----|-----|
| 配置文件 | `/opt/api-mvp/.env.production` |
| MySQL数据 | `/Data/api-mvp/mysql` |
| 备份文件 | `/Data/api-mvp/backups/mysql` |
| 品牌资源 | `/opt/api-mvp/backend/data/brand-assets` |
| TLS证书 | `/etc/ssl/api-gateway/` |
| Nginx配置 | `/etc/nginx/sites-available/api-gateway` |
| 日志文件 | `docker compose logs` |

## 性能指标基线

首次部署后建议检查：

```bash
# 数据库连接时间
time curl http://127.0.0.1:8000/readyz

# 容器内存占用
docker stats

# 磁盘I/O
iostat -x 1 5

# 网络连接
netstat -an | grep ESTABLISHED | wc -l
```

## 紧急恢复步骤

如果部署出问题：

```bash
# 1. 查看错误日志
docker compose --env-file .env.production -f docker-compose.prod.yml logs | head -100

# 2. 检查磁盘空间
df -h /Data

# 3. 重启所有服务
docker compose --env-file .env.production -f docker-compose.prod.yml down
docker compose --env-file .env.production -f docker-compose.prod.yml up -d

# 4. 从备份恢复（如果数据损坏）
gunzip < /Data/api-mvp/backups/mysql/latest.sql.gz | \
  docker compose --env-file .env.production -f docker-compose.prod.yml exec -T mysql \
  mysql -u api_user -p password api_gateway

# 5. 重新初始化
docker compose --env-file .env.production -f docker-compose.prod.yml down -v
# 重新执行部署脚本
./scripts/deploy-prod.sh --build
```

## 定期维护任务

### 每周
- [ ] 检查磁盘使用情况
- [ ] 查看错误日志
- [ ] 验证备份完整性

### 每月
- [ ] 更新依赖包
- [ ] 检查安全补丁
- [ ] 测试恢复流程

### 每季度
- [ ] 审查和优化性能
- [ ] 检查证书过期时间
- [ ] 更新文档
