# API、输入校验与安全专项审计

## 审计范围与方法

- 检查对象：FastAPI 入口/异常边界、Pydantic 模型、Gateway 代理、OpenAPI 导入、上游 URL 校验、文件/CSV 上传和日志字段。
- 只读静态检查：`backend/app/main.py`、`config.py`、`schemas.py`、`routers/admin.py`、`services/openapi_import.py`、`services/gateway_proxy.py`。
- 隔离运行：使用 `.venv` 直接调用解析器、URL 校验和 Pydantic 模型；执行 Gateway/开发者平台相关 Mock 测试。未调用真实上游、SMTP 或文件服务。

## 发现

### APISEC-001：OpenAPI YAML 锚点/别名可触发递归异常并可能耗尽资源

- 严重程度：P1
- 状态：已确认（异常触发已复现；资源耗尽规模和生产影响待验证）
- 影响范围：管理员 OpenAPI 文档解析接口 `/api/admin/tools/{tool_id}/openapi/parse`、上传接口及同步流程。
- 触发条件：管理员提交包含 YAML 自引用锚点/别名的文档，例如：
  ```yaml
  openapi: "3.0.0"
  info: {}
  paths: &p
    /x:
      get: {responses: {"200": {description: ok}}}
      self: *p
  ```
- 复现步骤：
  1. 在项目目录执行 `.venv\\Scripts\\python.exe`，调用 `load_openapi_document` 和 `parse_openapi_document_detailed`。
  2. `yaml.safe_load` 成功构造循环对象；随后 `_walk_for_remote_refs`、`_resolve_local_refs` 或 `_operation_text` 递归遍历时抛出 `RecursionError: maximum recursion depth exceeded`。
  3. 当前 `parse_openapi` 仅捕获 `OpenApiImportError`，未捕获 `RecursionError`；统一异常处理会记录异常并返回 500。重复请求可持续消耗 CPU/线程。
- 预期行为：恶意或异常 YAML 应在大小/节点/深度限制内返回 422，不能让递归异常穿透到 500，也不能导致进程资源耗尽。
- 实际行为：循环别名输入在解析后触发未处理 `RecursionError`；深层非循环别名也可能超过 Python 递归深度。
- 证据：`backend/app/services/openapi_import.py:47-58,17-27,104-126`；`backend/app/routers/admin.py:3171-3185`；隔离脚本实际输出 `RecursionError maximum recursion depth exceeded`。
- 涉及文件和代码位置：`openapi_import.py` 的 `_walk_for_remote_refs`、`_resolve_local_refs`、`routers/admin.py` 的 `parse_openapi`/`upload_openapi`。
- 修复建议：解析前拒绝 YAML 别名/锚点或使用带别名计数、对象 ID 访问集合和最大递归深度/节点数的安全遍历；统一捕获 `RecursionError` 并返回 422；对导入接口增加请求级限流和并发上限。
- 建议新增的回归测试：自引用锚点、互相引用锚点、超过深度/节点限制的文档均返回 422；合法本地 `$ref` 仍可导入；异常不会写入内部堆栈给客户端。
- 是否阻断上线：是。管理员可调用的导入入口在未修复前可被构造输入打成 500/资源耗尽。

### APISEC-002：新建工具路径未校验 `token_label`，可保存非法/控制字符 Header 名

- 严重程度：P2
- 状态：已确认（输入接受和 Header 构造已复现；真实网络栈是否形成可利用注入待验证）
- 影响范围：管理员 `POST /api/admin/tools` 创建/更新工具，及后续所有向该工具上游发送的请求。
- 触发条件：提交 `auth_type="header_api_key"` 且 `token_label` 含空格、CR/LF 或其他非法字符。`ToolConfigRequest` 只限制长度；创建路径未调用 `_validate_tool_candidate`。
- 复现步骤：
  1. `ToolConfigRequest.model_validate` 接受 `token_label="Bad Header"` 和 `token_label="X-Test\\r\\nX-Evil: 1"`。
  2. `create_or_update_tool`（`admin.py:2681-2754`）直接将 `payload.token_label` 写入 `ToolUpstream.token_label`。
  3. `upstream_headers`（`gateway_proxy.py:408-418`）将其作为字典键；隔离调用 `httpx.Request` 可接受包含空格/CRLF 的 Header 名，最终行为依赖 HTTP 客户端/代理。
