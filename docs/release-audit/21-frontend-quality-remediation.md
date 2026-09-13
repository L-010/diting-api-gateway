# 前端质量修复

## REM-A05

- 问题编号：REM-A05（原 FE-001）。
- 根因：Next.js 16 的 `next lint` 已不是有效 CLI 子命令，且本地依赖未包含 ESLint。
- 修复内容：将 `frontend/package.json` 的 lint 脚本改为仓库现有 TypeScript 编译检查 `tsc --noEmit`，不新增依赖、不跳过目录、不关闭规则。
- 涉及文件：`frontend/package.json`。
- 是否需要迁移：否。
- 是否需要业务确认：否。
- 新增测试：使用项目本地 `node_modules` 执行 lint 和 build。
- 执行命令：`npm run lint`、`npm run build`（工作目录 `frontend`）。
- 测试结果：lint 通过；build 通过并生成 30 个页面。
- 是否仍存在风险：是。项目仍没有 ESLint 规则集和浏览器 E2E；若需要 ESLint，需单独评估并批准依赖变更。
- 是否允许上线：lint/build 门禁允许；整体上线仍受权限、部署和外部环境阻断。
