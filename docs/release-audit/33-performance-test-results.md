# 性能与并发结果

命令：`.venv\Scripts\python.exe backend\tests\performance\run_local_gateway_baseline.py`

隔离 SQLite 的 200 次 owner 预检查结果：p50 `0.181ms`、p95 `0.332ms`、p99 `0.485ms`、均值 `0.224ms`。这只是单进程、内存 SQLite、无网络上游的微基准，不是 API 压测或生产容量证明。

k6、Locust 均未安装；未定义 QPS、并发、p95/p99、CPU、内存和数据库连接目标。SQLite 的多 worker/multi-instance claim、限流、worker 抢占和幂等竞争仍未验证，继续为 P1 阻断项。
