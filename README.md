# API Gateway 开发者平台 MVP

生产服务器部署只参考 [SERVER_DEPLOY_COMMANDS.md](SERVER_DEPLOY_COMMANDS.md)。当前指南是无 TLS 的 HTTP 直连流程，包含配置生成、部署、验证、管理员初始化和故障排查；其他部署文档不作为本次服务器部署依据。

本项目是一个本地可执行的开发者 API 平台 MVP：公开用户提交注册申请，管理员审批后，用户生成一个全局 API Key，并通过统一 Gateway 调用全部已发布工具。管理员可以接入多工具、配置每个工具唯一部署服务器，导入 OpenAPI/Swagger，发布路由，并查看调用监控与审计。

核心调用模型：

```text
用户程序（携带 X-API-Key） -> 平台 Gateway -> 真实后端服务
```

工具上游由管理员配置并受主机白名单约束。公开页面和用户门户不会展示真实上游地址、部署方式或凭据。

## 本地启动

1. 在 PowerShell 执行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
   ```

2. 编辑 `.env`，填写 `TOMODD_UPSTREAM_TOKEN`，并确保密钥、数据库路径和 `ALLOWED_UPSTREAM_HOSTS` 配置完整。不要提交 `.env`。

3. 初始化管理员：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\seed-admin.ps1 -Username admin -Password '<符合策略的强密码>'
   ```

   该命令幂等，不回显密码；管理员账号不会通过 CSV 或公开注册创建。

4. 启动后端和前端：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\run-backend.ps1
   powershell -ExecutionPolicy Bypass -File .\scripts\run-frontend.ps1
   ```

   可先通过 `.env` 配置 SMTP，也可以登录管理后台后在“平台设置 -> 邮箱配置”中保存。数据库配置会即时覆盖环境变量。配置完成后，另开一个 PowerShell 启动可选邮件进程：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\run-email-worker.ps1
   ```

   配置了任务或制品能力的环境还必须启动文件同步与清理进程；SQLite 本地环境只能启动一个：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\run-file-worker.ps1
   ```

   分布式文件契约、生命周期和工具适配说明见 [docs/distributed-files.md](docs/distributed-files.md)。

   未配置 SMTP 时后端照常启动，但公开注册、验证邮件、邮箱变更和自助找回会准确显示为关闭；管理员创建、CSV 导入和强制临时密码仍可用。

5. 打开 `http://127.0.0.1:3000`。

## 首版闭环

- 用户：邮箱验证注册 -> 管理员审批 -> 登录 -> 生成全局 API Key -> 复制接口文档示例 -> 在自己的程序里调用 Gateway -> 查看调用与任务 -> 处理通知和账户安全。
- 管理员：审批用户 -> 设置平台邮件和新用户默认策略 -> 按用户覆盖每分钟请求上限 -> 查看/禁用用户 Key -> 配置工具与部署服务器 -> 导入 OpenAPI/Swagger -> 处理风险/差异 -> 发布路由 -> 查看调用监控与审计。
- 所有工具统一使用访问策略和通用资源规则，不再维护 tomoDD 或其它工具专用适配器。

## 开发者体验

- 用户侧统一为使用概览、工具与接口、API 密钥、调用记录、任务中心、通知和账户设置。
- API 文档通过 `/api/me/api-docs` 获取脱敏聚合数据，只展示 `/gateway/{tool_slug}{gateway_path}`，不展示真实上游服务器地址、上游路径、上游 Token 或实例拓扑。
- API 文档展示方法、Gateway 路径、参数、请求体示例、响应示例、错误码，并支持按工具筛选、关键词搜索和复制 curl/Python/JavaScript 示例。
- 快速接入页展示 Base URL、`X-API-Key`、全局 Key 说明、最小可运行示例、错误处理建议和 `request_id` 排障说明。
- 全局 API Key 页面支持创建、一次性复制完整 Key、复制前缀、查看状态、最后使用时间、过期时间和禁用；完整 Key 永不恢复。
- 调用记录页按排障字段展示 request_id、工具、接口、状态码、错误码、耗时、请求/响应流量和时间。
- 平台设置页集中管理用户侧统一网关根地址、SMTP 和新用户默认每分钟请求上限。数据库中的网关地址优先于 `PUBLIC_GATEWAY_BASE_URL`；两者均为空时用户文档使用当前访问域名。SMTP 密码只保存密文且不回显；默认值只复制给保存后的新注册或新导入用户。
- 用户档案的“限制设置”可以覆盖单个用户的每分钟请求上限；`0` 表示不增加用户级限制，工具自身限流继续生效。

## 管理员部署服务器

