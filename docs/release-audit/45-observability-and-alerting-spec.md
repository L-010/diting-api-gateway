# 可观测性与告警规范

## 范围和现状

项目已有 JSON 日志、管理员只读 metrics/alerts 查询、文件 worker 心跳，以及 `/livez` 和 `/readyz`。尚未证明指标外送、日志运行时脱敏、告警接收人、确认/关闭状态机和值班路由。本规范不假设 Prometheus、OpenTelemetry、云厂商或任何具体产品；接入平台需将以下名称、标签和状态映射到实际实现。

所有指标和日志均不得包含密钥、Cookie、API Key 全值、SMTP 密码、TomoDD token、请求体中的敏感字段或文件正文。用户 ID、组织 ID（未来存在时）和资源 ID 只能按批准的脱敏/哈希规则使用。

## 指标命名和公共标签

建议使用 `api_gateway_` 前缀；公共标签限制为 `service`、`environment`、`region`、`route_group`、`method`、`status_class`、`dependency`。禁止高基数原始 URL、请求 ID、用户名和 token 作为指标标签。日志应包含时间、级别、事件名、状态码、耗时、请求 ID 和脱敏依赖名。

## 指标目录

| 域 | 指标/事件 | 最低维度 | 告警占位符 |
|---|---|---|---|
| HTTP | 请求总数、吞吐、5xx 数和比率、4xx 比率、错误码计数 | route_group、method、status_class | `HTTP_5XX_RATE_P1`、`HTTP_LATENCY_P1`、`HTTP_ERROR_RATE_P2` |
| 延迟 | p50、p95、p99、超时数 | route_group、dependency | `HTTP_P95_MS=<待定>`、`HTTP_P99_MS=<待定>` |
| 健康 | `/livez` 状态、`/readyz` 状态、连续失败次数 | endpoint、environment | `LIVEZ_DOWN_P1`、`READYZ_NOT_READY_P1` |
| 数据库 | 连接成功/失败、池使用率、连接等待、慢查询数/耗时、事务回滚 | database、query_group | `DB_CONNECT_P1`、`DB_SLOW_QUERY_P2`、`DB_POOL_EXHAUSTED_P1` |
| 迁移 | 开始、成功、失败、当前 head、耗时 | revision、environment | `MIGRATION_FAILED_P1`、`MIGRATION_HEAD_DRIFT_P1` |
| Worker | email/file worker 存活、最后心跳、任务成功/失败、重试、队列积压 | worker、task_type | `WORKER_HEARTBEAT_P1`、`QUEUE_BACKLOG_P2`、`TASK_FAILURE_P1` |
| Gateway | 上游 4xx/5xx、连接失败、超时、重试、未知结果 | dependency、route_group | `GATEWAY_UPSTREAM_ERROR_P1`、`GATEWAY_TIMEOUT_P1`、`GATEWAY_RETRY_STORM_P1` |
| SMTP | 连接、认证、投递成功/失败、Outbox 积压、最终失败 | smtp、failure_code | `SMTP_FAILURE_P1`、`EMAIL_OUTBOX_BACKLOG_P2` |
| TomoDD | 连通、认证、响应错误、超时、限流、未知结果 | dependency、operation | `TOMODD_AUTH_P1`、`TOMODD_TIMEOUT_P1`、`TOMODD_RATE_LIMIT_P2` |
| 文件上游 | manifest 错误、下载失败、大小超限、上游超时、同步积压 | tool、operation、error_code | `FILE_UPSTREAM_P1`、`FILE_SYNC_BACKLOG_P2` |
| 安全 | 登录失败、账号锁定、权限拒绝、CSRF 失败、可疑来源 | event、route_group | `LOGIN_FAILURE_SPIKE_P2`、`AUTHZ_DENIED_SPIKE_P1`、`CSRF_FAILURE_P1` |
| 幂等 | 命中、冲突、重复请求、未知结果重试 | route_group、result | `IDEMPOTENCY_CONFLICT_P2`、`DUPLICATE_SIDE_EFFECT_P1` |
| 备份 | 备份成功/失败、校验失败、恢复验证成功/失败、最后成功时间 | backup_type、environment | `BACKUP_FAILED_P1`、`BACKUP_VERIFY_FAILED_P1` |

