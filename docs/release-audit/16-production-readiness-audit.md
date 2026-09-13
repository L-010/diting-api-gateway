# 生产就绪性审计

## 结论摘要

当前代码和仓库证据不足以签署生产上线。后端功能测试在项目 `.venv` 中复核为 `90 passed, 1 warning`，但生产启动、迁移门禁、备份恢复、readiness、TLS/反向代理、容量目标和外部告警均没有可审计的部署制品或演练记录。结论是“有条件阻断”，不是对业务代码正确性的否定。

## 就绪性发现

### PROD-001：缺少可审计的生产部署制品与回滚流程

- 严重程度：P1
- 状态：检查受阻。
- 影响范围：发布、故障恢复、版本回滚和变更责任。
- 触发条件：需要在服务器上部署、滚动升级或故障切换。
- 复现步骤：检查根目录和递归文件，未发现 Dockerfile/Compose、systemd/Windows Service、反向代理配置、CI/CD workflow 或回滚脚本；仅有 `scripts/run-*.ps1`。
- 预期行为：有固定制品、环境变量清单、迁移顺序、健康探针、停止/重启、回滚和审批记录。
- 实际行为：仓库只提供本地开发启动和可选 worker 脚本；生产拓扑依赖外部信息，无法验证。
- 证据：`scripts/run-backend.ps1`、`scripts/run-frontend.ps1`、`scripts/run-email-worker.ps1`、`scripts/run-file-worker.ps1`；目录扫描结果。
- 涉及文件和代码位置：`scripts/`、根目录部署文件缺失。
- 修复建议：建立版本化部署清单（进程、代理、TLS、端口、密钥、数据库、worker、备份、回滚）；将应用启动与迁移分离并记录责任人。
- 建议新增的回归测试：在隔离环境执行一次安装、升级、回滚和崩溃恢复演练，保存日志与耗时。
- 是否阻断上线：是。

### PROD-002：备份、恢复和数据完整性演练没有证据

- 严重程度：P1
- 状态：检查受阻。
- 影响范围：SQLite 数据库、调用审计、用户资料、Key 元数据和文件索引的灾难恢复。
- 触发条件：磁盘损坏、误删、迁移失败或主机丢失。
- 复现步骤：搜索 `backup|restore|snapshot|回滚` 及脚本目录；未发现项目内备份/恢复脚本或 RPO/RTO 文档。维护脚本仅删除保留期数据。
- 预期行为：明确加密备份、频率、保留、恢复到临时库的校验、RPO/RTO 和访问审计。
- 实际行为：未提供任何可执行恢复方案；数据库是本地 SQLite 文件，不能由应用代码推断可靠备份。
- 证据：`backend/app/maintenance.py:16-42`、`scripts/cleanup-retained-data.ps1`；`rg` 未发现备份实现。
- 涉及文件和代码位置：`backend/app/maintenance.py`、`scripts/`、`data/`。
- 修复建议：由运维提供加密备份和异地副本策略，至少完成一次恢复到隔离数据库并执行 Alembic/schema/抽样校验。
- 建议新增的回归测试：定期自动恢复演练，验证关键表计数、迁移 head、应用只读查询和敏感值不出现在日志。
- 是否阻断上线：是。

### PROD-003：HTTPS、反向代理、Cookie Secure 与公网地址未闭环

- 严重程度：P1
- 状态：检查受阻/待运维确认。
- 影响范围：公网传输安全、会话 Cookie、CORS、Gateway 客户端接入。
- 触发条件：前端/后端暴露到 HTTPS 域名，或由反向代理终止 TLS。
- 复现步骤：读取 `backend/app/routers/auth.py:154-172`，Cookie 的 `secure` 由 `APP_ENV != development` 控制；读取 `config.py:46-47` 的默认 `FRONTEND_ORIGIN` 和 `PUBLIC_GATEWAY_BASE_URL`；仓库无代理/TLS配置。
- 预期行为：生产域名、TLS 终止、可信代理头、CORS Origin、Cookie Secure/SameSite 和 Gateway 公网根地址一致并经过浏览器验证。
- 实际行为：代码默认本机 HTTP 地址；公网 DNS/TLS/代理需外部配置，未有证据证明已配置。
- 证据：`backend/app/config.py:46-47,68-82`；`backend/app/routers/auth.py:154-172`；`frontend/next.config.ts` rewrite 到 `127.0.0.1:8000`。
- 涉及文件和代码位置：上述文件。
- 修复建议：提供生产域名与代理配置，强制 HTTPS，设置可信 Origin，验证 Secure Cookie、CSRF 和 `/gateway` 外部 Base URL。
- 建议新增的回归测试：真实浏览器（隔离账号）检查登录 Cookie 属性、跨域写请求、代理转发和 HTTPS 重定向。
- 是否阻断上线：是，若服务公网可访问。

### PROD-004：健康检查和可观测性不能证明服务已就绪或可告警

