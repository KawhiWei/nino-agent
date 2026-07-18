# Nino Data Demo Database

The local database runs PostgreSQL `12.18` and is initialized from deterministic SQL only. The
dataset contains 25,040 orders and more than 100,000 related rows across twelve months, including
fixed Golden Cases plus generated payment, refund, resource, and anomaly scenarios.

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

This reset is required after changing the PostgreSQL image or seed because `/docker-entrypoint-initdb.d`
scripts run only when the data volume is empty. Verify `server_version` and row counts after reset:

```bash
docker compose exec -T db psql -U nino -d nino_data_demo -c \
  "SELECT current_setting('server_version');"
docker compose exec -T db psql -U nino -d nino_data_demo < database/tests/assertions.sql
```

This deletes only the `nino-agent` synthetic demo database volume. Never use these development credentials outside a local environment.
