# 第五阶段发布准备包汇总

## 最终结论

**禁止生产上线。** 本阶段只生成可重复执行的验证脚本、配置模板、测试模板和运行手册；没有连接生产、没有使用生产凭证、没有修改真实基础设施，也没有声称任何生产条件已验证。只有所有 P1 阻断项获得真实、可复核、与目标拓扑一致的证据后，才允许重新评估发布门禁。

## 1. 本阶段新增的脚本、测试和文档

脚本/模板：

- `scripts/release_audit/production_preflight.py`
- `scripts/release_audit/migration_precheck.py`
- `scripts/release_audit/migration_execute.py`
- `scripts/release_audit/health_validate.py`
- `scripts/release_audit/tls_proxy_validate.py`
- `scripts/release_audit/post_release_smoke.py`
- `scripts/release_audit/browser_preflight.py`
- `scripts/release_audit/run_e2e.py`
- `scripts/release_audit/performance_probe.py`
- `scripts/release_audit/third_party_connectivity.py`
- `scripts/release_audit/production-env.template`
- `scripts/release_audit/rollback.py`
- `tests/e2e/release_core.spec.ts`

审计文档：

- `41-tenant-decision-package.md`：租户与管理员权限决策包。
- `42-production-deployment-runbook.md`：生产部署、迁移、健康、TLS、进程和回滚手册。
- `43-production-validation-checklist.md`：目标环境验证清单。
- `44-backup-recovery-runbook.md`：数据库、文件、配置、恢复、RPO/RTO 和回滚决策树。
- `45-observability-and-alerting-spec.md`：厂商无关指标、告警、负责人和关闭规则。
- `46-third-party-integration-test-plan.md`：SMTP、TomoDD、文件服务隔离联调方案。
- `47-e2e-execution-plan.md`：Playwright 依赖、浏览器前置和三种执行模式。
- `48-performance-capacity-plan.md`：性能参数、容量门槛和 SQLite 限制。
- `49-external-input-checklist.md`：解除阻断所需外部输入和验收标准。

## 2. 已经自动化的外部验证项

已自动化为可执行入口，但尚未在目标生产环境执行：

- 配置存在性、生产目标标记、数据库引擎策略和 worker 兼容性预检。
- 迁移 head/current 和备份证据前置检查。
- 在显式批准后执行迁移并检查迁移后 head；默认拒绝。
- `/livez`、`/readyz`、`/health` 目标验证。
- HTTPS、证书校验和不降级重定向检查。
- 发布后健康、公开 API、未认证拒绝和批准只读 API 冒烟。
- Node/npm/本地 Playwright/浏览器 dry-run 前置检查；不自动安装。
- 三种 E2E 模式的受控 runner 和核心只读 Playwright 测试模板。
- 只读性能探针及 p95/p99/错误率门槛检查。
- 隔离第三方 TCP/TLS 连通性检查；认证、限流、未知结果和副作用仍需供应商沙箱证据。
- 回滚前置条件检查和人工步骤编号；不自动 downgrade、删除、清库或停非本脚本进程。

## 3. 必须由业务方或基础设施提供的最小输入

1. 最终租户策略、admin 范围、owner/shared/admin_only 语义及批准记录。
2. 目标服务器/测试环境访问范围、受控账号、维护窗口和证据路径。
3. 托管、进程、反向代理、TLS、域名、worker/实例拓扑和重启方案。
4. SMTP、TomoDD、文件服务的非生产隔离地址、最小凭证、故障注入和清理责任。
5. DAU、峰值并发、数据规模、吞吐、p95/p99、错误率、资源和连接门槛。
6. 数据库/文件/配置备份频率、保留期、RPO、RTO、恢复责任和同引擎恢复环境。
7. 监控平台、指标/日志接入、告警接收组、升级路径、确认和关闭规则。
8. 发布窗口、迁移负责人、回滚制品负责人、数据库恢复负责人和业务验收负责人。

## 4. 仍然禁止上线的阻断项

- 租户业务策略和管理员跨边界规则未确认，真实授权矩阵未执行。
- SQLite 多 worker/多实例、目标数据库容量和性能 SLO 未验证。
- SMTP、TomoDD、文件服务未完成隔离凭证和完整失败路径联调。
- 生产目标迁移、托管、TLS、反向代理、进程重启和真实回滚未演练。
- 生产数据库/文件备份、同引擎恢复、业务抽样、RPO/RTO 未证明。
- 监控外送、P1/P2 告警路由、值班确认/关闭和恢复证据未闭环。
- Playwright 匹配浏览器和核心浏览器 E2E 未执行。
- 性能目标、容量模型、持续压测和多 worker/实例限制未签署。

## 5. 收到外部输入后的验证顺序

1. 校验输入完整性、有效期、批准人和敏感值未泄露。
2. 在隔离测试环境补齐租户/权限模型和真实负向矩阵。
3. 安装/核对固定 Playwright 浏览器，执行 local/test E2E。
4. 使用非生产第三方凭证完成成功、超时、认证、限流、幂等/重试、脱敏和清理。
5. 依据 DAU/并发/数据量/SLO 执行测试环境容量和持续压测，明确 SQLite 或目标数据库限制。
6. 在目标服务器执行生产预检、TLS/代理/进程演练和迁移前备份验证。
7. 在发布窗口执行批准迁移，验证 Alembic head、readiness、重启和只读冒烟。
8. 执行同引擎备份恢复、业务抽样、RPO/RTO 和应用版本回滚演练。
9. 接入监控平台，注入合成故障验证告警确认、升级、恢复和关闭证据。
10. 汇总所有 P1/P2 证据，由业务、安全、数据库、基础设施和发布负责人签署最终门禁；任一 P1 缺证据即保持禁止上线。

## 6. 是否修改业务代码、生产配置、依赖或数据库

- 业务代码：未修改。
- 生产配置：未修改；仅新增不含秘密值的环境变量模板。
- 依赖：未安装、未升级、未修改 Python/Node 依赖或锁文件。
- 数据库：未连接、未迁移、未修改真实数据库；未修改历史 Alembic migration。
- 基础设施和第三方：未连接、未发布、未调用真实服务、未使用生产凭证。

## 7. 上线许可

当前不允许上线。阶段五的交付物是条件发布包，不是上线批准。除非所有 P1 阻断项获得真实目标环境验证证据、业务和基础设施完成签署，并重新通过最终发布门禁，否则结论必须保持“禁止上线”。
