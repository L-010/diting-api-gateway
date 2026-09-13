# 部署前检查清单

## 📋 本地开发环境检查

### 代码质量
- [ ] 所有Python代码已通过linting
- [ ] 所有TypeScript代码无类型错误
- [ ] 没有硬编码的敏感信息（密钥、Token）
- [ ] 所有依赖已在requirements.txt和package.json中声明
- [ ] `.gitignore` 包含 `.env` 和 `.env.*` 的排除规则
- [ ] `.env.production` 不在版本控制中

### Docker配置
- [ ] 后端Dockerfile语法正确
  ```bash
  docker build -f backend/Dockerfile -t test:latest .
  ```
- [ ] 前端Dockerfile语法正确
  ```bash
  cd frontend && docker build -f Dockerfile -t test:latest .
  ```
- [ ] docker-compose.prod.yml 有效
  ```bash
  docker compose -f docker-compose.prod.yml config > /dev/null
  ```
- [ ] ✅ 后端端口已改为 `0.0.0.0:8000`
- [ ] ✅ 前端端口已改为 `0.0.0.0:3000`
- [ ] ✅ email-worker 和 file-worker 已添加healthcheck

### 数据库迁移
- [ ] 所有Alembic迁移文件位置正确
  ```bash
  ls backend/alembic/versions/ | wc -l  # 应该显示 24 个迁移文件
  ```
- [ ] `alembic.ini` 配置正确
- [ ] `scripts/migrate.py` 可以正常执行
- [ ] EXPECTED_ALEMBIC_HEAD = a6b7c8d9e0f1

### 脚本可执行性
- [ ] `scripts/gen-env-production.sh` 可执行
  ```bash
  bash scripts/gen-env-production.sh --help 2>/dev/null || echo "脚本可运行"
  ```
- [ ] `scripts/migrate-prod.sh` 可执行
- [ ] `scripts/init-admin.sh` 可执行
- [ ] `scripts/deploy-prod.sh` 可执行
- [ ] 脚本中的路径引用正确

---

## 📤 GitHub准备

### 仓库创建
- [ ] 已在GitHub创建新仓库
- [ ] 仓库名称确定（如：api-gateway）
- [ ] 仓库描述完整
- [ ] 选择合适的可见性（Private/Public）

### 代码推送
- [ ] 本地已初始化Git
  ```bash
  git status  # 应显示正常状态
  ```
- [ ] 已添加所有源代码
  ```bash
  git add .
  git status  # 确认没有遗漏文件
  ```
- [ ] 已创建首次提交
  ```bash
  git log --oneline | head -1  # 显示最新提交
  ```
- [ ] 已添加GitHub远程
  ```bash
  git remote -v  # 显示origin URL
  ```
- [ ] 已推送到main分支
  ```bash
  git branch -M main
  git push -u origin main
  ```

### 验证推送内容
- [ ] 访问GitHub查看代码已上传
- [ ] `.env.production` 不在仓库中（✅ .gitignore生效）
- [ ] `backend/app/` 目录完整
- [ ] `frontend/` 目录完整
- [ ] `docker-compose.prod.yml` 已更新
- [ ] `scripts/` 目录包含所有脚本
- [ ] `nginx/api-gateway.conf.example` 存在

---

## 🖥️ Ubuntu服务器准备

### 系统要求
- [ ] Ubuntu 20.04 LTS 或 22.04 LTS
- [ ] 至少4GB内存
- [ ] 至少20GB磁盘空间（用于Docker镜像和MySQL数据）
- [ ] 网络连接正常
  ```bash
  ping github.com  # 测试连接
  ```

### Docker安装
- [ ] Docker已安装
  ```bash
  docker --version  # 应显示 20.10+
  ```
- [ ] Docker Compose已安装
  ```bash
  docker compose version  # 应显示 2.0+
  ```
- [ ] Docker daemon正在运行
  ```bash
  sudo systemctl status docker
  ```
- [ ] 当前用户有Docker权限（可选）
  ```bash
  docker ps  # 不需要sudo即可运行
  ```

### 系统依赖
- [ ] Git已安装
  ```bash
  git --version
  ```
- [ ] curl已安装（部署脚本需要）
  ```bash
  curl --version
  ```
- [ ] openssl已安装
  ```bash
  openssl version
  ```
- [ ] Python3已安装
  ```bash
  python3 --version
  ```

### 目录准备
- [ ] 已创建部署目录
  ```bash
  sudo mkdir -p /opt/api-gateway
  sudo chown $USER:$USER /opt/api-gateway
  ```
