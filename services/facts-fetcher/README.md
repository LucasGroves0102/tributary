# facts-fetcher

FR2a. XBRL company facts → `bronze.company_facts` → `silver.financial_facts` →
`gold.quarterly_financials`. Two jobs share the bronze format and the set-based SQL refreshes
(`silver.refresh_financial_facts(text[])`, `gold.refresh_quarterly_financials(text[])`,
`db/migrations/011_bulk_financials.sql`). The tracked-company list and CIK mapping come from
[company-loader](../company-loader); the [Query API](../query-api) serves the gold rows.

**`facts-fetcher`**: the companyfacts API for every CIK of every tracked company, one
transaction per company. Unchanged payloads (same SHA-256) only bump `last_checked_at`.

**`facts-fetcher-bulk`**: every SEC filer, from the nightly `companyfacts.zip` (one request,
streamed to `$BULK_DIR`; `If-Modified-Since` skips an unchanged file). Entries are read one at
a time; only those whose hash differs from the stored payload are parsed and written, in
transactions of 50 that also advance the checkpoint in `bronze.bulk_loads`. A run stops after
`--limit` changed entries (`$BULK_LIMIT`, default 500) and the next run resumes after the last
committed entry, so the first load is a series of short runs and a crash loses one batch at
most. When a run reaches the end it also builds gold for companies that have silver facts but
no gold rows (a CIK mapped after its facts were loaded). `--zip PATH` loads a local file;
`--dir` sets the download directory. `--rounds N --pause-seconds S` (`$BULK_ROUNDS`,
`$BULK_PAUSE_SECONDS`) runs up to N batches from one download with a pause between them,
stopping once the file is fully loaded.

Bronze keeps, per distinct payload, only the facts of tags in `silver.metric_tags`
(`payload`, what SQL reads) plus the full payload gzip-compressed (`raw_gzip`) for the latest
payload of each CIK. Measured on a 400-company sample of the bulk file, extrapolated to all
~20k entries: full payloads as jsonb ≈ 4 GB, gzip ≈ 1.4 GB, mapped facts ≈ 0.26 GB.

`facts-fetcher --rebuild` skips the SEC: it re-projects each CIK's stored raw payload with the
current `silver.metric_tags`, then re-derives silver and gold. Use it after changing the tags
or the gold rules.

## Run

```bash
make financials                                    # facts-fetcher for tracked companies
make financials-rebuild                            # facts-fetcher --rebuild
make financials-bulk ZIP=/path/to/companyfacts.zip LIMIT=500   # facts-fetcher-bulk, local zip
```

On k3s: `financials` CronJob nightly (04:00 New York) and `financials-bulk` (05:00, 12 rounds
of 500 with 10 minute pauses; ships suspended).

Config (env): `DATABASE_URL`, `SEC_USER_AGENT` (both required; the bulk job needs no
`SEC_USER_AGENT` with `--zip`), `BULK_DIR` (default the system temp dir), `BULK_LIMIT`
(default 500), `BULK_ROUNDS` (default 1), `BULK_PAUSE_SECONDS` (default 0), `LOG_LEVEL`.

## Tests

`pytest services/facts-fetcher`: the full bronze → silver → gold path against trimmed real
companyfacts payloads in `tests/fixtures/`, and the bulk loader on small zips. Database tests
need `TEST_DATABASE_URL`.
