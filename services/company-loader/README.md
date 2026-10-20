# company-loader

FR5, week 1. Batch job that builds company identity in `silver.companies`,
`silver.company_ciks` and `silver.company_aliases`. Run it daily; each run is idempotent.
Every later stage joins through these tables: [facts-fetcher](../facts-fetcher) and
[doc-fetcher](../doc-fetcher) read the tracked CIKs, gold rows are keyed by `company_id`, and
the [Query API](../query-api) searches companies with `silver.resolve_company`.

Sources:
- `company_tickers.json`: every SEC-registered ticker, CIK and current name (one request)
- `data.sec.gov/submissions`: former names and industry, fetched for tracked companies only
  (the bulk load below covers every company; this keeps tracked ones current between loads)
- `src/company_loader/data/`: curated files the SEC doesn't publish
  - `tracked.csv`: companies whose financials and documents get fetched: the 20 demo
    companies plus TSLA (the demo watchlist trap) and LLY (in the adversarial ground truth).
    Every ticker must be in `tests/fixtures/company_tickers.json`
  - `successions.csv`: old CIK → new CIK (e.g. Google Inc. → Alphabet)
  - `ticker_history.csv`: retired tickers (e.g. FB)

`company_id` is the first CIK in a company's lineage, so it never changes when a successor
appears. Tickers from `company_tickers.json` are tracked over time: when one disappears its
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
  time, period of report, primary document
- `silver.companies` metadata for known companies: SIC and industry, tickers, exchanges, fiscal
  year end (MMDD), state of incorporation, filer categories; former names as aliases
  (`silver.refresh_company_metadata`)
- `silver.filings`: the live filings.raw sink (`bronze.filings`) plus the bulk history, one row
  per (accession, CIK), `source` = feed / daily-index / seed / submissions

History is never published to `filings.raw`, so doc-fetcher and the ops freshness views only see
live filings. The SEC's `acceptanceDateTime` is labelled UTC but runs ahead of it by the US
Eastern offset; `accepted_at` stores the corrected time (`tributary_sec.acceptance_time`).

Each run downloads the zip through `libs/sec` to `--download-dir` (default `/tmp`;
If-Modified-Since the last finished load; a 304 ends the run), then loads at most `--max-ciks`
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

On k3s this is the `submissions` CronJob (20,000 CIKs x 10 rounds, 10 minute pauses). It ships
suspended; start a run with `kubectl -n tributary create job --from=cronjob/submissions
submissions-1`.

## Tests

`pytest services/company-loader`: identity building against trimmed real SEC responses in
`tests/fixtures/` and the real curated files; zip reading and submissions parsing. `test_db.py`
and `test_bulk_db.py` need `TEST_DATABASE_URL`.
