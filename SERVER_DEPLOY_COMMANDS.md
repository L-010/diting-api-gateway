# 地震局 API 网关唯一部署指南

本文件是当前仓库唯一需要参考的部署入口。当前版本按“无 TLS 的 HTTP 直连”部署：前端使用 `3000` 端口，后端使用 `8000` 端口，不需要证书或 Nginx。该模式只适合内网或临时验收，正式公网使用前必须配置 HTTPS，并把 `SECURE_COOKIES` 改为 `true`。

以下命令在 Ubuntu 服务器上执行。示例服务器地址为 `10.2.210.10`；如果实际地址不同，只需在生成配置时输入实际地址。

## 1. 准备服务器

服务器需要安装 Git、Docker Engine、Docker Compose v2、OpenSSL 和 curl。当前用户需要能执行 `docker`，并拥有 `sudo` 权限。

```bash
docker --version
docker compose version
git --version
openssl version
curl --version
```

如果当前用户还不能执行 Docker，请将用户加入 Docker 用户组后重新登录：

```bash
sudo usermod -aG docker "$USER"
```

## 2. 拉取代码

代码统一放在 `/Data/earthquake-api-gateway/project`，数据库、品牌资源和备份放在其父目录。下面的命令可以重复执行；已有项目目录时只会快进更新代码。

```bash
sudo mkdir -p /Data/earthquake-api-gateway/mysql
sudo mkdir -p /Data/earthquake-api-gateway/brand-assets
sudo mkdir -p /Data/earthquake-api-gateway/backups/mysql
sudo chown -R "$USER":"$USER" /Data/earthquake-api-gateway

cd /Data/earthquake-api-gateway
if [ -d project/.git ]; then
  cd project
  git fetch origin main
  git checkout main
  git pull --ff-only origin main
else
  git clone https://github.com/L-010/diting-api-gateway.git project
  cd project
fi
```

确认当前代码包含生产 Compose 文件：

```bash
test -f docker-compose.prod.yml
test -f scripts/deploy-prod.sh
test -f .env.production.example
```

## 3. 生成生产配置

仓库不包含 `.env.production`，不会把任何生产密钥提交到 Git。使用仓库自带生成器创建配置：

```bash
cd /Data/earthquake-api-gateway/project
bash scripts/gen-env-production.sh
```

按提示填写：

- 前端访问地址：`http://10.2.210.10:3000`，或浏览器实际访问的 HTTP Origin。
- 公开网关地址：`http://10.2.210.10:8000`。
- TomoDD 上游地址：填写 backend 容器可以访问的真实地址。若 TomoDD 在本机 `18000` 端口，使用 `http://host.docker.internal:18000`；若在其他服务器，填写其 IP/域名和端口。
- 允许的上游主机：必须包含上一步地址的主机名或 IP，例如 `host.docker.internal,127.0.0.1,localhost`。
- TomoDD Token：上游不需要认证时留空；需要认证时填写真实 Token。
- 两个数据库密码：直接回车自动生成。生成器只使用字母和数字，已避免数据库 URL 特殊字符问题；两个密码会自动保持不同。

生成器会自动完成以下关键设置：

- `APP_ENV=production`
- `EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1`
- `SECURE_COOKIES=false`（当前 HTTP 模式必须为 `false`）
- `DATABASE_URL` 使用正确的 MySQL URL 格式和 `charset=utf8mb4`
- 数据目录为 `/Data/earthquake-api-gateway/...`

检查权限和关键值（不要输出完整密钥）：

```bash
stat -c '%a' .env.production
grep -E '^(APP_ENV|MYSQL_DATABASE|MYSQL_USER|DATABASE_URL|FRONTEND_ORIGIN|PUBLIC_GATEWAY_BASE_URL|SECURE_COOKIES|EXPECTED_ALEMBIC_HEAD|BACKEND_PORT|FRONTEND_PORT)=' .env.production
```

第一条命令应输出 `600`。如果手动修改数据库密码，`DATABASE_URL` 中必须对密码进行 URL 编码；推荐重新运行生成器或继续使用自动生成的字母数字密码。

## 4. 配置并启动

先验证 Compose 文件，再执行构建、迁移和启动。必须使用下面的完整命令，不要省略生产配置文件参数：

