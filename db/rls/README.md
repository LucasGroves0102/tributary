# rls

FR8, week 2. Postgres row-level security for the Query API. This folder holds no SQL: the
`query_api` role, its grants and the policies are in
[`../migrations/007_query_api_rls.sql`](../migrations/007_query_api_rls.sql) (column grants in
`008_filing_urls.sql`), applied by `make migrate`. Tests:
`services/query-api/tests/test_rls.py`.

`017_ui_data.sql` grants `gold.latest_financials` (security_invoker, so the policies above
apply), `gold.financials_coverage` (company ids only, public) and `silver.filings` (the public
SEC filing index).

`018_segments.sql` adds the `granted_financials` policy on `gold.segment_financials` (and
grants the security_invoker view `gold.segments`).
