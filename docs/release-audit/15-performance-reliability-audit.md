# 性能与运行可靠性专项审计

## 审计范围与测试边界

本专项静态检查数据库访问、限流、Gateway 转发、监控统计、分页、文件 worker 和资源释放；仅在项目 `.venv` 中运行现有后端测试，不进行压测，不访问生产或真实第三方。

执行命令：

```text
rg -n "select\(|\.all\(\)|offset\(|limit\(|AsyncClient|timeout|retry|with_for_update" backend/app
.venv\Scripts\python.exe -m pytest backend\tests -p no:cacheprovider -q
```

测试结果：`90 passed, 1 warning`（12.71 秒）。项目未发现 k6/Locust/压测脚本、性能目标、基准报告或 CI 配置，因此不将该结果解释为性能通过。

## 发现

### PERF-001：SQLite 数据库限流在并发/多实例下缺少原子递增保障

- 严重程度：P1（达到多实例或高并发目标时）
- 状态：静态证据支持，未做并发压测；容量目标待业务/运维确认。
- 影响范围：API Key/用户限流准确性、SQLite 写锁和横向扩展。
- 触发条件：两个请求同时读取同一窗口桶，或多个进程/实例共享 SQLite。
- 复现步骤：读取 `backend/app/services/rate_limit.py:14-30,33-51`；代码先 `select` 再新增/自增，事务提交由调用方完成。模型虽有唯一约束，但冲突和锁等待处理未在此函数中定义。
- 预期行为：并发请求严格按配额计数，冲突可重试并返回一致 429；多实例共享同一限流状态。
- 实际行为：SQLite/QueuePool 仅提供本地实现；高并发下可能出现锁竞争或唯一约束异常，跨实例限流一致性未验证。
- 证据：`backend/app/services/rate_limit.py:1-51`；`backend/app/models.py:679-687,704-711`；`docs/ARCHITECTURE.md:119-123` 明确建议生产迁移 Redis/Prometheus/Otel。
- 涉及文件和代码位置：上述文件。
- 修复建议：生产采用支持原子计数的 Redis/网关限流或 PostgreSQL 原子 UPSERT；定义锁等待、重试和降级策略，并以目标 QPS/并发压测验收。
- 建议新增的回归测试：多线程/多进程同时消费同一窗口，断言不超发、不出现未处理 IntegrityError，并验证实例间共享计数。
- 是否阻断上线：达到多实例/高并发目标时是；仅本地单进程 MVP 时记录为已知限制。

### PERF-002：监控分位数接口将窗口内全部耗时加载到 Python

- 严重程度：P2
- 状态：已确认代码路径；实际数据规模和延迟待验证。
- 影响范围：管理员监控 `/api/admin/monitor/metrics`、用户概览，内存和响应时间。
- 触发条件：窗口内 `gateway_requests` 记录数量增长，调用监控或概览频繁刷新。
- 复现步骤：读取 `backend/app/routers/admin.py:4266-4297`，`durations = [ ... session.execute(select(GatewayRequest.duration_ms).where(*conditions)).all()]`；用户概览 `portal.py:370-380` 还使用排序 + offset 计算 p95。
- 预期行为：分位数查询应有有界内存/预聚合，或对大窗口采用数据库近似/异步指标。
- 实际行为：管理员指标把所有匹配行物化为 Python 列表，再计算 p50/p95/p99；无硬上限。用户概览对大 offset 也会退化。
- 证据：`backend/app/routers/admin.py:4266-4297`、`backend/app/routers/portal.py:370-380`。
- 涉及文件和代码位置：上述行号。
- 修复建议：按时间桶预聚合延迟直方图/指标，或限制窗口与样本量并明确近似算法；增加索引和查询超时。
- 建议新增的回归测试：生成大于阈值的合成调用记录，验证内存/响应上限和结果误差；接口在超大窗口时返回可解释的限制。
- 是否阻断上线：建议上线前完成容量验证；超过容量目标时阻断。

### PERF-003：Gateway 路由匹配每次加载工具全部候选路由

- 严重程度：P2
- 状态：已确认代码路径；路由规模影响待验证。
- 影响范围：Gateway 首字节延迟、数据库读放大和 CPU 正则匹配。
- 触发条件：单工具发布大量路由或高 QPS 调用。
- 复现步骤：读取 `backend/app/services/gateway_proxy.py:86-100`；查询按 `tool_id/method` 取全部启用且已发布路由，然后逐条 Python 模板匹配，未按静态路径做数据库过滤。
- 预期行为：利用索引和静态前缀/哈希路由表缩小候选，复杂模板有界匹配。
- 实际行为：候选集 `.all()` 全量加载后逐条匹配；未发现路由数量上限或缓存。
- 证据：`backend/app/services/gateway_proxy.py:86-100`。
- 涉及文件和代码位置：同上。
- 修复建议：增加 `(tool_id, method, is_enabled)` 等索引，按路径前缀预筛选或构建内存只读路由索引，并在发布变更时失效缓存。
- 建议新增的回归测试：合成 1k/10k 路由测量 p95，确保匹配候选与内存有界且发布后索引一致。
- 是否阻断上线：路由规模超过容量目标时阻断；MVP 小规模需记录基准。

### PERF-004：部分管理列表接口无分页，可能一次性物化全表

