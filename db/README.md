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
| `gold` | what the Query API serves, with provenance (FR6): `quarterly_financials`, `filing_sections` (view), `segment_financials` (view `segments`) | `gold.refresh_quarterly_financials`, `gold.refresh_segments` |
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
