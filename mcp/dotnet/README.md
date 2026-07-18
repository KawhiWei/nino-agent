# Nino Data MCP（.NET）

该项目使用官方 .NET MCP SDK 实现只读数据工具。API 传输协议为 MCP Streamable HTTP，入口是
`POST /mcp`；同时保留 `--stdio`，供本地 MCP Client 使用。

## 工具

| Tool | 用途 |
|---|---|
| `nino_data_get_order_detail` | 读取订单、客户资源、供应商资源、支付、退款和汇总金额 |
| `nino_data_query_summary` | 按产品、渠道或日期汇总有效非测试已支付订单，并返回确定性总计和亏损订单总数 |
| `nino_data_find_anomalies` | 返回确定性的负毛利及其他受支持异常 |

项目刻意不提供任意 SQL Tool。所有输入都经过校验，每条数据库命令都是参数化只读查询。

## 运行

在 `nino-agent` 根目录执行：

```bash
docker compose up -d db nino-data
curl http://127.0.0.1:8091/health
```

本地开发：

```bash
dotnet run --project mcp/dotnet/src/Nino.Data.Mcp
dotnet test mcp/dotnet/Nino.Data.Mcp.slnx
```

配置：

| 环境变量 | 默认值 |
|---|---|
| `NINO_DATA_MCP_URLS` | `http://127.0.0.1:8091` |
| `NINO_DATA_DB_CONNECTION_STRING` | 使用 `nino_data_readonly` 的本地演示数据库 |
| `NINO_DATA_TEST_DB_CONNECTION_STRING` | 集成测试使用的同一本地只读数据库 |

Server 使用无状态 Streamable HTTP，因此 Agent Runtime 可以重连和扩展，不依赖进程内 MCP Session。
当前 MVP（最小可行产品）明确延后身份认证；不要在不受信任的开发网络之外暴露 `8091` 端口。
