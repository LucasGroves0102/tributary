# doc-fetcher

FR2b, week 1. Consumes `filings.raw`; for 10-K, 10-Q and 8-K filings, fetches the filing index
page, then the primary document, stores it gzip-compressed in `bronze.filing_documents` and
publishes `documents.fetched` for the [parser](../parser). For 8-Ks it also stores the EX-99.x
exhibits (press releases) in `bronze.filing_exhibits`. Each accession is fetched once.

Which filings (`main.py` `Coverage`):

- **tracked companies**: every 10-K, 10-Q and 8-K event, from any source (feed, daily index, seed);
- **every other operating company** (gold financials, the document crawl's ~6,000): new filings
  from the live feed (`source: feed`), so their documents and sections arrive within minutes. 8-Ks
  only when one of their items, read from the filing index (the first request anyway), is in
  `FETCH_8K_ITEMS` (default the material items: 1.01-1.03, 1.05, 2.01-2.06, 3.01-3.03, 4.01, 4.02,
  5.01-5.03, 7.01, 8.01; kept in step with the crawl's `CRAWL_8K_ITEMS`); an index that lists no
  items is fetched. `FETCH_OPERATING=false` goes back to tracked companies only.

The [document crawl](#document-crawl) still takes everything else (amendments, daily-index
filings, the 8-Ks left out here, anything a fetch error dropped) at its next plan, and skips what
this stored first. Before 2026-11-09 untracked companies waited for that plan: over the week to
2026-11-09 (feed 10-K/10-Q/8-K of operating companies), the median document arrived 138 minutes after
acceptance for 10-Qs (90th percentile 323) and 140 for 8-Ks (322), against 1-2 minutes for tracked
companies; sections followed within a minute either way. This costs 2-3 SEC requests per filing
(index, document, exhibits) at the `live` class of the shared limit: ~3,800 filings a week. The
busiest 10 minutes of that fortnight (2026-11-05, 16:00 ET) brought 244 filings, ~1.2 requests/s;
one consumer takes 1-2 s a filing, so the end of such a burst waits 5-15 minutes, a quiet hour's
filing a minute or two.

| | |
|---|---|
| Input | `filings.raw` (consumer group `doc-fetcher`); tracked CIKs (`silver.companies.tracked`) and operating CIKs (companies with `gold.quarterly_financials`) via `silver.company_ciks`, re-read every 5 minutes |
| Output | `bronze.filing_documents`, `bronze.filing_exhibits`, `documents.fetched` |
| Metrics | `tributary_documents_fetched_total{form}`, `tributary_documents_passed_total{form}` (8-Ks of operating companies with none of `FETCH_8K_ITEMS`), shared Kafka counters from [libs/telemetry](../../libs/telemetry) |

Documents are referenced, not embedded, in `documents.fetched`: a 10-K is ~2 MB of HTML,
over Kafka's default 1 MB message limit. Event shapes are in
[streaming/topics.md](../../streaming/topics.md).

## Jobs

| Command | Make target | Does |
|---|---|---|
| `doc-fetcher` | `make up-all` | the consumer above |
| `doc-fetcher-seed` | `make seed-documents` | publishes each tracked company's last 2 10-Ks and last 4 10-Qs (within 2 years) and last 180 days of 8-Ks to `filings.raw` with `source: seed` and the acceptance time corrected to UTC (`tributary_sec.acceptance_time`), so history flows through the same path as live filings without counting as live in the freshness views |
| `doc-fetcher-exhibits` | `make exhibits` | fetches EX-99.x exhibits for stored 8-Ks that have none, then `make exhibits` re-parses |
| `doc-crawler` | `make crawl` | the document crawl below: every operating company's filings, not just tracked ones |

On k3s, `seed-documents` is a suspended CronJob for filling a new database
(`kubectl -n tributary create job --from=cronjob/seed-documents seed-1`).

Config (env): `SEC_USER_AGENT`, `DATABASE_URL` (both required), `KAFKA_BOOTSTRAP_SERVERS`
(default `localhost:9092`), `FETCH_OPERATING` (default `true`), `FETCH_8K_ITEMS` (default the
material items above; `all` for every 8-K), `METRICS_PORT`, `LOG_LEVEL`, and the Kafka authentication variables of [libs/kafka](../../libs/kafka) (SASL/SCRAM on k3s).
SEC requests share one rate limit with every other service (`libs/sec`): `SEC_CALLER_CLASS`
(`live` for the consumer, `bulk` for the seed job, default `batch`) and
`SEC_LIMITER_DATABASE_URL` (default `DATABASE_URL`).

## Document crawl

`doc-crawler` (`crawl.py`, `crawl_fetch.py`, `crawl_db.py`; migration `020_doc_crawl.sql`)
fetches the 10-K, 10-K/A, 10-Q, 10-Q/A and 8-K primary documents and 8-K EX-99.x exhibits of
every company with gold financials (the operating companies, ~6,000) for the last `CRAWL_YEARS`
years, into the same bronze tables, in the same gzip format the live doc-fetcher writes and
both parsers read. 8-Ks only with an item in `CRAWL_8K_ITEMS` (default `2.02,7.01,8.01`:
results, Reg FD and other events, where the press releases are; `all` for every 8-K).

| | |
|---|---|
| Input | `ops.doc_crawl_plan` over `silver.filings` (+ 8-K items from `bronze.submission_filings`), `gold.quarterly_financials` |
| Output | `bronze.filing_documents`, `bronze.filing_exhibits`, `documents.backfill` (parsed by `parser-backfill`), `ops.doc_crawl_targets` |
| Ops | `ops.doc_crawl_status` (one row: state, totals, rate, ETA, projected size), `ops.doc_crawl_progress` (per form and year), both readable by `ops_reader` |
| Metrics | `tributary_doc_crawl_filings_total{form,outcome}`, `_files_total`, `_bytes_total{kind=raw\|stored}`, `_events_published_total`, `_remaining_filings`, `_disk_free_bytes`, `_min_free_bytes`, `_state{state}`, `_last_progress_timestamp_seconds` |
| Alerts | `DocCrawlStalled`, `DocCrawlPausedForDisk`, `DiskSpaceLow` (monitoring chart) |

How it runs:

- **A Deployment, not a CronJob** (`services.doc-crawler`, shipped with `replicas: 0`; on hermes it
  and `parser-backfill` run at 1 and stay on after the 5-year crawl): the crawl
  takes days, Kubernetes restarts it after a crash or node reboot, Prometheus scrapes it the whole
  time (CronJobs here aren't scraped), and `Recreate` keeps it to one pod. When the queue is
  empty it idles and re-plans every `CRAWL_PLAN_HOURS` (6), so it keeps catching what the live
  doc-fetcher leaves (amendments, daily-index filings, other 8-Ks, fetch errors) after the nightly
  `submissions` load. Since 2026-11-09 the live doc-fetcher stores every operating company's new
  10-K, 10-Q and material 8-K from the feed; scaling the crawler to 0 would stop the rest.
- **Queue and checkpoint** in `ops.doc_crawl_targets`, one row per filing. A round claims
  `CRAWL_BATCH` targets (tracked companies first, then most recent first) with a 30-minute lease,
  fetches them with `CRAWL_WORKERS` threads, and stores each filing's documents together with its
  `done` mark in one transaction. A killed run's claims are taken back when the lease expires;
  on SIGTERM unstarted targets are released at once. Re-planning and re-running never
  duplicate anything (keys: accession number); a filing the live doc-fetcher stored meanwhile is
  marked done without a request.
- **Requests**: 10-K/10-Q fetch the primary document named in the submissions history directly
  (one request); 8-Ks read the filing index for the primary document and exhibits, like
  doc-fetcher. All through `sec_client` with `SEC_CALLER_CLASS=bulk`, so the crawl only gets
  tokens the live and batch callers leave and the shared limit keeps the host under the SEC's
  10 req/s. Bodies are gzipped and hashed while they stream; files over
  `CRAWL_MAX_DOCUMENT_MB` (100) or empty are skipped. 404s fail at once; other errors retry
  with backoff (10 min, doubling) up to `CRAWL_MAX_ATTEMPTS` (4); a batch where everything
  fails backs off 5 minutes (`erroring`). Requeue failures with
  `UPDATE ops.doc_crawl_targets SET status = 'pending', attempts = 0 WHERE status = 'failed'`.
- **Parsing**: events go to `documents.backfill` (outbox: a stored filing without a confirmed
  event is republished next round), consumed by `parser-backfill`, the same image as the live
  parser with `PARSER_TOPIC` / `PARSER_GROUP`. The live parser only reads `documents.fetched`,
  so a backlog of crawled documents never delays a new filing (`streaming/topics.md`).
- **Disk guard**: before each round, free space on `CRAWL_DISK_PATH` (`/tmp`, an emptyDir on the
  node disk, which on hermes is the filesystem holding host Postgres) must be at least
  `CRAWL_MIN_FREE_GB` (50), else the crawler pauses (`disk_low`) and rechecks every 5 minutes.
- **Pause** without a deploy: `UPDATE ops.doc_crawl_control SET paused = true` (and `false`).

Sizes, from samples (gzip -9 as stored, measured on the NVIDIA fixtures): 10-K 1.97 MB →
166 KB (11.9×), 10-Q 1.52 MB → 95 KB (16.0×), 8-K 29 KB → 5.6 KB (5.3×). xz -9 would save
another ~28% on 10-K/10-Q (~5-7 GB in total), not worth a second format in both parsers.
Section text is 9-21% of the HTML. Tracked-company averages in bronze (parser-rs README):
10-K 3.3 MB, 10-Q 2.3 MB, 8-K 44 KB, EX-99 exhibit 310 KB. `ops.doc_crawl_status` reports the
measured compression ratio and the projected final stored size as the crawl goes.

Before starting on hermes, check the target count without writing anything:

```sql
BEGIN;
SELECT ops.doc_crawl_plan((current_date - interval '5 years')::date, '{2.02,7.01,8.01}');
SELECT form, count(*) FROM ops.doc_crawl_targets GROUP BY form;
ROLLBACK;
```

## Tests

`pytest services/doc-fetcher`: filing-index parsing (documents, exhibits, 8-K items) and seed
selection against saved EDGAR pages and submissions in `tests/fixtures/`; the live consumer's
coverage and 8-K item filter (`test_main.py`, fakes for the connection and producer); the crawl (`test_crawl.py`: fetching behind
`httpx.MockTransport`, batching, retries, resume, disk guard and publishing with an in-memory
queue and producer; `test_crawl_db.py`: planning, claim order, leases, storing and the ops views,
with `TEST_DATABASE_URL`).
