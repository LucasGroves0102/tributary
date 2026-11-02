# poller

FR1, week 1. Polls the EDGAR latest-filings feed and publishes each new filing to the
`filings.raw` Kafka topic, keyed by CIK. Downstream, [bronze-loader](../bronze-loader) writes
the events to `bronze.filings` and [doc-fetcher](../doc-fetcher) fetches documents for tracked
companies.

Feed: https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom

Each feed entry is one *filer* of a filing, so a Form 4 appears twice (insider = Reporting,
company = Issuer) with the same accession number. Events are deduped on `(accession_no, cik)`
and carry `role` so downstream stages can filter. The dedupe memory is in-process, so a restart
can republish up to one feed's worth of entries; consumers are idempotent on the key. The event
shape is in [streaming/topics.md](../../streaming/topics.md).

| | |
|---|---|
| Input | EDGAR latest-filings Atom feed (through [libs/sec](../../libs/sec)) |
| Output | `filings.raw` (`source: feed`, exact acceptance time) |
| Metrics | `tributary_poller_polls_total{result}`, `tributary_poller_last_success_timestamp_seconds` |

## Run

```bash
make up-all                                  # in docker-compose, with the other services
set -a; . ./.env; set +a; .venv/bin/poller   # locally
```

Config (env): `SEC_USER_AGENT` (required), `KAFKA_BOOTSTRAP_SERVERS` (default
`localhost:9092`), `POLL_INTERVAL_SECONDS` (default 30), `METRICS_PORT`, `LOG_LEVEL`, and the Kafka authentication variables of [libs/kafka](../../libs/kafka) (SASL/SCRAM on k3s).
SEC requests share one rate limit with every other service (`libs/sec`): `SEC_CALLER_CLASS`
(`live` for the poller, `batch` for the backfill job) and `SEC_LIMITER_DATABASE_URL` (default
`DATABASE_URL`; without either, a local 2 req/s limit).

## Backfill (FR1b)

`poller-backfill --days 7` (or `--start/--end`, `YYYY-MM-DD`; `--end` defaults to yesterday)
publishes filings from EDGAR's daily master indexes to `filings.raw` with
`source: daily-index`. It catches what the live poller missed (downtime, or more than 100
filings between polls) and what the bulk history lacks: `submissions.zip` can carry a stale copy
of a company (on 2026-11-02 it still lacked Dentsply Sirona's 8-K of 2026-10-09, which the SEC's
per-company page had), and a company is only reloaded when its copy changes. Downstream
consumers are idempotent, so filings the poller already caught keep their exact acceptance time.
`bronze.filings_daily_coverage` shows, per day, how much the live poller caught.

Without `DATABASE_URL` it publishes every filing of each day. With it (the nightly job) it is a
completeness check: each day's `(accession, filer)` rows are looked up in `bronze.filings` and
`bronze.submission_filings` (what `silver.filings` unions), only the missing ones are published,
and the day is recorded in `ops.filing_completeness` (migration 027: accessions indexed, missing
at that check, rows republished; ops_reader reads `ops.filing_completeness_status`). The lookup
takes a few seconds per day.

```bash
make backfill DAYS=7
```

On k3s it runs nightly as the `backfill` CronJob over a rolling 30 days (`--days 30`, 02:00 New
York), so a day keeps being rechecked for a month and `missing` drops to 0 after a republish.
Filings it republishes have no 8-K items (the index doesn't carry them), so the document crawl's
8-K item filter won't fetch their documents.

## Tests

`pytest services/poller`: feed and daily-index parsing against saved responses in
`tests/fixtures/`, and the poll loop with a fake publisher and clock. `test_completeness_db.py`
(needs `TEST_DATABASE_URL`) checks the missing-filing lookup and the per-day record.
