# 第七阶段：外部服务错误脱敏结果

## 执行证据

- 新增测试：`backend/tests/test_phase7_local_risks.py` 中 SMTP 持久化/审计脱敏、Worker 重试状态脱敏、结构化日志脱敏。
- 命令：`.venv\Scripts\python.exe -m pytest backend\tests\test_phase7_local_risks.py -p no:cacheprovider -q`，最终 `8 passed`；与多进程专项合并执行为 `10 passed`。
- 所有异常由 Mock SMTP、合成异常或现有 Fake HTTP 客户端产生；没有配置真实凭据或连接真实服务。

| 依赖/异常 | 响应与状态验证 | 脱敏结果 | 结论 |
|---|---|---|---|
| SMTP 未配置 | 既有回归确认注册 503 且不创建不可投递 Outbox。 | 不返回配置值。 | 已验证通过 |
| SMTP 认证/超时形式的异常 | Outbox 进入 retry/failed，审计保存错误类别。 | `SMTP_DELIVERY_FAILED:异常类型`，不保存密码、Token 或 Cookie。 | 已确认并修复 |
| TomoDD/上游 4xx、5xx、超时 | 既有 Mock Gateway 测试确认 4xx/5xx、502/504、失败记录和幂等失败重放。 | HTTP 不回显上游正文。 | 已验证通过 |
| 文件服务认证/超时/部分失败 | 既有 Mock 文件测试确认失败、退避、删除不误标成功和恢复状态。 | Worker retry 保存 `FILE_SYNC_FAILED:异常类型`。 | 已确认并修复 |
| 数据库/Worker 异常 | 未处理 HTTP 为通用 500；Worker 心跳和日志只写错误类别。 | JSON formatter 清理常见 Authorization、Token、密码、Cookie 和带凭据 URL；不输出栈正文。 | 已确认并修复 |

## 实现变更

- 新增 `app/redaction.py`，将外部错误持久化为类别和异常类型。
- SMTP Outbox、文件同步 retry、Worker 日志和心跳不再存储原始异常文本。
- JSON 日志 formatter 对消息和扩展字段做脱敏，异常仅记录异常类型；SMTP 连通性 API 不再回显服务端错误文本。

## 剩余项

- 仍需外部环境验证：真实供应商可能使用未覆盖的秘密格式、网关/日志采集链路的二次处理，以及真实邮件或上游已执行但响应丢失的副作用。
- 审计仍保留必要操作元数据；它不包含完整 Key、SMTP 密码或原始外部异常正文。
