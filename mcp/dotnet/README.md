# Nino Data MCP (.NET)

Read-only data tools implemented with the official .NET MCP SDK. The API transport is
MCP Streamable HTTP at `POST /mcp`; `--stdio` is retained for local MCP clients.

## Tools

| Tool | Purpose |
|---|---|
| `nino_data_get_order_detail` | Load an order and its customer resources, supplier resources, payments, refunds, and totals. |
| `nino_data_query_summary` | Summarize paid, non-test orders by product, channel, or day. |
| `nino_data_find_anomalies` | Return deterministic negative-margin anomalies. |

There is intentionally no arbitrary SQL tool. All inputs are validated and every database
command is a parameterized, read-only query.

## Run

From `nino-agent`:

```bash
docker compose up -d db nino-data
curl http://127.0.0.1:8091/health
```

For local development:

```bash
dotnet run --project mcp/dotnet/src/Nino.Data.Mcp
dotnet test mcp/dotnet/Nino.Data.Mcp.slnx
```

Configuration:

| Environment variable | Default |
|---|---|
| `NINO_DATA_MCP_URLS` | `http://127.0.0.1:8091` |
| `NINO_DATA_DB_CONNECTION_STRING` | Local demo database using `nino_data_readonly` |
| `NINO_DATA_TEST_DB_CONNECTION_STRING` | Same local read-only database for integration tests |

The server uses stateless Streamable HTTP so an Agent Runtime can reconnect and scale without
depending on in-process MCP session state. Authentication is deliberately deferred from this MVP;
do not expose port `8091` outside a trusted development network.
