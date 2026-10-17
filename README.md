# Tributary

Real-time SEC filings platform: streams new EDGAR filings and company financials into Postgres,
resolves every company to one identity, and serves an AI agent that answers questions with
citations — only from the data each caller is allowed to see.

## Quickstart

```bash
cp .env.example .env   # set SEC_USER_AGENT to your name/email
make up                # Kafka + Postgres
make up-all            # + poller
make consume           # watch filings arrive (or open the Kafka UI at http://localhost:8080)
```

## Layout

| Path | What |
|---|---|
| `services/` | One folder per deployable service (poller, fetcher, parser, parser-rs, query-api, agent) |
| `db/` | Migrations, medallion transforms, RLS policies, app schema |
| `streaming/` | Debezium configs and Kafka topic docs |
| `authz/` | OpenFGA model |
| `tests/` | Adversarial, integration and propagation tests |
| `deploy/` | Helm chart and Terraform |
| `observability/` | OpenTelemetry, Prometheus, Grafana |
| `benchmarks/` | Python vs Rust parser |
