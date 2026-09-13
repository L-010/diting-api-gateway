# 部署与迁移修复

## REM-A04 / REM-E01

- 问题编号：REM-A04、REM-E01
- 根因：仓库默认脚本是开发热重载，迁移头文档过期，缺少 readiness、发布前检查和可审计的备份/回滚流程。
- 修复内容：新增 `scripts/run-backend-production.ps1`（无 `--reload`、显式 worker）、`scripts/preflight-release.py`（安全配置和单 head 检查）、`/livez` 与检查数据库/迁移版本的 `/readyz`；架构文档更新为 `a1b2c3d4e5f6`。新增幂等表正式 migration。
- 涉及文件：`scripts/run-backend-production.ps1`、`scripts/preflight-release.py`、`backend/app/main.py`、`backend/alembic/versions/a1b2c3d4e5f6_gateway_idempotency_records.py`、`docs/ARCHITECTURE.md`。
- 是否需要迁移：是，新增 migration；未修改历史 migration。
- 是否需要业务确认：是，生产代理/TLS、worker 数量、数据库、备份 RPO/RTO 和回滚责任仍需运维确认。
- 新增测试：readiness/liveness、迁移空库/重复升级/降级后升级、生产脚本静态检查。
- 执行命令：`.venv\Scripts\python.exe -m alembic heads`; `.venv\Scripts\python.exe -m alembic upgrade head`; `.venv\Scripts\python.exe -m alembic downgrade f9a0b1c2d3e4`; `.venv\Scripts\python.exe -m alembic upgrade head`; `.venv\Scripts\python.exe -m pytest backend\tests\test_release_controls.py -q`。
- 测试结果：单 head `a1b2c3d4e5f6`；隔离 SQLite 升级、重复升级、回退再升级成功；readiness 测试通过。
- 是否仍存在风险：是。真实备份恢复、TLS/代理、进程托管、值班告警和生产目标未在本地证明。
- 是否允许上线：否，需完成外部部署演练和备份恢复证据。

## 运行手册约束

生产顺序必须是：隔离备份 -> `preflight-release.py` -> 在目标库执行 `scripts/migrate.py` -> 检查 `/readyz` -> 启动生产脚本。应用启动不会隐式迁移；迁移失败必须停止发布。开发脚本 `scripts/run-backend.ps1` 仅限本地开发。
