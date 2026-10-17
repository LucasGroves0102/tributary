# query-api

FR8, week 2. Read-only HTTP API over the gold tables; the agent (step 8) reaches data only
through it. Every data route is permission-checked twice:

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
| `GET /v1/me` | token | companies the caller can read, with tiers |
| `GET /v1/companies/search?q=` | token | candidates (public identity) + caller's access |
| `GET /v1/companies/{id}/financials?metrics=&quarters=` | financials | values + citation + freshness |
| `GET /v1/companies/{id}/filings` | filings | recent filings and their sections |
| `GET /v1/companies/{id}/sections/{key}?form=&max_chars=` | filings | section text + citation |

OpenAPI docs at `http://localhost:8000/docs`.

Config (env): `DATABASE_URL`, `FGA_API_URL`, `QUERY_API_JWT_SECRET` (all required), `PORT`.
