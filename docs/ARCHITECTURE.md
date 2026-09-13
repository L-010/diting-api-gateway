# 架构说明

## 产品边界

本地 MVP 从“门户型 API 管理”调整为“开发者 API 平台”：

- 公开层：平台介绍、工具介绍、注册申请。
- 用户层：API 文档、快速接入、全局 API Key、调用记录。
- 管理层：用户审批、调用监控、工具配置、OpenAPI/Swagger 导入、路由发布、审计。
- Gateway 层：`/gateway/{tool_slug}/{path:path}`，只允许已发布且启用的路由。

## 调用链路

```text
用户程序
  └─ X-API-Key: agw_...
      └─ 平台 Gateway
          ├─ Key 哈希校验、过期/禁用校验
          ├─ 用户 approved / active / must_change_password 校验
          ├─ 工具启用与已发布路由匹配
          ├─ 使用工具配置的唯一上游服务器
          ├─ 按发布路由策略执行限流、Content-Type、上传/下载大小、超时、幂等键和流式策略校验
          ├─ 按通用访问策略校验资源归属、过滤列表并登记资源
          ├─ 丢弃客户端凭证并注入可选的网关机器凭证
          ├─ 记录脱敏调用监控
          └─ 真实后端服务
```

## 用户生命周期

- 公开注册创建 `approval_status=pending`、`is_active=false` 的普通用户。
- 管理员审批通过后设置 `approval_status=approved`、`is_active=true`。
- 管理员拒绝后设置 `approval_status=rejected`、`is_active=false`，登录返回拒绝原因。
- 管理员停用不改变审批结论，但会使已有会话和 API Key 不可继续调用。
- CSV 导入保留为管理员批量开通通道，导入用户默认 approved 且 `must_change_password=true`。

## API Key 策略

新 Key 默认 `scopes="*"`，表示可调用全部已发布且工具启用的接口。旧 `tomodd:*` 与 `tool:{slug}:*` 仍保留兼容，避免破坏已有测试或历史 Key。

平台 API Key 与上游 Token 完全分离：

- 平台 Key：随机生成，只展示一次，数据库保存 HMAC 哈希和前缀。
- 上游 Token：使用 `APP_ENCRYPTION_KEY` 加密保存，只由后端转发器解密。
- 管理员 Key 治理：管理员可以按用户查看 Key 前缀、状态、作用域和最后使用时间，并禁用问题 Key；完整 Key 永不回显。
- Key 生命周期：Key 支持过期时间、用户主动禁用、管理员强制禁用、禁用时间、禁用操作者、禁用原因、最近使用时间和轮换提示；管理员可按 Key 前缀检索，并查看近 24 小时失败/限流信号。

## 开发者门户视图

用户侧不是管理后台的简化版，而是面向程序调用的脱敏开发者门户。当前只保留四个核心视图：

- API 文档：由 `/api/me/api-docs` 聚合全部已发布工具和接口，仅返回 Gateway 路径、方法、参数、请求体、响应模型、执行策略摘要和错误码，不返回真实上游服务器地址、上游路径、上游 Token 或实例拓扑。
- 快速接入：展示 Base URL、`X-API-Key`、全局 Key 模式、最小 curl/Python/JavaScript 示例、错误处理建议和 `request_id` 排障方式。
- 全局 API Key：展示 Key 前缀、状态、过期时间、最后使用时间、禁用状态和轮换提示；完整 Key 只在创建后显示一次。
- 调用记录：展示当前用户自己的 request_id、工具、接口、状态码、错误码、耗时、请求/响应流量和时间，用于和管理员监控联动排障。

旧 `/api/me/tools/{tool_slug}/endpoints` 为兼容保留，但普通用户返回中的 `upstream_path` 会被替换为 Gateway 路径，避免通过浏览器接口泄露后端路由映射细节。新页面优先使用 `/api/me/api-docs`。

## 工具接入与上游服务器

管理员可以创建工具、配置唯一上游部署服务器、测试连接、导入 OpenAPI/Swagger、预览差异、编辑接口并发布路由。已有工具的运行配置采用乐观版本号、提交前健康检查和加密历史快照；Gateway 始终使用工具当前生效的 `base_url` 作为真实后端根地址。

用户文档中的统一入口由数据库平台设置、`PUBLIC_GATEWAY_BASE_URL` 环境变量、浏览器当前域名依次回退决定。该值只用于公开 Gateway 根地址，不参与真实上游寻址，也不会修改 DNS、证书或反向代理。

新服务器上的新服务接入遵循“先白名单、再草稿、再导入、再确认策略、再发布”的顺序：后端只接受 `ALLOWED_UPSTREAM_HOSTS` 中的上游主机；管理员可通过 `/api/admin/platform/onboarding` 查看当前接入边界；OpenAPI/Swagger 解析只生成候选接口和差异，不直接暴露给用户；每个放行接口必须确认 `authenticated`、`owner`、`shared` 或 `admin_only` 策略，最终发布一次性上线全部放行接口。

管理员页面和审计事件只显示脱敏后的上游主机，真实 Token、认证 Header、查询参数和敏感路径不出现在普通展示字段中。工具详情页提供工具级连接测试；Gateway 真实转发失败时返回受控 `502/504` 并写入调用监控，不提供实例优先级、恢复、故障转移或多环境实例选择。

