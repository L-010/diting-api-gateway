# 现有测试与覆盖情况

## 测试盘点

| 类型 | 发现 | 命令/范围 | 最近可见结果 | 缺口 |
|---|---|---|---|---|
| 单元/服务测试 | 有 | `.venv\Scripts\python.exe -m pytest backend\tests -p no:cacheprovider -q` | 本阶段 `90 passed, 1 warning` | 未生成覆盖率报告；边界组合和异常注入仍需扩展 |
| 集成/API 测试 | 有限 | `test_developer_platform.py`、`test_gateway_boundaries.py` 等使用 FastAPI TestClient 和临时 SQLite | 随 pytest 全部通过 | 真实数据库、真实 HTTP 上游、SMTP、跨进程未覆盖 |
| Gateway 代理测试 | 有 | `test_gateway_proxy_mock.py` | 通过；使用 FakeAsyncClient | 无真实网络、TLS、负载、连接池和流式长连接验证 |
| 前端组件测试 | 未发现 | 未发现 Jest/Vitest/RTL 配置或测试文件 | 无结果 | 组件状态、错误态、窄屏和复制交互未自动化 |
| 端到端测试 | 未发现 | 未发现项目 E2E 测试目录/脚本；虽有 `.playwright-cli` 目录，但未发现项目测试入口 | 无结果 | 注册审批、Key、文档、调用、文件下载完整浏览器流程缺失 |
| 权限专测 | 未发现独立套件 | 权限断言分散在后端平台/Gateway/文件测试 | 相关断言随 pytest 通过 | 角色矩阵、越权组合、跨用户/跨工具/跨资源系统化覆盖不足 |
| 安全扫描 | 未发现 | 无依赖扫描、SAST、DAST 或 secret scan 配置 | 无结果 | 生产依赖、容器、配置和日志扫描缺失 |
| 性能测试 | 未发现 | 无压测脚本、目标或基准报告 | 无结果 | p95/p99、限流、并发下载、SQLite 锁竞争未知 |
| CI | 未发现 | 未找到 `.github/workflows`、Buildkite 等配置 | 无结果 | 自动测试、构建、迁移和发布门禁未确认 |
| 部署前检查 | 有文档命令 | `TEST_REPORT.md` 记录 pytest、`npm run build`、迁移、Alembic current、tomoDD 预检 | 历史报告称通过；本阶段只重新执行 pytest/current | 报告日期旧于本次审计，且本地部署/真实服务状态需要重验 |

## 测试执行边界

- 本阶段实际执行的 pytest 使用测试内部临时 SQLite 和 mock 上游；没有执行会触碰现有业务数据库的迁移或清理。
- 未执行 `scripts/verify-tomodd.ps1`，因为它会访问外部/本地第三方服务；未执行邮件、文件 worker，因为会产生外部投递或后台状态变化。
- 未执行前端 build/lint，避免生成构建产物和在未确认 Node 依赖状态下扩大变更面；历史 `TEST_REPORT.md` 仅作为已有记录，不视为本阶段复核结果。

## 覆盖判断

后端核心服务和若干安全边界已有较强的代码级测试，但真实业务正确性、浏览器交互、生产配置、第三方依赖、性能、CI 和部署门禁仍缺少证据。不得由 `90 passed` 推断系统已满足上线标准。
