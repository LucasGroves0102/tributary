# doc-fetcher

FR2b, week 1. Consumes `filings.raw`; for 10-K, 10-Q and 8-K filings, fetches the filing index
page, then the primary document, stores it gzip-compressed in `bronze.filing_documents` and
publishes `documents.fetched` for the [parser](../parser). For 8-Ks it also stores the EX-99.x
exhibits (press releases) in `bronze.filing_exhibits`. Each accession is fetched once. Forms 3, 4
and 5 in the same feed are queued and stored as [insider filings](#insider-filings-forms-3-4-5).

A filing is stored whole or not at all (migration 059): the index, the document and every exhibit
are fetched first, then the document, its exhibits and an `ops.document_outbox` row commit in one
transaction. The outbox row is deleted when Kafka confirms `documents.fetched`; an unconfirmed
event (Kafka down, a crash in between) is sent again at start and every minute
(`tributary_documents_republished_total`). The oldest owed rows are taken among those whose
document is still there, and a row whose document was deleted is cleared, so no number of those
can hold the others back. A fetch error stores nothing, so the
[document crawl](#document-crawl) plans the filing like any other. (Before, an 8-K whose exhibit
fetch failed was committed without exhibits or an event, and the crawl then counted it as
published: it was never parsed.) A database error about one event's data skips that event
(`SKIPPED{reason="db"}`) instead of crash-looping the consumer; a lost connection still restarts it.

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
companies; sections followed within a minute either way. After the change went live (2026-11-09,
from 3 pm ET to the day's last filing at 5:19 pm), 413 of the feed's 428 10-K/10-Q/8-Ks were
stored and parsed, the median 53 s after acceptance and the 95th percentile 77 s (at most 139 s),
including the 4 pm hour's 363 filings; the other 15 were 8-Ks with none of the items or filings of
companies without financials. This costs 2-3 SEC requests per filing
(index, document, exhibits) at the `live` class of the shared limit: ~3,800 filings a week. The
busiest 10 minutes of that fortnight (2026-11-05, 16:00 ET) brought 244 filings, ~1.2 requests/s;
one consumer takes 1-2 s a filing, so the end of such a burst waits 5-15 minutes, a quiet hour's
filing a minute or two.

| | |
|---|---|
| Input | `filings.raw` (consumer group `doc-fetcher`); tracked CIKs (`silver.companies.tracked`) and operating CIKs (companies with `gold.quarterly_financials`) via `silver.company_ciks`, re-read every 5 minutes |
| Output | `bronze.filing_documents`, `bronze.filing_exhibits`, `documents.fetched`; insider filings: `bronze.ownership_documents` → `silver.insider_filings`, `silver.insider_transactions` (below) |
| Metrics | `tributary_documents_fetched_total{form}`, `tributary_documents_passed_total{form}` (8-Ks of operating companies with none of `FETCH_8K_ITEMS`), `tributary_documents_republished_total` (owed events sent again), `tributary_insider_filings_fetched_total{form}`, shared Kafka counters from [libs/telemetry](../../libs/telemetry) |
| Ops tables | `ops.document_outbox` (events owed), `ops.insider_queue` (Forms 3/4/5 waiting, or `failed` with the error), `ops.insider_findings` (what parsing one noticed) |

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
| `doc-fetcher-insiders` | (Helm `insiders-backfill`, `insiders-catchup`) | the bounded insider backfill below, and its daily 7-day catch-up |

On k3s, `seed-documents` is a suspended CronJob for filling a new database
(`kubectl -n tributary create job --from=cronjob/seed-documents seed-1`).

Config (env): `SEC_USER_AGENT`, `DATABASE_URL` (both required), `KAFKA_BOOTSTRAP_SERVERS`
(default `localhost:9092`), `FETCH_OPERATING` (default `true`), `FETCH_8K_ITEMS` (default the
material items above; `all` for every 8-K), `FETCH_INSIDERS` (default `true`), `METRICS_PORT`, `LOG_LEVEL`, and the Kafka authentication variables of [libs/kafka](../../libs/kafka) (SASL/SCRAM on k3s).
The insider job reads `INSIDERS_DAYS` and `INSIDERS_LIMIT` ([below](#insider-filings-forms-3-4-5)),
the crawl its `CRAWL_*` settings ([Document crawl](#document-crawl)).
SEC requests share one rate limit with every other service (`libs/sec`): `SEC_CALLER_CLASS`
(`live` for the consumer; `bulk` for the crawl, the seed and the insider jobs in the Helm values;
default `batch`) and `SEC_LIMITER_DATABASE_URL` (default `DATABASE_URL`).

## Insider filings (Forms 3, 4, 5)

A Form 3/4/5 is the SEC's structured ownershipDocument XML, so it's read, not guessed (#117;
`ownership.py`, migration 054). One request per filing: the full submission text
(`<accession>.txt`), whose `<ownershipDocument>` element is stored as filed in
`bronze.ownership_documents` and parsed in the same transaction by
`silver.refresh_insider_filings` into `silver.insider_filings` (owners, roles, period) and
`silver.insider_transactions` (each line: code, shares, price, acquired/disposed, owned after,
direct/indirect). Idempotent on the accession; a paper filing without the XML is logged and
skipped. No new Kafka client or topic: the live doc-fetcher already reads `filings.raw`.

- **Live** (`FETCH_INSIDERS`): every Form 3/4/5 in the feed under a tracked or operating company's
  CIK, other than the reporting owner's own line (the feed lists a Form 4 once per filer). The
  consumer only queues it (`ops.insider_queue`, migration 059) and fetches the queue, oldest first,
  when Kafka has nothing for it (up to 10 s per idle second), or for 5 s after 5 minutes of
  continuous traffic: 10-Ks, 10-Qs and 8-Ks never wait behind Form 4s (3,268 of them on
  2026-03-03 used to go through the same sequential consumer first).
- **Backfill** (`doc-fetcher-insiders`, Helm `insiders-backfill`, suspended; run once): the
  operating companies' Forms 3/4/5 in `bronze.submission_filings` over the last `INSIDERS_DAYS`
  (90) days that bronze doesn't have, newest first, at most `INSIDERS_LIMIT` (60,000), at the
  bulk class. Running it again resumes (it only takes what's missing); a longer window is
  `INSIDERS_DAYS=365`. Deadline 17 h.
- **Daily catch-up** (Helm `insiders-catchup`, 08:45 after the nightly submissions load): the same
  job over 7 days, for what the live path missed (an outage, a feed gap).

One filing never stops the others: a date that doesn't exist (`2026-02-30`) is read as NULL and a
repeated element as its first value, each with a row in `ops.insider_findings` (`bad_date`,
`several_values`; also `not_well_formed`, and `parse_failed` when Postgres refused the parse: the
filing is then kept in bronze alone).

An outage never loses one. A transient error (no connection, a timeout, a 5xx, a 429 or the SEC's
rate-limit 403) never fails a queued filing: it is retried after 2, 4, 8 ... 128 minutes, then
every 3 hours for as long as the outage lasts (the backfill and the catch-up log it and take it
again next run). A 404 within 24 hours of the feed is EDGAR lagging the feed and is retried the
same way; after that it counts as gone. A filing that can't be stored (gone, no ownershipDocument,
a value Postgres refuses), or that hit any other error 5 times, is kept in `ops.insider_queue` as
`failed` with its error: the live queue leaves it, and the daily catch-up retakes it once a run
while it is inside its 7 days, after the filings it never tried.

Volume and cost, measured 2026-11-09 on hermes' submissions index: operating issuers had 178,877
insider filings in the last 365 days (160,099 Form 4, 14,933 Form 3, 2,438 4/A, 927 Form 5, the
rest amendments) from 4,699 issuers, ~490 a day: one request each, a fraction of the live class.
NVIDIA had 84. The five fixtures' XML averages 8.6 KB, 1.7 KB as Postgres stores it (TOAST), with
about five lines of ~130 bytes each. So a year is ~300 MB of bronze and ~110 MB of silver before
indexes; the 90-day backfill ~44,000 requests (about 2 h at the full 7/s when the bulk class is
idle, longer while the document crawl runs), ~75 MB and ~30 MB.

## Document crawl

`doc-crawler` (`crawl.py`, `crawl_fetch.py`, `crawl_db.py`; migration `020_doc_crawl.sql`)
fetches the 10-K, 10-K/A, 10-Q, 10-Q/A and 8-K primary documents and 8-K EX-99.x exhibits of
every company with gold financials (the operating companies, ~6,000) for the last `CRAWL_YEARS`
years (or since `CRAWL_SINCE`, a date, when that is earlier: a backfill's fixed start), into the same
bronze tables, in the same gzip format the live doc-fetcher writes and
both parsers read. 8-Ks only with an item in `CRAWL_8K_ITEMS` (default `2.02,7.01,8.01`:
results, Reg FD and other events, where the press releases are; `all` for every 8-K).
Production also takes the material-event items (1.01-1.03, 1.05, 2.01, 2.03-2.06, 3.01-3.03, 4.01,
4.02, 5.01-5.03: deals, debt, leadership, impairments, auditor changes, non-reliance), set in the
Helm values: about 65,000 more 8-Ks over the 5 years, about 1.2 GB.
Production's window is 11 years since 2026-11-09 (`CRAWL_YEARS`), reaching back to late 2015 for the
documents of 2016 onward: about 288,000 more filings, roughly +53 GB in the database and 29 hours of fetching at
the crawl's spare rate, then search indexing.

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
  marked done and published without a request or an event: the live side's `documents.fetched`
  announces it, sent or still owed in `ops.document_outbox`. A filing both stored in a race (the
  live side owes its event) is skipped the same way when the crawl publishes, so it is parsed
  once.
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

### Documents before 2021

The first 5-year plan stored documents from 2021-10-21; XBRL figures go back to 2009. Since 2026-11-09
production's window is 11 years (`CRAWL_YEARS`), so the crawl fetches the documents of 2016 onward
(the "From 2016" figures below), newest first. Reaching further back is one setting, `CRAWL_SINCE`
(e.g. `2013-01-01`, not set): the plan then reaches back to that date, and the window never shortens.
`CRAWL_YEARS` alone rolls (14 years reaches 2012-11-09 on that day and moves a day each day), so it
can't stage 2016 then 2013. Measured on hermes (2026-11-09, read only, before the change) for
2013-01-01 to 2021-10-20, operating companies, production's 20 8-K items:

| | 10-K | 10-K/A | 10-Q | 10-Q/A | 8-K | Total |
|---|---|---|---|---|---|---|
| Filings | 27,178 | 3,409 | 80,782 | 2,374 | 294,098 | 407,841 |
| SEC requests | 27,178 | 3,409 | 80,782 | 2,374 | ~815,000 | ~928,000 |
| Stored (gzip) | 4.8 GB | 0.3 GB | 8.0 GB | 0.2 GB | 4.4 GB | 17.8 GB |
| Section text (silver) | 4.6 GB | 0.2 GB | 4.9 GB | 0.1 GB | 3.1 GB | 12.9 GB |
| Search passages (gold) | 13.0 GB | 0.6 GB | 14.2 GB | 0.3 GB | 7.5 GB | 35.6 GB |
| What changed (gold) | 1.5 GB | | 5.9 GB | | | 7.5 GB |

About 74 GB more database (97 GB on 2026-11-09), the weekly full backup about 26 GB larger, ~41
hours of fetching at the crawl's 10,000 filings an hour (the 5-year crawl's rate; parsing keeps pace) and ~53
hours of section-index. From 2016 instead: 287,853 filings, ~53 GB and ~19 GB, ~29 and ~37 hours.
Pre-2019 HTML (no inline XBRL) stores at 0.67 (10-K) and 0.72 (10-Q) of today's per-filing size: 24
sampled 2015 documents beside the same companies' 2025 ones. The parser reads them: of the 24, Item 1A
in 11 of 12 10-Ks, Item 7 in 12 (one a cross-reference), MD&A in 11 of 12 10-Qs. Where it costs: the
weekly full dump and the Sunday restore test (`FREE_SPACE_FACTOR` x the dump) on the same disk
([deploy/host](../../deploy/host/README.md)).

Before changing the window or the items on hermes, check how many filings it adds without writing
anything (here `CRAWL_SINCE=2013-01-01` with production's 20 items; the function returns the count):

```sql
BEGIN;
SELECT ops.doc_crawl_plan('2013-01-01',
  '{1.01,1.02,1.03,1.05,2.01,2.02,2.03,2.04,2.05,2.06,3.01,3.02,3.03,4.01,4.02,5.01,5.02,5.03,7.01,8.01}');
SELECT form, count(*) FROM ops.doc_crawl_targets GROUP BY form;
ROLLBACK;
```

## Tests

`pytest services/doc-fetcher`: filing-index parsing (documents, exhibits, 8-K items) and seed
selection against saved EDGAR pages and submissions in `tests/fixtures/`; the live consumer's
coverage and 8-K item filter (`test_main.py`, fakes for the connection and producer); the crawl (`test_crawl.py`: fetching behind
`httpx.MockTransport`, batching, retries, resume, disk guard and publishing with an in-memory
queue and producer; `test_crawl_db.py`: planning, claim order, leases, storing and the ops views,
with `TEST_DATABASE_URL`); the outbox (`test_main.py`: one transaction per filing, republishing
what Kafka didn't confirm, no second event when the crawl and the live side both store a filing);
insider filings (`test_ownership.py`: the XML read from real NVIDIA submissions in
`tests/fixtures/ownership`, bad dates and repeated elements, a parse failure kept in bronze, the
queue's backoff through a simulated outage, the backfill going on after an error; the database
cases need `TEST_DATABASE_URL`).
