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

`{id}` is the canonical 10-digit `company_id`. Prometheus metrics are on `/metrics` (including
`tributary_permission_checks_total{tier,result}`); the web proxy blocks it and `/docs` from the
public. OpenAPI docs at `http://localhost:8000/docs`.

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
`test_api.py` uses a fake authorizer and store; `test_rls.py` wires the API to an authorizer that
allows everything and checks that row-level security alone still hides ungranted rows (needs
`TEST_DATABASE_URL`).