- 每个工具只配置一个上游部署服务器，真实地址由后端保存，管理端只展示脱敏后的主机。
- 新服务器上的新服务接入流程：先把新服务主机加入后端 `ALLOWED_UPSTREAM_HOSTS`，再在“新建工具”页保存工具草稿，执行连接测试，导入 OpenAPI/Swagger，检查风险和路由映射，最后发布安全接口。
- “新建工具”页会展示当前允许上游主机、支持规范版本、支持方法和统一 Gateway 模式，帮助管理员判断新服务器是否满足接入条件。
- 已创建工具可从工具工作台进入“运行配置”，修改唯一上游根地址与端口、机器认证、工具级限流、TLS、重试、超时和网络隔离声明。在线路由相关变更会在提交时重新探测候选上游，失败不覆盖线上配置；保存使用版本号防止并发覆盖，并保留加密历史快照用于回滚。
- 工具详情页支持“一键发布全部放行接口”：发布前校验全部放行策略和路径冲突，成功时不跳过任何放行接口，失败时不修改任何线上路由。
- 工具详情页只提供工具级连接测试，不提供新增实例、优先级、启用/停用、恢复等实例池操作。
- Gateway 调用时固定转发到该工具当前生效的唯一上游根地址；工具级限流按“API Key + 工具 + 分钟”隔离，上游不可达或超时会返回受控 `502/504` 并写入调用监控。
- 管理端只展示脱敏后的上游地址，端口、路径、查询参数、Token 和认证 Header 不作为普通展示字段输出。

## 多工具接入治理

- 管理员接入新服务器上的新科研服务时，按标准流程推进：工具基本信息 -> 部署服务器 -> 上游地址 -> 认证方式 -> 健康检查路径 -> OpenAPI/Swagger 文档来源 -> 接口导入预览 -> 风险分析 -> 路由映射 -> 发布确认 -> 审计留痕。
- `/api/admin/platform/onboarding` 会返回统一 Gateway 模式、允许上游主机、支持规范版本、支持方法和访问模式；“新建工具”页面会直接展示这些边界。
- OpenAPI/Swagger 导入后不会自动发布全部接口。`admin/internal/debug/deprecated`、全局资源列表、删除类接口和资源归属不清晰接口默认进入阻断治理。
- 工具详情页展示“标准接入治理清单”、待确认访问策略、最近导入批次和已发布/候选/排除/阻断统计。
- 同步差异页支持生成差异、逐条接受/排除/阻断/延后，并在确认导入批次时由后端阻止仍未决策的 blocker 差异。
- 单接口精修页支持编辑 Gateway 路径、Content-Type、限流、超时、风险标记、公开说明，并可显式排除或阻断接口；相关操作均写入管理员审计。
- 访问策略是发布门禁：无状态接口使用 `authenticated`，用户资源使用 `owner`，全局共享数据使用 `shared`，管理接口使用 `admin_only`。
- `owner` 规则通过路径参数、查询参数或 JSONPath 校验和登记 dataset/job/artifact 等任意资源；提取或过滤失败时返回 502，不返回未过滤数据。

## 安全约束

- 公开注册必须验证邮箱；验证完成后才进入管理员待审批队列，审批通过后才可登录和创建 Key。
- 新 Key 默认作用域为 `*`，可调用全部“已发布且工具启用”的接口；旧 `tomodd:*` 和 `tool:{slug}:*` 仍兼容。
- 平台 API Key 只展示一次，数据库仅保存带 pepper 的哈希、前缀、状态和过期时间。
- 管理员只能查看用户 Key 前缀、状态和最后使用时间，可以禁用问题 Key，但不能查看或恢复完整 Key。
- 上游 Bearer Token 使用 `APP_ENCRYPTION_KEY` 加密保存，只由后端转发器解密使用。
- 平台 SMTP 密码同样使用 `APP_ENCRYPTION_KEY` 加密保存，管理员接口只返回是否已配置，不返回密文或原文。
- Gateway 只允许已发布路由，不提供任意 URL 代理，不展示真实上游地址或 Token。
- 每条已发布路由都有独立执行策略：访问模式、资源规则、Content-Type、上传/下载大小、超时、流式上传/下载、幂等键、重试、风险等级和路由版本。
- 调用监控只记录脱敏元数据、Key 前缀、状态码、耗时、流量和 request_id，不保存请求正文或敏感 Header。
- Gateway 入口失败和代理失败都会返回统一 `{code,message,request_id,details?}` 并写入调用监控，管理员可按 request_id 排查。
- 后端输出结构化 JSON 日志，控制面访问日志和 Gateway 调用日志统一携带 `request_id`、状态码、耗时等字段；Gateway 日志额外携带脱敏 Key 前缀、工具、路由、错误码和流量。
- 管理端提供 `/api/admin/monitor/metrics` 与 `/api/admin/monitor/alerts`，用于查看请求量、成功/错误率、p50/p95/p99、限流次数、Key 异常和实时告警。
- API Key 生命周期包含创建、用户禁用、管理员禁用、过期时间、禁用时间/原因、最近使用、按前缀检索和异常调用信号；完整 Key 永不回显或恢复。
- OpenAPI 导入为全局资源列表、删除类和资源型接口生成风险与访问策略建议；管理员必须逐项放行或阻断，全局列表原样共享需要二次确认。
- 统一错误格式：`{code,message,request_id,details?}`。

更多设计与排障步骤见 [架构说明](docs/ARCHITECTURE.md) 和 [本地运行手册](docs/LOCAL_RUNBOOK.md)。
