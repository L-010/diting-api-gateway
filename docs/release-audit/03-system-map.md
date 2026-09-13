# 系统结构地图

## 请求入口

### 前端

- Next.js App Router 入口：`frontend/app/layout.tsx`；首页 `frontend/app/page.tsx`。
- 公开页面：`/`、`/login`、`/register`、`/verify-email`、`/forgot-password`、`/reset-password`、`/public/tools/[toolSlug]`、兼容 `/public/tomodd`。
- 用户页面：`/portal`、`/quickstart`、`/tools`、`/tools/[toolSlug]`、`/api-keys`、`/calls`、`/notifications`、`/account`、`/change-password`、`/tasks`、`/tasks/[taskId]`、`/files`。
- 管理页面：`/admin`、`/admin/users`、`/admin/users/[userId]`、`/admin/tools`、`/admin/tools/new`、`/admin/tools/[toolId]` 及导入、差异、端点、运行配置、监控、审计、文件、任务和设置页面。
- API 客户端：`frontend/lib/api.ts`，所有非 GET/HEAD/OPTIONS 请求自动发送双提交 CSRF Header，使用 `credentials: include`。

### 后端

- 应用入口：`backend/app/main.py`；生命周期校验配置并初始化 `user/admin` 角色。
- 中间件：请求 ID 中间件、CORS、统一 HTTP/校验/未处理异常 JSON 响应、结构化日志（`backend/app/main.py:41-156`）。
- 路由模块：`auth.py` 认证与账户、`public.py` 公开目录、`portal.py` 用户门户、`admin.py` 管理 API、`files.py` 文件/制品、`gateway.py` 统一 Gateway。
- 端点规模：认证 15、公开 3、用户门户 18、管理员 68、文件 25、Gateway 1，另有 FastAPI 文档和 `/health`。

## 认证、会话与权限

- 登录创建 JWT 会话 Cookie 和可读 CSRF Cookie；会话包含用户 ID、密码变更状态和 `session_version`（`backend/app/services/security.py:40-54`）。
- `get_current_user` 校验 Cookie、JWT、账号审批、启用状态、会话版本和锁定状态；`require_not_password_change` 阻断临时密码用户访问受保护门户；`require_admin` 查询 `user_roles` 的 `admin` 角色（`backend/app/routers/deps.py:27-62`）。
- 写操作依赖 `validate_csrf`，同时限制 Origin 与 Cookie/Header 相等（`backend/app/routers/deps.py:65-74`）。
- Gateway 不使用会话 Cookie，而使用 `X-API-Key`，校验 Key 哈希、状态/过期、用户状态、工具和已发布路由（`backend/app/routers/gateway.py:90-121`）。

## 数据模型与关系

- 身份域：`users`、`roles`、`user_roles`、`api_keys`、`auth_action_tokens`、`notifications`、`email_outbox`。
- 工具治理域：`tools`、`tool_upstreams`、`tool_upstream_instances`、`openapi_specs`、`tool_import_batches`、`api_endpoints`、`endpoint_access_policies`、`openapi_diff_items`、`published_routes`、`tool_config_revisions`、`tool_integration_routes`。
- 资源/文件域：`external_resources`、`tool_storage_endpoint_revisions`、`remote_files`、`remote_file_links`、`file_sync_jobs`、`file_worker_heartbeats`、`file_download_authorizations`、`file_download_events`、`file_download_slots`。
- 监控/审计/限流域：`gateway_requests`、`admin_audit_events`、`platform_settings`、`rate_limit_buckets`、`user_rate_limit_buckets`。
- 完整列清单由 `Base.metadata.sorted_tables` 只读输出；当前数据库表名由 SQLite `sqlite_master` 只读输出。

## Gateway 与资源流程

1. 客户端向 `/gateway/{tool_slug}/{path}` 发送 `X-API-Key`。
2. Gateway 匹配启用工具和已发布路由，检查 scope、访问策略、内容类型、请求/响应大小、超时、幂等键和限流。
3. 根据工具唯一上游根地址渲染上游路径，丢弃客户端凭证并按配置注入机器凭证；资源型策略执行 owner 校验、列表过滤、资源登记和删除标记。
4. JSON 响应可登记远程文件；文件正文由上游工具服务器持有，门户先创建短期下载授权，再由 `/api/file-downloads/{authorization_id}` 流式代理。
5. 成功或失败写入 `gateway_requests`，响应带服务端 `x-request-id`。

## 异步、缓存与外部集成

- 邮件：`email_outbox` + `app.email_worker`，SMTP 配置存在时发送验证、找回、邮箱变更和管理员测试邮件。
- 文件：`app.file_worker` 周期性同步/校验/删除远程文件并记录心跳，SQLite 文档要求单 worker。
- 定时维护：`app.maintenance cleanup` 清理保留期数据，需要外部计划任务配置。
- 缓存：未发现 Redis 或独立缓存实现；登录限流和 Gateway 限流使用数据库/进程内逻辑，生产横向扩展能力待验证。
- 外部服务：tomoDD-SP/其它白名单 HTTP 上游、SMTP；未发现支付、短信、对象存储实现。

## 部署入口

- 本地入口由 `scripts/run-backend.ps1` 和 `scripts/run-frontend.ps1` 提供。
- 生产反向代理、TLS、容器、进程守护、数据库服务化和日志采集未在当前代码库确认；`LOCAL_RUNBOOK.md` 明确 Gateway 公网地址配置不会自动配置 DNS/TLS/Nginx/Caddy。
