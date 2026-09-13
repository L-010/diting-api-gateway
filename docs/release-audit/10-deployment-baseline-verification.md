# 部署基线验证

## 审计范围与执行边界

本专项只读检查启动脚本、应用生命周期、Alembic、数据库连接、worker、健康检查和环境变量。未启动业务服务、未执行迁移升级/回滚、未触碰现有 `data/api_gateway.db`，也未访问真实 tomoDD、SMTP 或文件服务。项目没有仓库级 `AGENTS.md`；按用户提供的第二阶段约束执行。

执行命令（均使用项目虚拟环境或 PowerShell 只读命令）：

```text
Get-Content scripts/run-backend.ps1
Get-Content scripts/run-frontend.ps1
Get-Content scripts/run-email-worker.ps1
Get-Content scripts/run-file-worker.ps1
Get-Content backend/app/main.py
Get-Content backend/app/config.py
Get-Content backend/app/database.py
Get-Content scripts/migrate.py
Get-Content backend/alembic/env.py
.venv\Scripts\python.exe -m alembic current
.venv\Scripts\python.exe -c "... from app.database import engine; print(type(engine.pool).__name__)"
.venv\Scripts\python.exe -m pytest backend\tests -p no:cacheprovider -q
```

环境证据：Alembic 当前输出为 `f9a0b1c2d3e4 (head)`；SQLAlchemy SQLite 引擎使用 `QueuePool`，URL 为 `sqlite:///./data/api_gateway.db`；测试结果为 `90 passed, 1 warning`。`.env` 敏感值未读取或保存，只记录变量存在性。

## 发现

### DEP-001：默认后端启动脚本仍使用开发热重载

- 严重程度：P1
- 状态：已确认（生产是否直接复用该脚本仍待运维确认）
- 影响范围：后端进程稳定性、发布可控性、文件监视开销和进程托管。
- 触发条件：按仓库提供的 `scripts/run-backend.ps1` 启动线上实例。
- 复现步骤：读取脚本第 5-6 行，可见 Uvicorn 绑定 `127.0.0.1:8000` 并传入 `--reload`。
- 预期行为：生产应有明确的非 reload 启动命令、worker 数量、优雅停止和进程守护策略。
- 实际行为：仓库仅提供开发脚本；未发现 Docker/Compose、服务单元、进程守护或 CI 发布配置。
- 证据：`scripts/run-backend.ps1:5-6`；`Get-ChildItem -Recurse -File -Include '*docker*','Dockerfile*','compose*','*.service'` 未发现生产单元。
- 涉及文件和代码位置：`scripts/run-backend.ps1:5-6`。
- 修复建议：新增经运维批准的生产启动单元（禁用 `--reload`、固定进程数、超时和优雅退出），并记录反向代理/TLS/端口拓扑；开发脚本保留为本地用途。
- 建议新增的回归测试：解析生产启动单元，断言不存在 `--reload`，并在临时进程中验证 SIGTERM/停止行为。
- 是否阻断上线：是。在生产启动方式确认并演练前阻断。

### DEP-002：架构文档迁移头与实际迁移头不一致

- 严重程度：P1
- 状态：已确认差异；实际部署后果待验证。
- 影响范围：新环境初始化、升级、回滚和发布基线。
- 触发条件：部署人员依据 `docs/ARCHITECTURE.md` 的旧迁移头进行校验或回滚。
- 复现步骤：执行 `.venv\Scripts\python.exe -m alembic current`，得到 `f9a0b1c2d3e4 (head)`；读取架构文档末段仍写 `c3d4e5f6a7b8`。
- 预期行为：文档、迁移链、目标数据库版本和发布制品指向同一 head。
- 实际行为：代码迁移链包含 `f9a0b1c2d3e4_reconcile_endpoint_route_governance.py`，文档落后。
- 证据：`backend/alembic/versions/f9a0b1c2d3e4_reconcile_endpoint_route_governance.py:1-15`；`docs/ARCHITECTURE.md:121-123`；Alembic 命令输出。
- 涉及文件和代码位置：`backend/alembic/versions/*`、`docs/ARCHITECTURE.md:121-123`。
- 修复建议：发布前统一文档和迁移链，生成带制品版本的迁移清单；对目标数据库执行只读 `current`/`heads` 校验并演练升级与回滚（临时副本）。
- 建议新增的回归测试：CI 中运行 `alembic heads` 与发布清单比对；迁移链应保持单 head。
- 是否阻断上线：是，直到基线统一并完成目标库校验。

