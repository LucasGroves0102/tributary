# poller

FR1, week 1. Polls the EDGAR latest-filings feed and publishes each new filing to the
`filings.raw` Kafka topic, keyed by CIK. Downstream, [bronze-loader](../bronze-loader) writes
the events to `bronze.filings` and [doc-fetcher](../doc-fetcher) fetches the documents of tracked
companies, every operating company's new 10-K, 10-Q and material 8-K, and their insiders' Forms
3, 4 and 5.

Feed: https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom

Each feed entry is one *filer* of a filing, so a Form 4 appears twice (insider = Reporting,
company = Issuer) with the same accession number. Events are deduped on `(accession_no, cik)`
and carry `role` so downstream stages can filter. The dedupe memory is in-process, so a restart
can republish up to `POLL_STARTUP_LOOKBACK_SECONDS` of entries; consumers are idempotent on the
key. The event shape is in [streaming/topics.md](../../streaming/topics.md).

| | |
|---|---|
| Input | EDGAR latest-filings Atom feed (through [libs/sec](../../libs/sec)) |
| Output | `filings.raw` (`source: feed`, exact acceptance time) |
| Metrics | `tributary_poller_polls_total{result}`, `tributary_poller_last_success_timestamp_seconds`, `tributary_poller_feed_pages_total`, `tributary_poller_unsettled_polls_total` |

## Paging

The feed lists filings newest first, 100 to a page (`start`, `count`). Reading only the first
page, the poller caught 93.4% of filings from 2026-10-20 to 2026-11-27: a burst of more than a
page between two polls (2,797 of 7,869 filings missed on 2026-11-16, the 13F and 10-Q deadline),
a stall, or an entry EDGAR listed late below entries already seen waited for the nightly check.
Each poll now reads pages until one is settled: it holds an entry an earlier poll saw, no new
entry below such an entry (a late listing: more may sit deeper), and reaches back
`POLL_LOOKBACK_SECONDS` (120) before the newest entry the last poll saw; or until the feed ends,
repeats a page (stopped paging) or `POLL_MAX_PAGES` (20). The first poll after a start reads back
`POLL_STARTUP_LOOKBACK_SECONDS` (600) before the feed's own time. A quiet poll is one request; a
burst or an outage takes a few more. A walk cut short at the cap counts in
`tributary_poller_unsettled_polls_total` and is the recheck's (below). A walk that fails part way
publishes what it found and keeps the last horizon, so the next poll walks back over the gap.

Measured before paging (feed rows of the 10 days to 2026-11-28): of the entries the poller did
catch, 368 were listed after newer ones had been seen, 326 of them within 2 minutes (the
lookback) and 42 later, and 233 accepted in the evening were listed only the next morning, hours
deep: those are the recheck's.

## Hourly recheck

`poller-recheck` (CronJob `filings-recheck`, weekdays hourly 06:05-23:05 New York) walks the
same feed back to midnight ET of the previous filing day (the last weekday with a filing in
bronze: Friday on a Monday, Wednesday after Thanksgiving), looks every `(accession, filer)` row
up in `bronze.filings` and `bronze.submission_filings` (the nightly check's lookup) and
republishes only the missing ones with `source: recheck`: the feed's own acceptance time and
role, so it is never counted as caught live nor in the live latency. Entries accepted in the
last 10 minutes (`--grace-minutes`) are left to the poller. Why the feed: it is EDGAR's only
listing of today's filings with exact times (the daily index appears after the day, the full
index nightly), and what the poller misses is timing, not form types. EDGAR lists nothing
between 10 pm and 6 am ET; at 6 am it lists the evening's late filings, which the 06:05 run
finds.

Each run is a row of `ops.filing_rechecks` (migration 169, `ops.filing_recheck_status` for
`ops_reader`): pages read, rows listed, missing, republished, and how far back the feed reached
(`complete` is false when it ended, stopped paging or hit `--max-pages` (400) before the
window's start). Requests: one per page, at the `batch` class; ~55-110 pages a run on a median
day (5,200 filer rows), ~1,500 a day. The nightly daily-index check (below) stays the final word.

## Run

```bash
make up-all                                  # in docker-compose, with the other services
set -a; . ./.env; set +a; .venv/bin/poller   # locally
```

Config (env): `SEC_USER_AGENT` (required), `KAFKA_BOOTSTRAP_SERVERS` (default
`localhost:9092`), `POLL_INTERVAL_SECONDS` (default 30), `POLL_MAX_PAGES` (20),
`POLL_LOOKBACK_SECONDS` (120), `POLL_STARTUP_LOOKBACK_SECONDS` (600), `METRICS_PORT`,
`LOG_LEVEL`, and the Kafka authentication variables of [libs/kafka](../../libs/kafka) (SASL/SCRAM on k3s).
SEC requests share one rate limit with every other service (`libs/sec`): `SEC_CALLER_CLASS`
(`live` for the poller, `batch` for the backfill job) and `SEC_LIMITER_DATABASE_URL` (default
`DATABASE_URL`; without either, a local 2 req/s limit).

## Backfill (FR1b)

`poller-backfill --days 7` (or `--start/--end`, `YYYY-MM-DD`; `--end` defaults to yesterday)
publishes filings from EDGAR's daily master indexes to `filings.raw` with
`source: daily-index`. It catches what the live poller and the hourly recheck missed and what
the bulk history lacks: `submissions.zip` can carry a stale copy
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
Filings it republishes carry no 8-K items (the index doesn't have them). Until the nightly
submissions load has a filing's items, the document crawl's 8-K item filter keeps it
(`ops.doc_crawl_plan`, migration 020: unknown items are kept).

## Tests

`pytest services/poller`: feed and daily-index parsing against saved responses in
`tests/fixtures/`, and the poll loop and the recheck with a fake publisher and clock.
`conftest.py` serves the saved feed page's 40 real entries as EDGAR pages them, so a test lists
them as the feed stood at an earlier poll (a burst, a late listing, a failed page, a feed that
stops paging). `test_completeness_db.py` and `test_recheck_db.py` (need `TEST_DATABASE_URL`)
check the missing-filing lookup, the per-day and per-run records and the recheck's window.
