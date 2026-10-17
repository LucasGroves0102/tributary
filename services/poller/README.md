# poller

FR1, week 1. Polls the EDGAR latest-filings feed and publishes each new filing to the
`filings.raw` Kafka topic, keyed by CIK.

Feed: https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom

Each feed entry is one *filer* of a filing, so a Form 4 appears twice (insider = Reporting,
company = Issuer) with the same accession number. Events are deduped on `(accession_no, cik)`
and carry `role` so downstream stages can filter.

Config (env): `SEC_USER_AGENT` (required), `KAFKA_BOOTSTRAP_SERVERS`, `POLL_INTERVAL_SECONDS`
(default 30), `LOG_LEVEL`.

## Backfill (FR1b)

`poller-backfill --days 7` (or `--start/--end`) publishes every filing from EDGAR's daily
master indexes to `filings.raw` with `source: daily-index`. It catches what the live poller
missed: downtime, or more than 100 filings between polls. Downstream consumers are idempotent,
so filings the poller already caught keep their exact acceptance time. Run it daily after the
index is published (around 10pm ET). `bronze.filings_daily_coverage` shows, per day, how much
the live poller caught.
