# 上线前审计基线摘要

## 审计范围与时间

- 项目绝对路径：`C:\Users\LiuTao\Desktop\API-mvp`。
- 检查日期：2026-08-26，Windows PowerShell，时区 Asia/Shanghai。
- 本阶段仅读取文件、读取 Git/数据库元数据、读取运行时元数据并执行后端测试；未修改业务代码、配置、数据库结构、依赖、测试或部署文件。
- 证据状态：仓库当前无 Git 提交，文件均为未跟踪状态；下文证据以当前工作区文件为准。

## 技术栈

- 后端：Python 3.13.9、FastAPI 0.139.2、Uvicorn、SQLAlchemy 2.0.51、Alembic 1.18.5、Pydantic 2、PyJWT、pwdlib Argon2、Cryptography/Fernet、httpx、jsonpath-ng。
- 前端：Next.js 16.2.11、React 19.2.8、TypeScript 5.9.3、lucide-react；本地依赖位于 `frontend/node_modules`。
- 数据库：SQLite 本地文件 `data/api_gateway.db`，Alembic 版本化迁移。
- 运行形态：前后端分离；Next.js rewrite 将 `/api`、`/gateway`、`/health` 转发到本机后端。

## 启动与测试

- 后端：`powershell -ExecutionPolicy Bypass -File scripts/run-backend.ps1`，绑定 `127.0.0.1:8000`，脚本使用 Uvicorn `--reload`。
- 前端：`powershell -ExecutionPolicy Bypass -File scripts/run-frontend.ps1`，Next 开发服务绑定 `127.0.0.1:3000`。
- 可选进程：`scripts/run-email-worker.ps1`、`scripts/run-file-worker.ps1`；维护清理脚本为 `scripts/cleanup-retained-data.ps1`。
- 后端测试：`.venv\Scripts\python.exe -m pytest backend\tests -p no:cacheprovider -q`，结果 `90 passed, 1 warning`。
- 已记录但本阶段未执行：前端 `npm run build`、Alembic upgrade、tomoDD 预检、seed、bootstrap、服务启动、清理任务。

## 角色与主要功能

- 代码明确角色：`user` 普通平台用户、`admin` 平台管理员（`backend/app/models.py:19-22`，`backend/app/main.py:25-30`）。
- 普通用户功能：注册/邮箱验证/登录、密码与邮箱管理、生成和禁用全局 API Key、API 文档、快速接入、调用记录、通知、任务与制品、文件下载。
- 管理员功能：审批/拒绝/停用用户、重置密码、用户限流与配额、Key 管理、平台邮件和 Gateway 设置、工具/上游配置、OpenAPI 导入与差异治理、接口发布、资源策略、文件管理、监控告警和审计。
- Gateway 功能：`/gateway/{tool_slug}/{path:path}`，执行 Key、用户状态、路由发布、scope、限流、内容类型、大小、超时、资源归属及调用记录。

## 关键数据与安全边界

- 身份/认证：`users`、`roles`、`user_roles`、`auth_action_tokens`；会话 JWT 放在 HttpOnly Cookie，CSRF 通过双提交 Cookie/Header（`backend/app/routers/deps.py:27-74`，`backend/app/routers/auth.py:154-172`）。
- API Key：数据库只存前缀和 HMAC pepper 哈希，完整 Key 仅创建响应返回一次（`backend/app/services/security.py:61-75`，`backend/app/models.py:129-144`）。
- 上游凭证：`tool_upstreams.token_ciphertext`、存储端点和配置修订中的密文，使用 Fernet；平台 SMTP 密码同样保存密文（`backend/app/models.py:185-210,394-412,658-676`）。
- 个人/业务数据：用户资料、审批信息、任务/资源归属、远程文件元数据、调用监控和管理员审计均落 SQLite；文件正文按文档应在工具服务器，平台通过授权流代理。
- 外部边界：tomoDD-SP 或管理员配置的白名单上游、SMTP、浏览器到 Next.js/后端；当前未发现 Redis、Prometheus、OpenTelemetry、支付或短信实现。

## 测试覆盖现状

- 后端测试文件 9 个，统计 90 个测试函数，覆盖认证会话、开发者平台、Gateway 边界与 mock 代理、登录限流、OpenAPI 导入、远程文件、安全服务。
- 未发现项目内前端组件测试、端到端测试、权限专用测试套件、性能压测、安全扫描或 CI 工作流文件；`frontend/package.json` 仅声明 `dev/build/start/lint`。
- 90 个后端测试通过不代表业务规则、浏览器流程、生产部署和真实第三方集成已经正确。

## P0/P1 初始风险摘要

- P1-001：运行脚本以开发服务器和 `--reload` 作为默认后端启动方式，生产启动方式和进程托管未在代码库中确认（待验证）。
- P1-002：`docs/ARCHITECTURE.md` 记载迁移头 `c3d4e5f6a7b8`，实际只读命令显示数据库为 `f9a0b1c2d3e4`，文档与运行状态不一致。
- P1-003：当前 `.env` 中 `TOMODD_UPSTREAM_TOKEN` 为空；本地运行配置因此可能无法完成需要上游认证的真实调用（已确认配置状态，影响待验证）。
- 未观察到可直接确认的 P0；生产安全性仍受未完成部署、权限和真实数据验证影响。

## 待业务确认

- 角色是否只有普通用户/管理员；是否需要组织、租户、项目或更细粒度管理员。
- `authenticated`、`owner`、`shared`、`admin_only` 各接口的真实业务含义和允许数据范围。
- 生产性能目标、日志/文件保留要求、可接受停机窗口、第三方测试范围和禁止触碰的数据。

## 本阶段命令与自检

- 执行过：PowerShell/Git 状态与目录读取、`rg` 文件/符号搜索、`.venv` 版本读取、Python 路由/模型元数据读取、Alembic current、SQLite 表名读取、后端 pytest。
- 未执行修改业务状态的命令：迁移升级、seed、清理、bootstrap、服务启动、前端构建、真实上游调用。
- 敏感值未输出或写入文档，仅记录环境变量名称和存在性。