- 预期行为：所有写入 `ToolUpstream.token_label` 的路径均只接受 RFC 7230 token 字符集，拒绝控制字符、空格、伪造头分隔符。
- 实际行为：候选配置更新路径有正则校验（`admin.py:928`），但首次创建/旧工具编辑路径绕过该校验，非法名称可持久化并进入请求构造。
- 证据：`backend/app/schemas.py:432-455`（无 token_label 格式校验）；`backend/app/routers/admin.py:2681-2754`；`backend/app/routers/admin.py:916-941`（仅候选路径校验）；`backend/app/services/gateway_proxy.py:413-417`；隔离脚本显示两种非法名称均被接受。
- 涉及文件和代码位置：`schemas.py`、`routers/admin.py`、`services/gateway_proxy.py`。
- 修复建议：在共享 Pydantic 字段验证器或单一服务函数中统一校验 Header 名；创建、更新、候选、回滚和迁移读取均复用；发送前再次拒绝控制字符并记录安全事件（不记录 Token）。
- 建议新增的回归测试：空格、冒号、CR/LF、非 ASCII 控制字符均返回 422；合法 `X-Upstream-API-Key` 可正常构造；所有配置入口行为一致。
- 是否阻断上线：否（当前需要管理员权限，主要风险为请求失败/潜在 Header 注入）；若上游链路包含可受攻击的代理或跨信任边界，应升级为 P1 并阻断。

### APISEC-003：上游 URL 和 OpenAPI 来源已实施主机白名单与路径约束

- 严重程度：P3（正向控制项，无缺陷）
- 状态：已确认
- 影响范围：工具上游配置、OpenAPI URL 导入和候选健康检查。
- 触发条件：管理员提交任意 URL、凭据、查询参数、片段或路径穿越。
- 复现步骤：隔离调用 `validate_upstream_url` 和 `validate_openapi_source_url`：外部主机、查询参数、凭据、`..` 路径均被拒绝；白名单根地址通过。
- 预期行为：禁止任意 URL 代理和 SSRF；仅允许 `ALLOWED_UPSTREAM_HOSTS` 中的 HTTP(S) 根地址/受控文档路径。
- 实际行为：`validate_upstream_url` 要求纯主机根地址；OpenAPI 来源要求白名单主机并拒绝凭据、查询、片段和路径穿越；代理关闭自动重定向。
- 证据：`backend/app/services/gateway_proxy.py:44-53`；`backend/app/routers/admin.py:285-294,322-333`；`config.py:64-75`；隔离脚本校验结果。
- 涉及文件和代码位置：`gateway_proxy.py`、`admin.py`、`config.py`。
- 修复建议：生产环境将白名单交由受控配置/网络策略管理，并增加 DNS 解析与重绑定防护；继续保持 `follow_redirects=False`。
- 建议新增的回归测试：IPv4/IPv6、端口、重定向、DNS 别名、编码绕过和路径穿越样例均按策略拒绝或明确允许。
- 是否阻断上线：否；生产白名单和网络隔离仍需部署确认。

### APISEC-004：请求体、文件和分页输入具备显式大小/范围限制

- 严重程度：P3（正向控制项，无缺陷）
- 状态：已确认
- 影响范围：Gateway 请求体、响应体、OpenAPI/CSV 导入、文件下载和列表分页。
- 触发条件：超大 body、超大文档/CSV、非法分页或超长查询参数。
- 复现步骤：静态检查 `read_bounded_request_body`/`bounded_request_stream`、OpenAPI 2 MiB、CSV 1 MiB、Pydantic `Field` 和 Query `ge/le` 限制；现有 Gateway 边界测试覆盖 413/415/响应大小。
- 预期行为：超过限制返回 413/422，不将无限输入读入内存。
- 实际行为：Gateway 读取和流式上传均按路由上限；OpenAPI、CSV 有独立上限；文件下载有工具/全局上限和并发槽位；分页最大 100。
- 证据：`gateway_proxy.py:155-170,483-512`；`admin.py:112,2091-2101,3209-3216`；`schemas.py` 多处 `Field`/`Query`；`files.py:393-397`。
- 涉及文件和代码位置：`gateway_proxy.py`、`admin.py`、`files.py`、`schemas.py`、`backend/tests/test_gateway_proxy_mock.py`。
- 修复建议：为所有新增端点建立统一输入上限审查清单；增加压测验证 SQLite/内存行为。
- 建议新增的回归测试：无 `Content-Length` 的分块上传、声明值欺骗、边界字节数、超大分页和多文件并发。
- 是否阻断上线：否。

