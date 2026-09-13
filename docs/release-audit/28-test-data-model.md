# 第四阶段合成数据模型

| 标签 | 主体 | 用途 |
|---|---|---|
| tenant-alpha | user-a、user-b | 验证同合成租户不同 user 的资源拒绝 |
| tenant-beta | user-a、user-b | 验证异合成租户的资源拒绝 |
| platform-admin | admin | 验证管理员接口和 `admin_only` |

- `owner`：alpha user-a 的 dataset、task、file、call；仅 owner 的 API Key 能访问。
- `shared`：模拟路由；代码当前对任意已认证 API Key 放行，不带 tenant 条件。
- `admin_only`：普通用户 403，admin API Key 允许。
- 数据不包含真实姓名、邮箱、Token、文件正文或第三方地址。测试中的 API Key、密码和邮箱均为合成值。
- 生产模型仅保存 `owner_user_id`，没有 tenant 字段，因此本数据模型不能证明生产多租户隔离。
