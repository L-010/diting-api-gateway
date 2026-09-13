# MySQL 生产部署与数据迁移

## 数据库要求

- MySQL 8.0，存储引擎使用 InnoDB。
- 数据库字符集使用 `utf8mb4`，排序规则推荐 `utf8mb4_0900_ai_ci`。
- 应用账号只授予目标数据库权限，不授予全局管理权限。

```sql
CREATE DATABASE api_gateway CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'api_user'@'%' IDENTIFIED BY '<强随机密码>';
GRANT ALL PRIVILEGES ON api_gateway.* TO 'api_user'@'%';
FLUSH PRIVILEGES;
```

密码放入 URL 前必须进行百分号编码。生产连接示例：

```env
DATABASE_URL=mysql+pymysql://api_user:<URL编码后的密码>@mysql:3306/api_gateway?charset=utf8mb4
```

## 首次空库部署

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe scripts\migrate.py
```

执行迁移后再启动后端、邮件 Worker 和文件 Worker。所有进程必须使用同一个 `DATABASE_URL`。

## 从 SQLite 迁移现有数据

1. 停止后端和 Worker，确保 SQLite 不再发生写入。
2. 备份 `data/api_gateway.db` 并记录 SHA-256。
3. 创建全新的空 MySQL 数据库，不要指向已有业务数据的数据库。
4. 使用独立环境变量提供目标 URL，避免提前修改应用 `.env`。

```powershell
$env:MYSQL_DATABASE_URL='mysql+pymysql://api_user:<URL编码后的密码>@127.0.0.1:3306/api_gateway?charset=utf8mb4'
.\.venv\Scripts\python.exe scripts\migrate_sqlite_to_mysql.py --prepare-target
Remove-Item Env:MYSQL_DATABASE_URL
```

迁移工具会完成以下保护：

- 校验源库和目标库均处于当前 Alembic head。
- 拒绝向非空目标库写入，不覆盖现有数据。
- 在单个事务中按依赖顺序复制所有业务表。
- 对每张表校验行数和完整内容摘要。
- 校验所有外键不存在孤儿记录。
- 任一写入或校验失败即回滚目标库数据。

## 切换与回滚

迁移通过后，将生产 `.env.production` 的 `DATABASE_URL` 改为已验证的 MySQL URL，执行生产预检并启动服务。验证 `/readyz`、管理员登录、用户登录、工具列表和任务/文件页面。

切换后不要同时向 SQLite 和 MySQL 写入。若上线验证失败，应停止全部应用进程，将 `DATABASE_URL` 恢复为迁移前配置并重新启动；保留原 SQLite 文件和备份，直至 MySQL 上线验收结束。
