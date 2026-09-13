# 业务逻辑与数据一致性专项审计

## 审计范围与执行边界

本专项只读检查认证生命周期、API Key、Gateway、OpenAPI 导入/发布、任务与文件同步、邮件 Outbox、监控告警、状态机、幂等和事务边界。未修改业务代码、配置、依赖、数据库结构、现有测试或部署文件；未调用真实第三方服务、SMTP、文件上游或生产数据库。

实际执行命令（命令中的反斜杠按 PowerShell 原样执行）：

```text
.venv\Scripts\python.exe -m pytest backend/tests/test_gateway_proxy_mock.py backend/tests/test_remote_files.py backend/tests/test_developer_platform.py -p no:cacheprovider -q
```

结果：`61 passed, 1 warning`。测试使用项目虚拟环境、临时 SQLite 和 Mock 上游；不能证明多进程、真实上游或浏览器流程正确。

## 发现

### BL-001

- 编号：BL-001
- 标题：OpenAPI 变更差异在单接口发布路径中不会应用到正式端点
- 严重程度：P1
- 状态：已确认
- 影响范围：管理员对已有 OpenAPI 接口执行同步后，接受某个 `changed` 差异并使用单接口发布时，Gateway 可能继续使用旧的上游路径、请求/响应 schema、operation 元数据和来源规范。
- 触发条件：工具已有接口；新 OpenAPI 文档改变同一 `(method, upstream_path)` 的操作；管理员调用差异决策 `accept`，确认批次后调用单接口 `PATCH /api/admin/endpoints/{endpoint_id}/publish`。
- 复现步骤：
  1. 导入并发布包含 `/health` 的 OpenAPI。
  2. 再导入将该操作的 `summary`、`description` 或 schema 改变的文档。
  3. 调用 `PATCH /api/admin/diffs/{diff_id}/decision`，决策 `accept`；调用 `POST /api/admin/import-batches/{batch_id}/confirm`。
  4. 调用 `PATCH /api/admin/endpoints/{endpoint_id}/publish`，随后读取端点或调用 Gateway。
- 预期行为：接受差异后，单接口发布应将新操作完整应用到 `ApiEndpoint` 和 `PublishedRoute`，或明确拒绝发布并提示需要批量应用。
- 实际行为：`_apply_diff_decision()` 只更新访问策略、差异决策和端点状态，不调用 `_apply_import_operation_to_endpoint()`；单接口发布随后直接读取旧端点字段。批量发布流程另行调用该函数，因此两条发布路径行为不一致。
- 证据：`backend/app/routers/admin.py:3650-3718` 的接受决策逻辑没有应用新 operation；`backend/app/routers/admin.py:3947-4004` 的单接口发布直接调用 `_publish_endpoint_without_commit()`；仅 `backend/app/routers/admin.py:3876-3884` 的批量发布会应用差异 operation。现有测试只验证批量发布后的说明更新（`backend/tests/test_developer_platform.py:1036-1045`）。
- 涉及文件和代码位置：`backend/app/routers/admin.py:1361-1370,1417-1453,3650-3718,3947-4004`。
- 修复建议：在接受 `changed`/`new` 差异时保存待应用 operation，并在单接口发布前原子应用；或禁止单接口发布未应用的差异。发布事务中同时更新端点、策略和路由版本，并记录旧/新 operation hash。
- 建议新增的回归测试：构造已发布端点，导入变更文档后走“accept + confirm + 单接口 publish”，断言 `ApiEndpoint`、`PublishedRoute` 的路径、schema、`source_spec_id`、operation hash 均为新值；同时验证失败时事务整体回滚。
- 是否阻断上线：是。任何生产 OpenAPI 同步都可能通过单接口入口，导致文档、路由和实际上游行为不一致。

### BL-002

- 编号：BL-002
- 标题：Idempotency-Key 仅检查存在性，未去重、缓存结果或转发给上游
- 严重程度：P1
- 状态：已确认
- 影响范围：配置了 `require_idempotency_key` 的 POST/DELETE 写接口；客户端超时重试或重复点击可能重复创建任务、文件或执行删除副作用。
- 触发条件：路由 `require_idempotency_key=True`，请求带任意 `Idempotency-Key`；随后客户端以相同 Key 重发，或 Gateway 在网络不确定时由调用方重试。
- 复现步骤：
  1. 管理员为写路由设置 `require_idempotency_key`。
  2. 使用同一 API Key 和同一 `Idempotency-Key: demo-1` 连续发送两次相同 POST。
  3. 在 Mock 上游记录请求头和请求次数，检查 Gateway 数据库是否存在幂等键记录。
