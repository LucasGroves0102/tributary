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
`localhost:9092`), `POLL_INTERVAL_SECONDS` (default 30), `METRICS_PORT`, `LOG_LEVEL`.

## Backfill (FR1b)

`poller-backfill --days 7` (or `--start/--end`, `YYYY-MM-DD`; `--end` defaults to yesterday)
publishes every filing from EDGAR's daily master indexes to `filings.raw` with
`source: daily-index`. It catches what the live poller missed: downtime, or more than 100
filings between polls. Downstream consumers are idempotent, so filings the poller already caught
keep their exact acceptance time. `bronze.filings_daily_coverage` shows, per day, how much the
live poller caught.

```bash
make backfill DAYS=7
```

On k3s it runs nightly as the `backfill` CronJob (`--days 2`, 02:00 New York).

## Tests

`pytest services/poller`: feed and daily-index parsing against saved responses in
`tests/fixtures/`, and the poll loop with a fake publisher and clock.
