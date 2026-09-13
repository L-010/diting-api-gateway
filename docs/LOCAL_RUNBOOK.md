# 本地运行手册

## 前置条件

- Windows PowerShell。
- 项目根目录 `.venv` 中的 Python 依赖；不要依赖全局 pip 或全局 Python 包。
- Node 依赖仅位于 `frontend/node_modules`，并由 `frontend/package-lock.json` 锁定。
- tomoDD-SP 可独立运行在 Docker Desktop 中，默认只读预检地址为 `http://127.0.0.1:18000`。

## 启动与验收路径

1. 初始化依赖与数据库：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
   ```

2. 配置 `.env`，至少确认：

   - `TOMODD_BASE_URL=http://127.0.0.1:18000`
   - `TOMODD_UPSTREAM_TOKEN=<独立上游 Token>`
   - `ALLOWED_UPSTREAM_HOSTS=127.0.0.1,localhost`
   - `PUBLIC_GATEWAY_BASE_URL=https://api.example.com`（可留空并在管理后台配置；生产环境必须使用 HTTPS）
   - `APP_SECRET_KEY`、`APP_ENCRYPTION_KEY`、`API_KEY_PEPPER` 为强随机值

3. 创建或确认管理员：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\seed-admin.ps1 -Username admin -Password '<强密码>'
   ```

4. 启动服务：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\run-backend.ps1
   powershell -ExecutionPolicy Bypass -File .\scripts\run-frontend.ps1
   ```

   SMTP 配置完整时，另开窗口启动邮件 Outbox 进程：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\run-email-worker.ps1
   ```

   SMTP 留空不会阻止项目启动，但公开注册、邮箱变更和自助找回会关闭。管理员创建、CSV 导入和临时密码流程不依赖 SMTP。

   163 邮箱建议使用 `smtp.163.com:465` 和隐式 SSL：端口填写 `465`，不要勾选“使用 STARTTLS”。`587 + STARTTLS` 依赖服务器出口允许 SMTP 欢迎语和 TLS 升级；如果连接测试显示失败阶段为“连接欢迎语”，应先检查出口防火墙/安全组，再切换到 465。SMTP 密码必须使用 163 后台生成的客户端授权码，不是网页登录密码。

   邮件 Worker 由进程管理器或一个独立 PowerShell 窗口托管，生产环境只启动明确数量的实例；不要重复手动启动多个同类 Worker。配置修改后先执行“测试已保存配置”，再发送测试邮件，并在“监控告警”确认 Outbox 状态为“已发送”。达到最大尝试次数的旧失败记录不会自动恢复，修复连接后需要重新入队测试邮件。

5. 管理员工作台：

   - 登录后进入“用户审批”，审批公开注册用户或通过 CSV 批量开通用户。
   - 在用户总览中点击“Key”，查看用户 Key 前缀、状态、过期、最后使用、近 24 小时失败/限流信号，并可按前缀检索或禁用问题 Key。
   - 进入“监控告警”，按用户、工具、状态码、request_id 查看调用结果、耗时和流量；页面会同步展示错误率、p50/p95/p99、Key 异常和实时告警。
   - 进入“工具管理”，配置 tomoDD 或其它通用工具。
   - 接入新服务器服务前，先确认新服务域名或 IP 已加入 `.env` 的 `ALLOWED_UPSTREAM_HOSTS`；否则保存工具或导入 URL 会被拒绝。
   - 进入“新建工具”页查看当前白名单、支持 OpenAPI/Swagger 版本、支持方法和统一 Gateway 模式，按页面提示录入上游根地址、认证方式、健康检查路径和限流/超时。
   - 每个工具只配置一个部署服务器；已有工具从工作台的“运行配置”修改服务器、认证、限流、TLS 和超时。上游地址仍受 `ALLOWED_UPSTREAM_HOSTS` 约束，普通用户不可见真实地址，Token 始终不回显。
   - 在“平台设置 -> 网关与域名”配置用户文档使用的统一公开根地址。该操作不会自动配置 DNS、TLS 证书或 Caddy/Nginx，切换前必须先验证外部转发链路。
   - 通过 URL、上传或粘贴导入 OpenAPI/Swagger。
   - 检查接口作用、风险和路径冲突，为每个接口选择平台用户、创建者资源、共享数据或仅管理员策略；资源策略不完整时补充资源类型、ID 来源和 JSONPath。
   - 使用批量采用建议、批量共享、批量仅管理员或批量阻断完成全部判断。全局列表设为共享时必须二次确认并填写审计原因。
   - 最终点击“一键发布全部放行接口”；任一接口策略错误或路径冲突时整体失败，不会部分发布或跳过。
   - 发布后的接口会生成不可变策略快照，包括访问模式、资源规则、Content-Type、上传/下载大小、超时、流式策略、幂等键、重试、风险等级和路由版本。

6. 用户开发者门户：

   - 审批通过后登录。
   - 进入“全局 API Key”生成 Key，完整 Key 只显示一次；后续只能查看前缀、状态、过期时间、最后使用时间和禁用状态。
   - 进入“快速接入”复制 Base URL、确认 `X-API-Key` Header、查看全局 Key 说明、最小 curl/Python/JavaScript 示例、错误处理建议和 request_id 排障说明。
   - 进入“API 文档”按工具筛选全部已发布接口，只查看 `/gateway/{tool_slug}/...` 路径、方法、参数、请求体、响应示例和错误码，不查看真实上游地址。
   - 在自己的程序里携带 `X-API-Key` 调用 Gateway。
   - 进入“调用记录”查看 request_id、工具、接口、状态码、错误码、耗时、请求/响应流量和时间，并可复制 request_id 交给管理员定位。

## 新科研工具接入操作清单

1. 确认新服务已在独立服务器或本机端口运行，并只把需要平台访问的主机加入 `.env` 的 `ALLOWED_UPSTREAM_HOSTS`。
2. 管理员进入“新建工具”，查看页面左侧的白名单、支持方法和 OpenAPI/Swagger 版本。
3. 填写工具 slug、显示名称、唯一上游根地址、网关机器认证、健康检查路径、超时、限流和网络隔离声明。
4. 保存草稿后进入工具详情，先执行连接测试，确认唯一部署服务器可达。
5. 进入“导入新接口”，通过上游 `/openapi.json`、URL、上传或文本粘贴导入 OpenAPI/Swagger。
6. 在导入预览中检查所有接口作用、风险和策略建议，并对每个接口作出放行或阻断判断。
7. 对每条接口进入“精修”：确认 Gateway 路径、上游路径、Content-Type、请求/响应大小、超时、流式策略、风险标记和公开说明。
8. 对不应该公开的接口点击“排除接口”；对必须禁止的接口点击“阻断接口”；所有操作都要填写原因。
9. 如果上游文档更新，进入“同步 OpenAPI”，生成差异后批量或逐条确认策略、放行或阻断；仍有未判断接口时不能发布。
10. 发布全部放行接口。创建者资源必须配置归属规则；全局列表必须明确设为共享或配置列表过滤，不能隐式放行。
11. 发布后用用户侧 API 文档确认只展示 `/gateway/{tool_slug}/...`，再用测试 Key 调用，并在管理员“监控告警”中用 request_id 查验记录、指标和告警。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests
.\.venv\Scripts\python.exe -m alembic current
cd frontend
npm run build
cd ..
powershell -ExecutionPolicy Bypass -File .\scripts\verify-tomodd.ps1
```

