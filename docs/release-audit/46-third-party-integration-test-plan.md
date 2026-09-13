# 第三方隔离联调测试计划

## 总原则

本计划只允许使用隔离测试租户、沙箱地址、最小权限凭证和可清理的合成数据。不得连接生产 SMTP、TomoDD、文件上游，不得使用生产 token、用户邮箱、真实文件或真实业务 ID。脚本 `scripts/release_audit/third_party_connectivity.py` 只做 TCP/TLS 连通性检查，不代表认证、业务成功或副作用安全已验证。

每次联调记录：环境标识、凭证名称（不记录值）、测试数据前缀、开始/结束时间、请求 ID、返回状态类别、重试次数、脱敏日志证据、清理结果和负责人。

仓库已有 `backend/tests/test_gateway_proxy_mock.py`、`backend/tests/test_developer_platform.py` 中的本地 Gateway/SMTP mock 覆盖；本阶段新增的核心 E2E 模板也不触发第三方写入。由于真实超时、限流、未知结果和清理行为依赖供应商沙箱契约，本阶段不伪造“联调通过”，而是提供下列待执行脚本和场景清单。

## 通用环境变量和凭证边界

| 变量名 | 用途 | 最小要求 |
|---|---|---|
| `THIRD_PARTY_ENV` | 必须为 `isolated` | 禁止为 production |
| `THIRD_PARTY_CONNECTIVITY_ACK` | 显式确认只读连通性 | `true` |
| `SMTP_TEST_ENDPOINT` | SMTP 主机和端口 | 沙箱主机，禁止生产域 |
| `SMTP_TEST_USERNAME` / `SMTP_TEST_PASSWORD` | SMTP 沙箱认证 | 只能投递到测试收件箱 |
| `SMTP_TEST_FROM` / `SMTP_TEST_RECIPIENT` | 发件人和收件人 | 预先批准的测试域 |
| `TOMODD_TEST_ENDPOINT` | TomoDD 沙箱根地址 | HTTPS、域名白名单 |
| `TOMODD_TEST_TOKEN` | TomoDD 最小作用域 token | 仅测试项目、可撤销、短期 |
| `FILE_TEST_ENDPOINT` | 文件服务沙箱地址 | 测试租户、测试 bucket/目录 |
| `FILE_TEST_TOKEN` | 文件服务最小权限 token | 仅读/写测试前缀，禁止删除他人数据 |
| `TP_TEST_PREFIX` | 合成数据前缀 | 例如 `release-audit-<日期>-` |

## SMTP

### 最小凭证和连通性

- 沙箱 SMTP 主机、端口、TLS 模式、用户名、密码、发件人和测试收件人。
- 账号只能发送到批准的测试域，不能访问通讯录、生产队列或管理员设置。
- 先运行 `python scripts/release_audit/third_party_connectivity.py smtp`，只确认网络/TCP；再由邮件负责人执行 SMTP EHLO、STARTTLS、认证和 NOOP 测试。

### 场景

| 场景 | 执行 | 通过标准 |
|---|---|---|
| 成功路径 | 发送一封带 `TP_TEST_PREFIX` 的测试邮件，验证 Outbox 成功和收件箱到达 | 平台状态成功、邮件到达、无敏感值日志 |
| 超时路径 | 沙箱注入连接/读取超时 | 请求在配置超时内失败，Outbox 进入可观测重试，不阻塞用户事务 |
| 认证失败 | 使用已撤销/错误的测试密码 | 返回认证失败，凭证不重试为成功，不泄露密码 |
| 限流路径 | 在沙箱允许范围内触发发送限制 | 返回可识别限流状态，重试遵循批准退避，不重复投递 |
| 幂等与重试 | 重复相同测试事件、模拟响应丢失 | 不产生未批准的重复副作用，未知结果需人工核对 |
| 脱敏 | 检查应用、worker、代理、SMTP 客户端日志 | 无密码、token、完整邮箱令牌或邮件正文敏感字段 |

