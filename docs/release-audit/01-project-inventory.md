# 项目与 Git 清单

## Git 状态

证据命令：`git rev-parse --is-inside-work-tree`、`git branch --show-current`、`git status --porcelain=v1`、`git log --oneline`。

- Git 仓库：是。
- 当前分支：`master`。
- 提交历史：命令输出为 `No commits yet on master`，当前没有可引用提交。
- 工作区：非干净；根目录和各子目录大量未跟踪文件。未发现已提交修改，因为没有提交；无法区分哪些未跟踪文件属于用户历史修改，需业务/仓库负责人确认。
- 本阶段没有执行任何改变 Git 状态的命令。

## 主要目录

| 目录/文件 | 观察 | 证据 |
|---|---|---|
| `backend/app` | FastAPI 应用入口、配置、模型、路由、服务 | 文件清单 |
| `backend/tests` | pytest 后端测试 | 文件清单 |
| `backend/alembic` | Alembic 环境和 18 个迁移脚本 | 文件清单 |
| `frontend/app` | Next.js App Router 页面，统计 37 个 `page.tsx` | `Get-ChildItem frontend/app -Filter page.tsx` |
| `frontend/components`、`frontend/lib` | UI 组件和 API/业务客户端 | 文件清单 |
| `scripts` | bootstrap、启动、worker、seed、迁移、验证、维护 | 文件清单 |
| `docs` | 架构、运行手册、分布式文件契约、需求/图表 | 文件清单 |
| `data` | SQLite 数据目录 | 根目录读取 |
| `.venv` | 项目 Python 虚拟环境 | 根目录读取 |

## 生成物、缓存与潜在敏感目录

- 生成/构建：`frontend/.next`、`output`、`.run`。
- 依赖/缓存：`.venv`、`frontend/node_modules`、`.pytest_cache`、`backend/.pytest_cache`、`.playwright-cli`、`frontend/tsconfig.tsbuildinfo`。
- 数据库/临时数据：`data`、根目录 `.tmp_remote_files_*.db`、`.run/*.db`。
- 潜在敏感文件：`.env`、SQLite 数据库、日志文件（`.gitignore` 也将 `.env`、`data/`、`*.log` 等列为忽略项）。本阶段没有读取或保存密钥值。

## 语言、框架与依赖

- Python 项目依赖见 `backend/requirements.txt`；前端依赖和脚本见 `frontend/package.json`。
- 主要依赖：FastAPI/Uvicorn、SQLAlchemy/Alembic、Pydantic Settings、pwdlib Argon2、PyJWT、Cryptography、httpx、python-multipart、PyYAML、jsonpath-ng；Next.js/React/TypeScript/lucide-react。

## 架构形态

- 属于前后端分离：前端 Next.js 与后端 FastAPI 分目录，`frontend/next.config.ts` 将 `/api`、`/gateway`、`/health` rewrite 到 `127.0.0.1:8000`。
- 不是明显 monorepo：当前只有一个应用仓库，但包含后端、前端、worker 和脚本多个运行单元。
- 多服务：后端 Web、前端 Web、可选邮件 worker、可选文件 worker、外部 tomoDD-SP/其它白名单上游。
- 多租户：代码有用户级资源归属与 `shared` 策略，但没有发现显式 `tenant`/`organization` 模型；是否将用户视作租户边界待业务确认。