OpenAPI 导入继续拒绝远程 `$ref`、非法路径、重复操作和未支持方法。治理策略会识别管理接口、全局列表、删除类接口和常见资源 ID，并生成通用访问策略建议。全局列表只有管理员明确确认共享并填写审计原因后才能原样返回；创建者资源通过 JSONPath、路径参数或查询参数执行归属校验、登记、删除标记和列表过滤。

## 多工具接入治理与通用资源规则

平台把“接入新服务器上的新科研服务”视为受控治理流程，而不是简单保存一个上游 URL。管理员必须完成：

1. 工具基本信息：名称、slug、用途边界和说明。
2. 部署服务器：每个工具仅配置一个上游根地址。
3. 上游地址：仅允许命中 `ALLOWED_UPSTREAM_HOSTS` 的根地址，不允许查询参数、凭据或任意 URL 代理。
4. 认证方式：支持无认证、Bearer Token、Header API Key；上游 Token 只允许替换，不回显。
5. 健康检查路径：用于连接测试和运行排障。
6. OpenAPI/Swagger 文档来源：支持上游拉取、URL、文件上传和文本粘贴。
7. 接口导入预览：只生成候选接口、导入批次和差异项。
8. 风险分析：默认阻断 `admin/internal/debug/deprecated`、全局资源列表、删除类接口和资源归属不清晰接口。
9. 路由映射：逐条确认 Gateway 路径、上游路径、方法、Content-Type、大小、超时、流式策略和风险等级。
10. 发布确认与审计留痕：发布、排除、阻断、差异决策、批次确认都需要原因并写入 `admin_audit_events`。

平台不再维护工具专用资源适配器。每个接口通过领域无关规则描述调用前归属校验、父资源校验、成功响应后的资源登记、删除标记和列表过滤。规则保存和发布前必须编译 JSONPath；无法安全提取或过滤时返回带 `request_id` 的 502，绝不回退为未过滤响应。

第一版资源只属于创建者，不支持用户间共享。`shared` 是接口级共享数据模式，只能用于管理员明确确认的全局数据；`admin_only` 接口不会出现在普通用户门户中。所有已审批用户的全局 API Key 自动覆盖当前和未来上线工具。

## Gateway 数据面执行策略

每条 `published_routes` 都保存一份发布时的执行策略快照，避免 Gateway 运行时只依赖全局默认值。策略字段包括：

- 方法、Gateway 路径、上游路径；
- 允许的 `Content-Type`；
- 最大请求字节数、最大响应字节数；
- 单接口请求超时；
- 是否允许流式上传、是否允许流式下载；
- 访问模式、资源规则和策略版本；
- 是否要求 `Idempotency-Key`；
- 是否允许安全重试；
- 风险等级、路由版本和策略 JSON。

Gateway 执行时先匹配已发布且启用的路由，再按该路由策略做请求校验。所有入口失败和代理失败都会返回统一 `{code,message,request_id,details?}`，并写入 `gateway_requests`，包括缺少 Key、Key 禁用/过期、账号不可调用、工具停用、上游配置缺失、scope 拒绝、限流、未发布路由、Content-Type 拒绝、上传过大、上游不可达、上游超时、上游非 JSON 错误、响应过大和流式下载中断。管理员可以用 `request_id` 在调用监控中定位脱敏记录。

## 调用监控

`gateway_requests` 是本地 MVP 的运营监控数据源，记录用户、Key 前缀、工具、接口、Gateway 路径、上游路径模板、状态码、上游状态码、耗时、请求/响应字节、错误码和 request_id。查询参数、客户端地址和 User-Agent 仅保存脱敏或哈希信息，不保存请求正文、完整 API Key、Authorization、Cookie 或上游 Token。

后端同时输出结构化 JSON 日志。控制面请求日志统一携带 `request_id`、方法、平台路径、状态码和耗时；Gateway 调用日志额外携带 `user_id`、脱敏 `api_key_prefix`、`tool_slug`、`route_id`、`endpoint_id`、上游状态、错误码和流量字段。日志不保存请求正文、真实上游 Token、完整 API Key、Authorization 或 Cookie。

管理员监控接口分为四类：

- `/api/admin/monitor/summary`：面向页面总览，返回总调用、成功/失败、平均耗时、流量和最近失败。
- `/api/admin/monitor/requests`：面向 request_id 排障，返回可分页查看的脱敏调用记录。
- `/api/admin/monitor/metrics`：面向运维指标，返回请求量、成功率、错误率、限流次数、p50/p95/p99 和告警计数。
- `/api/admin/monitor/alerts`：面向实时告警，当前从 SQLite 调用记录实时计算 Key 失败率异常、工具 5xx 异常和上传/下载接近上限。

本地阶段不直接引入 Prometheus/OpenTelemetry，但字段命名按后续指标外送预留；生产环境建议把 `gateway_requests` 作为审计事实表，把实时指标、限流、熔断和告警状态迁移到 Redis/Prometheus/OpenTelemetry 组合。

## 数据与迁移

运行数据库使用 SQLite，并通过 Alembic 保持版本化迁移。当前迁移头为 `a2b3c4d5e6f7`（包含 Gateway 幂等记录表和用户名规范化唯一键）。后续迁移到 PostgreSQL 时应保持 SQLAlchemy 模型和 Alembic 迁移路径一致；生产级限流、会话和熔断状态应切换到 Redis。
