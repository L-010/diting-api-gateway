# 数据与安全边界

## 数据分类与位置

| 数据类别 | 存储位置 | 传输/访问入口 | 删除或失效方式 | 证据与不确定性 |
|---|---|---|---|---|
| 用户身份与审批资料 | SQLite `users`、`user_roles`、`roles` | 认证 API、管理员用户 API、用户门户 | 管理员停用/状态变更；物理删除未确认 | `models.py:80-127`；删除策略待业务确认 |
| 密码 | `users.password_hash` | 登录、改密、找回、管理员重置 | 覆盖哈希；不会返回原文 | `security.py:21-38`、`auth.py` |
| 会话 Token | 浏览器会话 Cookie；数据库无明文会话表 | 受保护 API Cookie | Cookie 过期、登出、`session_version` 递增 | `security.py:40-54`、`auth.py:154-172` |
| CSRF Token | `agw_csrf` 非 HttpOnly Cookie + Header | 前端写请求、Origin 校验 | Cookie 过期/清除 | `deps.py:65-74`；跨域生产配置待验证 |
| API Key | `api_keys.prefix`、`secret_hash`；完整值仅创建响应 | Gateway `X-API-Key`、Key 管理 API | 用户/管理员禁用、过期、撤销状态 | `models.py:129-144`、`security.py:61-75` |
| 邮箱验证/找回 Token | `auth_action_tokens.token_hash` | 邮件链接和认证 API | 消费、过期、维护清理 | `models.py:610-626`、`user_lifecycle.py` |
| 个人资料/邮箱 | `users`、通知和审计可能有脱敏详情 | 用户/管理员 API、邮件 | 覆盖/账号处置；保留期未完全确认 | `auth.py`、`schemas.py` |
| 上游凭证 | 工具、存储端点、配置修订的 `token_ciphertext`；SMTP 密文 | 仅后端转发/worker 解密使用 | 替换、清空、修订退休 | `security.py:78-87`、`models.py`；密钥轮换流程待确认 |
| 工具/API 定义 | `tools`、`tool_upstreams`、OpenAPI/端点/路由/策略表 | 管理 API、用户脱敏文档、Gateway | 管理员停用、排除、阻断、修订/回滚 | `models.py:160-371` |
| 任务与资源 | `external_resources`、集成路由、任务适配配置 | 用户/管理员任务 API、Gateway 资源策略 | 删除标记、状态变更；上游真实删除依赖适配器 | `portal.py`、`resource_policies.py` |
| 文件正文与元数据 | 正文按契约存工具服务器；平台 `remote_files` 等保存 ID、大小、hash、状态和关联 | Gateway 捕获、文件列表、短期授权下载、worker | 过期、删除 pending/deleted、隔离、同步失败 | `distributed-files.md`、`models.py:430-563`；真实上游删除待验证 |
| 调用与审计 | `gateway_requests`、`admin_audit_events`、结构化日志 | 用户调用记录、管理员监控/审计、日志系统 | 调用记录默认 90 天维护清理；日志外部保留未确认 | `maintenance.py:16-42`、`observability.py` |
| 限流与锁定状态 | `rate_limit_buckets`、`user_rate_limit_buckets`、`users` 登录字段 | Gateway/登录内部服务 | 维护清理、窗口自然过期、解锁 | `rate_limit.py`、`login_limit.py` |

## 已观察安全边界

- 上游主机由 `ALLOWED_UPSTREAM_HOSTS` 限制，配置校验拒绝凭据、查询和片段；Gateway 不提供任意 URL 代理（`config.py:64-119`，`gateway_proxy.py`）。
- 管理/用户展示应使用脱敏上游主机和 Token 配置状态；用户文档只返回 Gateway 路径（`portal.py:735-844`、`schemas.py`）。
- 日志和调用记录设计为脱敏元数据，不保存完整 Key、Authorization、Cookie、请求正文或上游 Token；实际运行日志内容未在本阶段启动服务验证。
- 文件下载需要短期授权并受用户/工具并发槽位限制，下载事件落库；真实并发、断点、超限和上游删除待验证。

## 潜在敏感日志位置

- `backend/app/main.py` 请求日志、`backend/app/services/observability.py`、`gateway_proxy.py`、`file_worker.py` 的日志调用。
- 代码中存在将异常截断写入 worker 心跳/日志的路径；本阶段未执行真实异常场景，不能确认所有异常文本均已脱敏。
- `.env`、SQLite 数据库、`.run` 和临时 `.db` 文件应视为敏感边界；本阶段只读元数据，未输出内容。
