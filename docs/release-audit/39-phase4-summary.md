# 第四阶段总结

新增关闭：生产 docs 暴露、Windows PowerShell 5.1 生产脚本解析失败、已知 Python/Node 依赖漏洞。新增测试 7 项：权限集成 6 项、readiness 数据库不可用 1 项；全量后端 `110 passed`。

实际覆盖：用户级 owner、shared、admin_only、API Key scope、会话失效、列表/详情/导出/file/task；E2E 仅 CLI/浏览器版本探测；安全扫描为 Bandit、pip-audit、npm audit、静态模式和密钥模式；性能为 owner 预检查微基准；部署/备份为临时 SQLite 本机演练。

仍阻断：租户业务规则、多实例 SQLite、真实隔离上游、浏览器 E2E、生产迁移目标验证、TLS/托管、生产备份监控、性能目标和告警业务闭环。

## 实际命令索引

- `.venv\Scripts\python.exe -m pytest backend\tests -p no:cacheprovider -q`：110 passed。
- `.venv\Scripts\python.exe backend\tests\performance\run_local_gateway_baseline.py`：本地 p50/p95/p99 微基准。
- `npm run lint`、`npm run build`（`frontend`）：均通过。
- `.venv\Scripts\python.exe -m alembic heads`、`current`、隔离库 `upgrade/downgrade/upgrade`：head 为 `a1b2c3d4e5f6`。
- `.venv\Scripts\python.exe scripts\phase4_deployment_rehearsal.py`：启动、健康、重启、恢复通过；offline SQL 非零并保留为阻断证据。
- `.venv\Scripts\bandit.exe -r backend\app -q -f txt`、`.venv\Scripts\pip-audit.exe -r backend\requirements.txt`、`npm audit --omit=dev --audit-level=high`：依赖扫描通过，Bandit 仅 9 个 Low 误报待复核。
- `.venv\Scripts\python.exe -m pip install "cryptography>=50,<51"`、`.venv\Scripts\python.exe -m pip install "pytest>=9.0.3,<10"`、`npm install --ignore-scripts`：仅更新开发/构建依赖与锁文件，未修改生产默认配置。
- `npx --yes --package @playwright/cli playwright-cli --help`、`npx --no-install playwright install --dry-run`：CLI 可用，匹配浏览器未安装。
- `rg` 危险 sink/密钥模式扫描、`.venv\Scripts\python.exe -m compileall -q backend scripts`、`git diff --check`：均通过。
