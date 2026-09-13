# 第七阶段：多进程 SQLite 结果

## 方法

- 命令：`.venv\Scripts\python.exe -m pytest backend\tests\test_phase7_multiprocess_sqlite.py -p no:cacheprovider -q`。
- 结果：`2 passed`。
- 每个用例创建独立临时 SQLite 文件，使用 `multiprocessing` 的 `spawn` 创建独立 Python 进程；注册和 Gateway 用独立 `TestClient` 检查 HTTP 响应，最终状态由新连接查询数据库。
- Gateway 上游为测试进程内本地 HTTP stub；没有真实 SMTP、TomoDD、文件服务或生产数据库。

## 实际结果

| 场景 | HTTP/进程结果 | 数据库最终状态 | 分类 |
|---|---|---|---|
| 两进程创建不同用户名 | 两次 201。 | 两个用户均存在。 | 已验证通过 |
| 两进程创建相同规范用户名 | 201 与 409。 | 仅一个 `username_key=samename`。 | 已验证通过 |
| 两进程相同 Idempotency-Key | 201 或受控 409，不出现 500；本次实际成功请求为 201。 | 仅一个 completed 幂等记录；本地 stub 仅收到一次。 | 已确认并修复 |
| 两进程领取同一同步任务 | 仅一个进程得到任务。 | 任务为 `running` 且只有一个有效租约。 | 已验证通过 |
| 数据库独占锁 | 锁释放后 HTTP 注册 201；实际等待至少 0.5 秒。 | 注册事务完整提交。 | 已验证通过 |
| Worker 崩溃后 | 进程在领取后以非零状态退出；租约过期后另一进程可重新领取。 | 同一任务可获得新的 `running` 租约。 | 已确认但属于架构限制 |
| journal 模式 | `PRAGMA journal_mode` 实际返回 `delete`。 | 项目未设置 WAL。 | 已确认但属于架构限制 |

## 修复

首次实验发现两个进程同时创建 `rate_limit_buckets` 时唯一约束会使 Gateway 请求变成 500。`services/rate_limit.py` 现对 SQLite 使用条件 UPSERT：成功计数、达到配额时 429，不再让唯一约束异常穿透。

## 限制与决策项

- SQLite 写入仍受单写者锁限制；本轮只验证短时锁等待和受控最终状态，不是多主机容量或高压吞吐验证。
- Worker 租约恢复提供至少一次处理，不能保证外部副作用恰好一次。外部任务/文件端的去重与补偿规则仍需业务确认。
- 没有修改生产 SQLite 配置，也没有把本地结果描述为生产容量结论。
