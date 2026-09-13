# 认证、权限与租户隔离专项审计

## 审计范围与方法

- 检查对象：`backend/app/routers/deps.py`、`auth.py`、`portal.py`、`files.py`、`gateway.py`、`admin.py`，以及 `services/resource_policies.py`、`services/gateway_proxy.py`、`services/security.py`。
- 只读证据：路由依赖、SQLAlchemy 查询条件、会话/JWT/CSRF 实现、API Key 校验和资源策略。
- 隔离运行：使用项目 `.venv` 执行认证、登录限流、Gateway 边界、Gateway Mock、开发者平台测试；未连接现有业务数据库或真实上游。
- 本专项未修改业务代码、配置、依赖、数据库结构或测试。

## 实际权限矩阵

| 主体/状态 | 浏览器会话 API | 管理 API | Gateway API Key | 资源可见范围 | 证据状态 |
|---|---|---|---|---|---|
| 未登录 | 仅公开配置、公开工具、认证入口 | 拒绝 | 无 Key 时拒绝 | 无用户资源 | 已由依赖和路由确认 |
| `user`，已审批、启用、无需改密 | `/api/me/*` | 拒绝 `require_admin` | 仅本人有效 Key；Key 账号状态再次校验 | 任务、调用、通知、文件查询均带 `user.id` 条件；Gateway `owner` 按资源归属过滤 | 代码确认；68 项相关测试通过 |
| `user`，待审批/拒绝/停用/需改密 | `get_current_user` 或 `require_not_password_change` 拒绝 | 拒绝 | Gateway 返回账号不可调用 | 不应返回受保护资源 | 已由代码和现有测试确认 |
| `admin` | 普通会话 API 可用 | 所有 `/api/admin/*` 使用 `require_admin` | 管理员也可使用自己的 Key；`admin_only` 路由由 Gateway 传入角色判定 | 管理端按平台全局查询；文件下载授权记录 `is_admin` | 代码确认；管理员流程测试通过 |
| 不同用户/潜在不同租户 | 未发现显式租户上下文 | 管理员可按用户查询 | `owner` 仅比较 `owner_user_id` | 用户资源按用户 ID 隔离；跨组织/租户边界不存在实现证据 | 待业务确认 |

## 发现

### AUTHZ-001：未发现显式租户/组织模型，跨租户隔离无法证明

- 严重程度：P1
- 状态：待业务确认（若产品不要求多租户，则降级为 P3 架构记录）
- 影响范围：`users`、`api_keys`、`external_resources`、`remote_files`、`gateway_requests`、工具和发布路由等全部业务数据。
- 触发条件：同一实例承载多个组织、项目或客户，且业务要求用户只能访问本租户数据。
- 复现步骤：
  1. 静态检查 `backend/app/models.py`，角色仅为 `user/admin`，未发现 `tenant_id`、组织成员关系或项目边界表。
  2. 检查 `portal.py`、`files.py`、`resource_policies.py`，资源条件使用 `owner_user_id` 或全局管理员身份，没有租户上下文条件。
  3. 使用现有测试可验证用户 A/B 的“用户级”资源归属，但无法构造真实租户边界。
- 预期行为：若产品定义多租户，任何列表、详情、编辑、删除、导出、下载、Gateway 资源策略和管理员查询都应强制带租户范围，并禁止跨租户 IDOR。
- 实际行为：代码只实现用户级 owner 隔离和管理员全局视图；不同用户是否属于同一租户没有可执行规则。
- 证据：`backend/app/models.py:19-22,80-144,303-316`；`backend/app/routers/portal.py:860-890`；`backend/app/routers/files.py:203-235`；`backend/app/services/resource_policies.py:260-298`。
- 涉及文件和代码位置：上述文件及 `docs/release-audit/05-role-permission-matrix.md`、`08-initial-risk-register.md` 中的 P1-004。
- 修复建议：由业务确认租户模型；若需要，增加不可伪造的租户上下文、成员关系和管理员范围，所有资源查询/写入统一注入租户条件；补充跨租户矩阵测试。
- 建议新增的回归测试：两个租户各创建用户、Key、任务、文件和调用记录；逐项验证列表/详情/下载/删除/Gateway owner/shared/admin_only 访问均不得跨租户；管理员范围也需按角色验证。
- 是否阻断上线：是（在多租户为产品要求且边界未补齐前）。

### AUTHZ-002：普通用户与资源 IDOR 防护（用户级）已确认有效

- 严重程度：P3（正向控制项，无缺陷）
- 状态：已确认
- 影响范围：用户任务、调用记录、通知、远程文件和文件下载授权。
- 触发条件：用户尝试替换任务、调用记录、文件或下载授权 ID。
- 复现步骤：现有 `test_gateway_boundaries.py` 验证 owner 对他人资源返回 404；`test_developer_platform.py` 验证其他用户调用详情隐藏；`files.py` 查询显式带 `owner_user_id == user.id`。
- 预期行为：他人资源统一返回 404，不泄露资源存在性。
- 实际行为：用户门户和文件接口均按当前用户 ID 过滤；下载授权查询同时限制 `actor_user_id`，无权资源返回 404。
- 证据：`backend/app/routers/portal.py:573-580,860-890`；`backend/app/routers/files.py:203-216,267-275,375-382`；`.venv\\Scripts\\python.exe -m pytest ...` 输出 68 passed。
- 涉及文件和代码位置：`portal.py`、`files.py`、`resource_policies.py`、`backend/tests/test_gateway_boundaries.py`、`test_developer_platform.py`。
- 修复建议：保持逐资源条件查询；新增统一授权辅助函数和跨用户参数化测试，避免后续新增接口遗漏。
- 建议新增的回归测试：对每个 `/api/me/*/{id}` 端点使用用户 B 的 ID，断言 404 且响应不包含对象字段。
- 是否阻断上线：否（当前证据支持用户级隔离，但不替代 AUTHZ-001 的租户确认）。

