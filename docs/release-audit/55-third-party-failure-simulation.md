# 第六阶段：第三方失败模拟

## 隔离方式

- SMTP：Monkeypatch `smtplib.SMTP`；没有连接真实服务器。
- TomoDD/上游工具：替换 `httpx.AsyncClient` 为 `FakeAsyncClient`；不建立网络连接。
- 文件服务：HTTP Mock 与临时 SQLite 文件任务；不存取真实文件正文。
- 数据库：临时 SQLite、嵌套事务和并发线程；不使用 `data/api_gateway.db`。

| 依赖失败 | 测试证据 | 结果与副作用结论 |
|---|---|---|
| SMTP 未配置 | `test_public_registration_closes_without_smtp`。 | 注册、找回、邮箱变更能力关闭并返回 503，不创建无法投递的 Outbox。 |
| SMTP 连接失败 | `test_email_outbox_failure_is_recorded_without_rolling_back_registration`。 | 注册仍 201；Outbox 进入 `failed`，尝试数递增，用户记录存在；不会返回伪成功的“已投递”。 |
| SMTP 重试 | `services/email_delivery.py:deliver_due_email` 与现有 Outbox 测试。 | 失败写 `retry/failed`、退避时间和审计；成功后正文清除。认证失败和超时走相同异常路径，未使用真实凭证。 |
| 上游 4xx/5xx/无效响应 | `test_proxy_wraps_non_json_upstream_error_and_records_it`、`test_proxy_invalid_json_response_does_not_create_resource_mapping`。 | 返回受控错误，不暴露上游正文；无效 JSON 不创建资源映射。 |
| 上游超时、连接失败、重定向 | `test_proxy_timeout_maps_to_504_and_records_failure`、`test_proxy_network_failure_marks_instance_unavailable_and_records_it`、重定向测试。 | 504/502；记录 Gateway 失败和实例健康状态；禁止跟随重定向。 |
| 幂等调用失败 | `test_幂等键重放上游失败结果且不重复调用`。 | 确定 503 结果被缓存重放，上游只调用一次，避免重试重复副作用。 |
| 文件服务不可用/超时/删除失败 | `routers/files.py:stream_authorized_file`、`services/remote_files.py:delete_remote_file` 的 Mock 路径，`test_文件同步失败保留退避重试状态`。 | 下载事件为 failed；同步任务释放 lease、保留错误并退避重试；删除失败不标为 deleted。 |
| Worker 中断与重复消费 | `test_download_slots_enforce_limit_and_reclaim_expired_lease`、`test_并发领取同一文件任务只返回一个持有者`。 | 过期下载租约转 interrupted；条件更新只授予一个 Worker lease。 |
| 事务性异常 | 循环 OpenAPI 导入回滚、资源策略嵌套事务测试。 | 文档/资源登记失败不留下部分 endpoint、route 或 resource 映射。 |

## 未关闭项

- SMTP 服务端返回文本可能包含部署细节；当前仅管理员可见 Outbox 错误，尚未对真实供应商错误做脱敏验证。
- 不可确定上游是否已执行但网络结果丢失时，平台采取失败关闭；跨系统补偿语义需要业务规则。
- 多进程/多主机 SQLite、真实重试定时器和真实大文件流均未在本阶段联调。
