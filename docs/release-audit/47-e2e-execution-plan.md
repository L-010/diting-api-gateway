# E2E 执行计划

## 当前依赖事实

- `frontend/package.json` 没有声明 `@playwright/test` 或 `playwright`。
- `frontend/node_modules` 是项目当前本地 Node 依赖目录；不能假设其中包含 Playwright。
- 阶段四只执行了 `npx --yes --package @playwright/cli playwright-cli --help` 和 `npx --no-install playwright install --dry-run`，CLI 可探测，但匹配浏览器未安装。
- 本阶段不自动安装生产依赖、不连接生产、不把浏览器探测结果解释为 E2E 通过。

## 前置检查脚本

执行前先在目标测试机或受控 CI 工作区运行：

```powershell
.venv\Scripts\python.exe scripts\release_audit\browser_preflight.py
```

脚本只检查 Node、npm、前端 package 声明、本地 Playwright 可执行文件和 `npx --no-install ... install --dry-run` 状态，不下载浏览器。当前依赖缺失时，由前端负责人决定是否把 `@playwright/test` 固定到开发依赖和锁文件；这会产生开发/构建依赖变更，必须单独评审，不能在生产环境临时 `npx --yes` 下载。

若批准安装浏览器，应使用与锁定 Playwright 版本匹配的受控安装命令，并把浏览器缓存路径、版本、哈希和安装日志纳入证据。安装不是生产验证本身。

推荐命令顺序为先执行 `npx --no-install playwright install --dry-run`，确认版本后再由前端负责人批准执行 `npx --no-install playwright install chromium`（或批准的浏览器集合）；当前仓库缺少本地 Playwright 包时两条命令都应失败并等待依赖评审，不得用 `npx --yes` 临时拉取。

## 核心 E2E 测试

新增模板 `tests/e2e/release_core.spec.ts`，只覆盖只读和页面加载：

- `/livez`、`/readyz`、`/api/public/config`、`/api/public/tools` 返回预期状态。
- 未认证 `/api/me` 返回 401，不泄露用户数据。
- `/login` 页面可以加载，HTTP 状态为 200，页面正文可见。
- 仅当外部注入 `E2E_SESSION_COOKIE` 时，验证只读 `/api/me=200`；不在测试中登录、注册、改密码或发送邮件。

执行入口：

```powershell
.venv\Scripts\python.exe scripts\release_audit\run_e2e.py --mode local
.venv\Scripts\python.exe scripts\release_audit\run_e2e.py --mode test
$env:E2E_PRODUCTION_READONLY_ACK="true"
.venv\Scripts\python.exe scripts\release_audit\run_e2e.py --mode production-readonly
```

PowerShell 中第三条命令应先设置 `$env:E2E_PRODUCTION_READONLY_ACK="true"` 再执行。三种模式都必须提供 `E2E_BASE_URL`；生产只读模式不得提供写入凭证、管理员 Cookie 或真实业务 ID。Runner 使用 `npx --no-install`，依赖缺失时失败，不从网络临时安装。

## 三种执行模式

| 模式 | 允许环境 | 允许动作 | 通过标准 |
|---|---|---|---|
| `local` | 本地开发或隔离 SQLite | 健康、公开接口、未认证页面和只读页面 | 脚本全通过；结果只用于开发回归 |
| `test` | 共享测试环境/隔离上游 | 核心只读流程、批准的测试会话、模拟失败 | 环境证据、浏览器版本和测试数据清理齐全 |
| `production-readonly` | 正式环境只读窗口 | 健康、公开接口、批准的只读用户 API、登录页 | 需业务/基础设施批准，失败立即阻断；不执行写入和第三方副作用 |

## 需要补充的业务 E2E 矩阵

收到租户决策和测试账号后，应扩展：用户注册/审批/邮箱验证、登录锁定、CSRF、owner/shared/admin_only、API Key scope、调用列表/详情/导出、文件授权/下载、任务状态、幂等冲突、管理员审计。每条用例必须有正向和负向断言、预置数据、清理责任和脱敏截图规则。

## 证据要求

保存 Playwright、浏览器、Node、前端锁文件和目标环境版本；保存用例摘要、失败 trace 的脱敏副本和退出码。不能只保存“浏览器能启动”或单个页面截图。任何外部输入缺失、浏览器不匹配、租户规则未定或真实上游未隔离，都保持相应 P1/P2 阻断。
