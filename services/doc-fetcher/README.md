# doc-fetcher

FR2b, week 1. Consumes `filings.raw`; for 10-K, 10-Q and 8-K filings by tracked companies,
fetches the filing index page, then the primary document, stores it gzip-compressed in
`bronze.filing_documents` and publishes `documents.fetched` for the [parser](../parser). For
8-Ks it also stores the EX-99.x exhibits (press releases) in `bronze.filing_exhibits`. Each
accession is fetched once.

| | |
|---|---|
| Input | `filings.raw` (consumer group `doc-fetcher`); tracked CIKs from `silver.companies` / `silver.company_ciks`, re-read every 5 minutes |
| Output | `bronze.filing_documents`, `bronze.filing_exhibits`, `documents.fetched` |
| Metrics | `tributary_documents_fetched_total{form}`, shared Kafka counters from [libs/telemetry](../../libs/telemetry) |

Documents are referenced, not embedded, in `documents.fetched`: a 10-K is ~2 MB of HTML,
over Kafka's default 1 MB message limit. Event shapes are in
[streaming/topics.md](../../streaming/topics.md).

## Jobs

| Command | Make target | Does |
|---|---|---|
| `doc-fetcher` | `make up-all` | the consumer above |
| `doc-fetcher-seed` | `make seed-documents` | publishes each tracked company's last 2 10-Ks and last 4 10-Qs (within 2 years) and last 180 days of 8-Ks to `filings.raw` with `source: seed` and the acceptance time corrected to UTC (`tributary_sec.acceptance_time`), so history flows through the same path as live filings without counting as live in the freshness views |
| `doc-fetcher-exhibits` | `make exhibits` | fetches EX-99.x exhibits for stored 8-Ks that have none, then `make exhibits` re-parses |

On k3s, `seed-documents` is a suspended CronJob for filling a new database
(`kubectl -n tributary create job --from=cronjob/seed-documents seed-1`).

Config (env): `SEC_USER_AGENT`, `DATABASE_URL` (both required), `KAFKA_BOOTSTRAP_SERVERS`
(default `localhost:9092`), `METRICS_PORT`, `LOG_LEVEL`.
SEC requests share one rate limit with every other service (`libs/sec`): `SEC_CALLER_CLASS`
(`live` for the consumer, `bulk` for the seed job, default `batch`) and
`SEC_LIMITER_DATABASE_URL` (default `DATABASE_URL`).

## Tests

`pytest services/doc-fetcher`: filing-index parsing and seed selection against saved EDGAR
pages and submissions in `tests/fixtures/`.
