# Tributary

Real-time SEC filings platform: streams new EDGAR filings and company financials into Postgres,
resolves every company to one identity, and serves an AI agent that answers questions with
citations — only from the data each caller is allowed to see.

## Quickstart

```bash
cp .env.example .env   # set SEC_USER_AGENT to your name/email
make up                # Kafka + Postgres
make migrate           # create/upgrade the schema
make companies         # load company identity (FR5); then: SELECT * FROM silver.resolve_company('FB');
make financials        # XBRL financials for tracked companies → gold.quarterly_financials
make seed-documents    # load recent 10-K/10-Q/8-K history (fetched and parsed by up-all)
make up-all            # + poller, bronze-loader, doc-fetcher, parser
make backfill DAYS=7   # fill in filings the live poller missed (EDGAR daily indexes)
make psql              # query it: SELECT * FROM bronze.filings ORDER BY filed_at DESC LIMIT 10;
make consume           # watch filings arrive (or open the Kafka UI at http://localhost:8080)
```

## Layout

| Path | What |
|---|---|
| `services/` | One folder per deployable service or job (poller, bronze-loader, company-loader, facts-fetcher, doc-fetcher, parser, fetcher, parser, parser-rs, query-api, agent) |
| `libs/` | Shared Python packages (`sec`: SEC HTTP client with User-Agent and rate limiting) |
| `db/` | Migrations, medallion transforms, RLS policies, app schema |
| `streaming/` | Debezium configs and Kafka topic docs |
| `authz/` | OpenFGA model |
| `tests/` | Adversarial, integration and propagation tests |
| `deploy/` | Helm chart and Terraform |
| `observability/` | OpenTelemetry, Prometheus, Grafana |
| `benchmarks/` | Python vs Rust parser |