### DEP-003：应用启动不会自动执行数据库迁移

- 严重程度：P1
- 状态：已确认行为；发布流程是否在外部执行迁移待确认。
- 影响范围：新实例启动、滚动发布和 schema 兼容性。
- 触发条件：只运行 `uvicorn app.main:app` 而未单独执行 `scripts/migrate.py`。
- 复现步骤：读取 `backend/app/main.py:33-37`，生命周期仅调用 `validate_runtime()` 和 `seed_roles()`；迁移仅在 `scripts/migrate.py:16-19` 中显式 `command.upgrade`。
- 预期行为：部署流水线应在应用启动前、受控锁和备份后执行迁移，或明确由平台单独负责。
- 实际行为：应用不会自动检测/升级 schema；缺少外部流水线定义，无法证明发布时一定执行迁移。
- 证据：`backend/app/main.py:33-37`；`scripts/migrate.py:16-19`；未发现 `.github/workflows` 或其他发布流水线文件。
- 涉及文件和代码位置：`backend/app/main.py:33-37`、`scripts/migrate.py:16-19`。
- 修复建议：在发布 runbook/流水线中加入显式迁移步骤、并发锁、失败停止和回滚策略；禁止应用进程隐式改生产 schema。
- 建议新增的回归测试：隔离临时数据库执行 `scripts/migrate.py`，验证从空库和上一版本升级；发布脚本缺迁移步骤时应失败。
- 是否阻断上线：是，直到迁移责任、锁和回滚演练有可审计证据。

### DEP-004：健康检查是静态存活检查，不验证数据库或依赖就绪

- 严重程度：P1
- 状态：已确认；是否被负载均衡器当作 readiness 使用待验证。
- 影响范围：流量切入、数据库故障发现、滚动发布和自动恢复。
- 触发条件：反向代理或编排系统将 `/health` 作为“可接流量”判据，而 SQLite 不可写、迁移未完成或上游不可用。
- 复现步骤：读取 `backend/app/main.py:159-162`；调用 `health()` 仅返回固定 `status=ok`、环境和 `tomodd_configured`，不创建数据库连接、不执行查询。
- 预期行为：区分 liveness 与 readiness；readiness 至少检查数据库连接/schema，按产品范围检查必要 worker/上游。
- 实际行为：数据库损坏或不可达时仍可能返回 `200 status=ok`；`tomodd_configured` 只表示 base URL 字符串非空，不代表网络可达或 Token 有效。
- 证据：`backend/app/main.py:159-162`；`.venv\Scripts\python.exe -c "... from app.main import health; print(health())"` 输出 `{'status': 'ok', ...}`。
- 涉及文件和代码位置：`backend/app/main.py:159-162`。
- 修复建议：增加不泄露敏感信息的 `/livez` 与 `/readyz`；readiness 使用短超时数据库探针及迁移版本校验，依赖检查失败返回 503。
- 建议新增的回归测试：临时数据库连接失败、迁移版本不符和必要依赖不可达时，readiness 应返回非 200；liveness 仍可用。
- 是否阻断上线：是，若该端点用于流量编排；否则至少在上线前标明仅为 liveness。

### DEP-005：SQLite、worker 和维护任务的生产托管边界未确认

