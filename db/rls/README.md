# rls

FR8, week 2. Postgres row-level security for the Query API. This folder holds no SQL: the
`query_api` role, its grants and the policies are in
[`../migrations/007_query_api_rls.sql`](../migrations/007_query_api_rls.sql) (column grants in
`008_filing_urls.sql`) and the later migrations below, applied by `make migrate`. Tests:
`services/query-api/tests/test_rls.py`.

## The rule

`030_public_access.sql` (open access) moved the read rule into one function and re-pointed every
policy at it: a row is visible when its company is in `authz.readable_companies(<tier>)`, which is
the session's `authz.user_access` rows (private grants) plus `authz.public_access` (the public
catalog, written by authz-sync while the app database's `public_catalogs` row is public) when
`authz.user_tiers` gives the user that tier. No `app.user_id` (unset or empty) reads nothing, public
catalog or not. The policies use `company_id IN (SELECT r FROM authz.readable_companies('<tier>') r)`
rather than a per-row `authz.can_read(company_id, tier)` because Postgres inlines the set-returning
function into a hashed SubPlan built once per query, but never inlines a scalar function with a
subquery (that form ran 50x slower on a 1.2M-row scan). `authz.can_read` is the same rule for one
company. A new policy should use the same `IN (SELECT r FROM authz.readable_companies(...) r)` form.

## Policies

| Tier | Tables | Since |
|---|---|---|
| `financials` | `gold.quarterly_financials` | 007 |
| | `gold.derived_financials` | 014 |
| | `gold.segment_financials` (and the security_invoker view `gold.segments`) | 018 |
| | `ops.quality_findings`, `ops.quality_companies` (data notes; query_api gets USAGE on `ops`) | 023 |
| `filings` | `silver.filing_sections` (behind `gold.filing_sections`) | 007 |
| | `gold.section_search`, `gold.section_diffs`, `gold.section_changes` | 021 |
| | `gold.business_profiles`, `gold.business_mentions`, `gold.business_peers` (a peer row needs both companies readable) | 047 |
| | `silver.insider_filings`, `silver.insider_transactions` | 054 |

`authz.user_access` and `authz.user_tiers` show a user only their own rows (`own_rows`);
`authz.public_access` is readable by any signed-in session (`signed_in`).

## Public metadata

Granted without a company policy, because it is the SEC's public index and identity, never a value
or a text: `gold.financials_coverage` (company ids with financials, 017), `silver.filings` (the
filing index, 017), `silver.sic_sectors` / `silver.company_sectors` (046), the columns
`(accession_no, cik, items)` of `bronze.submission_filings` (8-K items, 045) and
`(cik, accession_no)` of `silver.financial_facts` (008). `gold.latest_financials` (017) and the
inlinable readers `gold.latest_value` (032), `gold.latest_reading` and `gold.latest_period` (035),
`gold.amount_currency` (057) and `silver.company_8ks` (045) are security invoker: they read as the
caller, so the policies and grants above apply.

## SECURITY DEFINER functions

Under a policy the planner uses an index condition only if its operator is leakproof. `=` on text
and date comparisons are; `@@` isn't (and only a superuser can mark it), so as `query_api` the GIN
index on `gold.section_search` never runs. Search therefore goes through SECURITY DEFINER functions
that apply `authz.readable_companies('filings')` themselves and return offsets and ranks, never text:
`gold.search_passages` (032) and `gold.search_page` (052: the same with an order and a total; what the
Query API calls). Others read values `query_api` has no grant on (the facts table's values), with
`authz.readable_companies('financials')` as their only rule: `gold.annual_per_share` (036),
`gold.figure_facts` (049) and `gold.filing_figures` (051). Each pins its `search_path`, has EXECUTE
revoked from PUBLIC and granted to `query_api` only, and a `CREATE OR REPLACE` must re-issue the
REVOKE/GRANT. `services/query-api/tests/test_rls.py` holds search, `figure_facts` and
`filing_figures` to the same cases as the policies (`test_readings_db.py` covers `annual_per_share`).

Aggregates and metadata only, no company rule needed: `ops.feed_status` (026), the public status
functions `ops.public_*` (028) and `silver.co_registrants` (047, so `query_api` still reads no
`bronze.filings`).

When reading a plan, EXPLAIN as `query_api`: an owner or BYPASSRLS login gets a plan `query_api`
doesn't.