### APISEC-005：统一异常响应不会向客户端返回堆栈，但递归异常仍会记录服务端堆栈

- 严重程度：P2
- 状态：待验证
- 影响范围：所有未处理异常及日志接收系统。
- 触发条件：解析器、上游客户端或 worker 抛出未预期异常。
- 复现步骤：`main.py` 的 `Exception` 处理器返回固定 `INTERNAL_ERROR` 和 request ID，不返回 `str(error)`；但 `http_logger.exception` 会写完整堆栈。需在隔离环境注入包含 Token/个人数据的异常文本并检查日志落盘/采集配置。
- 预期行为：客户端不泄露内部路径/堆栈；服务端日志经过敏感字段脱敏并有访问控制。
- 实际行为：客户端边界已固定；日志脱敏和生产采集链路本阶段未启动验证。
- 证据：`backend/app/main.py:82-156`；`services/observability.py`、`file_worker.py` 日志调用；基线 P2-004。
- 涉及文件和代码位置：`main.py`、`services/observability.py`、`file_worker.py`。
- 修复建议：对异常文本执行统一敏感模式过滤；日志 schema 仅允许错误类型/代码和截断摘要；增加 secret-scan 与隔离日志测试。
- 建议新增的回归测试：异常消息含 API Key、Authorization、Cookie、SMTP 密码样例时，日志不得出现原文；HTTP 响应仍为固定内部错误。
- 是否阻断上线：建议上线前验证；当前不直接阻断。

## 输入模型与安全配置结论

- `ToolConfigRequest`、`ToolConfigurationCandidateRequest` 使用 `extra="forbid"`；其他多数模型默认忽略未知字段，当前路由按白名单字段赋值，未观察到直接批量赋值漏洞，但建议统一 `extra="forbid"` 以减少未来回归风险。
- OpenAPI 导入使用 `yaml.safe_load`，拒绝远程/文件 `$ref`，限制 2 MiB；但 APISEC-001 表明安全遍历仍需别名/循环防护。
- `main.py` CORS 仅允许配置的 `frontend_origin`、凭据和显式 Header；写请求由 CSRF 双提交和 Origin 校验保护。生产 HTTPS、反向代理 Header 和 Cookie Secure 行为需部署验证。
- `config.py:103-119` 启动时校验 APP_SECRET_KEY、APP_ENCRYPTION_KEY、API_KEY_PEPPER 长度/格式以及 TOMODD 主机白名单；未发现默认密钥可直接启动。
- 未发现命令执行、模板渲染或任意本地文件路径拼接入口；Gateway 上游路径参数使用 `quote(..., safe="")`，文件下载路径由受控适配器模板生成。

## 工具与命令

- `.venv\\Scripts\\python.exe -m pytest backend/tests/test_auth_session.py backend/tests/test_login_limit.py backend/tests/test_gateway_boundaries.py backend/tests/test_gateway_proxy_mock.py backend/tests/test_developer_platform.py -p no:cacheprovider -q`：`68 passed, 1 warning`。
- 隔离 Python 脚本：OpenAPI YAML 自引用、上游 URL/文档 URL、Header 名和 Pydantic 未知字段边界检查；未输出任何密钥或 Token。
- 未运行 ZAP、Codex Security、依赖漏洞扫描、真实 HTTP 上游或生产日志采集；环境中未发现已配置的安全扫描工具。

## 本专项汇总

- 发现总数：5（其中 2 项已确认问题、2 项正向控制项、1 项待验证）。
- 按严重程度：P1 1、P2 2、P3 2、P0 0。
- 已确认上线阻断项：APISEC-001（OpenAPI 递归输入）；APISEC-002 为 P2，暂不阻断但需修复并回归测试。
- 检查受阻：生产日志脱敏、DNS 重绑定、真实反向代理/CORS/Cookie、依赖扫描和高并发资源耗尽未在本阶段执行。
