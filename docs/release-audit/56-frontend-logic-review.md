# 第六阶段：前端逻辑复核

## 实际验证

| 检查 | 命令/范围 | 结果 |
|---|---|---|
| 路由类型生成 | `frontend\node_modules\.bin\next.cmd typegen` | 通过。 |
| TypeScript 类型检查/lint | `npm run lint`，脚本为 `tsc --noEmit` | 通过。先前与 build 并行时 `.next/types` 被重建造成缺文件；顺序执行后未复现。 |
| 生产构建 | `npm run build` | 通过，30 个页面生成。 |
| API 客户端 | `frontend/lib/api.ts` 静态审查 | 统一 `credentials: include`；非安全方法带 CSRF；401 广播给 Shell；错误包含服务端 message/request_id。 |
| 会话与权限页面 | `components/portal-shell.tsx` | 读取 `/api/me`；401 跳登录，403 显示无权限，强制改密跳改密页；不是靠导航隐藏。 |
| 高风险表单与重复提交 | 登录、注册、改密、找回、重置、API Key、文件操作、工具导入/发布/设置页面 | 主要写操作有 loading/busy 禁用、`try/catch/finally`、成功后刷新或状态回滚；API Key 完整值 60 秒清除。 |
| 加载、空、失败状态 | calls、tasks、files、notifications、admin files/monitor/tools 及共用 `page-state.tsx` | 已有加载、空列表、重试和错误展示；表格操作提供 disabled 状态。 |
| 枚举/字段对齐 | `frontend/lib/remote-files.ts`、`frontend/lib/developer-docs.ts`、`frontend/lib/admin-tools.ts` 对照 `schemas.py`/`models.py` | 文件状态、access mode、route policy 和 Gateway 调用字段可对齐；前端 API Key 页面明确显示当前仅全局 Key。 |

## 静态审查限制与未知项

- 本阶段未启动浏览器 E2E；没有断言真实 DOM 焦点、移动断点、网络取消或浏览器下载中断。
- `verify-email` 页面异步请求没有显式取消标记；页面离开后的状态更新风险低，但未以组件测试验证。
- 前端没有单元测试框架，未为本阶段新增依赖或为测试重构页面；高风险流程以 TypeScript、构建和服务端 HTTP 契约回归为主。
- `shared`、告警确认/关闭、任务取消/业务重试没有可供前端正确实现的后端契约，不能宣称前端已覆盖这些产品流程。
