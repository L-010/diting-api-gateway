# 部署演练

命令：`.venv\Scripts\python.exe scripts\phase4_deployment_rehearsal.py`

结果：`preflight=0`、空库 migrate=0、`/livez=200`、`/readyz=200`、生产 `/docs=404`、停止后重启 `/readyz=200`、缺失 `APP_SECRET_KEY` 的 preflight 非零退出。服务由生产脚本在 `127.0.0.1:18081`、`AGW_WORKERS=1` 启动，无 `--reload`。

本轮修复：生产脚本支持白名单 `AGW_HOST` 与 `AGW_PORT` 覆盖（默认仍为 `0.0.0.0:8000`），并转换为 UTF-8 BOM 以兼容 Windows PowerShell 5.1。离线 `alembic upgrade head --sql` 退出非零，原因是现有历史迁移不支持完整 offline SQL 生成；未修改历史 migration，此项仍待部署方案确认。

未验证：云托管、TLS、反向代理、目标数据库、真实回滚、多个 worker。
