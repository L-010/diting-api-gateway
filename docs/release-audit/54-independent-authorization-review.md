# 第六阶段：独立授权复核

## 复核原则

- 从 HTTP 路由依赖、服务层查询条件和最终 SQLite 状态重新检查，没有复用此前结论作为判定依据。
- 核心证据：`routers/deps.py`、`routers/gateway.py`、`services/resource_policies.py`、`routers/portal.py`、`routers/files.py` 及 `backend/tests/integration/test_isolated_authorization.py`。
- 合成数据使用多个普通用户和一个管理员；测试标签仅帮助说明，不是 tenant 实现。

| 入口/操作 | 匿名 | 本人/普通用户 | 其他普通用户 | 管理员 | 数据库最终状态与结论 |
|---|---:|---:|---:|---:|---|
| `/api/me`、calls、files、tasks | 401 | 200，仅本人数据 | 详情 ID 404，列表无泄露 | 管理员通过管理端查询 | 测试验证 call、CSV、file、task；查询均有 `owner_user_id`。 |
| Gateway `owner` 详情/列表/写入 | 无 Key 401 | 所有者 200 | 404，且 body 不含资源 ID | admin Key 也 404 | `ExternalResource` 以所有者映射为准；列表过滤只保留 owner ID。 |
| Gateway `shared` | 无 Key 401 | 200 | 200 | 200 | 当前代码只要求有效 Key；这是代码行为，不等同已批准的跨用户业务规则。 |
| Gateway `admin_only` | 无 Key 401 | 403 | 403 | 200 | 在 `proxy_gateway_request` 服务端检查角色，不依赖文档/UI。 |
| 管理 API | 401 | 403 | 403 | 200 | `require_admin` 查询角色关系；管理员可查询跨用户管理数据。 |
| API Key scope | 无/失效 401 | 匹配工具 scope 允许 | 不匹配 scope 403 | admin Key 仍需匹配工具 scope | `api_key_allows_tool` 在 Gateway 入口、上游调用前执行。 |
| 会话过期、登出、改密后 | 401 | 当前会话有效 | 旧 Cookie 401 | 相同 | `session_version` 被递增，后续 `/api/me` 读取拒绝。 |
| 文件下载授权 | 401 | 本人可创建授权和下载 | 404 | admin 授权可下载 | 授权绑定 actor；下载时再次校验 actor、admin 角色和文件可下载状态。 |
| 导出/批量类 | 401 | CSV 仅本人 calls；个人列表受 owner 限制 | 无法通过改 ID 读取 | admin 接口单独授权 | 当前没有用户侧批量写删接口；管理员批量发布由 admin 依赖保护。 |

## 结论与限制

- 本地隔离测试证明的是用户级所有权，不是 tenant/organization 隔离。
- 错误响应对 owner 资源使用 404，测试确认没有回显另一用户资源 ID；这降低存在性泄露。
- `shared` 跨用户可见、admin 对 owner Gateway 路由不绕过是当前可推断行为，均需业务确认是否符合产品。
- 所有列出的授权入口均在服务端检查；未发现仅靠前端隐藏的已验证入口。真实浏览器、批量外部上游副作用和新接入工具的策略 JSON 仍需按每条路由回归。
