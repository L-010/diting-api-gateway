# 上线前工业级审计第二阶段总结

## 总体结论

当前项目不能依据第二阶段证据签署生产上线。后端现有测试通过，但已确认两个 P1 业务一致性问题和一个 P1 API 安全问题；部署、readiness、备份恢复、容量和真实浏览器流程仍缺少证据。建议先修复已确认 P1，再补齐生产拓扑、租户规则、幂等语义、隔离 E2E 和性能基准。

## 发现数量

| 专项 | 总数 | P0 | P1 | P2 | P3 |
|---|---:|---:|---:|---:|---:|
| 部署基线（10） | 6 | 0 | 6 | 0 | 0 |
| 业务逻辑（11） | 7 | 0 | 4 | 3 | 0 |
| 认证权限（12） | 4 | 0 | 1 | 1 | 2 |
| API 安全（13） | 5 | 0 | 1 | 2 | 2 |
| 前端 E2E（14） | 2 | 0 | 0 | 2 | 0 |
| 性能可靠性（15） | 7 | 0 | 2* | 5 | 0 |
| 生产就绪（16） | 6 | 0 | 5 | 1 | 0 |
| **合计（去重前）** | **37** | **0** | **19** | **14** | **4** |

`*` PERF-001、PERF-007 是达到多实例/容量目标时的条件性 P1；部分部署/生产文档与初始风险存在同一事实的重复记录。按风险实体去重后，P1 重点集中在：OpenAPI 递归输入、OpenAPI 单接口发布不一致、幂等失效、生产启动/迁移/readiness/备份/监控/容量证据缺失，以及多 worker/多租户/Token 的条件性风险。

## 已确认上线阻断项

1. `APISEC-001`：OpenAPI YAML 自引用可触发 `RecursionError`，导入入口未受控处理，可能返回 500 并造成资源消耗。
2. `BL-001`：接受 OpenAPI changed diff 后走单接口发布不会应用新 operation，治理状态、文档和 Gateway 路由可能不一致。
3. `BL-002`：`Idempotency-Key` 只检查存在，不去重、不保存结果、不转发；若写接口依赖幂等语义，重复请求可重复产生副作用。
4. `FE-001`：前端 lint 命令当前不可执行，发布静态质量门禁失效。
5. `DEP-001/002/003/004`、`PROD-001/002/004`、`PERF-007`：没有可审计的生产启动、迁移/回滚、readiness、备份恢复、外部监控和容量基准；在生产上线前均应阻断，除非由外部制品和演练记录补证。

## 第一阶段 P1 验证结果

| 初始风险 | 第二阶段结果 |
|---|---|
| P1-001 `--reload` | 已再次确认脚本事实；生产是否复用待运维确认，当前阻断 |
| P1-002 迁移头不一致 | 已再次确认：实际 `f9a0b1c2d3e4`，架构文档仍 `c3d4e5f6a7b8`，阻断 |
| P1-003 tomoDD Token 为空 | 配置为空再次确认；未调用真实上游，按功能范围条件性阻断 |
| P1-004 租户隔离未确认 | 权限专项确认无显式 tenant/organization；若产品多租户，P1 阻断 |
| P1-005 SQLite 扩展能力 | 限流/worker/监控依赖 SQLite 的代码路径确认；未有容量目标，按规模条件性阻断 |

## 权限与前端覆盖

- 权限实际覆盖：未登录、普通用户资源 ID 条件、Gateway `admin_only` 后端判定、会话版本和 CSRF 有代码与定向测试证据；跨租户、真实 user/admin 浏览器操作和 owner/shared/admin_only 完整矩阵因账号/业务规则缺失未执行。
- 前端实际覆盖：`npm run build` 成功；`npm run lint` 失败；浏览器真实交互、移动端、键盘、错误态、刷新/返回、重复点击和管理员流程未完成，均明确标为检查受阻或部分确认。

## 性能与安全工具覆盖

- 性能测试：未执行 k6、Locust 或压测；仅完成静态数据库/Gateway/worker 检查和 90 项后端测试。原因是没有性能目标、生产拓扑和可安全施压的隔离上游。
- 安全工具：未发现或运行 ZAP、Codex Security、依赖漏洞扫描、SAST/DAST、secret scan；完成静态安全审计和隔离输入边界脚本。
- 受阻检查：真实 SMTP、tomoDD、文件服务、生产代理/TLS/Cookie、DNS 重绑定、多 worker 并发、备份恢复、值班告警和跨租户越权。

## 仍需业务/运维确认

- 是否存在组织/租户/项目边界，以及管理员是否也需要范围隔离。
- API Key 全局 `*` scope、PublishedRoute scope 和认证动作令牌在账号停用后的预期语义。
- Gateway 写接口的幂等契约、OpenAPI 单接口发布的产品流程、监控告警是否要求确认/关闭闭环。
- tomoDD、SMTP、任务/文件能力是否属于本次上线范围；生产 QPS、并发、p95/p99、文件大小和增长率目标。
- 生产数据库、worker 数量、反向代理/TLS、备份 RPO/RTO、清理计划任务和发布/回滚责任。

## 第二阶段实际运行过的命令

- `git status --short`、`rg --files`、PowerShell 目录/文件读取。
- `.venv\Scripts\python.exe -m pytest backend\tests -p no:cacheprovider -q`：`90 passed, 1 warning`。
- `.venv\Scripts\python.exe -m pytest backend/tests/test_auth_session.py backend/tests/test_login_limit.py backend/tests/test_gateway_boundaries.py backend/tests/test_gateway_proxy_mock.py backend/tests/test_developer_platform.py -p no:cacheprovider -q`：`68 passed, 1 warning`。
- `.venv\Scripts\python.exe -m pytest backend/tests/test_gateway_proxy_mock.py backend/tests/test_remote_files.py backend/tests/test_developer_platform.py -p no:cacheprovider -q`：`61 passed, 1 warning`。
- `.venv\Scripts\python.exe -m alembic current`：`f9a0b1c2d3e4 (head)`。
- `.venv\Scripts\python.exe -m alembic upgrade head`：仅作用于系统临时隔离 SQLite 数据库。
- `npm run build`（`frontend`）：成功。
- `npm run lint`（`frontend`）：失败，`next lint` 项目目录错误。
- 隔离 Uvicorn `127.0.0.1:18080`，`Invoke-WebRequest` 检查 `/health`、未登录 API 和注册降级。
- Browser in-app 本机页面访问尝试：连接受阻，未执行写入或账号操作。

## 修改文件与副作用

本阶段仅新增以下审计文档：

- `docs/release-audit/10-deployment-baseline-verification.md`
- `docs/release-audit/11-business-logic-audit.md`
- `docs/release-audit/12-authentication-authorization-audit.md`
- `docs/release-audit/13-api-security-audit.md`
- `docs/release-audit/14-frontend-e2e-audit.md`
- `docs/release-audit/15-performance-reliability-audit.md`
- `docs/release-audit/16-production-readiness-audit.md`
- `docs/release-audit/17-phase2-evidence-index.md`
- `docs/release-audit/18-phase2-summary.md`

未修改业务代码、配置、依赖、数据库结构、现有测试或部署文件；未执行真实外部副作用；未输出密码、Token、Cookie、API Key 或 SMTP 密码；未覆盖用户已有修改。
