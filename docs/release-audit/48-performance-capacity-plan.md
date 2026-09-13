# 性能与容量执行计划

## 现状和不可推断项

阶段四的 owner 预检查微基准为内存 SQLite、单进程、无网络上游的 200 次测量：p50 `0.181ms`、p95 `0.332ms`、p99 `0.485ms`。这不是 API 压测、数据库容量、worker 能力或生产 SLO 证据。当前未定义 QPS、并发、数据量、CPU、内存、连接池、p95/p99 和错误率门槛。

SQLite 的明确限制：

- 单进程、单实例、低并发本地演练不能证明多 worker 安全。
- 多 worker 共享 SQLite 文件会面临写锁、忙等待、连接和文件系统一致性问题；必须默认视为未验证。
- 多实例共享 SQLite 文件不作为生产方案，除非基础设施和业务书面批准单实例例外，并完成目标存储、锁、备份、恢复和 worker=1 证据。
- 多 worker、多实例、容器横向扩展和跨节点 worker 抢占均不能从当前测试推断。

## 参数模板

使用 `scripts/release_audit/performance_probe.py` 对批准的只读路径执行，或由性能负责人使用 k6/Locust 等受控工具进行完整压测。探针默认只请求 `/livez`，不能代表核心 API 容量；必须替换为真实批准的只读 API。

```text
PERF_REQUESTS=<请求总数>
PERF_CONCURRENCY=<并发数>
PERF_DURATION_SECONDS=<持续时间>
PERF_P95_MS=<p95 门槛>
PERF_P99_MS=<p99 门槛>
PERF_ERROR_RATE=<错误率门槛>
PERF_THROUGHPUT_RPS=<吞吐目标>
PERF_CPU_PERCENT=<CPU 门槛>
PERF_MEMORY_PERCENT=<内存门槛>
PERF_DB_CONNECTIONS=<连接上限>
PERF_QUEUE_BACKLOG=<队列积压门槛>
PERF_PRODUCTION_READONLY_ACK=true  # 仅 production-readonly 模式
```

执行示例（值必须由业务/基础设施提供，示例不代表目标）：

```powershell
.venv\Scripts\python.exe scripts\release_audit\performance_probe.py --mode local --base-url http://127.0.0.1:8000 --path /livez --requests 100 --concurrency 5
.venv\Scripts\python.exe scripts\release_audit\performance_probe.py --mode test --base-url https://<测试域名> --path /api/public/tools --requests <N> --concurrency <C>
```

生产只读压测必须额外批准，禁止对写入、注册、上传、删除、邮件、TomoDD 作业创建和文件下载接口施压。

## 必测场景

1. **基线**：单实例、单 worker、空载到目标并发，记录 p50/p95/p99、吞吐、错误率、CPU、内存、数据库连接和日志量。
2. **容量**：逐步提高并发和数据量，记录达到 SLO 前的最大稳定吞吐以及饱和点。
3. **持续**：按 `<持续时间>` 运行，观察内存泄漏、连接泄漏、队列积压、慢查询和日志增长。
4. **故障**：隔离数据库连接、上游超时、SMTP/文件失败和 worker 重启，验证错误率、恢复时间和幂等行为。
5. **多进程**：仅在目标部署方案允许时测试 worker=1、worker>1、多实例；SQLite 方案必须把每种组合记录为独立结果，不得合并推断。
6. **数据规模**：使用接近上线的用户数、资源、调用、文件元数据和审计记录分布；合成数据不得包含真实个人信息。

## 门槛和判定

门槛必须在压测前由业务、基础设施和数据库负责人填写并签字：

- 目标吞吐：`<QPS/RPS>`。
- 稳定并发：`<并发用户/连接>`。
- p95：`<毫秒>`；p99：`<毫秒>`。
- HTTP 5xx/错误率：`<百分比>`。
- CPU、内存、数据库连接、慢查询、队列积压：`<阈值>`。
- 恢复时间：`<秒/分钟>`，与 RTO 对齐。

任何一个核心门槛失败、结果不可重复、压测数据未清理、观测指标缺失或只在 SQLite 微基准通过，都不能解除 P1 性能阻断。性能报告必须包括环境拓扑、版本、数据量、参数、原始摘要、异常解释和签署人。