## 告警规范模板

每条实际规则必须补齐以下字段：

```text
告警名称：<稳定名称>
等级：P1/P2/P3
表达式：<厂商无关逻辑和窗口>
阈值：<数值、百分比、持续时间待业务/基础设施确认>
抑制：<部署、维护窗口、依赖级联规则>
负责人：<角色和轮值组>
升级路径：<确认时限、二线、业务负责人>
证据：<日志/指标/追踪/事件链接>
关闭条件：<恢复窗口、根因说明、验证命令和业务确认>
```

## 最低告警规则（阈值待填）

| 告警 | 建议触发 | 等级 | 负责人/升级 | 关闭条件 |
|---|---|---|---|---|
| 5xx 比率超标 | 核心 API 在 `<窗口>` 内超过 `<HTTP_5XX_RATE>` | P1 | 应用值班 -> 发布负责人 -> 业务 | 连续 `<恢复窗口>` 低于阈值，核心冒烟通过 |
| p95/p99 延迟超标 | 核心 API p95/p99 超过 `<SLO>` | P1 | 应用值班 -> 性能负责人 | 延迟恢复且无队列/数据库异常 |
| `/readyz` 非 200 | 连续 `<次数>` 或 `<分钟>` | P1 | 基础设施 -> 数据库/应用 | `/readyz=200`，迁移 head 正确 |
| 数据库连接池耗尽 | 使用率或等待超过 `<阈值>` | P1 | 数据库值班 -> 应用 | 连接恢复、慢查询和锁争用解释完成 |
| 迁移失败/head 漂移 | 迁移失败或非批准 revision | P1 | 发布负责人 -> 数据库负责人 | 回滚/修复证据与版本一致 |
| Worker 心跳超时 | 超过 `<心跳 TTL>` 无心跳 | P1 | Worker 负责人 -> 基础设施 | 重启后任务抽样成功 |
| 队列积压 | backlog 超过 `<数量>` 或 `<时长>` | P2 | Worker 负责人 -> 业务 | 积压回落并验证无丢任务 |
| Gateway 上游失败/超时 | 单依赖错误率/超时超过 `<阈值>` | P1 | 集成负责人 -> 上游负责人 | 上游恢复、未知结果已核对 |
| SMTP 最终失败 | Outbox 达最大尝试或失败率超过 `<阈值>` | P1 | 邮件负责人 -> 业务 | 隔离测试邮件成功且积压清零/有处置 |
| TomoDD/文件异常 | 认证、限流、超时或 manifest 错误超过 `<阈值>` | P1/P2 | 集成负责人 | 成功/失败/重试路径留证 |
| 登录失败/权限拒绝突增 | 相对基线超过 `<倍数>` 或绝对值 `<阈值>` | P2/P1 | 安全值班 -> 应用 | 来源解释、无攻击迹象或完成处置 |
| CSRF 异常 | 核心路径失败率超过 `<阈值>` | P1 | 安全/前端负责人 | 代理 Origin、Cookie 和客户端版本核对 |
| 幂等冲突/重复副作用 | 冲突率或重复副作用大于 `<阈值>` | P1/P2 | API 负责人 -> 业务 | 记录一致、未知结果未重放 |
| 备份/恢复验证失败 | 任一关键备份失败或恢复抽样失败 | P1 | 数据库/基础设施 | 新备份恢复验证通过并签署 |

## 告警确认和关闭业务规则待确认

- P1/P2 的确认时限、升级次数、是否自动创建事件。
- 告警是否允许自动关闭；自动关闭前需要多少个连续健康窗口。
- 部署和维护窗口如何抑制，抑制期间是否仍记录 P1 安全/数据告警。
- “上游未知结果”是否允许重试、谁批准重试、如何核对副作用。
- 登录失败、CSRF、权限拒绝的隐私保留期和安全响应流程。
- 备份失败是否立即禁止发布、禁止迁移或触发灾备切换。

未确认时默认：P1 不自动关闭、不静默；必须人工确认、记录根因和恢复证据。当前只读聚合接口不能被声称为已完成的运营闭环。
