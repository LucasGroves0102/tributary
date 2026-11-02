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

`023_data_notes.sql` grants `ops.quality_findings` and `ops.quality_companies` (data notes) under
the same `granted_financials` policy; query_api gets USAGE on `ops` for them.

`030_public_access.sql` (open access) moves the read rule into one function and re-points all
nine policies at it: a row is visible when its company is in
`authz.readable_companies(<tier>)`, which is the session's `authz.user_access` rows (private
grants) plus `authz.public_access` (the public catalog, written by authz-sync while the app
database's `public_catalogs` row is public) when `authz.user_tiers` gives the user that tier.
No `app.user_id` (unset or empty) reads nothing, public catalog or not. The policies use
`company_id IN (SELECT r FROM authz.readable_companies('<tier>') r)` rather than a per-row
`authz.can_read(company_id, tier)` because Postgres inlines the set-returning function into a
hashed SubPlan built once per query, but never inlines a scalar function with a subquery (that
form ran 50x slower on a 1.2M-row scan). `authz.can_read` is the same rule for one company.
A new policy should use the same `IN (SELECT r FROM authz.readable_companies(...) r)` form.
