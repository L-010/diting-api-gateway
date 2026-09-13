# 第六阶段：边界、异常与模糊测试结果

## 方法

- 使用现有 pytest 和 Mock HTTP 客户端，没有新增生产或测试依赖。
- 固定随机种子 `20260826`：`test_local_logic_invariants.py:test_固定种子文件名边界输入保持安全`，共 200 个可重复输入。
- 输入只在内存/临时 SQLite 中处理；没有真实 SMTP、TomoDD、文件服务、数据库或敏感值。

| 类别 | 覆盖与证据 | 结果 |
|---|---|---|
| 注册输入 | 空白、Unicode 单字符用户名、控制字符、重复邮箱/用户名、密码长度：`test_registration_accepts_unicode_and_single_character_username`、`test_registration_rejects_duplicate_email`。 | 合法 Unicode 可注册；控制字符 422；重复 409。 |
| 会话/认证 | 匿名、无效 Key、待审批、停用、过期、scope、退出和改密后旧 Cookie：`test_gateway_boundaries.py`、集成授权套件。 | 401/403/404/409 受控，无资源泄露。 |
| HTTP 与 Gateway | 非法 JSON、Content-Type、超大声明体、负/非法 Content-Length、超大响应、重定向、网络失败、超时、非 JSON 响应：`test_gateway_proxy_mock.py`、`test_负数内容长度被拒绝`。 | 全部映射到受控 400/413/415/422/502/504，并记录 Gateway 调用；未将预期异常当作成功。 |
| 分页与过滤 | page/page_size 下限上限、时间窗口逆序、调用/文件/任务筛选：`test_admin_audit_supports_paginated_operational_filters`、门户及文件集成测试。 | FastAPI 参数校验或 422；列表始终按后端 owner 条件过滤。 |
| UUID/不存在/删除资源 | 他人 call/file/task ID、owner pre-check、下载授权、墓碑清理。 | 非所有者统一 404；文件重新出现时清除 `deleted_at`，避免旧清理误删有效元数据。 |
| OpenAPI | YAML/Swagger 2、嵌套本地引用、直接/互相循环、无效/远程引用、大合法 schema、内部接口标记：`test_openapi_import.py`。 | 循环或不合法输入受控拒绝，失败导入不残留 batch/spec/endpoint。 |
| 文件名与元数据 | 路径穿越符、控制字符、Unicode、长度、类型、大小、sha256：`test_remote_files.py` 与固定种子测试。 | 名称无路径与控制字符，最多 255；非法类型降级为安全类型；过大文件标记错误。 |
| 并发 | 用户名大小写变体、文件 Worker 同时 claim、幂等同 Key 的唯一记录。 | 修复后数据库拒绝大小写变体；同一同步任务只返回一个 lease；幂等成功或确定失败不重复上游调用。 |

## 发现并处置的 500 风险

| 输入/场景 | 修复前 | 修复后 |
|---|---|---|
| 并发绕过用户名预检查 | SQLite 可写入大小写变体；唯一冲突若发生可变成未处理 `IntegrityError`。 | `username_key` 数据库唯一索引，注册捕获冲突返回 409 `REGISTRATION_CONFLICT`。 |
| 负 `Content-Length` | 服务层未拒绝负值。 | 400 `Content-Length 不合法`。 |
| 新 migration 后 readiness | 数据库升级到新 head 会被 `/readyz` 误判 503。 | head 预期更新为 `a2b3c4d5e6f7`，回归测试通过。 |

## 未做的随机化范围

- 没有引入 Hypothesis 或专用 JSON/OpenAPI 生成器，避免本阶段新增依赖和大范围运行时间；现有解析器测试覆盖已知递归、非法引用与大文档。
- 未对真实网络中断、浏览器中止下载、HTTP 分块畸形报文或多进程 SQLite 锁争用做压力级随机测试，列入剩余风险。
