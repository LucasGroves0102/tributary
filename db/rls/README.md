# rls

FR8, week 2. Postgres row-level security for the Query API. This folder holds no SQL: the
`query_api` role, its grants and the policies are in
[`../migrations/007_query_api_rls.sql`](../migrations/007_query_api_rls.sql) (column grants in
`008_filing_urls.sql`), applied by `make migrate`. Tests:
`services/query-api/tests/test_rls.py`.
