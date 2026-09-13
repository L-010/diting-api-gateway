# 功能清单

状态定义：`已确认` 表示代码和页面/API 均有直接证据；`部分确认` 表示入口和部分链路存在但真实业务正确性、外部依赖或完整流程未验证；`待确认` 表示需要业务规则或环境证据。

| 功能 | 角色 | 前端入口 | 后端入口 | 数据模型 | 权限/敏感/外部 | 测试 | 状态与证据 |
|---|---|---|---|---|---|---|---|
| 注册申请与邮箱验证 | 公开用户 | `/register`、`/verify-email` | `/api/auth/register`、`/api/auth/verify-email` | `users`、`auth_action_tokens`、`email_outbox` | 公开；密码/邮箱敏感；SMTP 可选 | 有开发者平台测试 | 部分确认；`auth.py:177-273`，`test_developer_platform.py` |
| 登录、登出、锁定 | 用户/管理员 | `/login` | `/api/auth/login`、`/api/auth/logout` | `users`、会话 Cookie | 会话/密码敏感；无外部 | 有会话/登录限流测试 | 已确认入口；业务安全仍需完整验证 |
| 密码/邮箱变更与找回 | 已登录用户/公开用户 | `/change-password`、`/forgot-password`、`/reset-password` | 认证路由对应端点 | `users`、`auth_action_tokens`、`email_outbox` | 密码/邮箱敏感；SMTP | 有部分测试 | 部分确认；`auth.py:304-600` |
| API Key 创建与禁用 | 已审批用户、管理员 | `/api-keys`、管理员用户 Key 页面 | `/api/me/api-keys`、`/api/admin/api-keys*` | `api_keys`、`admin_audit_events` | 完整 Key/哈希敏感；无外部 | 有开发者平台、安全测试 | 已确认流程；完整 Key 一次性返回，`auth.py:611-688` |
| 用户 API 文档与快速接入 | 已登录用户 | `/tools`、`/quickstart` | `/api/me/api-docs`、`/api/me/quickstart` | `tools`、`api_endpoints`、`published_routes` | 脱敏上游信息；无外部 | 有开发者平台测试 | 部分确认；未验证浏览器复制与实际调用 |
| Gateway 代理调用 | 带 Key 的用户程序 | 页面示例/用户程序 | `/gateway/{tool_slug}/{path:path}` | `api_keys`、`tools`、`published_routes`、`gateway_requests` | Key/请求元数据敏感；调用白名单上游 | 有边界和 mock 代理测试 | 部分确认；真实上游、负载和生产网络未验证 |
| 调用记录/监控/告警 | 用户、管理员 | `/calls`、`/admin/monitor` | `/api/me/calls*`、`/api/admin/monitor/*` | `gateway_requests` | 脱敏日志/运营数据；无直接外部 | 有监控相关测试 | 已确认入口；指标准确性和保留策略待验证 |
| 用户审批、停用、导入 | 管理员 | `/admin/users*` | `/api/admin/users*`、`/api/admin/users/import` | `users`、`user_import_batches`、`user_roles` | 管理员/个人信息；SMTP 部分可选 | 有开发者平台测试 | 部分确认；CSV 边界与生产数据未验证 |
| 平台设置 | 管理员 | `/admin/settings` | `/api/admin/settings/*` | `platform_settings` | SMTP 密码/公网地址敏感；SMTP | 有部分测试 | 部分确认；部署链路未验证 |
| 工具与唯一上游配置 | 管理员 | `/admin/tools*` | `/api/admin/tools*` | `tools`、`tool_upstreams`、`tool_config_revisions`、实例表 | 上游 URL/Token 敏感；HTTP 上游 | 有工具契约测试 | 部分确认；真实白名单和健康检查未执行 |
| OpenAPI/Swagger 导入与差异治理 | 管理员 | 工具导入/差异页面 | `/api/admin/tools/{id}/openapi/*`、`/api/admin/diffs/*` | `openapi_specs`、批次、端点、差异、策略 | 上游文档可含敏感 schema；HTTP/文件上传 | 有 OpenAPI 与开发者平台测试 | 部分确认；未验证恶意文档、超大输入和真实上游 |
| 接口策略与发布 | 管理员 | 端点详情、工具工作台 | `/api/admin/endpoints/*`、`bulk-publish` | `endpoint_access_policies`、`published_routes` | 权限与资源边界；无外部直接依赖 | 有 Gateway 边界/开发者平台测试 | 部分确认；业务授权矩阵待确认 |
| 任务中心 | 已审批用户、管理员 | `/tasks*`、`/admin/tasks*` | `/api/me/tasks*`、`/api/admin/tasks*` | `external_resources`、`tools`、集成路由 | 用户资源/上游任务 ID；白名单上游 | 有部分远程文件/平台测试 | 部分确认；真实任务生命周期未验证 |
| 文件/制品同步、授权下载、删除 | 用户、管理员、文件 worker | `/files`、任务文件页、管理员文件页 | `files.py` 全部端点、文件 worker | `remote_files`、授权/事件/slot、同步 job | 文件正文/元数据敏感；上游文件服务 | 有 11 个远程文件测试 | 部分确认；真实大文件、并发、清理未验证 |
| 管理审计 | 管理员 | `/admin/audit` | `/api/admin/audit`，服务 `audit.py` | `admin_audit_events` | 管理操作敏感；无外部 | 有部分平台测试 | 已确认入口；完整不可抵赖/留存要求待确认 |
| tomoDD 调试/兼容页 | 管理员或公开访问取决于页面 | `/debug/tomodd/pipeline`、`/public/tomodd` | 主要复用工具/Gateway API | 工具、端点、资源 | 真实上游 Token 不应展示；tomoDD | 有只读预检脚本和 mock 测试 | 待真实环境确认；本阶段未调用外部服务 |

说明：表中“代码中有入口”不等于功能已正确实现；未验证项均保留为部分确认或待确认。
