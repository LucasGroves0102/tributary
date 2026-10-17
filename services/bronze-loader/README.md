# bronze-loader

FR4, week 1. Consumes `filings.raw` and writes each event to `bronze.filings`.

Inserts are idempotent on `(accession_no, cik)`, and Kafka offsets are committed only after
the Postgres transaction commits, so a crash replays at most one batch without duplicating rows.

Config (env): `DATABASE_URL` (required), `KAFKA_BOOTSTRAP_SERVERS`, `BATCH_SIZE` (default 500),
`LOG_LEVEL`.