- [ ] 已创建数据目录
  ```bash
  sudo mkdir -p /Data/api-mvp/{mysql,brand-assets,backups/mysql}
  sudo chmod 777 /Data/api-mvp
  ```

### TLS证书准备
- [ ] 已获取TLS证书（选择以下之一）
  - [ ] Let's Encrypt通过Certbot
    ```bash
    sudo certbot certonly --standalone -d your-domain.com
    ```
  - [ ] 自签名证书（测试用）
    ```bash
    sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
      -keyout /etc/ssl/api-gateway/privkey.pem \
      -out /etc/ssl/api-gateway/fullchain.pem
    ```
- [ ] 证书文件可读
  ```bash
  sudo ls -la /etc/ssl/api-gateway/ # 或Let's Encrypt路径
  ```
- [ ] 证书路径已记录（用于gen-env-production.sh）

### Nginx准备
- [ ] Nginx已安装
  ```bash
  nginx -v
  ```
- [ ] Nginx支持HTTPS
  ```bash
  nginx -t  # 应显示配置有效
  ```

---

## ⚙️ 部署配置准备

### GitHub克隆
- [ ] 已在服务器克隆项目
  ```bash
  cd /opt
  git clone https://github.com/YOUR_USERNAME/api-gateway.git
  cd api-gateway
  ```
- [ ] 所有文件已下载
  ```bash
  ls -la | grep -E "docker-compose|scripts|backend|frontend"
  ```

### 环境配置生成
- [ ] 已运行 `gen-env-production.sh`
  ```bash
  ./scripts/gen-env-production.sh
  ```
- [ ] 已回答所有配置问题
  - [ ] 数据库名称
  - [ ] 数据库用户
  - [ ] 数据库密码
  - [ ] MySQL root密码
  - [ ] 服务器域名
  - [ ] TLS证书路径
  - [ ] TLS私钥路径
  - [ ] TOMODD上游地址
  - [ ] TOMODD上游Token

### 配置验证
- [ ] `.env.production` 已生成
  ```bash
  ls -la .env.production  # 权限应为 600
  ```
- [ ] 配置不包含占位值
  ```bash
  grep "replace-with-" .env.production  # 应无输出
  ```
- [ ] 关键配置已正确设置
  ```bash
  grep "^APP_ENV=production" .env.production
  grep "^DATABASE_URL=mysql" .env.production
  grep "^EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1" .env.production
  ```

---

## 🚀 部署执行

### 部署前最后检查
- [ ] `.env.production` 文件安全
  ```bash
  stat .env.production | grep Access
  ```
- [ ] Docker Compose配置有效
  ```bash
  docker compose --env-file .env.production -f docker-compose.prod.yml config > /dev/null
  ```
- [ ] 所有脚本可执行
  ```bash
  ls -la scripts/*.sh | grep rwx
  ```

### 执行部署脚本
- [ ] 运行部署（可能需要10-15分钟）
  ```bash
  ./scripts/deploy-prod.sh --build
  ```
- [ ] 部署成功（脚本无错误输出）
- [ ] 所有容器已启动
  ```bash
  docker compose --env-file .env.production -f docker-compose.prod.yml ps
  ```

### 容器健康检查
- [ ] MySQL容器健康
  ```bash
  docker compose --env-file .env.production -f docker-compose.prod.yml ps | grep mysql.*healthy
  ```
- [ ] 后端容器健康
  ```bash
  docker compose --env-file .env.production -f docker-compose.prod.yml ps | grep backend.*healthy
  ```
- [ ] 前端容器健康
  ```bash
  docker compose --env-file .env.production -f docker-compose.prod.yml ps | grep frontend.*healthy
  ```

### 数据库迁移验证
- [ ] 迁移已执行
  ```bash
  docker compose --env-file .env.production -f docker-compose.prod.yml exec mysql \
    mysql -u api_user -p$(grep MYSQL_PASSWORD .env.production | cut -d= -f2) api_gateway \
    -e "SHOW TABLES;" | wc -l  # 应显示 > 5
  ```

### 创建管理员
- [ ] 初始化脚本已运行
  ```bash
  ./scripts/init-admin.sh admin 'YourPassword123!'
  ```
- [ ] 返回成功消息

---

## 🌐 应用访问验证

### 本地测试（服务器上）
- [ ] 后端健康检查
  ```bash
  curl -I http://localhost:8000/livez
  # 应返回 HTTP/1.1 200
  ```
- [ ] 前端可访问
  ```bash
  curl -I http://localhost:3000
  # 应返回 HTTP/1.1 200 或 307 (重定向)
  ```

### Nginx配置
- [ ] Nginx配置已复制
  ```bash
  sudo cp nginx/api-gateway.conf.example /etc/nginx/sites-available/api-gateway
  ```