- 预期行为：相同作用域（用户/Key、路由、幂等键）的请求只产生一次上游副作用；重复请求返回首次结果或明确冲突；幂等键应按契约传递给支持幂等的上游。
- 实际行为：`ensure_idempotency_policy()` 只判断 Header 是否为空；`GatewayRequest` 没有幂等键字段或唯一约束；`upstream_headers()` 只构造 `x-request-id`、`accept`、`content-type` 和机器认证头，不转发 `Idempotency-Key`。因此相同 Key 不会被识别，重复请求会再次到达上游。
- 证据：`backend/app/services/gateway_proxy.py:230-233,408-418`；`backend/app/services/gateway_proxy.py:561-579`；`backend/app/models.py:564-594` 无幂等键列；路由策略默认值在 `backend/app/routers/admin.py:1290-1295`。
- 涉及文件和代码位置：`backend/app/services/gateway_proxy.py:230-233,408-418,561-579`；`backend/app/models.py:564-594`；`backend/app/routers/admin.py:1261-1316`。
- 修复建议：增加受作用域约束的幂等记录（请求指纹、状态、首次响应、过期时间）和唯一约束；请求处理中使用原子 claim；明确是否转发 Header，避免把不可信幂等键跨路由复用；对不支持安全重放的 DELETE/POST 默认拒绝自动重试。
- 建议新增的回归测试：相同 Key 并发两次只调用 Mock 上游一次；参数不同返回幂等冲突；首次失败后的重试策略、过期键和不同用户/路由作用域分别验证。
- 是否阻断上线：是，若任何写接口依赖该策略保证不重复创建、扣费或删除；否则需业务明确将其降级为“仅要求客户端自带追踪 Header”。

### BL-003

- 编号：BL-003
- 标题：SQLite 文件 worker 抢占任务没有行级锁，多进程可能重复执行
- 严重程度：P1
- 状态：待验证
- 影响范围：文件/任务同步、远程删除、校验和重试；重复执行可能重复请求上游、重复通知或并发删除。
- 触发条件：两个或更多 file worker 进程同时运行，数据库为 SQLite，且存在到期的 `FileSyncJob`。
- 复现步骤：
  1. 在隔离 SQLite 中插入一条 `pending` 到期任务。
  2. 并发调用两个进程的 `claim_sync_jobs(session)`。
  3. 记录两次返回的 job id 和 lease token，再观察上游 Mock 请求次数。
- 预期行为：一个任务在同一时刻只能被一个 worker claim；其他 worker 应跳过已占用任务。
- 实际行为：SQLite 分支不调用 `with_for_update(skip_locked=True)`；两个会话都先执行相同 SELECT，再把行改为 `running` 并提交，存在双重 claim 窗口。代码仅依赖 lease 过期恢复，没有原子条件更新。
- 证据：`backend/app/services/remote_files.py:769-790` 明确仅非 SQLite 使用 `with_for_update`；模型虽有唯一 `target_key`，但不能防止同时读取同一任务（`backend/app/models.py:480-499`）。
- 涉及文件和代码位置：`backend/app/services/remote_files.py:769-790`；`backend/app/file_worker.py:39-76`；`backend/app/models.py:480-499`。
- 修复建议：生产使用支持行锁的数据库；SQLite 单 worker 模式需启动锁/租约原子更新并在部署检查中强制单实例；增加 worker instance 竞争和崩溃恢复指标。
- 建议新增的回归测试：两个独立 SQLite 会话并发 claim，断言只有一个成功；worker 崩溃后 lease 到期可被另一实例接管且不会重复产生不可逆删除。
- 是否阻断上线：当生产可能运行多于一个 file worker 或使用多进程部署时阻断；单 worker 且有进程级互斥时需业务/运维确认。

### BL-004

- 编号：BL-004
- 标题：邮件 Outbox 没有 claim/租约，多邮件 worker 可能重复投递
- 严重程度：P1
- 状态：待验证
- 影响范围：注册验证、找回密码、邮箱变更、审批通知和管理员测试邮件；重复邮件可能造成用户困惑及一次性链接重复发送。
- 触发条件：两个或更多 email worker 同时处理同一到期 `pending/retry` 记录。
- 复现步骤：
  1. 在隔离 SQLite 中插入一条到期 `EmailOutbox`。
  2. 并发运行两个 `deliver_due_email()`，用 Mock SMTP 统计 `send_message` 次数。
  3. 检查最终 outbox 状态和 attempts。
