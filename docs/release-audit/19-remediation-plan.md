# 第三阶段问题归并与修复计划

## 归并方法

第二阶段共记录 37 项原始发现（P0：0、P1：19、P2：14、P3：4）。本计划按共同代码根因、部署责任或缺失前置条件归并；同一事实在部署、性能和生产就绪报告中的重复记录只计入一个修复问题。状态为“待实施”的项目尚未被视为关闭。业务规则未知时采用失败关闭或保留上线阻断，不以假设替代确认。

## A 类：已确认且可以直接修复

### REM-A01：OpenAPI 本地引用循环未受控

- 问题编号：REM-A01
- 根因：导入解析为门户示例递归展开本地 `$ref` 时没有维护访问栈，YAML 别名形成的循环也未在入口转化为受控校验错误。
- 关联的原始审计发现：APISEC-001、APISEC-005。
- 严重程度：P1。
- 当前状态：待实施。
- 是否确认：是。
- 是否阻断上线：是。
- 修复范围：OpenAPI YAML 加载、JSON Pointer 解析、schema 展开和管理员导入错误映射；拒绝循环/非法引用且不写入部分导入数据。
- 是否需要数据库迁移：否。
- 是否需要业务确认：否。
- 回滚方式：回退解析服务与相应测试；数据库架构和已有 OpenAPI 数据不变。
- 回归测试方案：简单/多层合法 schema、直接/双节点/深层循环、非法引用、超大合法文档、失败后无部分写入，以及 HTTP 4xx 响应不含内部细节。
- 预计涉及文件：`backend/app/services/openapi_import.py`、`backend/app/routers/admin.py`、`backend/tests/test_openapi_import.py`、`backend/tests/test_developer_platform.py`。

### REM-A02：OpenAPI changed diff 单接口发布未应用新 operation

- 问题编号：REM-A02
- 根因：单接口发布路径绕过了批量发布路径的 operation 应用逻辑，接受差异后的待应用 operation 未在发布事务中原子写回端点和路由。
- 关联的原始审计发现：BL-001。
- 严重程度：P1。
- 当前状态：待实施。
- 是否确认：是。
- 是否阻断上线：是。
- 修复范围：差异接受、批次确认、单接口发布、端点读取和路由写入的一致事务；明确 deleted operation 的既有状态机行为。
- 是否需要数据库迁移：否。
- 是否需要业务确认：否，删除操作沿用现有“接受并确认后从候选端点移除/禁用”的实现语义；若代码证据不一致则失败关闭并记录。
- 回滚方式：回退发布路径实现；发布操作有事务回滚，不对历史 migration 做改写。
- 回归测试方案：新增、修改、删除、多个 operation、重复发布、发布中途失败、重新读取、未授权发布。
- 预计涉及文件：`backend/app/routers/admin.py`、`backend/tests/test_developer_platform.py`。

### REM-A03：Gateway 写请求的 Idempotency-Key 没有实际幂等语义

- 问题编号：REM-A03
- 根因：仅校验请求头存在，未持久化主体/路由/请求指纹/执行状态/响应，无法原子竞争或重放首次结果。
- 关联的原始审计发现：BL-002。
- 严重程度：P1。
- 当前状态：待实施。
- 是否确认：是。
- 是否阻断上线：是（配置 `require_idempotency_key` 的写接口）。
- 修复范围：Gateway 请求预检、持久化幂等记录、唯一约束、过期清理、受控响应重放和并发等待/冲突处理；不存储原始敏感请求体。
- 是否需要数据库迁移：是，新增正式 Alembic migration，不修改历史 migration。
- 是否需要业务确认：是。默认仅对已显式配置 `require_idempotency_key` 的非安全方法生效；上游超时/进程崩溃后的副作用是否可安全重试无法由本地代码确认，默认不重放未知结果。
- 回滚方式：应用回退前先停止新请求；数据库 migration 使用明确 downgrade 删除独立表，生产执行须先备份并经运维批准。
- 回归测试方案：首次、顺序重复、并发重复、相同 Key 不同主体/路由/请求、不同请求体冲突、上游失败后重试、过期、事务回滚和进程重启后重放；处理中的记录超过 5 分钟按失败关闭。
- 预计涉及文件：`backend/app/models.py`、`backend/alembic/versions/*`、`backend/app/services/gateway_proxy.py`、`backend/app/routers/gateway.py`、`backend/app/maintenance.py`、`backend/tests/test_gateway_proxy_mock.py`、`backend/tests/test_gateway_boundaries.py`。

