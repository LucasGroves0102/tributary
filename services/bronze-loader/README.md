# bronze-loader

FR4, week 1. Consumes `filings.raw` (from [poller](../poller), `poller-backfill` and
`doc-fetcher-seed`) and writes each event to `bronze.filings`, the raw sink of live filings.
`silver.filings` and the `ops` freshness views read that table; they tell the producers apart
by the event's `source` (`feed`, `daily-index`, `seed`; see `streaming/topics.md`).

| | |
|---|---|
| Input | `filings.raw` (consumer group `bronze-loader`) |
| Output | `bronze.filings`: one row per `(accession_no, cik)`, full event in `payload`, plus Kafka partition and offset |
| Metrics | `tributary_bronze_filings_total{result="inserted\|duplicate"}`, shared consumer counters from [libs/telemetry](../../libs/telemetry) |

Inserts are idempotent on `(accession_no, cik)`, and Kafka offsets are committed only after
the Postgres transaction commits, so a crash replays at most one batch without duplicating rows.
Malformed events are logged, counted in `tributary_messages_skipped_total` and dropped (no
dead-letter topic yet). A topic that doesn't exist yet is not an error.

## Run

```bash
make up-all                                         # in docker-compose
set -a; . ./.env; set +a; .venv/bin/bronze-loader   # locally
```

Config (env): `DATABASE_URL` (required), `KAFKA_BOOTSTRAP_SERVERS` (default `localhost:9092`),
`BATCH_SIZE` (default 500), `METRICS_PORT`, `LOG_LEVEL`.

## Tests

`pytest services/bronze-loader`. `test_db.py` runs against Postgres with migrations applied and
is skipped unless `TEST_DATABASE_URL` is set.
