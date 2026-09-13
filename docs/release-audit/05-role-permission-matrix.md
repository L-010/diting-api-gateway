# 角色与权限初始矩阵

## 角色概念

- 明确角色：`user`、`admin`，由 `RoleName` 和 `seed_roles()` 证实。
- 用户状态：`pending/approved/rejected` 审批状态、`is_active`、`must_change_password`、登录锁定、Key 状态、工具状态和端点发布状态。
- 资源范围：`owner` 创建者资源、`shared` 共享数据、`authenticated` 已认证接口、`admin_only` 管理接口；由 `AccessPolicyRequest` 和资源策略服务证实。
- 未发现显式组织/租户/项目角色模型；是否需要多租户边界待业务确认。

## 初始矩阵

| 角色/状态 | 页面 | API | 数据可见范围 | 创建/修改/删除 | 管理操作 | 证据 | 待确认 |
|---|---|---|---|---|---|---|---|
| 公开访客 | 首页、注册、登录、公开工具目录 | `/api/public/*`、注册/登录/验证端点 | 公开工具脱敏信息 | 注册申请；不能访问用户资源 | 无 | `public.py:15-60`、前端公开页面 | 公开目录字段和注册频控 |
| 普通用户，已审批且已改密码 | 用户门户全部核心页面 | `/api/me/*`；带 Key 调用已发布 Gateway | 自己的资料、Key 元数据、调用记录、任务/文件；接口策略允许的 owner/shared 数据 | 创建/禁用自己的 Key；个人资料、密码、邮箱；删除自己可删文件 | 无 | `deps.py:27-56`、`auth.py`、`portal.py`、`files.py` | 共享数据范围、跨工具资源是否允许 |
| 普通用户，待审批/拒绝/停用 | 登录/错误状态页面 | 受 `get_current_user` 阻断；Gateway 受用户状态阻断 | 不应访问受保护数据 | 不应执行受保护写操作 | 无 | `deps.py:37-49`、Gateway 认证逻辑 | 拒绝原因是否对本人展示、恢复流程 |
| 普通用户，必须改临时密码 | 改密码页 | 仅允许认证与改密码相关入口；`require_not_password_change` 阻断门户 | 最小账户状态 | 修改密码 | 无 | `deps.py:53-56` | 管理员临时密码的有效期和通知 |
| 管理员 | 管理后台全部页面 | `/api/admin/*`，以及普通认证接口 | 全平台用户、工具、调用、文件和审计的管理视图，字段应脱敏 | 审批/停用/重置用户；禁用用户 Key；配置工具、策略、路由、文件状态和平台设置 | 导入、发布、阻断、监控、审计、配额和限流 | `require_admin`、`admin.py`、管理员前端页面 | 是否区分超级管理员、只读管理员、运维管理员 |
| 文件 worker | 无页面 | 无 HTTP 用户 API；内部 `file_worker.py` | 读取/更新同步作业、远程文件元数据、心跳 | 同步、校验、删除/过期处理 | 无人工管理权限 | `file_worker.py:39-89` | worker 运行身份、部署隔离和失败重试 |
| 邮件 worker | 无页面 | 无用户 API；内部 `email_worker.py` | 读取待发送密文邮件 | 投递并更新 outbox 状态 | 无 | `email_worker.py:13-26`、`email_delivery.py` | SMTP 凭证和投递审计要求 |

## 权限证据边界

- 管理 API 几乎都显式 `Depends(require_admin)`；文件和门户 API 多数使用 `require_not_password_change`，资源归属检查位于 `portal.py`、`files.py`、`resource_policies.py` 和 Gateway 代理服务。
- `scopes="*"` 默认允许全部已发布且启用工具接口，旧 scope 格式保留兼容；真实业务是否接受全局 Key 及未来工具自动继承，需业务确认。
- 当前矩阵是代码初始值，不是最终授权规范。未能从代码确认的项均保持“未知/待确认”。
