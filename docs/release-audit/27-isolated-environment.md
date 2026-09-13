# 第四阶段隔离环境

- 数据库：pytest 使用 `tmp_path` 下文件型 SQLite；部署演练使用系统临时目录。两者结束后自动释放，未使用 `data/api_gateway.db`。
- 合成主体：tenant-alpha 与 tenant-beta 标签各有两个 user，另有一个 admin。标签仅存在测试说明，生产模型没有 tenant/organization 字段。
- 假上游：集成测试以 `FakeUpstreamClient` 模拟 tomoDD 与文件上游，不建立网络连接；SMTP 使用 `smtp.test.invalid` 与 `EMAIL_TEST_MODE=true`，不启动邮件 worker。
- 隔离约束：`TOMODD_BASE_URL` 仅为 `127.0.0.1:19091`；不设置真实 Token、SMTP 密码或文件服务凭据。
- 可重复入口：`.venv\Scripts\python.exe -m pytest backend\tests\integration\test_isolated_authorization.py -p no:cacheprovider -q`。

测试夹具位于 `backend/tests/integration/test_isolated_authorization.py`，每次运行创建、使用并释放独立 SQLite 文件。
