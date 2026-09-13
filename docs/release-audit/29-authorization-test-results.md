# 权限测试结果

命令：`.venv\Scripts\python.exe -m pytest backend\tests\integration\test_isolated_authorization.py -p no:cacheprovider -q`

结果：`6 passed`。全量回归含本测试为 `110 passed`。

| 场景 | 实际响应 | 结论 |
|---|---:|---|
| 未登录访问 `/api/me`、calls、files、admin | 401 | 通过 |
| owner 访问 owner 路由 | 200 | 通过 |
| 同合成租户另一 user 访问 owner 资源 | 404 | 拒绝且隐藏存在性 |
| 异合成租户 user 访问 owner 资源 | 404 | 拒绝且隐藏存在性 |
| `shared` 访问另一 user 的资源标识 | 200 | 当前代码放行，待业务确认 |
| 普通 user 访问 `admin_only` | 403 | 通过 |
| admin 访问 `admin_only` | 200 | 通过 |
| admin 访问 owner 资源 | 404 | 当前代码不绕过 owner 策略，待业务确认 |
| call 列表、详情、CSV 导出、file、task 修改 ID | 非 owner 404/无泄露 | 通过 |
| 不含工具 scope 的 API Key | 403 `API_KEY_SCOPE_DENIED` | 通过 |
| 普通 user / admin 管理接口 | 403 / 200 | 通过 |
| logout、修改密码后的旧 Cookie | 401 | 通过 |

未执行：真实 tenant ACL、批量写删、真实文件下载与上游副作用；这些不应解释为已验收。
