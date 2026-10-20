# company-loader

FR5, week 1. Batch job that builds company identity in `silver.companies`,
`silver.company_ciks` and `silver.company_aliases`. Run it daily; each run is idempotent.

Sources:
- `company_tickers.json`: every SEC-registered ticker, CIK and current name (one request)
- `data.sec.gov/submissions`: former names and industry, fetched for tracked companies only
  (the bulk load below covers every company; this keeps tracked ones current between loads)
- `src/company_loader/data/`: curated files the SEC doesn't publish
  - `tracked.csv`: companies whose financials and documents get fetched
  - `successions.csv`: old CIK → new CIK (e.g. Google Inc. → Alphabet)
  - `ticker_history.csv`: retired tickers (e.g. FB)

`company_id` is the first CIK in a company's lineage, so it never changes when a successor
appears. Tickers from `company_tickers.json` are tracked over time: when one disappears its
row gets a `valid_to` instead of being deleted.

Look up a company: `SELECT * FROM silver.resolve_company('FB');`

Config (env): `SEC_USER_AGENT`, `DATABASE_URL` (both required), `LOG_LEVEL`.

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
  per (accession, CIK), `source` = feed / daily-index / submissions

History is never published to `filings.raw`, so doc-fetcher and the ops freshness views only see
live filings. The SEC's `acceptanceDateTime` is labelled UTC but runs ahead of it by the US
Eastern offset; `accepted_at` stores the corrected time.

Each run downloads the zip through `libs/sec` (If-Modified-Since the last finished load; a 304
ends the run), then loads at most `--max-ciks` CIKs whose content changed, 100 CIKs per
transaction, with `bronze.submissions_loads` recording progress per zip snapshot. A rerun skips
what earlier runs loaded, so the first load is many short runs and a crash loses one batch.
Memory stays bounded: the zip is read entry by entry (`bulkzip.py`; `zipfile` would hold ~780 MB
of directory entries).

```bash
set -a; . ./.env; set +a
.venv/bin/company-loader-submissions --zip /path/to/submissions.zip --max-ciks 500
```

Config (env): `DATABASE_URL`, `SEC_USER_AGENT` (unless `--zip`), `SUBMISSIONS_DBNAME` (load
another database on the same server).