- 预期行为：同一 outbox 记录只被一个 worker 发送；发送中应有租约或原子状态变更。
- 实际行为：函数先 SELECT `pending/retry` 记录，随后在 SMTP 发送成功后才把状态改为 `sent`；选择和状态变更之间没有行锁、claim 状态或唯一发送令牌，两个进程可同时发送。
- 证据：`backend/app/services/email_delivery.py:45-62` 查询未加锁；发送发生在 `:64-72`，状态更新和提交在 `:96-114`；`backend/app/email_worker.py:19-25` 支持持续运行但无单实例互斥。
- 涉及文件和代码位置：`backend/app/services/email_delivery.py:45-114`；`backend/app/email_worker.py:13-25`；`backend/app/models.py:625-646`。
- 修复建议：增加 `sending` 状态、租约和原子 claim；支持数据库行锁或明确部署单 worker；发送失败时只允许持有租约的 worker 回写重试状态。
- 建议新增的回归测试：并发 worker 发送同一记录只调用 SMTP 一次；发送超时、进程崩溃和租约过期后的重试不重复超过业务允许次数。
- 是否阻断上线：当生产可能运行多于一个 email worker 时阻断；单 worker 部署需运维确认并纳入启动门禁。

### BL-005

- 编号：BL-005
- 标题：PublishedRoute.scopes 字段未参与 Gateway 授权判断
- 严重程度：P2
- 状态：待业务确认
- 影响范围：若产品计划按接口或路由限制 API Key 权限，当前实际授权会比路由配置更宽。
- 触发条件：管理员将 `PublishedRoute.scopes` 设置为受限值，而 API Key 仍包含工具级或全局 scope。
- 复现步骤：
  1. 创建包含多个已发布接口的工具，并将其中一个路由 `scopes` 设置为不包含当前 Key 的值。
  2. 使用该 Key 调用受限路由和允许路由。
  3. 对比数据库中的 route scope 与响应。
- 预期行为：Gateway 同时校验 Key scope 和路由 scope，拒绝未授权接口。
- 实际行为：Gateway 仅调用 `api_key_allows_tool()`，只匹配 `*`、`tool:{slug}:*` 等工具级 scope；没有读取 `route.scopes`。
- 证据：`backend/app/routers/gateway.py:110-117,179-193`；`PublishedRoute.scopes` 定义于 `backend/app/models.py:340-360`，但 `rg` 未发现 Gateway 对该字段的读取。现有测试只覆盖工具级 scope（`backend/tests/test_gateway_boundaries.py:239-247`）。
- 涉及文件和代码位置：`backend/app/routers/gateway.py:110-117,179-193`；`backend/app/models.py:340-360`。
- 修复建议：明确 scope 设计（工具级或路由级二选一）；若保留路由级字段，定义匹配和继承规则并在 Gateway、文档、Key 管理界面保持一致；删除未使用字段也应完成迁移和文档清理。
- 建议新增的回归测试：路由 scope 与 Key scope 的允许/拒绝矩阵，包含全局 `*`、工具级、单操作 scope 和旧版 tomodd scope。
- 是否阻断上线：若业务要求接口级授权则阻断；若 API Key 按设计始终是工具全局权限，则标记字段为待清理的治理项。

### BL-006

- 编号：BL-006
- 标题：停用/拒绝账号不会使已签发的认证动作令牌失效
- 严重程度：P2
- 状态：待业务确认
- 影响范围：邮箱验证、邮箱变更、密码找回等一次性令牌；账号状态变化后，令牌仍可能执行对应数据变更。
- 触发条件：账号已收到验证/找回/邮箱变更令牌，管理员随后拒绝或停用账号，用户在令牌有效期内提交令牌。
- 复现步骤：
  1. 为已验证用户签发密码找回或邮箱变更令牌。
  2. 管理员停用账号（`session_version` 增加）。
  3. 在令牌未过期前调用对应公开端点。
  4. 检查密码、邮箱和 `consumed_at` 是否发生变化。
