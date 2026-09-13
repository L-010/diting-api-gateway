# 隔离测试环境

## 已建立的能力

- 使用项目 `.venv`、临时 SQLite、合成用户和 Mock 上游/SMTP；未连接生产数据库、真实 Token、Cookie、邮件或第三方。
- 现有 `backend/tests` 通过 `Base.metadata.create_all()` 创建临时库；新增 release controls 覆盖 liveness/readiness。
- Gateway 测试覆盖用户级资源边界、管理员路由、Key 状态、请求体限制、幂等键顺序重放和请求指纹冲突。
- OpenAPI 测试覆盖合法嵌套、直接循环、互相循环、非法引用和失败无写入的解析层行为；管理员集成测试覆盖 changed diff 单接口发布。

## 未完成覆盖

跨租户模型、真实 user/admin 浏览器矩阵、SMTP/tomoDD/文件上游、真实 TLS/代理和移动端 Playwright 仍因业务规则或环境缺失未验证，保留为 P1/P2 阻断风险。