- 严重程度：P1
- 状态：检查受阻/待运维确认。
- 影响范围：并发写入、文件同步、邮件投递、数据保留和故障恢复。
- 触发条件：生产沿用 SQLite 或同时启动多个文件 worker，或未配置 `cleanup-retained-data.ps1` 的计划任务。
- 复现步骤：读取 `backend/app/database.py:17-22`（SQLite `check_same_thread=False`、默认 QueuePool）；读取 `backend/app/file_worker.py:79-89`（永久循环）；读取 `backend/app/maintenance.py:16-42`（仅命令调用）；仓库未提供计划任务/服务定义。
- 预期行为：明确 PostgreSQL/SQLite 选型、worker 单实例/租约、计划任务、崩溃重启、备份和告警。
- 实际行为：README 仅说明 SQLite 本地只能运行一个文件 worker；没有生产守护、备份恢复或任务调度证据。
- 证据：`backend/app/database.py:17-22`、`backend/app/file_worker.py:79-89`、`backend/app/maintenance.py:16-42`、`README.md:40-48`。
- 涉及文件和代码位置：上述文件。
- 修复建议：提供生产拓扑和 runbook；高并发环境迁移 PostgreSQL，限流/会话状态评估 Redis；为 worker/maintenance 配置单实例锁、重启策略、失败告警和备份恢复演练。
- 建议新增的回归测试：隔离数据库并发写入、worker 租约抢占、维护任务重复执行均应可观测且幂等。
- 是否阻断上线：是，若生产目标包含任务/制品或多实例；否则仍需书面确认适用范围。

### DEP-006：TomoDD Token 为空的失败路径未做真实验证

- 严重程度：P1（条件性）
- 状态：配置为空已确认；上游是否必需及实际失败码待验证。
- 影响范围：需要认证的 tomoDD Gateway 调用、健康检查和发布校验。
- 触发条件：`TOMODD_UPSTREAM_TOKEN` 为空且上游要求 Bearer/Header 凭证。
- 复现步骤：按基线仅检查 `.env` 变量存在性（不输出值）；读取 `config.py:32-33` 和 `gateway_proxy.py` 的 `upstream_headers`，空密文不会添加认证 Header。未调用真实上游。
- 预期行为：上线前明确工具认证模式；缺凭证时发布门禁或 readiness 应明确失败，调用返回可诊断的受控错误。
- 实际行为：配置校验不要求 Token；代码允许无 Token 启动，真实上游行为无法由本地证据确认。
- 证据：`backend/app/config.py:32-33,117-119`；`backend/app/services/gateway_proxy.py` 中 `upstream_headers`；基线记录 `.env` 的该变量为空。
- 涉及文件和代码位置：`backend/app/config.py:32-33,117-119`、`backend/app/services/gateway_proxy.py`。
- 修复建议：若 tomoDD 为上线必需，使用专用最小权限测试凭证验证 health/只读调用，并在发布校验中区分 `auth_type != none` 的凭证缺失；禁止记录 Token。
- 建议新增的回归测试：无 Token 时认证上游调用返回明确错误码且不泄露 Header；无认证工具仍可正常运行。
- 是否阻断上线：若 tomoDD 属于上线范围，是；否则记录为不适用并提供替代工具证据。

## 第一阶段 P1 验证结论

`P1-001`（`--reload`）：脚本事实再次确认，生产托管仍待验证，当前阻断。`P1-002`（迁移头）：差异再次确认，当前阻断。`P1-003`（Token 为空）：配置状态确认，未执行真实上游调用，按功能范围条件性阻断。`P1-005`（SQLite 扩展）：连接与限流实现确认，容量/并发实测受目标缺失阻断，按生产规模条件性阻断。`P1-004`（租户隔离）不属于本专项，保留权限专项结论。

## 未执行检查与原因

未启动前后端、未执行 `npm run build`、未执行迁移升级/回滚、未启动 email/file worker、未配置或调用真实 SMTP/tomoDD；原因是本阶段禁止业务副作用且缺少隔离生产拓扑、测试凭证和容量目标。未修改业务代码、配置、依赖、数据库或测试。
