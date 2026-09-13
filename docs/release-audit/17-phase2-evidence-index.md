# 第二阶段证据索引

## 证据分级

- A：项目虚拟环境或本机隔离服务实际运行结果。
- B：代码、配置、迁移链和脚本的直接静态证据。
- C：因账号、业务规则、生产拓扑或工具缺失而未执行的检查。

## 命令证据

| 编号 | 命令/操作 | 结果 | 级别 | 用途 |
|---|---|---|---|---|
| E-001 | `git status --short`、目录扫描 | 全量未跟踪工作区；未覆盖已有文件 | A/B | 审计准备和修改边界 |
| E-002 | `.venv\Scripts\python.exe -m pytest backend\tests -p no:cacheprovider -q` | `90 passed, 1 warning` | A | 后端回归基线 |
| E-003 | `.venv\Scripts\python.exe -m alembic current` | `f9a0b1c2d3e4 (head)` | A | 迁移头验证 |
| E-004 | `npm run build`（`frontend`） | 成功，30 个静态页面生成 | A | 前端构建 |
| E-005 | `npm run lint`（`frontend`） | 失败，`next lint` 项目目录错误 | A | 前端质量门禁 |
| E-006 | 隔离临时 SQLite + `.venv` Uvicorn | `127.0.0.1:18080` 启动成功；数据库通过全量迁移 | A | 真实 HTTP 安全边界 |
| E-007 | 隔离 HTTP `/health`、`/api/me`、`/api/admin/users`、注册 | 200、401、401、503 | A | 健康、未授权和 SMTP 降级 |
| E-008 | Browser in-app 访问本机前端 | 连接被拒绝/3001 无服务；3000 命令行可读 | C | 浏览器 E2E 受阻证据 |
| E-009 | 认证/Gateway 定向测试 | `68 passed, 1 warning` | A | 权限和会话专项 |
| E-010 | Gateway/文件/开发者平台定向测试 | `61 passed, 1 warning` | A | 业务逻辑专项 |

## 发现映射

| 发现 | 文档 | 证据级别 | 上线判断 |
|---|---|---|---|
| BL-001、BL-002 | `11-business-logic-audit.md` | B | P1，条件满足时阻断 |
| BL-003、BL-004 | `11-business-logic-audit.md` | B/C | 多 worker 时阻断 |
| AUTHZ-001、AUTHZ-004 | `12-authentication-authorization-audit.md` | B/C | 待业务确认 |
| APISEC-001、APISEC-002 | `13-api-security-audit.md` | A/B | APISEC-001 P1 阻断 |
| FE-001、FE-002 | `14-frontend-e2e-audit.md` | A/C | P2，建议阻断门禁 |
| DEP-001~006 | `10-deployment-baseline-verification.md` | A/B/C | 多项 P1 阻断 |
| PERF-001~007 | `15-performance-reliability-audit.md` | B/C | 按容量条件阻断 |
| PROD-001~006 | `16-production-readiness-audit.md` | B/C | 生产就绪不足，阻断 |

## 运行环境与副作用声明

- 运行 Python 检查均使用项目 `.venv`；前端使用仓库已有 `frontend/node_modules`，未安装依赖。
- 隔离服务只绑定本机地址，使用临时数据库和合成密钥；未读取或输出真实秘密。
- 未发送邮件、上传/删除真实文件、调用真实 tomoDD/第三方、执行生产迁移/清理或压测。
- 本阶段仅在 `docs/release-audit/` 新增审计文档；未修改业务代码、配置、依赖、数据库结构、现有测试或部署文件。
