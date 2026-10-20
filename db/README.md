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
| `bronze` | data as received: `filings` (filings.raw sink), `company_facts` (XBRL payloads), `filing_documents`, `filing_exhibits` (gzip), `submissions`, `submission_filings`, load checkpoints | bronze-loader, facts-fetcher, doc-fetcher, company-loader |
| `silver` | typed and resolved: `companies`, `company_ciks`, `company_aliases`, `metrics`, `metric_tags`, `financial_facts`, `filing_sections`, `filings` (view) | company-loader, facts-fetcher (via SQL functions), parser |
| `gold` | what the Query API serves, with provenance (FR6): `quarterly_financials`, `filing_sections` (view) | `gold.refresh_quarterly_financials` |
| `authz` | `user_access`: who can read which company and tier | authz-sync |
| `ops` | freshness and pipeline-health views for Grafana and the ops exporter | views only |

Transforms are SQL functions (`silver.refresh_financial_facts`, `gold.refresh_quarterly_financials`,
`silver.refresh_company_metadata`); Python services only fetch data and call them. Company
lookup: `SELECT * FROM silver.resolve_company('FB');`.

Row-level security: `007_query_api_rls.sql` creates the `query_api` role and policies on
`gold.quarterly_financials` and `silver.filing_sections` (behind `gold.filing_sections`) that
return only rows granted in `authz.user_access` for the transaction's `app.user_id`. `010_ops.sql` creates the `ops_reader` group role (SELECT on `ops` views only).

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
