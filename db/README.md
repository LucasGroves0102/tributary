# db

All SQL for Tributary. Two Postgres databases on one server:

| Database | Migrations | Contents |
|---|---|---|
| `tributary` | [`migrations/`](migrations) | the bronze / silver / gold medallion, access table, ops views |
| `app` | [`app/migrations/`](app) | users, organizations, plans, grants, watchlists ([app/README.md](app/README.md)) |

OpenFGA keeps its own `openfga` database, migrated by OpenFGA itself.

## Schemas in `tributary`

| Schema | Holds | Written by |
|---|---|---|
| `bronze` | data as received: `filings` (filings.raw sink), `company_facts` (XBRL payloads), `filing_documents`, `filing_exhibits` (gzip), `filing_xbrl` (facts extracted from the documents' inline XBRL), `submissions`, `submission_filings`, load checkpoints | bronze-loader, facts-fetcher, doc-fetcher, company-loader |
| `silver` | typed and resolved: `companies`, `company_ciks`, `company_aliases`, `metrics`, `metric_tags`, `financial_facts`, `filing_sections`, `filings` (view), `segment_axes`, `segment_metrics`, `segment_metric_tags`, `segment_facts` | company-loader, facts-fetcher (via SQL functions), parser |
| `gold` | what the Query API serves, with provenance (FR6): `quarterly_financials`, `filing_sections` (view), `segment_financials` (view `segments`), `section_search`, `section_diffs`, `section_changes` | `gold.refresh_quarterly_financials`, `gold.refresh_segments`, `gold.refresh_section_search`, parser-index |
| `authz` | `user_access` (private grants: user, company, tier), `user_tiers` (each user's plan tiers), `public_access` (public catalog: company, tier); `readable_companies(tier)`, the read rule every policy uses | authz-sync |
| `ops` | freshness and pipeline-health views for Grafana and the ops exporter | views only |

Transforms are SQL functions (`silver.refresh_financial_facts`, `gold.refresh_quarterly_financials`,
`silver.refresh_company_metadata`); Python services only fetch data and call them. Company
lookup: `SELECT * FROM silver.resolve_company('FB');`.

Row-level security: `007_query_api_rls.sql` creates the `query_api` role and policies on
`gold.quarterly_financials` and `silver.filing_sections` (behind `gold.filing_sections`) that
return only rows the transaction's `app.user_id` may read; since `030_public_access.sql` that is
`authz.readable_companies(tier)`: private grants (`authz.user_access`) plus the public catalog
(`authz.public_access`, within the user's `authz.user_tiers`). No `app.user_id`, no rows
([rls/](rls/README.md)). `017_ui_data.sql` adds what the
research UI reads: `gold.latest_financials` (security_invoker, RLS applies),
`gold.financials_coverage` (company ids with financials only; runs as its owner, so public),
`silver.filings` granted as the public SEC filing index, and the functions
`gold.expected_filings`, `gold.filing_recap`, `gold.filing_flags`, `gold.peer_candidates`. `010_ops.sql` creates the `ops_reader` group role (SELECT on `ops` views only).
`018_segments.sql` adds segment and KPI data from the stored 10-K/10-Q documents' inline XBRL
(facts-fetcher's `facts-fetcher-segments`): `bronze.filing_xbrl`, `silver.segment_facts`,
`gold.segment_financials` (RLS `granted_financials`, like quarterly financials) and the view
`gold.segments` (fiscal/calendar labels), the `segment_sum_*` quality checks
(`ops.check_segments`) and `ops.segment_load_status` for `ops_reader`.
`020_doc_crawl.sql` adds the document crawl's queue and checkpoint (`ops.doc_crawl_targets`,
planned by `ops.doc_crawl_plan`), its pause switch (`ops.doc_crawl_control`) and the
`ops.doc_crawl_progress` / `ops.doc_crawl_status` views for `ops_reader`.
`021_section_diffs_search.sql` adds full-text search and "what changed" over parsed sections,
both built by the parser's `parser-index` job: `gold.section_search` (one row per ~2,000-character
passage: offsets + tsvector, GIN index, no text; `gold.refresh_section_search`),
`gold.section_diffs` / `gold.section_changes` (paragraph-level Risk Factors and MD&A diffs vs the
comparable earlier filing: offsets and word-diff opcodes, no text), all three under RLS
`granted_filings`; the checkpoint `ops.section_index` with `ops.section_index_priors` /
`ops.section_index_pending`, and `ops.section_change_summary` / `ops.section_index_status` (counts
only) for `ops_reader`. All three gold tables are derived: rebuild them by running the job.
`022_sec_limiter_role.sql` adds the `sec_limiter` group role: EXECUTE on `ops.sec_take_token` /
`ops.sec_report_throttle` only (now SECURITY DEFINER with a fixed `search_path`, revoked from
PUBLIC), no table access. The login is created on the host
([deploy/helm/tributary](../deploy/helm/tributary/README.md), "SEC limiter login").
`023_data_notes.sql` adds the `restatement` check (severity `info`) to `ops.check_financials` and
`ops.quality_summary`, and lets the Query API read `ops.quality_findings` / `ops.quality_companies`
(USAGE on `ops`, RLS `granted_financials`) for the product's data notes.
`024_gold_ignores_proxy_statements.sql` (found by the [golden 10-K figures](../tests/golden/README.md)):
`gold.refresh_quarterly_financials` skips facts from proxy and information statements (forms
starting DEF or PRE; silver keeps them), and adds Costco's 10-Q pre-tax tag
(`IncomeLossAttributableToParent`, third `pretax_income` tag). Existing data changes on
`make financials-rebuild`.
`025_filing_sections_company_pushdown.sql` puts `company_id` first in `gold.filing_sections`'
DISTINCT ON key (same rows), so a company filter reaches the base tables' indexes instead of
de-duplicating every section first.
`033_filing_sections_accession_pushdown.sql` (listed here with its sibling) makes the view output
`s.accession_no`, the key column: a filter on accession now reaches the base tables too. Before
it, `gold.filing_flags` and so `/v1/filings/{acc}/recap` sorted all 1.8M sections and timed out,
and `gold.refresh_section_search` sorted them once per batch (locally 3.3 s → 12 ms).
`026_feed_status.sql` adds `ops.feed_status()`: when the newest filing was accepted by EDGAR and
when it reached bronze, two timestamps and nothing else. It is SECURITY DEFINER (query_api still
can't read `bronze.filings`) and backs the Query API's public `/v1/status`, which the
[outside uptime check](../deploy/uptime/README.md) reads.
`027_filing_completeness.sql` adds `ops.filing_completeness` (per day of EDGAR's daily index:
accessions indexed, accessions missing from bronze at the last check, rows republished), written
by the nightly completeness check ([poller-backfill](../services/poller/README.md#backfill-fr1b)),
and `ops.filing_completeness_status` for ops_reader. Counts only.
`028_public_status.sql` backs the public status page (Query API `/v1/status/data`): four SECURITY
DEFINER functions (search_path pinned, EXECUTE only for query_api, like 026) that return
aggregates only: `ops.public_ingest()` (last 7 days of `bronze.filings`: filings, how many the live
feed caught, p50/p95 seconds from acceptance to bronze), `ops.public_completeness()` (the last 30
days of `ops.filing_completeness`), `ops.public_coverage()` (companies with gold financials by a
loose index scan of its primary key, stored documents, search-indexed filings and sections) and
`ops.public_quality()` (`ops.quality_summary`). It also indexes `bronze.filings (filed_at)` so the
7-day window is a range scan; the index is built inside the migration, so writes to
`bronze.filings` wait for it.
`029_gold_quality_fixes.sql` fixes two causes of the quality checks' errors in
`gold.quarterly_financials`: flow metrics tagged at an instant (UnitedHealth's dividend payment
dates) no longer become extra quarter rows (1,838 rows on hermes), and a derived quarter that
comes out negative for a metric that can't be (revenue, costs, capex, D&A, SBC, buybacks,
dividends) is left missing instead of published (11,320 rows; e.g. Cytek's Q4 2022 revenue at
-$112.7M after a later 10-K re-tagged the year). Needs `make financials-rebuild`.
`030_public_access.sql` (open access, layer 2): `authz.public_access` and `authz.user_tiers`
(written by authz-sync), the read rule `authz.readable_companies(tier)` / `authz.can_read`,
`authz.catalog_companies()` (catalog membership: companies with gold financials or a stored
document, read by authz-sync) and the nine `granted_*` policies re-pointed at the rule. Ships
with `public_access` empty: no one's access changes until the catalog is opened
([app/README.md](app/README.md), "Open access").
`032_open_access_speed.sql` keeps the cross-company routes fast with the catalog open, without
changing who reads what: `gold.latest_value(company, span, metric)` (`gold.latest_financials`' rule
as one index lookup per table, security invoker, inlined), a last column `filed_on` on
`silver.filings` (the date from the row's own table, so a window reaches both UNION branches), and
`gold.search_passages` (search's first stage: `@@` isn't leakproof, so under RLS the GIN index on
`gold.section_search` is unusable; SECURITY DEFINER with `authz.readable_companies('filings')`
applied inside, search_path pinned, `query_api` only, returns offsets and ranks, no text), and
`gold.expected_filings_recent` (017's estimates for every readable company, from the last 1,500 days
of reports). No index is built; the view takes a brief lock (`lock_timeout` 30 s, the migration fails rather than queueing
readers).
`031_gold_one_quarter_series.sql` gives `gold.quarterly_financials` one non-overlapping series of
quarters per company and metric. Gold kept one row per period end, so a quarter reported with two
(start, end) pairs survived twice: an end date a day apart (NVIDIA's Q2 FY2011 ends Aug 1 in the
10-Q, Jul 31 in a later 10-K), last year's quarters re-labelled to another calendar in this
year's comparatives (ATI, Dine Brands), calendar-month quarters next to fiscal ones after a
change of fiscal year (TranSuite.org). On hermes that was 11,080 `overlapping_quarters` findings
at 620 companies. The rule, for duration rows (instants are points and keep one row per date):
(a) calendar votes: per company and date, how many metrics have a balance-sheet value on it and
how many have any fact ending on it; a quarter scores its last day plus the day before its first
day, so the series whose boundaries carry the company's own balance sheets wins (a re-labelled
comparative has none); (b) rank, best first: instant votes, metric votes, then 029's order
(tag priority, reported over derived, latest filed, accession, ...), then period end: total, so
refreshes stay byte-identical; the same order picks the one row per period end; (c) two rows
overlapping by more than 7 days conflict (quarters are 80-120 days, so they can't share a
series; up to 7 days is a start tagged a day or two early, tolerated like the TTM's 0-7 day
gaps); (d) greedy, best first: a row is dropped only for a kept row it conflicts with (no
quarter is lost to a chain), and rows without a conflict are untouched. Derived quarters never
mix series: both inputs come from one year-to-date chain (same start), and the start-day vote
keeps the Q4 of the chain whose quarters are kept. A read-only simulation on hermes' gold took
the 620 companies from 11,041 overlaps to 1,686, all of 1-7 days, dropping ~6,400 rows; the
fixture companies (Costco's 16-week Q4 included) and the golden 10-K figures are unchanged.
Trade-off: when a company re-labels and also restates last year's quarters (ATI 2022-2023),
gold keeps the original filing's dates and values. Needs `make financials-rebuild`.

## Migrations

`scripts/migrate.sh <dir> <database-url>` applies `<dir>/*.sql` in name order, each once, in
one transaction together with its row in `public.schema_migrations`.

```bash
make migrate    # both databases, from .env
make seed-app   # demo users in the app database
make psql       # psql shell on tributary
```

Files are numbered (`001_bronze.sql`, ...) and never edited after they land: changes go in a
new file. On k3s, the Helm chart's `migrate` pre-install/upgrade Job runs the same script from
the image built by [`Dockerfile`](Dockerfile) and seeds demo users when `demo.enabled`.

[`rls/`](rls) and [`transforms/`](transforms) hold no SQL; their READMEs point to the
migrations that define those objects.