禁止：向真实用户发信、使用生产 From、复制生产邮件模板中的个人信息、把“SMTP 连接成功”当作邮件到达和恢复闭环证据。

## TomoDD

### 最小凭证和连通性

- `TOMODD_TEST_ENDPOINT` 必须是隔离 HTTPS 地址，且主机在 `ALLOWED_UPSTREAM_HOSTS` 白名单。
- `TOMODD_TEST_TOKEN` 只能访问健康、只读和测试作业接口；不得授予管理员、生产数据或删除权限。
- 先运行 `python scripts/release_audit/third_party_connectivity.py tomodd`，再用 TomoDD 沙箱提供的健康接口和合成资源 ID。

### 场景

| 场景 | 执行 | 通过标准 |
|---|---|---|
| 成功路径 | 查询健康、创建/查询一个可清理的合成任务（如沙箱支持） | 响应结构、请求 ID、资源归属和状态映射正确 |
| 超时路径 | 沙箱延迟或断开 | 平台返回明确上游超时，未知结果不自动重放有副作用操作 |
| 认证失败 | 使用过期或错误测试 token | 返回认证失败并记录脱敏错误码，立即停止后续操作 |
| 限流路径 | 按沙箱限制触发 429 | 平台保留 Retry-After（如有），退避有上限，不形成重试风暴 |
| 幂等与重试 | 相同幂等键并发/重复，模拟连接中断 | 同一请求不重复创建资源；未知结果进入核对流程 |
| 脱敏 | 检查 Gateway、worker 和错误日志 | 不记录 token、Authorization、完整上游 URL 查询参数 |

禁止：调用生产根地址、使用真实任务 ID、把错误重试到未知环境、通过修改白名单绕过 SSRF 校验。

## 文件服务

### 最小凭证和连通性

- `FILE_TEST_ENDPOINT` 指向隔离文件服务/对象存储，`FILE_TEST_TOKEN` 只能访问测试前缀。
- 测试数据必须带 `TP_TEST_PREFIX`，文件大小覆盖普通文件、边界大小和受限大小，不使用真实文件正文。
- 先运行 `python scripts/release_audit/third_party_connectivity.py file`，再执行文件服务提供的 manifest、HEAD/GET 和下载测试。

### 场景

| 场景 | 执行 | 通过标准 |
|---|---|---|
| 成功路径 | 拉取合成 manifest、同步元数据、只读下载 | owner 归属、大小、哈希、状态和下载授权一致 |
| 超时路径 | manifest/下载延迟或断开 | 上游超时可观测，下载槽释放，状态进入失败/待重试 |
| 认证失败 | 失效测试 token | 认证错误不泄露文件存在性，不继续下载 |
| 限流路径 | 沙箱触发 429/并发限制 | 平台返回明确错误，遵守退避和并发上限 |
| 幂等与重试 | 重复 manifest、重复下载授权、断点失败 | 不生成重复元数据或重复副作用，租约可回收 |
| 脱敏 | 检查日志、审计、错误响应 | 不记录签名 URL、Authorization、文件正文和完整路径 |

禁止：下载真实文件、调用删除接口、绕过文件大小/重定向校验、把临时文件复制演练当作真实文件恢复证明。

## 清理策略

1. 每项测试使用唯一 `TP_TEST_PREFIX`，优先调用沙箱提供的清理接口或由服务负责人在控制台清理。
2. 清理前先保存只包含计数、状态和证据编号的结果；不保存正文、token 或签名 URL。
3. 清理失败不允许重复扩大权限；记录遗留对象、责任人和最晚清理时间。
4. 平台数据库中的合成元数据按项目保留规则处理，Codex 不执行生产删除或清库。

## 通过门槛

每个供应商必须完成成功、超时、认证失败、限流、幂等/重试和日志脱敏六类场景；所有未知结果必须有业务规则和人工核对证据。仅连通性通过、Mock 通过或本地测试通过，不能解除外部依赖 P1 阻断。