### REM-A04：生产启动、迁移基线和 readiness 缺少项目内保障

- 问题编号：REM-A04
- 根因：仓库只有开发热重载启动脚本，`/health` 是静态存活结果，迁移头文档滞后且缺少可执行的迁移前检查。
- 关联的原始审计发现：DEP-001、DEP-002、DEP-003、DEP-004、PROD-004、PROD-006、P3-001。
- 严重程度：P1。
- 当前状态：待实施。
- 是否确认：是。
- 是否阻断上线：是，直至生产制品和运行演练也有证据。
- 修复范围：明确开发/生产脚本、liveness/readiness（数据库和 Alembic head）、迁移预检、文档迁移 head、保留清理和回滚/备份运行手册。
- 是否需要数据库迁移：否（但必须验证现有迁移链）。
- 是否需要业务确认：是，生产进程数、代理、TLS、数据库、RPO/RTO 与调度责任由运维确定。
- 回滚方式：启动/检查脚本可独立回退；应用 schema 不因启动检查自动改写。
- 回归测试方案：临时 SQLite 空库升级至 head、重复升级、失败升级事务、readiness 的数据库/版本失败路径、生产脚本静态断言不含 `--reload`。
- 预计涉及文件：`scripts/run-backend.ps1`、新增 `scripts/run-backend-production.ps1`/`scripts/preflight-release.py`、`scripts/migrate.py`、`backend/app/main.py`、`docs/ARCHITECTURE.md`、新增审计文档和测试。

### REM-A05：前端 lint 脚本与 Next.js 16 不兼容

- 问题编号：REM-A05
- 根因：`next lint` 已不再是当前 Next.js CLI 的有效子命令，项目未配置独立 ESLint 依赖。
- 关联的原始审计发现：FE-001。
- 严重程度：P2。
- 当前状态：待实施。
- 是否确认：是。
- 是否阻断上线：是，作为静态质量门禁未恢复前。
- 修复范围：仅修正脚本为本地现有 TypeScript/Next 可执行检查；不安装依赖、不跳过目录或关闭规则。
- 是否需要数据库迁移：否。
- 是否需要业务确认：否。
- 回滚方式：还原 `package.json` 脚本。
- 回归测试方案：使用 `frontend/node_modules` 执行 lint、build 及已有前端检查。
- 预计涉及文件：`frontend/package.json`、`docs/release-audit/21-frontend-quality-remediation.md`。

### REM-A06：工具 Header 名未校验

- 问题编号：REM-A06
- 根因：创建工具时 `token_label` 未按 HTTP Header field-name 语法验证。
- 关联的原始审计发现：APISEC-002。
- 严重程度：P2。
- 当前状态：待实施。
- 是否确认：是。
- 是否阻断上线：否，但应随本轮修复。
- 修复范围：输入 schema 与管理端创建/更新路径的 token label 格式校验。
- 是否需要数据库迁移：否。
- 是否需要业务确认：否。
- 回滚方式：回退输入校验；既有非法数据在读取/转发处仍按安全默认拒绝。
- 回归测试方案：合法 Header 名、控制字符、空白、冒号和非 ASCII 名称的允许/拒绝矩阵。
- 预计涉及文件：`backend/app/schemas.py`、`backend/app/routers/admin.py`、`backend/tests/test_developer_platform.py`。

## B 类：已确认但需要设计或迁移

### REM-B01：多 worker 的 claim 与限流原子性