调用日志默认保留 90 天。将以下命令配置为计划任务即可清理超期调用记录和已过期认证令牌：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\cleanup-retained-data.ps1
```

## 故障边界

- 上游未启动：管理端健康检查和 Gateway 返回受控 `502/504`，页面不得伪造成功。
- 用户未审批：登录返回 `PENDING_APPROVAL`。
- 用户被拒绝：登录返回 `REGISTRATION_REJECTED`。
- 用户停用：登录返回 `ACCOUNT_DISABLED`，已有会话和 Key 也不可继续使用。
- Key 被用户或管理员禁用：Gateway 返回 `401`。
- Key 过期：Gateway 返回 `401`，管理员只能查看前缀、过期状态和最近失败信号，不能恢复完整 Key。
- 入口鉴权失败、工具停用、限流、未发布路由、上传过大、上游错误等 Gateway 失败：均返回统一 `{code,message,request_id,details?}`，并写入调用监控。
- 后端日志为单行 JSON。排障时优先搜索同一个 `request_id`，控制面日志可看到平台路径、状态码和耗时；Gateway 日志可看到用户、Key 前缀、工具、路由、错误码和流量。
- 运维指标接口：管理员登录后访问 `/api/admin/monitor/metrics` 查看请求量、成功/错误率、限流次数、p50/p95/p99；访问 `/api/admin/monitor/alerts` 查看 Key 失败率异常、工具 5xx 和流量接近上限。
- 未发布路由：Gateway 返回 `404`。
- 超过上传/下载限制：Gateway 返回 `413`。
- 非法 Content-Type：Gateway 返回 `415`。
- 资源规则提取、登记或列表过滤失败：Gateway 返回带 `request_id` 的受控 `502`，不返回未过滤数据。
- 跨用户访问创建者资源：Gateway 返回 `404`，并且请求不会转发到上游。
- 上游连接测试失败：管理端展示工具上游不可达；Gateway 仍固定转发到该工具的唯一部署服务器，并在真实调用失败时返回受控 `502/504`。