- 严重程度：P1
- 状态：已确认代码限制；生产接入待验证。
- 影响范围：流量编排、故障发现、值班响应和 SLO。
- 触发条件：依赖 `/health` 或 SQLite 监控接口作为生产探针/告警源。
- 复现步骤：`main.py:159-162` 的 `/health` 不访问数据库；`admin.py:4266-4297` 从 SQLite 实时计算指标；架构文档仅建议未来接入 Prometheus/OpenTelemetry。
- 预期行为：liveness/readiness 分离，指标外送到可靠监控，告警有阈值、通知渠道和值班手册。
- 实际行为：健康端点静态返回 OK，监控数据与业务库耦合，未发现 Prometheus/Otel exporter、Sentry 或告警接收配置。
- 证据：`backend/app/main.py:159-162`；`backend/app/routers/admin.py:4243-4297`；`docs/ARCHITECTURE.md:115-123`。
- 涉及文件和代码位置：上述文件。
- 修复建议：实现数据库/schema readiness，接入外部指标与错误监控，定义告警去重、通知和响应责任；保留 SQLite 事实表但不作为唯一实时指标后端。
- 建议新增的回归测试：依赖故障、数据库锁和上游超时时，探针/指标/告警应产生预期状态且不泄露敏感信息。
- 是否阻断上线：是。

### PROD-005：环境变量缺失与 Token 为空的策略依赖业务范围

- 严重程度：P1（条件性）
- 状态：部分已确认、部分待业务确认。
- 影响范围：启动、注册/找回、Gateway 上游认证和加密数据可恢复性。
- 触发条件：生产沿用 `.env.example` 空值，或 `TOMODD_UPSTREAM_TOKEN` 为空而启用需认证工具。
- 复现步骤：读取 `backend/app/config.py:100-122`；启动校验只强制三项安全密钥、Fernet 和 tomoDD 主机白名单，不强制 SMTP、公网地址或上游 Token。公开注册代码会在 SMTP 未配置时返回 503。
- 预期行为：按产品范围建立必需/可选配置清单；必需依赖缺失时在发布前失败，非必需能力明确降级并告警。
- 实际行为：应用可在无 SMTP/Token 时启动，功能在请求时失败或关闭；未有生产配置清单。
- 证据：`backend/app/config.py:100-122`；`backend/app/routers/auth.py` 注册 SMTP 检查；`.env.example` 空模板值。
- 涉及文件和代码位置：`backend/app/config.py`、`backend/app/routers/auth.py`、`.env.example`。
- 修复建议：发布前执行不输出秘密的配置 lint；根据上线功能将 SMTP、Token、公网地址设为必需或明确降级，建立 Secret Manager 与轮换流程。
- 建议新增的回归测试：分别测试必需配置缺失、SMTP 未配置、无认证工具和认证上游无 Token 的启动/请求错误码。
- 是否阻断上线：若对应功能属于上线范围，是；否则需业务方书面标记不适用。

### PROD-006：数据保留清理依赖外部计划任务，失败不可见

- 严重程度：P2
- 状态：代码事实已确认；计划任务和告警检查受阻。
- 影响范围：调用记录、认证令牌、限流桶的容量、隐私保留和查询性能。
- 触发条件：未配置或计划任务执行失败，导致 `CALL_LOG_RETENTION_DAYS` 数据长期累积。
- 复现步骤：读取 `maintenance.py:16-42` 和 `scripts/cleanup-retained-data.ps1`；清理仅在人工/外部调度调用，代码未提供调度器或失败告警。
- 预期行为：有固定频率、锁、执行指标、失败重试和告警，保留策略与合规要求一致。
- 实际行为：默认调用记录保留 90 天，任务调度和结果监控未在仓库确认。
- 证据：`backend/app/maintenance.py:20-35`；`backend/app/config.py:52`；`scripts/cleanup-retained-data.ps1`。
- 涉及文件和代码位置：上述文件。
- 修复建议：在生产调度平台配置任务与失败告警；记录最后成功时间和删除数量，定期验证索引/磁盘增长。
- 建议新增的回归测试：临时数据库插入过期记录，执行一次清理并验证幂等、统计输出和失败退出码。
- 是否阻断上线：建议上线前确认；若有合规保留要求则阻断。

## 上线门禁矩阵

| 门禁 | 代码证据 | 当前状态 | 上线判断 |
|---|---|---|---|
| 非 reload 生产启动与守护 | 仅开发脚本 | 缺失 | 阻断 |
| 迁移 head 与目标库一致 | 当前 `f9a0b1c2d3e4`，架构文档旧 | 不一致 | 阻断 |
| 迁移/回滚演练 | 仅有手工脚本 | 无记录 | 阻断 |
| 备份/恢复/RPO/RTO | 未发现实现 | 缺失 | 阻断 |
| liveness/readiness | 仅静态 `/health` | 不足 | 阻断 |
| HTTPS/CORS/Cookie/代理 | 依赖外部部署 | 无证据 | 公网时阻断 |
| 监控、错误跟踪、告警和值班 | SQLite 实时计算，无外送配置 | 无证据 | 阻断 |
| 性能容量与并发压测 | 未发现工具/目标 | 缺失 | 阻断 |
| SMTP/TomoDD/文件 worker | 可选脚本，凭证/拓扑缺失 | 待业务确认 | 按功能范围阻断 |
| 数据清理与保留 | 外部计划任务 | 待运维确认 | 合规场景阻断 |

## 审计自检

本专项只新增本文件；未修改业务代码、配置、依赖、数据库或现有测试。未启动服务、未执行迁移/清理、未发送邮件、未上传/删除文件、未调用真实第三方，未输出任何密码、Token、Cookie 或 API Key。所有无法由仓库确认的生产结论均标记为“检查受阻/待验证/待业务确认”。
