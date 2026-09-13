# 🚀 快速修复指南

如果你正在服务器上遇到部署问题，按以下步骤快速修复：

---

## 场景 1: 提示 "EXPECTED_ALEMBIC_HEAD 必须为 f9a0b1c2d3e4"

在服务器上执行：

```bash
cd /Data/earthquake-api-gateway

# 添加正确的迁移版本号
echo "EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1" >> .env.production

# 验证
grep EXPECTED_ALEMBIC_HEAD .env.production
```

---

## 场景 2: 提示 "无法设置品牌资源目录权限"

在服务器上执行：

```bash
cd /Data/earthquake-api-gateway

# 添加目录配置
cat >> .env.production << 'EOF'
MYSQL_DATA_DIR=/Data/earthquake-api-gateway/mysql
BRAND_ASSET_DIR_HOST=/Data/earthquake-api-gateway/brand-assets
BACKUP_DIR=/Data/earthquake-api-gateway/backups/mysql
BACKEND_PORT=8000
FRONTEND_PORT=3000
EOF

# 手动创建目录
sudo mkdir -p /Data/earthquake-api-gateway/mysql
sudo mkdir -p /Data/earthquake-api-gateway/brand-assets
sudo mkdir -p /Data/earthquake-api-gateway/backups/mysql

# 设置权限
sudo chown -R 10001:10001 /Data/earthquake-api-gateway/brand-assets
```

---

## 场景 3: Git 仓库目录名不匹配

如果你克隆后目录名是 `diting-api-gateway`：

```bash
cd /Data
mv diting-api-gateway earthquake-api-gateway
cd earthquake-api-gateway
```

或者重新克隆时指定目录名：

```bash
cd /Data
rm -rf diting-api-gateway  # 如果存在
git clone https://github.com/L-010/diting-api-gateway.git earthquake-api-gateway
cd earthquake-api-gateway
```

---

## 完整的一键修复脚本

如果你在服务器上已经有项目目录，运行此脚本一次性修复所有问题：

```bash
#!/bin/bash
cd /Data/earthquake-api-gateway

# 备份原配置
cp .env.production .env.production.backup.$(date +%Y%m%d_%H%M%S)

# 检查并添加缺失的配置项
if ! grep -q "EXPECTED_ALEMBIC_HEAD" .env.production; then
    echo "" >> .env.production
    echo "# 数据库迁移版本控制" >> .env.production
    echo "EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1" >> .env.production
fi

if ! grep -q "MYSQL_DATA_DIR" .env.production; then
    echo "" >> .env.production
    echo "# Docker Compose 配置" >> .env.production
    echo "MYSQL_DATA_DIR=/Data/earthquake-api-gateway/mysql" >> .env.production
    echo "BRAND_ASSET_DIR_HOST=/Data/earthquake-api-gateway/brand-assets" >> .env.production
    echo "BACKUP_DIR=/Data/earthquake-api-gateway/backups/mysql" >> .env.production
    echo "BACKEND_PORT=8000" >> .env.production
    echo "FRONTEND_PORT=3000" >> .env.production
fi

# 创建必要的目录
sudo mkdir -p /Data/earthquake-api-gateway/mysql
sudo mkdir -p /Data/earthquake-api-gateway/brand-assets
sudo mkdir -p /Data/earthquake-api-gateway/backups/mysql

# 设置权限
sudo chown -R 10001:10001 /Data/earthquake-api-gateway/brand-assets

echo "✅ 配置修复完成！"
echo ""
echo "现在可以运行部署："
echo "bash scripts/deploy-prod.sh"
```

保存为 `fix-config.sh` 并执行：

```bash
chmod +x fix-config.sh
./fix-config.sh
```

---

## 验证修复是否成功

```bash
cd /Data/earthquake-api-gateway

# 检查配置文件
echo "=== 检查 EXPECTED_ALEMBIC_HEAD ==="
grep EXPECTED_ALEMBIC_HEAD .env.production

echo "=== 检查目录配置 ==="
grep MYSQL_DATA_DIR .env.production

echo "=== 检查目录是否存在 ==="
ls -ld /Data/earthquake-api-gateway/mysql
ls -ld /Data/earthquake-api-gateway/brand-assets

echo "=== 检查权限 ==="
ls -ln /Data/earthquake-api-gateway/brand-assets | head -2
```

期望输出：
```
EXPECTED_ALEMBIC_HEAD=a6b7c8d9e0f1
MYSQL_DATA_DIR=/Data/earthquake-api-gateway/mysql
drwxr-xr-x ... /Data/earthquake-api-gateway/mysql
drwxr-xr-x 10001 10001 ... /Data/earthquake-api-gateway/brand-assets
```

---

## 如果还有问题

请提供以下信息：

1. 执行 `bash scripts/deploy-prod.sh` 的完整错误输出
2. `.env.production` 文件内容：`cat .env.production`
3. Docker 版本：`docker --version && docker compose version`
4. 当前用户权限：`whoami && groups`
