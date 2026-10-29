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
| `authz` | `user_access`: who can read which company and tier | authz-sync |
| `ops` | freshness and pipeline-health views for Grafana and the ops exporter | views only |

Transforms are SQL functions (`silver.refresh_financial_facts`, `gold.refresh_quarterly_financials`,
`silver.refresh_company_metadata`); Python services only fetch data and call them. Company
lookup: `SELECT * FROM silver.resolve_company('FB');`.

Row-level security: `007_query_api_rls.sql` creates the `query_api` role and policies on
`gold.quarterly_financials` and `silver.filing_sections` (behind `gold.filing_sections`) that
return only rows granted in `authz.user_access` for the transaction's `app.user_id`. `017_ui_data.sql` adds what the
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
`026_feed_status.sql` adds `ops.feed_status()`: when the newest filing was accepted by EDGAR and
when it reached bronze, two timestamps and nothing else. It is SECURITY DEFINER (query_api still
can't read `bronze.filings`) and backs the Query API's public `/v1/status`, which the
[outside uptime check](../deploy/uptime/README.md) reads.

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
