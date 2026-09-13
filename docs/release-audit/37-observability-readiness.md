# 可观测性与就绪性

- 已验证：`/livez` 只表明进程可响应；`/readyz` 验证配置、数据库和 Alembic head，数据库不可用的单元测试返回 503。
- 已有：JSON 日志、管理员 metrics/alerts 只读查询、文件 worker 心跳。
- 未有：指标外送、日志脱敏运行时证明、告警接收人、确认/关闭状态机、值班路由、Prometheus/OpenTelemetry 配置。

建议模板（未部署）：告警规则应至少覆盖 `/readyz!=200`、5xx 比率、Gateway 上游不可用、邮件 outbox 最终失败、文件 worker 心跳超时、迁移失败、备份失败；每条包含等级、负责人、确认时间、关闭原因和恢复证据。
