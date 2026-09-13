# 第六阶段：状态机与不变量测试

## 执行环境

- 命令：`.venv\Scripts\python.exe -m pytest backend\tests -p no:cacheprovider -q`。
- 数据库：pytest 临时 SQLite；上游、SMTP 和文件服务均为 Mock/stub。
- 最终结果：`120 passed, 1 warning`。warning 来自 Starlette 对 `httpx` 兼容层的弃用提示，不是测试失败。

## 已实现状态机

| 对象 | 合法转移与测试证据 | 非法/重复操作 | 原子性与不变量 |
|---|---|---|---|
| 注册申请 | `pending/inactive -> approved/active`、`pending -> rejected`：`test_developer_platform.py:test_public_registration_waits_for_admin_approval`、`test_rejected_registration_cannot_login`。邮箱令牌签发、替换和单次使用：`test_action_tokens_are_hashed_single_use_and_reissued_tokens_replace_old`。 | 已批准重复批准 409；重复注册 409；未验证、待审批、拒绝、停用登录均受控拒绝。 | 注册建立角色、令牌、Outbox、审计于同一事务；用户名规范键唯一，竞态冲突返回 409：`test_local_logic_invariants.py:test_注册遇到并发唯一冲突返回受控响应`。 |
| 会话 | 登录 -> 已认证；登出或改密/找回/管理员重置 -> 全部旧会话失效：`test_auth_session.py`、`integration/test_isolated_authorization.py:test_logout_and_password_change_invalidate_old_sessions`。 | 缺 Cookie 401；CSRF 不匹配 403；账户锁定 423。 | `session_version` 是服务端失效源；API 响应和后续 `/api/me` 一致。 |
| API Key | 创建 -> active，禁用 -> disabled，过期 -> Gateway unavailable：`test_key_expiry_contract_repeat_disable_and_admin_password_reset`、`test_api_key_audit_events_are_redacted`。 | 重复禁用 409；scope 不足 403：`test_api_key_scope_and_admin_management_are_server_enforced`。 | 完整 Key 只一次返回；审计和列表不含 secret；Key 状态、Gateway 响应与管理列表一致。 |
| OpenAPI 批次与路由 | 解析 -> diff 决策 -> 确认 `applied` -> 发布；配置版本可回滚：`test_import_batch_confirmation_requires_all_diff_decisions`、`test_admin_bulk_publish_publishes_all_accepted_import_diffs`、`test_tool_configuration_update_is_versioned_tested_and_reversible`。 | 未决策、策略未确认、阻断/冲突路径拒绝；已确认批次重复确认保持 200 幂等，不重复破坏状态。 | 循环/非法引用导入无写入：`test_cyclic_openapi_import_rolls_back_all_database_writes`；上游删除/阻断立即停用数据面路由。 |
| Idempotency-Key | 无记录 -> `processing` -> `completed` 或 `failed`；相同指纹完成后重放：`test_proxy_reuses_idempotent_result_and_forwards_key_once`。 | 缺 Key 422；同 Key 不同体 422；处理中 409；不可重放结果 409。 | 上游失败重放而不重复调用：`test_幂等键重放上游失败结果且不重复调用`；过期记录替换：`test_过期幂等键允许新的首次调用`。 |
| 文件同步任务 | `pending/retry/stale-running -> running -> pending/complete/retry`：`test_terminal_sync_job_is_rescheduled_for_periodic_verification`、`test_文件同步失败保留退避重试状态`。 | 非所有者文件、任务、调用关联读取均 404；删除未确认/不支持/下载中/运行任务中拒绝。 | 两线程争抢一个任务只有一个返回持有者：`test_并发领取同一文件任务只返回一个持有者`；失败释放租约；文件重新出现清除删除墓碑：`test_文件重新出现时清除删除墓碑`。 |
| 文件下载 | 授权 -> `started` -> `completed/failed/interrupted`：`test_download_slots_enforce_limit_and_reclaim_expired_lease`。 | 并发槽满 429；授权过期/非所有者 404；上游错误有受控 4xx/5xx。 | 过期租约转为 interrupted 并释放槽；下载事件、槽和审计结果一致。 |

## 未实现或仅上游定义的状态机

- 告警仅实时派生，代码没有确认、关闭、重复确认或非法跳转的持久状态机。
- 平台没有创建、执行、取消和业务重试任务的控制接口，只追踪已从 Gateway 响应登记的远程任务并重试文件同步。
- 文件上传正文不存于平台；上游上传的“部分成功、取消、覆盖”语义不能从本仓库推断。
- `shared`、管理员是否跨 owner 资源和细粒度 Key scope 的产品状态语义未定义。
