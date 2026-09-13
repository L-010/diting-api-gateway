# 性能测试说明

本地微基准入口为 `backend/tests/performance/run_local_gateway_baseline.py`。它只使用合成数据和 SQLite，不替代目标部署方式的 k6/Locust 压测；结果记录在 `docs/release-audit/33-performance-test-results.md`。
