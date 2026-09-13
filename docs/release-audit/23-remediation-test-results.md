# 修复后测试结果

## 实际执行

- `.venv\Scripts\python.exe -m pytest backend\tests -p no:cacheprovider -q`：103 passed，1 warning。
- `.venv\Scripts\python.exe -m pytest backend\tests\test_openapi_import.py backend\tests\test_gateway_proxy_mock.py backend\tests\test_developer_platform.py -p no:cacheprovider -q`：66 passed，1 warning。
- `.venv\Scripts\python.exe -m pytest backend\tests\test_release_controls.py backend\tests\test_gateway_proxy_mock.py -p no:cacheprovider -q`：22 passed。
- `npm run lint`：通过（`tsc --noEmit`）。
- `npm run build`：通过，30 个页面。
- `.venv\Scripts\python.exe -m alembic heads`：`a1b2c3d4e5f6 (head)`。
- 隔离 SQLite：空库 upgrade、重复 upgrade、downgrade 到 `f9a0b1c2d3e4`、再次 upgrade 均成功。
- `git diff --check`：通过。

## 未执行

真实生产迁移、备份恢复、Playwright 浏览器 E2E、跨租户权限、Codex Security/SAST、k6/Locust 性能和真实 SMTP/tomoDD/文件服务均未执行；原因是没有安全隔离环境、业务规则或性能目标。