- [ ] 配置已编辑
  ```bash
  grep "server_name" /etc/nginx/sites-available/api-gateway | grep -v "^#"
  grep "ssl_certificate" /etc/nginx/sites-available/api-gateway | grep -v "^#"
  ```
- [ ] Nginx配置有效
  ```bash
  sudo nginx -t
  # 应显示 "syntax is ok"
  ```
- [ ] Nginx已重载
  ```bash
  sudo systemctl reload nginx
  # 或 sudo nginx -s reload
  ```

### 远程访问测试
- [ ] DNS已解析（等待传播，最多24小时）
  ```bash
  nslookup api.example.com
  ```
- [ ] HTTP重定向工作
  ```bash
  curl -I http://api.example.com
  # 应返回 301 重定向到 https://
  ```
- [ ] HTTPS可访问
  ```bash
  curl -I https://api.example.com
  # 应返回 HTTP/1.1 200
  ```
- [ ] 管理后台可登录
  - 访问 https://api.example.com
  - 使用管理员账户登录
  - 应进入管理后台

---

## 📊 部署后验证

### 功能检查
- [ ] 前端页面正常加载
- [ ] 管理员可以登录
- [ ] 可以创建API Key
- [ ] 可以查看调用记录
- [ ] 可以配置上游服务

### 性能检查
- [ ] 页面加载速度可接受（< 3秒）
- [ ] API响应速度正常（< 500ms）
- [ ] 容器CPU使用率正常（< 80%）
- [ ] 容器内存使用率正常（< 80%）

### 日志检查
- [ ] 后端日志无ERROR
  ```bash
  docker compose --env-file .env.production -f docker-compose.prod.yml logs backend | grep ERROR
  ```
- [ ] 前端日志无异常
  ```bash
  docker compose --env-file .env.production -f docker-compose.prod.yml logs frontend | tail -20
  ```
- [ ] MySQL日志无异常
  ```bash
  docker compose --env-file .env.production -f docker-compose.prod.yml logs mysql | tail -20
  ```

### 监控设置
- [ ] 已设置容器日志轮转（docker-compose.prod.yml已配置）
- [ ] 考虑设置监控告警
- [ ] 考虑设置定期备份

---

## 🔐 安全检查

### 凭据安全
- [ ] `.env.production` 权限为600
  ```bash
  stat -c "%a" .env.production  # 应显示 600
  ```
- [ ] `.env.production` 未被提交到Git
  ```bash
  git log --all --full-history -- .env.production  # 应无输出
  ```
- [ ] 密钥已正确生成（非默认值）
  ```bash
  grep "replace-with-" .env.production  # 应无输出
  ```

### 网络安全
- [ ] MySQL端口未暴露到公网
  ```bash
  # 检查docker-compose.prod.yml，mysql服务无ports配置
  ```
- [ ] 仅80/443端口对外开放
  ```bash
  sudo ufw status | grep -E "80|443"
  ```
- [ ] 防火墙已启用
  ```bash
  sudo ufw status
  ```

### SSL/TLS
- [ ] 证书有效期充足
  ```bash
  sudo openssl x509 -in /path/to/cert.pem -noout -dates
  ```
- [ ] 自动续期已配置（Let's Encrypt）
  ```bash
  sudo certbot renew --dry-run
  ```

---

## 📝 文档检查

- [ ] README.md已更新（如需要）
- [ ] DEPLOYMENT_REVIEW.md已阅读
- [ ] GITHUB_DEPLOYMENT.md已备份
- [ ] 部署步骤已记录
- [ ] 管理员密码已安全保管
- [ ] 数据库密码已安全保管

---

## ✅ 最终验收

**部署负责人签名**：_________________  
**部署日期**：_________________  
**验收时间**：_________________

### 确认事项
- [ ] 所有检查项均已完成
- [ ] 所有测试均已通过
- [ ] 应用已可用于生产环境
- [ ] 备份和恢复流程已测试
- [ ] 团队成员已培训

---

## 故障恢复计划

### 如需回滚
```bash
# 1. 停止当前部署
docker compose --env-file .env.production -f docker-compose.prod.yml stop

# 2. 恢复备份（如有）
mysql -u root -p < backup-YYYYMMDD_HHMMSS.sql

# 3. 重启
docker compose --env-file .env.production -f docker-compose.prod.yml up -d
```

### 获取帮助
- 查看 `DEPLOYMENT_REVIEW.md` 的故障排查章节
- 查看容器日志
- 查看Nginx错误日志：`/var/log/nginx/error.log`
- 查看系统日志：`sudo journalctl -xe`

