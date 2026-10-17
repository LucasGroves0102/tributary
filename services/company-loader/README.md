# company-loader

FR5, week 1. Batch job that builds company identity in `silver.companies`,
`silver.company_ciks` and `silver.company_aliases`. Run it daily; each run is idempotent.

Sources:
- `company_tickers.json`: every SEC-registered ticker, CIK and current name (one request)
- `data.sec.gov/submissions`: former names and industry, fetched for tracked companies only
- `src/company_loader/data/`: curated files the SEC doesn't publish
  - `tracked.csv`: companies whose financials and documents get fetched
  - `successions.csv`: old CIK → new CIK (e.g. Google Inc. → Alphabet)
  - `ticker_history.csv`: retired tickers (e.g. FB)

`company_id` is the first CIK in a company's lineage, so it never changes when a successor
appears. Tickers from `company_tickers.json` are tracked over time: when one disappears its
row gets a `valid_to` instead of being deleted.

Look up a company: `SELECT * FROM silver.resolve_company('FB');`

Config (env): `SEC_USER_AGENT`, `DATABASE_URL` (both required), `LOG_LEVEL`.
