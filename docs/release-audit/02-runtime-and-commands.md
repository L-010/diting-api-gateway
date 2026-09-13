# 运行环境与命令

## 已确认环境

- 操作系统：Windows，命令环境为 PowerShell；证据来自 `LOCAL_RUNBOOK.md` 和本次 PowerShell 执行环境。
- Python：3.13.9；pip 26.1.2；路径 `C:\Users\LiuTao\Desktop\API-mvp\.venv\Scripts\python.exe`。
- Node.js：v24.13.0；npm：11.6.2。Node 不是项目虚拟环境，项目依赖目录为 `frontend/node_modules`。
- Python 包版本：FastAPI 0.139.2、SQLAlchemy 2.0.51、Pydantic 2.13.4、Alembic 1.18.5、httpx 0.28.1、pytest 8.4.2、cryptography 45.0.7。
- 数据库：配置 scheme 为 SQLite；当前路径解析为 `data/api_gateway.db`。

## 命令矩阵

| 用途 | 命令 | 状态/副作用说明 |
|---|---|---|
| 初始化 | `powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1` | 会创建/安装依赖、生成 `.env`、迁移数据库；本阶段未执行 |
| 后端开发启动 | `powershell -ExecutionPolicy Bypass -File scripts/run-backend.ps1` | `uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload`；本阶段未启动 |
| 前端开发启动 | `powershell -ExecutionPolicy Bypass -File scripts/run-frontend.ps1` | `npm run dev`，默认 3000；本阶段未启动 |
| 前端生产启动 | `npm run start` | 依赖已有构建产物；未执行 |
| 前端构建 | `cd frontend; npm run build` | 可能生成 `.next`；本阶段未执行 |
| 前端 lint | `cd frontend; npm run lint` | package script 存在；未执行，Next 版本兼容性待验证 |
| 后端测试 | `.venv\Scripts\python.exe -m pytest backend\tests -p no:cacheprovider -q` | 已执行，90 passed，1 warning；测试使用临时 SQLite/Mock |
| 迁移 | `.venv\Scripts\python.exe -m alembic upgrade head` 或 `scripts/migrate.py` | 修改数据库；本阶段未执行 |
| 当前迁移 | `.venv\Scripts\python.exe -m alembic current` | 已执行，输出 `f9a0b1c2d3e4 (head)` |
| 管理员 seed | `scripts/seed-admin.ps1` | 写入管理员账号；本阶段未执行 |
| 邮件 worker | `scripts/run-email-worker.ps1` | 依赖 SMTP，持续轮询；本阶段未启动 |
| 文件 worker | `scripts/run-file-worker.ps1` | 持续同步/清理远程文件；本阶段未启动 |
| 数据清理 | `scripts/cleanup-retained-data.ps1` | 删除超期调用/令牌/限流桶；本阶段未执行 |
| tomoDD 只读预检 | `scripts/verify-tomodd.ps1` | 文档称只读；本阶段未执行外部网络调用 |

## 端口与外部依赖

- 前端：`127.0.0.1:3000`；后端：`127.0.0.1:8000`；tomoDD-SP 默认 `127.0.0.1:18000`。
- 运行依赖：SQLite 文件、可选 SMTP、tomoDD-SP 或管理员配置的上游工具服务；配置了任务/制品时还依赖文件 worker。
- 未发现代码级 Redis、消息队列、支付、短信或对象存储 SDK；文件正文按契约保存在工具服务器，由平台代理。

## 环境变量名称与状态

仅记录名称和是否存在，不记录值。`.env` 中：`APP_ENV`、`APP_SECRET_KEY`、`APP_ENCRYPTION_KEY`、`API_KEY_PEPPER`、`DATABASE_URL`、`ALLOWED_UPSTREAM_HOSTS`、`TOMODD_BASE_URL`、`MAX_UPLOAD_BYTES`、`SESSION_COOKIE_NAME`、`FRONTEND_ORIGIN` 有值；`TOMODD_UPSTREAM_TOKEN` 为空。`.env.example` 还声明 `MAX_DOWNLOAD_BYTES`、`PUBLIC_GATEWAY_BASE_URL`、`CALL_LOG_RETENTION_DAYS`、SMTP、远程文件限制/并发/租约/manifest 等变量，其中若干为空模板值。配置字段完整来源为 `backend/app/config.py:17-62`。

## 生产启动不确定性

- 代码库提供的是开发启动脚本，包含 `--reload`；未发现 Dockerfile、Compose、Makefile、Taskfile 或 `.github/workflows`。生产进程托管、反向代理、TLS、横向扩展和健康检查策略待确认。
- `docs/ARCHITECTURE.md` 与实际迁移头不一致，部署前必须以迁移链和目标数据库状态为准。
