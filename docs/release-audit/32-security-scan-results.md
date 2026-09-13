# 安全扫描结果

范围仅限当前工作区和本机隔离代码，未扫描生产地址。

| 工具/范围 | 命令 | 结果 |
|---|---|---|
| Bandit | `.venv\Scripts\bandit.exe -r backend\app -q -f txt` | 0 High/Medium，9 Low；均为 token/password 字段清理时的空字符串或掩码误报，待复核 |
| Python 依赖 | `.venv\Scripts\pip-audit.exe -r backend\requirements.txt` | 修复后 0 已知漏洞 |
| Node 生产依赖 | `npm audit --omit=dev --audit-level=high` | 修复后 0 漏洞 |
| 前端危险 sink | `rg` 搜索 DOM XSS、eval、storage、postMessage、跳转模式 | 未命中 |
| 密钥模式 | 排除 `.env`、数据库、依赖、构建物后的 `rg` | 无云密钥/私钥命中；一个合成测试字符串命中，非秘密 |
| ZAP / Semgrep / Gitleaks / Codex Security | 工具探测 | 未安装，未执行 |

已修复依赖：`cryptography` 50.0.1、`pytest` 9.1.1、Next 16.3.3；生产 OpenAPI/docs 在 `APP_ENV=production` 下关闭。扫描不能证明系统不存在其它漏洞。