- 严重程度：P2
- 状态：已确认静态风险；具体端点使用规模待验证。
- 影响范围：管理员工具、端点、版本、差异和 Key 页面，内存与响应体大小。
- 触发条件：数据表增长后调用无 `page/page_size` 的列表接口。
- 复现步骤：`rg` 显示 `admin.py` 中工具、端点、版本、差异等多处 `select(...).all()`；例如 `list_tools`、`list_endpoints`、`list_tool_versions`、`list_import_diffs` 均未统一分页。
- 预期行为：所有可增长集合有服务端分页、最大页大小和排序稳定性。
- 实际行为：部分接口直接 `.all()`；虽有若干 overview/catalog 接口分页，但未覆盖全部列表。
- 证据：`backend/app/routers/admin.py:2616-2622,3021-3024,3311-3313,3352-3360` 及 `rg -n "\.all\(\)"` 输出。
- 涉及文件和代码位置：`backend/app/routers/admin.py` 上述位置。
- 修复建议：统一分页响应契约和上限；导出/差异操作采用流式或异步任务，避免单请求大响应。
- 建议新增的回归测试：大于页大小的合成数据验证响应条数、稳定排序、has_more 和最大限制。
- 是否阻断上线：数据规模达到未定义阈值前建议整改；高规模生产应阻断。

### PERF-005：文件 worker 单批 20 个作业，SQLite 抢占在本地不使用行锁

- 严重程度：P2
- 状态：代码事实已确认；多进程行为待验证。
- 影响范围：文件同步吞吐、重复处理和 SQLite 锁竞争。
- 触发条件：多个 worker 或进程同时执行 `claim_sync_jobs`，尤其使用 SQLite。
- 复现步骤：读取 `remote_files.py:769-790`；查询最多 `.limit(20)`，非 SQLite 才添加 `with_for_update(skip_locked=True)`；SQLite 分支先读后批量更新状态。
- 预期行为：同一作业只能被一个 worker 租约持有，抢占冲突可安全重试。
- 实际行为：文档要求 SQLite 单 worker；若误启多个实例，可能重复领取或出现锁错误，未有进程级测试。
- 证据：`backend/app/services/remote_files.py:769-790`；`README.md:40-48`、`docs/distributed-files.md:13`。
- 涉及文件和代码位置：上述文件。
- 修复建议：SQLite 明确单实例互斥锁；生产数据库使用 `FOR UPDATE SKIP LOCKED`，并监控租约过期/重复执行。
- 建议新增的回归测试：两个隔离 worker 并发 claim 同一临时数据库，断言无重复作业；租约过期后可恢复。
- 是否阻断上线：启用文件能力且无法保证单 worker 时是。

### PERF-006：Gateway 非流式响应和 JSON 响应会完整缓冲到内存

- 严重程度：P2
- 状态：已确认代码路径；并发/响应大小目标待验证。
- 影响范围：大文件/大 JSON、并发请求的进程内存和 GC 压力。
- 触发条件：路由未启用流式下载，或上游返回 JSON 且接近 `max_response_bytes`。
- 复现步骤：读取 `gateway_proxy.py` 非流式分支，`bytearray()` 累积 `response.aiter_bytes()`；JSON 分支使用 `await response.aread()` 后再解析。
- 预期行为：在明确上限下有界缓冲；大响应优先流式或异步文件通道。
- 实际行为：虽有响应大小检查，但达到上限前每个并发请求均占用完整响应内存；未进行并发内存测试。
- 证据：`backend/app/services/gateway_proxy.py` 非流式响应处理（约 760 行起）及 JSON `await response.aread()` 分支；配置默认 `MAX_DOWNLOAD_BYTES=1GiB`。
- 涉及文件和代码位置：`backend/app/services/gateway_proxy.py`、`backend/app/config.py:35-36`。
- 修复建议：降低并按路由限制最大响应，统一流式传输/临时文件策略，设置全局并发和内存预算。
- 建议新增的回归测试：合成大响应并发请求，断言超过限制时及时中断且进程内存不线性失控。
- 是否阻断上线：达到大文件/高并发目标时是；小流量 MVP 需先完成容量基准。

### PERF-007：没有项目级性能目标、压测工具或基准门禁

- 严重程度：P1
- 状态：已确认缺口。
- 影响范围：无法判断 p95/p99、吞吐、锁竞争、下载并发和限流是否满足上线目标。
- 触发条件：任何生产容量承诺或多实例部署决策。
- 复现步骤：检查 `frontend/package.json`、脚本目录和测试目录；未发现 k6/Locust/pytest-benchmark、SLO 或 CI 性能门禁。
- 预期行为：上线前应有核心 API、Gateway、监控、并发写入和文件下载的基准及容量模型。
- 实际行为：仅有 90 个后端功能测试；本阶段未执行高负载测试，原因是缺少目标、生产拓扑和安全隔离上游。
- 证据：`docs/release-audit/07-existing-test-coverage.md`；`frontend/package.json`；`Get-ChildItem scripts`；本次 pytest 输出。
- 涉及文件和代码位置：测试与脚本目录。
- 修复建议：提供 QPS、并发、p95/p99、文件大小/增长率和 SLO；在隔离临时数据库与 mock 上游建立可重复压测，并纳入发布门禁。
- 建议新增的回归测试：固定数据集下的基线测试，包含并发读取/写入、限流、超时、分页、流式下载和 worker 恢复。
- 是否阻断上线：是，直到至少完成与目标对应的受控基准；功能测试通过不能替代性能证据。

## 总体判断

现有实现适合作为单机 SQLite MVP 的功能验证，不能据此证明多进程、高并发、大文件或长期运行可靠性。P1-001、P1-005、P1-007 为容量/部署条件性阻断；所有结论均未对生产地址施压。