- 问题编号：REM-B01
- 根因：SQLite 使用“先读后写”而没有跨进程原子 claim/计数；邮件与文件 worker 都缺少可审计的单实例约束。
- 关联的原始审计发现：BL-003、BL-004、PERF-001、PERF-005、DEP-005。
- 严重程度：P1（多 worker/多实例时）。
- 当前状态：待设计。
- 是否确认：代码事实确认，生产拓扑未确认。
- 是否阻断上线：生产可能多实例或同时运行多个同类 worker 时是。
- 修复范围：明确单 worker 部署门禁或实现数据库原子 claim/租约；限流改用支持原子操作的生产存储，并增加竞争指标。
- 是否需要数据库迁移：可能需要。
- 是否需要业务确认：是，生产数据库和 worker 拓扑。
- 回滚方式：保持单实例运行并停止额外 worker；对 schema 变更按独立 migration 回退。
- 回归测试方案：两个独立会话/进程竞争同一 job/email/限流桶；租约过期恢复；不可逆外部副作用 Mock。
- 预计涉及文件：`backend/app/services/remote_files.py`、`backend/app/services/email_delivery.py`、`backend/app/services/rate_limit.py`、模型/migration、生产 runbook。

### REM-B02：告警、指标与容量保障

- 问题编号：REM-B02
- 根因：告警仅是即时查询，指标依附 SQLite 明细表且没有外送、确认闭环、性能目标或压测门禁。
- 关联的原始审计发现：BL-007、PERF-002、PERF-003、PERF-004、PERF-006、PERF-007、P1-005、PROD-004。
- 严重程度：P1/P2，取决于上线容量和告警承诺。
- 当前状态：待设计。
- 是否确认：代码限制确认，SLO/容量目标未确认。
- 是否阻断上线：当前是，直到完成最小性能基准和监控/告警接入证据。
- 修复范围：性能目标、受控基准、指标外送或明确监控集成、告警指纹/状态机（若产品承诺闭环）、分页/查询边界和路由容量设计。
- 是否需要数据库迁移：告警闭环时需要。
- 是否需要业务确认：是，SLO、容量、告警和值班要求。
- 回滚方式：保留只读监控页面；外部采集和告警规则由运维按版本回退。
- 回归测试方案：合成大数据分页/窗口边界、基准脚本、指标失败脱敏、告警状态流转（确认业务需要时）。
- 预计涉及文件：`backend/app/routers/admin.py`、`backend/app/routers/portal.py`、`backend/app/services/gateway_proxy.py`、部署配置、性能脚本和运行手册。

### REM-B03：认证动作令牌失效策略

- 问题编号：REM-B03
- 根因：动作令牌验证不复查账户当前启用/审批状态，停用流程也未撤销未消费令牌。
- 关联的原始审计发现：BL-006。
- 严重程度：P2。
- 当前状态：待业务确认后实施。
- 是否确认：实现事实确认，期望产品策略未确认。
- 是否阻断上线：若停用必须立即终止所有账户动作则是。
- 修复范围：令牌撤销状态、账户状态复查、管理员生命周期操作和审计。
- 是否需要数据库迁移：可能需要（若增加撤销原因/时间）。
- 是否需要业务确认：是。
- 回滚方式：按已确认的账户生命周期策略回退。
- 回归测试方案：停用/拒绝/重置密码前后各类令牌的使用和不可重放矩阵。
- 预计涉及文件：`backend/app/services/user_lifecycle.py`、`backend/app/routers/auth.py`、`backend/app/routers/admin.py`、模型/migration、认证测试。

## C 类：环境缺失导致未验证

### REM-C01：隔离权限与浏览器 E2E 证据不足

- 问题编号：REM-C01
- 根因：没有可重复的合成多角色/多租户夹具和项目级浏览器 E2E 运行配置。
- 关联的原始审计发现：FE-002、AUTHZ-002、AUTHZ-003、DEP-006、PROD-003 的未验证部分。
- 严重程度：P1/P2。
- 当前状态：待实施测试能力。
- 是否确认：是。
- 是否阻断上线：权限隔离未验证时是。
- 修复范围：临时数据库、合成用户、Mock SMTP/上游、服务端权限测试及可用时本地浏览器 E2E。
- 是否需要数据库迁移：否。
- 是否需要业务确认：跨租户的真实定义仍需确认。
- 回滚方式：测试夹具和临时数据库可删除，不触碰运行数据库。
- 回归测试方案：未登录、user/admin、owner/shared/admin_only、批量、导出/下载/删除、会话过期和重复点击；移动视口和返回导航。
- 预计涉及文件：`backend/tests/`、新增 `tests/security/` 或现有测试目录、前端 E2E 配置（仅现有依赖支持时）、`docs/release-audit/22-isolated-test-harness.md`。

