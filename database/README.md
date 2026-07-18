# Nino Data 演示数据库

本地数据库使用 PostgreSQL `12.18`，只通过确定性 SQL 初始化。数据集包含 25,040 个订单和超过
100,000 条关联记录，覆盖 12 个月，包括固定 Golden Case（黄金案例）以及生成的支付、退款、资源和
异常场景。

## 启动

```bash
docker compose up -d db
docker compose ps
```

数据库默认暴露在 `localhost:55432`，避免与本机可能已占用的 `5432` 端口冲突。

连接信息：

```text
数据库 Database: nino_data_demo
用户名 Username: nino
密码 Password: nino_dev_password
Schema: nino_data
```

MCP 服务使用独立的 `nino_data_readonly` 账号，该账号只有 `SELECT` 权限。
`database/migrations/002_mcp_readonly.sql` 负责创建这个本地开发角色。

演示数据包含五类关联实体：

| 实体 | PostgreSQL 表 |
|---|---|
| 订单 Order | `nino_data.order_info` |
| 支付 Payment | `nino_data.pay_info` |
| 退款 Refund | `nino_data.refund_info` |
| 客户资源 Customer resource | `nino_data.customer_resource_info` |
| 供应商资源 Supplier resource | `nino_data.supplier_resource_info` |

## 验证

运行三条便于人工阅读的查询：

```bash
docker compose exec -T db psql -U nino -d nino_data_demo < database/queries/verification.sql
```

运行可执行断言：

```bash
docker compose exec -T db psql -U nino -d nino_data_demo < database/tests/assertions.sql
```

## 重置

如需通过 migration 和 seed 文件重新创建演示数据库，删除本项目的 Docker volume 后重新启动：

```bash
docker compose down -v
docker compose up -d db
```

修改 PostgreSQL 镜像或 seed 后必须重置，因为 `/docker-entrypoint-initdb.d` 脚本只在数据卷为空时运行。
重置后验证 `server_version` 和行数：

```bash
docker compose exec -T db psql -U nino -d nino_data_demo -c \
  "SELECT current_setting('server_version');"
docker compose exec -T db psql -U nino -d nino_data_demo < database/tests/assertions.sql
```

该操作只删除 `nino-agent` 的合成演示数据库 volume。禁止在本地环境之外使用这些开发凭据。
