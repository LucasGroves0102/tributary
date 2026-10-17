# facts-fetcher

FR2a, week 1. Batch job: fetches XBRL company facts for every CIK of every tracked company,
stores each new payload in `bronze.company_facts`, then refreshes
`silver.financial_facts` and `gold.quarterly_financials` through SQL functions
(`db/migrations/003_financials.sql`). One transaction per company.

Unchanged payloads (same SHA-256) only bump `last_checked_at`, so daily runs are cheap.

`--rebuild` skips the SEC and re-derives silver and gold from stored bronze; use it after
changing `silver.metric_tags` or the gold rules.

Config (env): `SEC_USER_AGENT`, `DATABASE_URL` (both required), `LOG_LEVEL`.