### AUTHZ-003：`admin_only` Gateway 路由后端已执行角色校验

- 严重程度：P3（正向控制项，无缺陷）
- 状态：已确认
- 影响范围：通过 API Key 访问的已发布接口。
- 触发条件：普通用户 Key 调用 `access_mode=admin_only` 路由。
- 复现步骤：`backend/tests/test_gateway_proxy_mock.py::test_proxy_blocks_admin_only_route_for_normal_user`。
- 预期行为：普通用户收到 403，管理员角色才可继续代理。
- 实际行为：`gateway.py` 将 `"admin" in get_user_roles(...)` 传入 `proxy_gateway_request`；服务在转发前拒绝非管理员。
- 证据：`backend/app/routers/gateway.py:228-236`；`backend/app/services/gateway_proxy.py:481-488`；测试通过。
- 涉及文件和代码位置：`gateway.py`、`gateway_proxy.py`、`test_gateway_proxy_mock.py`。
- 修复建议：保留后端强制校验；前端隐藏仅作为可用性措施，不得作为授权依据。
- 建议新增的回归测试：普通用户、管理员、停用管理员、无角色用户分别调用 `admin_only` 路由。
- 是否阻断上线：否。

### AUTHZ-004：API Key 默认为全局 `*` scope，细粒度授权待业务确认

- 严重程度：P2
- 状态：待业务确认
- 影响范围：用户创建的所有 API Key 及未来新增工具。
- 触发条件：用户创建 Key 后，Key `scopes` 固定保存为 `*`；`api_key_allows_tool` 将其解释为所有工具。
- 复现步骤：`auth.py:617-658` 创建 Key 时写入 `scopes="*"`；`gateway.py:110-117` 对 `*` 直接放行；现有测试断言新 Key scopes 为 `[*]`。
- 预期行为：若业务要求最小权限，Key 应绑定明确工具/接口 scope，并可轮换、撤销。
- 实际行为：普通用户无法在创建时选择范围，单个 Key 可调用所有已发布且非策略阻断的工具。
- 证据：`backend/app/routers/auth.py:635-643`；`backend/app/routers/gateway.py:110-117`；`backend/tests/test_developer_platform.py` Key 流程断言。
- 涉及文件和代码位置：`auth.py`、`gateway.py`、`models.py:129-144`。
- 修复建议：确认全局 Key 是否为产品设计；若不是，增加 scope 白名单、默认最小权限、管理端授权和 scope 变更审计。
- 建议新增的回归测试：Key 仅允许授权工具；撤销/过期/停用后所有调用均返回 401；新增工具不应自动被旧 Key 继承（除非明确全局 scope）。
- 是否阻断上线：视业务确认结果；要求最小权限时阻断。

## 认证与会话控制结论

- JWT 会话包含 `sub`、`sv`、`exp`，服务端每次请求比较数据库 `session_version`；登出、改密、重置密码和管理员停用均递增版本，旧会话失效（`security.py:40-54`、`auth.py:448-469,570-608`、`admin.py:1900-1914,2124-2154`）。
- 会话 Cookie 为 HttpOnly、SameSite=Lax；写请求要求双提交 CSRF Cookie/Header，并校验配置的前端 Origin（`auth.py:154-172`、`deps.py:65-74`）。Origin 为空时依赖双提交 Token，生产反向代理是否正确传递 Origin 待部署验证。
- 登录使用进程内 IP+用户名固定窗口限流；多进程/多实例不共享状态，扩展能力属于部署风险而非本专项已确认漏洞（`login_limit.py`）。
- API Key 数据库存储 HMAC pepper 哈希和前缀，完整密钥仅创建响应返回一次；未发现日志输出完整 Key（`security.py:61-75`、`models.py:129-144`）。

## 执行命令与阻断项

- `.venv\\Scripts\\python.exe -m pytest backend/tests/test_auth_session.py backend/tests/test_login_limit.py backend/tests/test_gateway_boundaries.py backend/tests/test_gateway_proxy_mock.py backend/tests/test_developer_platform.py -p no:cacheprovider -q`：`68 passed, 1 warning`。
- 未提供多租户定义、跨租户测试账号或生产角色清单，因此 AUTHZ-001、AUTHZ-004 保持“待业务确认”；未启动真实服务、未使用真实账号或数据库。

## 本专项汇总

- 发现总数：4（其中 2 项为正向控制项）。
- 按严重程度：P1 1、P2 1、P3 2、P0 0。
- 已确认上线阻断项：无直接已确认 P0/P1；AUTHZ-001 在产品要求多租户时为上线阻断。
- 前端限制与后端授权：前端管理员页面隐藏不作为安全边界；后端 `require_admin`、资源用户 ID 条件和 Gateway `admin_only` 已实际执行。
