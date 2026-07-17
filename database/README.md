# Nino Data Demo Database

## Start

```bash
docker compose up -d db
docker compose ps
```

The database is exposed on `localhost:55432` by default because local port `5432` may already be in use.

Connection values:

```text
Database: nino_data_demo
Username: nino
Password: nino_dev_password
Schema: nino_data
```

The MCP service uses a separate `nino_data_readonly` login that has `SELECT` permission only.
`database/migrations/002_mcp_readonly.sql` creates this local-development role.

The demo dataset contains five related entities:

| Entity | PostgreSQL table |
|---|---|
| Order | `nino_data.order_info` |
| Payment | `nino_data.pay_info` |
| Refund | `nino_data.refund_info` |
| Customer resource | `nino_data.customer_resource_info` |
| Supplier resource | `nino_data.supplier_resource_info` |

## Verify

Run the three human-readable queries:

```bash
docker compose exec -T db psql -U nino -d nino_data_demo < database/queries/verification.sql
```

Run executable assertions:

```bash
docker compose exec -T db psql -U nino -d nino_data_demo < database/tests/assertions.sql
```

## Reset

To recreate the demo database from migration and seed files, remove this project's Docker volume and start the service again:

```bash
docker compose down -v
docker compose up -d db
```

This deletes only the `nino-agent` synthetic demo database volume. Never use these development credentials outside a local environment.