```bash
cd /Data/earthquake-api-gateway/project
bash scripts/pre-deploy-check.sh
docker compose --env-file .env.production -f docker-compose.prod.yml config --quiet
bash scripts/deploy-prod.sh --build
```

部署脚本会创建目录、构建镜像、启动 MySQL、执行 Alembic 迁移，然后启动 backend、frontend、email-worker 和 file-worker。迁移 head 必须是 `a6b7c8d9e0f1`；如果看到其他版本，先确认已拉取 `origin/main` 的最新代码，不要把环境变量改成旧版本。

## 5. 验证服务

```bash
cd /Data/earthquake-api-gateway/project
docker compose --env-file .env.production -f docker-compose.prod.yml ps
curl -fsS http://127.0.0.1:8000/livez
curl -fsS http://127.0.0.1:8000/readyz
curl -fsSI http://127.0.0.1:3000/
```

预期结果：`livez` 返回 HTTP 200；`readyz` 返回 HTTP 200 且 `database`、`migration` 都是 `ok`；前端返回 HTTP 200 或合法重定向；五个 Compose 服务均为运行状态。

从浏览器访问 `http://10.2.210.10:3000`。如果服务器启用了 UFW，只允许可信内网访问这两个临时 HTTP 端口，不要对公网开放。生产环境故意关闭 `/docs`、`/redoc` 和 `/openapi.json`，返回 404 属于正常行为。

## 6. 创建管理员

部署成功后执行，脚本会交互式询问用户名和密码，不会询问邮箱：

```bash
cd /Data/earthquake-api-gateway/project
bash scripts/init-admin.sh
```

密码至少 12 位，并同时包含大写字母、小写字母和数字，且不能包含用户名。SMTP 未配置时管理员仍可登录；公开注册、验证邮件和自助找回会保持关闭。

## 7. 运维命令

所有 Compose 操作都显式指定生产环境文件：

```bash
cd /Data/earthquake-api-gateway/project
docker compose --env-file .env.production -f docker-compose.prod.yml ps
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=200 backend
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=200 frontend
docker compose --env-file .env.production -f docker-compose.prod.yml restart
docker compose --env-file .env.production -f docker-compose.prod.yml stop
bash scripts/migrate-prod.sh
bash scripts/backup-mysql.sh
```

日志输出到 Docker 标准输出，使用 `docker compose logs` 查看；项目没有 `/app/logs` 日志目录。不要使用 `docker compose down -v` 排障，它会删除 Compose 资源并可能造成不可恢复的数据风险。

## 8. 常见故障定位

### `EXPECTED_ALEMBIC_HEAD` 错误

```bash
git pull --ff-only origin main
grep '^EXPECTED_ALEMBIC_HEAD=' .env.production
grep -n 'EXPECTED_ALEMBIC_HEAD' scripts/deploy-prod.sh
```

三处都应使用 `a6b7c8d9e0f1`。

### MySQL 连接失败

确认 `MYSQL_PASSWORD` 和 `MYSQL_ROOT_PASSWORD` 不同，并确认 `DATABASE_URL` 中的密码与 `MYSQL_PASSWORD` 完全一致。密码含 `@`、`#`、`/` 等字符时必须 URL 编码；最简单的处理方式是重新运行生成器并让它自动生成字母数字密码。

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=200 mysql
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

### 页面可以打开但不能登录

当前无 TLS 模式必须保持：

```text
SECURE_COOKIES=false
FRONTEND_ORIGIN=http://实际访问地址:3000
```

浏览器地址、`FRONTEND_ORIGIN` 和端口必须完全一致。修改后执行：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --force-recreate backend frontend
```

### 上游调用失败

确认 TomoDD 服务确实在运行，并且 backend 容器能访问它。`127.0.0.1` 在容器内只代表 backend 容器自身；本机上游应使用 `host.docker.internal`，其他服务器应使用可路由 IP/域名，同时把该主机加入 `ALLOWED_UPSTREAM_HOSTS`。

## 9. 以后启用 HTTPS

获取证书并配置 Nginx 后，在 `.env.production` 中将前端和网关地址改为同一个 `https://` 域名，并设置：

```text
SECURE_COOKIES=true
```

然后重新创建 backend 和 frontend 容器。启用 HTTPS 后，不再需要对公网开放 3000 和 8000，应只开放 80/443 并由 Nginx 反向代理。
