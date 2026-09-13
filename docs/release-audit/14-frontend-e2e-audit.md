# 前端真实使用与 E2E 专项审计

## 审计范围与执行边界

- 检查页面入口、前端 API 客户端、Next.js rewrite、登录/注册状态处理、构建和 lint 命令。
- 只读静态检查和本机合成服务验证；未使用真实账号、真实数据、SMTP、文件服务或第三方上游。
- 本阶段未修改前端或后端业务代码、配置、依赖、数据库或测试。

## 实际执行

| 检查 | 命令/证据 | 结果 |
|---|---|---|
| 前端生产构建 | `npm run build`（`frontend`） | 已通过；TypeScript、静态页面生成和路由收集成功 |
| 前端 lint | `npm run lint`（`frontend`） | 失败：Next 16 将 `next lint` 解释为目录参数，报 `Invalid project directory .../frontend/lint`；未发现替代 lint 配置 |
| 浏览器连接 | in-app Browser 访问 `http://127.0.0.1:3001/login` | 检查受阻：3001 无服务；复用已有 3000 进程时浏览器运行时仍报告本机连接被拒绝。命令行 `Invoke-WebRequest http://127.0.0.1:3000/login` 返回 200 |
| 后端真实 HTTP | 隔离 SQLite + 合成密钥启动 `127.0.0.1:18080`，调用 `/health`、未登录 `/api/me`、`/api/admin/users`、注册 | `/health` 200；受保护 API 401；SMTP 未配置时注册 503 `REGISTRATION_DISABLED` |

## 页面与流程覆盖矩阵

| 流程 | 静态入口 | 浏览器真实操作 | 状态 |
|---|---|---|---|
| 首页、公开工具目录 | `/`、`/tools`、`/public/tools/[toolSlug]` | 未执行 | 检查受阻/待补充 |
| 注册、验证、重发邮件 | `/register`、`/verify-email` | 未执行；隔离 HTTP 已确认 SMTP 缺失返回受控 503 | 部分确认 |
| 登录、退出、会话过期 | `/login`、认证 API | 未执行真实浏览器交互；未登录 API 401 已确认 | 部分确认 |
| 用户门户、Key、调用、通知 | `/portal`、`/api-keys`、`/calls`、`/notifications` | 未执行 | 检查受阻 |
| 工具文档、Gateway 调用 | `/tools/[toolSlug]`、`/quickstart` | 未执行真实上游调用；Mock 后端测试通过 | 部分确认 |
| 任务、文件、下载 | `/tasks`、`/files` | 未执行真实文件服务、下载和浏览器流式操作 | 检查受阻 |
| 管理审批、工具、导入、监控、审计 | `/admin/**` | 未提供隔离管理员账号，未执行 | 检查受阻 |
| 移动端、键盘、返回/刷新、重复点击、失败态 | 全部页面 | 未执行 Playwright 视口和交互矩阵 | 检查受阻 |

## 发现

### FE-001：`npm run lint` 脚本与当前 Next.js 版本不兼容

- 编号：FE-001
- 标题：前端 lint 命令无法执行
- 严重程度：P2
- 状态：已确认
- 影响范围：前端静态质量门禁、CI 发布检查。
- 触发条件：在 `frontend` 目录执行 `npm run lint`。
- 复现步骤：执行命令，输出 `next lint` 后报 `Invalid project directory provided, no such directory: ...\frontend\lint`，退出码 1。
- 预期行为：lint 命令应调用当前框架支持的 ESLint 入口并可作为发布门禁运行。
- 实际行为：脚本把 `lint` 当作 Next 项目目录参数，未执行任何 lint 规则。
- 证据：`frontend/package.json` 的 `"lint": "next lint"`；本阶段命令输出。
- 涉及文件和代码位置：`frontend/package.json`。
- 修复建议：按当前 Next 版本配置独立 ESLint 命令和依赖，或明确移除该脚本并在 CI 使用受支持的检查方式；补充 TypeScript/ESLint 门禁。
- 建议新增的回归测试：CI 执行 lint 并断言退出码为 0；对故意引入的规则错误断言失败。
- 是否阻断上线：建议阻断前端发布门禁，至少在确认替代静态检查前阻断。

### FE-002：关键浏览器流程没有可执行的项目级 E2E 证据

- 编号：FE-002
- 标题：注册、审批、Key、文件和管理流程未完成真实浏览器验证
- 严重程度：P2
- 状态：检查受阻
- 影响范围：所有用户可见流程、响应式布局、加载/错误/权限态和浏览器会话行为。
- 触发条件：需要验证真实页面交互、隔离测试账号、移动端视口或第三方依赖时。
- 复现步骤：检查 `frontend/package.json`、`frontend` 测试目录和仓库脚本，未发现 Playwright/Cypress 项目测试入口；本阶段浏览器连接本机页面受阻，且无测试账号/租户。
- 预期行为：每个关键流程至少有正常、空态、失败、权限不足、会话过期、重复点击、刷新/返回和移动端证据。
- 实际行为：仅完成 `npm run build` 和静态代码检查；历史 `.playwright-cli` 产物不是当前可复现测试入口。
- 证据：`frontend/package.json` 仅有 `dev/build/start/lint`；`docs/release-audit/07-existing-test-coverage.md`；本阶段浏览器连接结果。
- 涉及文件和代码位置：`frontend/app/**`、`frontend/components/**`、`frontend/lib/api.ts`。
- 修复建议：提供隔离 user/admin 账号、租户和 Mock 上游，建立 Playwright 流程及 CI 产物；覆盖窄屏、键盘和错误态。
- 建议新增的回归测试：登录/退出、审批状态、Key 创建/禁用、Gateway 文档、任务文件下载、管理员导入发布和越权矩阵。
- 是否阻断上线：建议阻断关键用户流程发布，直到至少完成隔离环境 E2E 烟测。

## 已确认的正向控制

- `frontend/lib/api.ts` 对非安全方法自动发送 CSRF Header，并使用 `credentials: include`；401 会触发统一未授权事件。
- 注册页会读取公开配置并在 SMTP 未配置时禁用提交按钮；后端隔离 HTTP 仍返回明确 `REGISTRATION_DISABLED`。
- 登录页区分待审批、拒绝、停用、锁定和邮箱未验证等后端错误码；前端限制不替代后端授权。
- `npm run build` 已在本地依赖上成功完成 30 个静态页面生成和 TypeScript 检查。

## 本专项汇总

- 发现总数：2（P2：2，P0/P1/P3：0）。
- 已确认问题：FE-001。
- 检查受阻：FE-002 及全部真实浏览器流程，原因是浏览器本机连接受阻、缺少隔离账号/租户/上游和项目 E2E 入口。
- 未执行：真实注册邮件、登录账号、管理员操作、文件上传/下载、Gateway 写调用和移动端交互。
