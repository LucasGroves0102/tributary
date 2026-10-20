# query-api

FR8, week 2. Read-only HTTP API over the gold tables; the [agent](../agent) and the
[web](../web) app reach data only through it. Every data route is permission-checked twice:

1. **OpenFGA** (`authorizer.py`): `can_read_financials` / `can_read_filings` on the company,
   before any query. Denied → 403 (same response whether the company exists or not).
   OpenFGA unreachable → 503: fail closed.
2. **Postgres row-level security** (`store.py`): queries run as the `query_api` role with
   `app.user_id` set for the transaction; policies return only rows in `authz.user_access`.
   This holds even if a route forgot layer 1.

The caller is identified by an HS256 JWT (`sub` = app user_id). The user is never read from
request parameters. Dev tokens: `make token USER=alice`.

| Route | Needs | Returns |
|---|---|---|
| `GET /health` | | `{"status": "ok"}` |
| `GET /v1/demo/users` | `DEMO_USERS` set | demo accounts (404 when disabled) |
| `POST /v1/demo/token` | `DEMO_USERS` set | an 8-hour token for a demo account |
| `GET /v1/me` | token | companies the caller can read, with tiers |
| `GET /v1/companies/search?q=` | token | candidates (public identity) + caller's access |
| `GET /v1/metrics` | | metric names served by `financials` |
| `GET /v1/companies/{id}/financials?metrics=&quarters=` | financials | values + citation + freshness |
| `GET /v1/companies/{id}/filings?limit=` | filings | recent filings and their sections |
| `GET /v1/filings/latest?limit=` | token | newest parsed filings across the companies the caller can read filings for |
| `GET /v1/companies/{id}/sections/{key}?form=&accession_no=&limit=&max_chars=` | filings | section text + citation |
| `POST /v1/formulas/validate` | token | AST, unit and inputs, or errors with positions |
| `POST /v1/formulas/evaluate` | financials, every company in the formula | series; each point cites every input's filing |

`{id}` is the canonical 10-digit `company_id`. Prometheus metrics are on `/metrics` (including
`tributary_permission_checks_total{tier,result}`); the web proxy blocks it and `/docs` from the
public. OpenAPI docs at `http://localhost:8000/docs`.

## Formulas

Chart layers can be formulas (`formula/`, spec in `docs/design/spec.md` §6):
`net_income / revenue`, `revenue / revenue[-4q] - 1`, `KO.revenue / PEP.revenue`,
`ttm(revenue)`, `abs(free_cash_flow)`. Both routes take
`{"formula", "default_company", "basis", "view", "from", "to"}`; `default_company` is the
company_id of metrics written without a ticker.

- **Grammar** (`grammar.py`): numbers, metric names (every base and derived metric),
  `TICKER.metric`, lags `[-Nq]` / `[-Ny]` on any non-constant term, `ttm()`, `abs()`, `+ - * /`,
  unary minus, parentheses. A recursive-descent parser builds an AST; nothing is evaluated.
  Limits: 500 characters, 24 levels of nesting, 100 nodes, 40 quarters of lag, 5 companies,
  20 distinct input series.
- **Checks** (`check.py`): unknown metrics (with "did you mean"), metrics without values in
  the requested view (`ttm(eps_diluted)`), and units: + and - need the same unit (a constant
  takes the other side's), * and / combine them (USD / shares = USD/shares, USD / USD = ratio).
- **Tickers** resolve through `silver.resolve_company` (best ticker match). A ticker that
  matches nothing and one the caller can't read give the same error
  ("KO isn't one of your companies"); suggestions come only from the caller's own companies.
- **SQL** (`compile.py`): one parameterized query over `gold.financials`; the SQL text holds
  no user input. Each input row gets a period key: with one company and basis `fiscal`, its
  fiscal quarter; otherwise (basis `calendar`, or several companies) the calendar quarter of
  migration 014's rule, using `calendar_primary` rows. A lag of n moves an input's rows n keys
  later. A point exists where the expression is not NULL: a missing input or a division by zero
  is a gap. `view` picks quarter or TTM inputs; `ttm(...)` forces TTM inside it.
- **Errors**: `{"code", "message", "pos", "length", "hint"}`. `validate` returns them with
  200 and `valid: false`; `evaluate` returns 422 (formula) or 403 (company) with
  `{"detail": {"errors": [...]}}`.

`evaluate` checks `can_read_financials` for every company the formula references before the
query, which runs through `Store` under RLS like every other route. Each point has `period`
(`FY2026 Q1` or `CY2025 Q4`), `period_key`, `period_end` (the latest of its unlagged inputs),
`value`, `source_accessions`, and per input its period, value, citation and freshness.

## Run

```bash
make up-all                                       # in docker-compose, on http://localhost:8000
set -a; . ./.env; set +a; .venv/bin/query-api     # locally
make token USER=alice                             # query-api-token: dev JWT for a user
```

Config (env): `DATABASE_URL`, `FGA_API_URL`, `QUERY_API_JWT_SECRET` (all required; the secret
must be at least 32 bytes), `FGA_API_TOKEN` (OpenFGA preshared key, when enabled),
`DEMO_USERS` (comma-separated accounts allowed to sign in without a password; unset disables
the demo routes), `PORT` (default 8000), `LOG_LEVEL`.

## Tests

`pytest services/query-api` (install with `pip install -e 'services/query-api[test]'`).
`test_api.py` and `test_formula_api.py` use a fake authorizer and store; `test_formula.py`
covers the formula grammar, checks and compiled SQL; `test_formula_db.py` evaluates formulas
over real gold rows (PepsiCo, Coca-Cola, Apple) with both permission layers (needs
`TEST_DATABASE_URL`); `test_rls.py` wires the API to an authorizer that
allows everything and checks that row-level security alone still hides ungranted rows (needs
`TEST_DATABASE_URL`).
