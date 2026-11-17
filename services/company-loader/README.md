# company-loader

FR5, week 1. Batch job that builds company identity in `silver.companies`,
`silver.company_ciks` and `silver.company_aliases`. Run it daily; each run is idempotent.
Every later stage joins through these tables: [facts-fetcher](../facts-fetcher) and
[doc-fetcher](../doc-fetcher) read the tracked CIKs, gold rows are keyed by `company_id`, and
the [Query API](../query-api) resolves tickers with `silver.resolve_company` and searches companies with `silver.search_companies`.

Sources:
- `company_tickers.json`: every SEC-registered ticker, CIK and current name (one request)
- `data.sec.gov/submissions`: former names and industry, fetched for tracked companies only
  (the bulk load below covers every company; this keeps tracked ones current between loads)
- `src/company_loader/data/`: curated files the SEC doesn't publish
  - `tracked.csv`: companies whose financials and documents get fetched: the 20 demo
    companies plus TSLA (the demo watchlist trap) and LLY (in the adversarial ground truth).
    Every ticker must be in `tests/fixtures/company_tickers.json`
  - `successions.csv`: old CIK → new CIK (e.g. Google Inc. → Alphabet)
  - `re_registrations.csv`: a predecessor CIK linked to a successor issuer (8-K12B/8-K12G3) that was
    already a company here, which keeps its id (Broadcom Inc. through Broadcom Ltd and Avago;
    Marvell). Each row has its evidence: the successor's own figures for periods before the move
    equal the predecessor's, or (before XBRL) the same name plus address or phone, and the
    predecessor's 10-K/10-Q/20-F/40-F stop at the move. Candidates the rules refused (mergers,
    predecessors still reporting, two candidates, figures that disagree) are in
    `re_registrations_uncertain.csv` beside this README, for the owner;
    `re_registrations_review.md` is the 2026-11-17 review of its 25 worth a look (9 linked)
  - `ticker_history.csv`: retired tickers (e.g. FB)

`company_id` is the first CIK in a company's lineage, so it never changes when a successor
appears; a re-registration link keeps the successor's id instead (its users, grants and saved work
hold it). A load that links a CIK to a company marks the company's gold as built at the epoch
(`ops.rebuild_soon`, migration 076): the hourly catch-up rebuilds it first, with the linked CIK's
companyfacts. The rows came from a read-only search of the stored submissions and facts
([db/research/re_registration_search.sql](../../db/research/re_registration_search.sql): every
8-K12B and 8-K12G3, candidates by name, address or phone, their filings and figures; its header has
the rules); the uncertain file's `for_the_owner` column marks the 25 worth a look. The nightly
coverage sweep lists a new unlinked re-registration (`make coverage KIND=re_registration`). Tickers from `company_tickers.json` are tracked over time: when one disappears its
row gets a `valid_to` instead of being deleted.

Look up a company: `SELECT * FROM silver.resolve_company('FB');`

```bash
make companies   # docker-compose job
```

On k3s it runs nightly as the `companies` CronJob (03:00 New York).

Config (env): `SEC_USER_AGENT`, `DATABASE_URL` (both required), `LOG_LEVEL`.
SEC requests share one rate limit with every other service (`libs/sec`): `SEC_CALLER_CLASS`
(`batch` for both jobs) and `SEC_LIMITER_DATABASE_URL` (default `DATABASE_URL`).

## Bulk submissions: `company-loader-submissions`

Loads the SEC's nightly `submissions.zip` (~1.6 GB, one JSON per CIK plus
`CIK..-submissions-NNN.json` pages for long histories) for every filer:

- `bronze.submissions`: each CIK's metadata (the JSON without its filing arrays) and content hash
- `bronze.submission_filings`: every filing per (accession, CIK): form, filing date, acceptance
  time, period of report, primary document, and an 8-K's item numbers (`items`, "2.02,9.01"),
  which the Query API's earnings and events read through `silver.company_8ks` (migration 045)
- `silver.companies` metadata for known companies: SIC and industry, tickers, exchanges, fiscal
  year end (MMDD), state of incorporation, filer categories; former names as aliases
  (`silver.refresh_company_metadata`)
- `silver.filings`: the live filings.raw sink (`bronze.filings`) plus the bulk history, one row
  per (accession, CIK), `source` = feed / daily-index / seed / submissions

History is never published to `filings.raw`, so doc-fetcher and the ops freshness views only see
live filings. The SEC's `acceptanceDateTime` is labelled UTC but runs ahead of it by the US
Eastern offset; `accepted_at` stores the corrected time (`tributary_sec.acceptance_time`).

Each run downloads the zip through `libs/sec` to `--download-dir` (default `/tmp`;
If-Modified-Since the last finished load, sent in UTC; a 304 ends the run), then loads at most `--max-ciks`
CIKs whose content changed (default 500), 100 CIKs per
transaction, with `bronze.submissions_loads` recording progress per zip snapshot. A rerun skips
what earlier runs loaded, so the first load is many short runs and a crash loses one batch.
`--rounds N --pause-seconds S` repeats the load up to N times from one download, pausing S
seconds between rounds and stopping early once the zip is fully loaded.
Memory stays bounded: the zip is read entry by entry (`bulkzip.py`; `zipfile` would hold ~780 MB
of directory entries).

```bash
set -a; . ./.env; set +a
.venv/bin/company-loader-submissions --zip /path/to/submissions.zip --max-ciks 500
```

Config (env): `DATABASE_URL`, `SEC_USER_AGENT` (unless `--zip`), `SUBMISSIONS_DBNAME` (load
another database on the same server; same as `--dbname`).

On k3s this is the `submissions` CronJob, nightly at 05:30 New York (`--max-ciks 20000 --rounds
60`, no pause; a night with an unchanged zip ends at the 304). Start an extra run with
`kubectl -n tributary create job --from=cronjob/submissions submissions-1`.

Sectors aren't loaded: the screener's 12 sectors and 88 industries are Tributary's own fixed
grouping of the SIC code this load stores (`silver.sic_sectors`, migration 046), and the fiscal
year end month people see is read from `fiscal_year_end` by `silver.fiscal_year_end_month` (042).

## Tests

`pytest services/company-loader`: identity building against trimmed real SEC responses in
`tests/fixtures/` and the real curated files; zip reading and submissions parsing. `test_db.py`
and `test_bulk_db.py` need `TEST_DATABASE_URL`.