### REM-C02：真实外部服务与生产环境证据缺失

- 问题编号：REM-C02
- 根因：没有专用 tomoDD/SMTP/文件测试环境、生产代理 TLS、凭证或可执行验证边界。
- 关联的原始审计发现：P1-003、DEP-006、PROD-003、PROD-005、APISEC-005、P2-004。
- 严重程度：P1/P2。
- 当前状态：环境阻断。
- 是否确认：缺口确认。
- 是否阻断上线：上线范围包含对应能力时是。
- 修复范围：由业务/运维提供隔离服务与最小权限凭证；本仓库仅实现不泄密的配置状态、readiness 和 Mock 验证。
- 是否需要数据库迁移：否。
- 是否需要业务确认：是。
- 回滚方式：不进行真实外部修改。
- 回归测试方案：仅在批准的隔离环境对 health、认证失败和受控 Mock 路径测试，检查日志脱敏。
- 预计涉及文件：部署配置、运行手册、隔离测试配置。

## D 类：必须业务确认

### REM-D01：租户、路由 scope 与资源共享契约

- 问题编号：REM-D01
- 根因：系统只有 user/admin 角色和用户级 owner/shared 策略，但未定义 tenant/organization；`PublishedRoute.scopes` 与 API Key scope 的优先级未实现或说明。
- 关联的原始审计发现：AUTHZ-001、AUTHZ-004、BL-005、P1-004。
- 严重程度：P1/P2。
- 当前状态：等待业务确认。
- 是否确认：实现事实确认，业务规则未确认。
- 是否阻断上线：产品是多租户或要求接口级 scope 时是。
- 修复范围：确认数据隔离模型、管理员范围、路由 scope 匹配/继承和 shared 语义后再设计 schema/API/UI。
- 是否需要数据库迁移：很可能需要。
- 是否需要业务确认：是。
- 回滚方式：不在规则未知时改动授权模型；保持失败关闭。
- 回归测试方案：确认后覆盖两租户、两用户、owner/shared/admin_only、导出/下载/删除和路由 scope 矩阵。
- 预计涉及文件：模型/migration、`routers/deps.py`、`gateway.py`、`resource_policies.py`、门户/文件/管理路由、前端和安全测试。

## E 类：文档、部署或流程问题

### REM-E01：生产发布、备份恢复、TLS、维护调度和回滚流程缺失

- 问题编号：REM-E01
- 根因：生产制品、进程托管、备份恢复演练、代理 TLS、计划任务和发布责任都不在仓库或没有可审计证据。
- 关联的原始审计发现：PROD-001、PROD-002、PROD-003、PROD-005、PROD-006、DEP-005、P2-002、P2-003。
- 严重程度：P1/P2。
- 当前状态：待文档和运维演练。
- 是否确认：缺口确认。
- 是否阻断上线：是。
- 修复范围：项目内提供最小运行手册、迁移/启动预检、备份恢复/回滚模板、配置检查和监控告警建议；外部制品与演练由运维补证。
- 是否需要数据库迁移：否。
- 是否需要业务确认：是，RPO/RTO、生产拓扑、域名/TLS、调度和容量。
- 回滚方式：按版本化发布 runbook；不可把开发脚本当生产回滚方案。
- 回归测试方案：隔离 SQLite 迁移/恢复演练、启动预检、文档命令审阅；生产环境另需经批准演练。
- 预计涉及文件：`docs/release-audit/20-deployment-and-migration-remediation.md`、`docs/release-audit/23-remediation-test-results.md`、`docs/release-audit/26-release-gate.md`、`README.md`、`docs/ARCHITECTURE.md`、脚本目录。

## 本阶段执行顺序

1. 实施 REM-A01 至 REM-A06，并为每项新增回归测试。
2. 建立隔离测试夹具，验证迁移、权限和可用的本地 HTTP/E2E 流程。
3. 完成项目内可交付的部署/readiness/备份恢复/性能文档与检查脚本。
4. 将 REM-B、REM-C、REM-D、REM-E 的未验证或等待确认状态写入风险登记和最终 release gate。
