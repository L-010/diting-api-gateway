# 生产部署验证运行手册

## 使用边界

本手册是“只缺外部输入即可执行”的模板。Codex 未执行其中任何生产命令，不连接生产、不使用生产凭证、不修改真实基础设施。目标环境执行者必须先填完 `docs/release-audit/49-external-input-checklist.md` 并保存证据编号。

新增脚本位于 `scripts/release_audit/`：

- `production_preflight.py`：配置、数据库策略、worker 和目标标记预检。
- `migration_precheck.py`：迁移头、当前版本和备份证据只读检查。
- `migration_execute.py --execute`：在明确批准后执行 `scripts/migrate.py`，不提供清库或降级。
- `health_validate.py`：验证 `/livez`、`/readyz`、`/health`。
- `tls_proxy_validate.py`：验证 HTTPS、证书校验和不降级重定向。
- `post_release_smoke.py`：发布后只读核心 API 冒烟。
- `rollback.py --confirm`：只输出人工回滚步骤，不自动改数据库或停止非本脚本进程。

所有脚本默认 fail-closed；缺少 `RELEASE_TARGET_ENV=production`、`RELEASE_VALIDATION_ACK=I_UNDERSTAND_TARGET_ENV`、备份证据或批准标记时以非零退出码结束。

## 阶段 A：发布前预检（只读）

1. 在目标服务器的受控会话中加载 `.venv` 和目标环境变量模板。密钥只从密钥管理系统注入，不写入 shell 历史或日志。
2. 复制 `scripts/release_audit/production-env.template` 为受控配置清单，仅记录变量名和状态。
3. 执行：

   ```powershell
   .venv\Scripts\python.exe scripts\release_audit\production_preflight.py
   ```

4. 预检必须确认：`APP_ENV=production`、三个安全密钥存在且非模板值、数据库策略已批准、`AGW_WORKERS` 与存储方案兼容、上游白名单和前端 Origin 为绝对地址。
5. 预检失败时停止发布，不尝试通过关闭校验、改用默认值或临时跳过测试来继续。

## 阶段 B：迁移前检查（只读）

数据库负责人先完成一次目标数据库备份，并用独立恢复验证或平台证据确认备份可用。之后注入：

```text
BACKUP_VERIFIED=true
BACKUP_VERIFICATION_ID=<证据编号，不是备份内容>
EXPECTED_ALEMBIC_HEAD=a5b6c7d8e9f0
```

执行：

```powershell
.venv\Scripts\python.exe scripts\release_audit\migration_precheck.py
```

脚本只调用 `alembic heads` 和 `alembic current`，不执行 upgrade、downgrade、离线 SQL 或清库。当前历史迁移的完整 offline SQL 生成已在阶段四失败，不能把该命令当作迁移审批依据。

## 阶段 C：迁移执行与验证

只有变更负责人批准、备份证据有效且发布窗口已开始时，才设置 `MIGRATION_CHANGE_APPROVED=true` 并执行：

```powershell
.venv\Scripts\python.exe scripts\release_audit\migration_execute.py --execute
```

执行后必须保存：命令退出码、目标迁移头、数据库平台审计记录和开始/结束时间。任何非零退出码都进入回滚决策树，禁止重复盲目执行。不得修改历史 Alembic migration；不得使用 `downgrade` 作为自动恢复手段。

## 阶段 D：进程启动、readiness 和代理验证

进程托管方案由基础设施提供。应用入口可复用 `scripts/run-backend-production.ps1`，但 host、port、worker、服务账户、日志路径和重启策略必须由托管方案固定。启动后执行：

```powershell
.venv\Scripts\python.exe scripts\release_audit\health_validate.py --base-url <内网应用地址>
.venv\Scripts\python.exe scripts\release_audit\tls_proxy_validate.py --base-url https://<正式域名>
```

要求：

- `/livez` 仅代表进程可响应；`/readyz` 必须同时报告配置、数据库和 Alembic head 正常。
- 反向代理只允许 HTTPS 入口，HTTP 到 HTTPS 的重定向不得降级到 HTTP；上游连接、超时、请求体大小和真实客户端 IP 规则需留证。
- 进程管理器必须能无损重启、限制 worker 数量、保留退出码和启动日志；不能使用 `--reload`。
- 重启后重复验证 `/readyz`，并确认旧进程已退出、无重复 worker、无端口抢占。

## 阶段 E：发布后只读冒烟

```powershell
.venv\Scripts\python.exe scripts\release_audit\post_release_smoke.py --base-url https://<正式域名>
```

脚本验证健康接口、公开配置、公开工具列表和未认证 `/api/me=401`。如要验证已批准的只读 Gateway 接口，额外提供 `SMOKE_READONLY_PATH` 和 `SMOKE_API_KEY`；Key 只能来自非生产或已审批的最小权限测试账户，脚本不会回显它。注册、登录、发邮件、上传、删除、重试和第三方写入不属于该只读冒烟，需按单独验收方案执行。

## 回滚触发与步骤

触发条件包括：迁移非零、`/readyz` 持续非 200、5xx 或延迟超过门槛、数据抽样不一致、TLS/代理错误、关键上游异常或安全告警无法解释。

1. 发布负责人宣布停止流量并记录事件号。
2. 保留当前版本日志、指标、迁移状态和备份证据。
3. 由制品负责人将应用制品切换到上一已批准版本；使用 `rollback.py --confirm` 仅确认前置条件和人工步骤。
4. 数据库只执行已批准的前向兼容修复或从经验证备份恢复；禁止未经评审的 Alembic downgrade、删除表或清库。
5. 回滚后重复 TLS、`/livez`、`/readyz`、只读冒烟和核心数据抽样。
6. 由业务、安全、数据库和基础设施负责人共同签署恢复证据，未签署前维持禁止上线/禁止恢复流量。

## 环境变量清单模板

完整名称模板见 `scripts/release_audit/production-env.template`。值不写入审计文档，只记录：变量名、来源系统、是否存在、最后轮换时间、验证人、证据编号。`APP_SECRET_KEY`、`APP_ENCRYPTION_KEY`、`API_KEY_PEPPER`、SMTP 密码、TomoDD token、API Key 和 Cookie 永远不得出现在日志、截图、工单或提交中。

当前仓库示例使用 Windows `.venv\Scripts\python.exe`；Linux/Unix 目标应使用同一项目虚拟环境的 `.venv/bin/python`，不得切换到系统 Python。

## 验收输出

每一步至少保存命令版本、退出码、时间、目标标识、配置状态摘要和证据链接。没有这些外部证据时，本手册只能算执行模板，不能解除发布门禁。
