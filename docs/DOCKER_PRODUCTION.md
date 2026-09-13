# Docker 生产部署

## 首次部署

在项目根目录复制并填写生产配置：

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

`APP_SECRET_KEY`、`API_KEY_PEPPER` 和 `MYSQL_PASSWORD` 必须使用随机值。数据库密码如果包含 URL 保留字符，必须先进行 URL 编码，并同步写入 `DATABASE_URL`。

已有证书应放在宿主机配置的路径，例如：

```text
/etc/ssl/api-gateway/fullchain.pem
/etc/ssl/api-gateway/privkey.pem
```

安装并配置宿主机 Nginx：

```bash
sudo apt-get update
sudo apt-get install -y nginx
./scripts/configure-nginx.sh
```

执行部署：

```bash
./scripts/deploy-prod.sh --build
```

脚本只创建新容器、数据库表和目录，不删除数据卷，不执行清库，不迁移当前 SQLite 数据。

## 常用命令

```bash
./scripts/deploy-prod.sh --status
./scripts/deploy-prod.sh --logs backend
./scripts/deploy-prod.sh --logs mysql
./scripts/deploy-prod.sh --stop
./scripts/migrate-prod.sh
./scripts/backup-mysql.sh
```

`configure-nginx.sh` 会根据 `SERVER_NAME` 渲染配置、检查证书、运行 `nginx -t` 并 reload。证书路径必须与 `.env.production` 一致。Nginx 反代到本机回环地址的 `3000` 和 `8000`，这两个端口不应开放公网。

## 维护任务

建议使用 root 的 cron 每天执行：

```cron
15 2 * * * cd /opt/api-mvp && ./scripts/backup-mysql.sh >> /var/log/api-gateway-backup.log 2>&1
45 2 * * * cd /opt/api-mvp && docker compose --env-file .env.production -f docker-compose.prod.yml run --rm backend python -m app.maintenance cleanup >> /var/log/api-gateway-cleanup.log 2>&1
```

备份默认保留 14 天并存放在 `/Data/api-mvp/backups/mysql`。当前方案只保存在本机；服务器磁盘、主板或整机故障可能同时损坏数据库和备份，正式运行后必须增加异地或对象存储副本。

## 验收

```bash
curl -fsS http://127.0.0.1:8000/livez
curl -fsS http://127.0.0.1:8000/readyz
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

`/readyz` 必须返回数据库和迁移均为 `ok`，迁移版本必须是 `a6b7c8d9e0f1`。生产环境的 `/docs`、`/redoc` 和 `/openapi.json` 应返回 404。

当前代码、生产模板和健康检查统一使用 Alembic head `a6b7c8d9e0f1`；`docs/release-audit` 下的旧审计记录仅用于历史追溯，不作为当前部署版本依据。