- 预期行为：账号被拒绝/停用后，未消费的动作令牌应失效；至少密码找回和邮箱变更应拒绝执行。
- 实际行为：`get_valid_action_token()` 只检查 hash、purpose、consumed_at 和 expires_at；`reset_password()`、`verify_email_change()` 未检查用户当前审批/启用状态。停用流程只更新用户状态和 session version。
- 证据：`backend/app/services/user_lifecycle.py:64-77`；`backend/app/routers/auth.py:336-373,538-566`；`backend/app/routers/admin.py:2030-2043`。是否允许停用账号继续完成已发出的动作属于业务规则，当前无专门测试。
- 涉及文件和代码位置：上述文件和行号。
- 修复建议：在停用/拒绝/重置密码时按用户和 purpose 原子消费或撤销未使用令牌；动作端点再次校验账号状态；记录撤销原因。
- 建议新增的回归测试：账号状态在令牌签发前后变化的矩阵，验证令牌不可重放、不可跨 purpose 使用，并确认管理员重置密码会使旧找回令牌失效。
- 是否阻断上线：需业务确认安全策略；若停用即应立即终止所有账户动作，则阻断。

### BL-007

- 编号：BL-007
- 标题：监控告警为每次查询即时计算，没有确认、关闭或持久化状态
- 严重程度：P2
- 状态：待业务确认
- 影响范围：Key 失败率、工具 5xx、文件 worker 离线、同步重试和存储水位告警；无法确认某条告警是否已处理，也无法形成告警生命周期审计。
- 触发条件：管理员重复访问 `/api/admin/monitor/alerts` 或 `/metrics`，期间底层调用记录仍满足阈值。
- 复现步骤：
  1. 在隔离数据库写入达到阈值的失败调用或待重试文件任务。
  2. 连续两次读取 `/api/admin/monitor/alerts`。
  3. 检查是否存在告警记录、确认人、确认时间、关闭原因或去重窗口。
- 预期行为：若需求包含运营告警，应有稳定的告警实体、去重、确认/关闭状态和审计记录；同一异常在窗口内不应无限产生新事件。
- 实际行为：`_monitor_alerts()` 每次从当前数据聚合返回内存字典，只有派生 ID（如 `file-sync-retries`），模型中没有 alert 表，也没有 acknowledge/close API。
- 证据：`backend/app/routers/admin.py:4111-4240`；`rg` 未发现告警模型或确认/关闭路由；`backend/app/models.py` 无告警表。
- 涉及文件和代码位置：`backend/app/routers/admin.py:4111-4240`；`backend/app/models.py` 全部模型定义。
- 修复建议：先由业务确认这是“查询型诊断”还是“需值班闭环的告警”；后者应增加持久化告警、指纹去重、状态流转、确认/关闭 API 和管理员审计。
- 建议新增的回归测试：同一异常在窗口内返回稳定指纹；确认后状态可查询且不重复提醒；关闭原因和操作者写入审计。
- 是否阻断上线：若上线承诺告警闭环则阻断；若仅提供实时诊断页面则记录为待确认的产品边界。

## 未发现或已覆盖的方面

- API Key 哈希存储、禁用、过期和账号状态校验已有代码及测试证据；本次未发现明文持久化。
- 资源注册使用 `ExternalResource(tool_id, kind, upstream_id)` 唯一约束，文件清单使用 `identity_key` 唯一约束并对所有者冲突执行隔离；跨租户是否允许共享上游 ID 仍需业务确认。
- Gateway 成功/失败路径均写入 `GatewayRequest`，响应策略失败会通过嵌套事务回滚资源登记后返回 502；未在真实上游验证连接中断和进程崩溃时的最终一致性。
- 文件删除接受上游 404 并标记本地 `deleted`，具备基本幂等性；真实上游删除失败恢复、worker 崩溃恢复和并发下载未运行验证。
- 监控计算、分页和时间范围参数有边界校验；未执行大数据量或跨时区压力测试。

## 汇总

- 本专项发现：7 项（P1：4，P2：3，P0/P3：0）。
- 已确认：BL-001、BL-002（2 项）。
- 待验证：BL-003、BL-004（2 项，依赖多 worker/多进程运行形态）。
- 待业务确认：BL-005、BL-006、BL-007（3 项）。
- 检查受阻：真实 SMTP、真实文件/任务上游、多进程 SQLite 并发和生产部署拓扑未提供，未执行外部副作用测试。
- 上线阻断判断：BL-001；BL-002 在写接口依赖幂等语义时；BL-003/BL-004 在生产允许运行多个相应 worker 时；BL-005/BL-006/BL-007 需先由业务确认其适用性。
- 本次未修改业务代码、配置、依赖、数据库或测试；仅新增本审计文档。
