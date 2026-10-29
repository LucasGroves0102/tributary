# app database

FR7, week 2. The application's own database (`app`), separate from the analytics database
(`tributary`): users, organizations, plans, subscriptions, company grants, watchlists, saved
work (workspaces, reports) and view preferences.
In production this would belong to a different service, so other components learn about
changes through Debezium CDC ([streaming/debezium](../../streaming/debezium)), never by querying
it directly. [authz-sync](../../services/authz-sync) turns those changes into OpenFGA tuples and
`authz.user_access`.

| Table | Streamed by CDC |
|---|---|
| `users` (with optional `org_id`), `subscriptions` (user → plan), `plan_tiers` (plan → tier), `company_grants` (company → user or organization) | yes |
| `organizations`, `plans`, `watchlists` | no |
| `workspaces`, `workspace_items` (saved charts, comps sets, screens; reports the agent writes, migration 005), `recent_filings` (migration 004; owner-only, forced RLS on `app.user_id`) | no: they grant nothing |
| `user_views` (per-user layout of Home, the screener and the watchlist; migration 006; owner-only, forced RLS) | no: it grants nothing |
| `demo_sandboxes` (sandbox user → expiry), `demo_creations` (creation rate limit, HMAC of the client address), `demo_agent_usage` / `demo_agent_daily` (assistant question caps) | no: they grant nothing |

Watchlists are preferences only and never grant access.

- `migrations/`: numbered schema migrations, applied by `make migrate`
- `seed.sql`: demo users covering every access path (`make seed-app`): alice (free; NVDA,
  AAPL; watches TSLA), bob (pro; org acme: NVDA, MSFT), carol (pro; no grants), dave (free; acme)

Demo sandboxes (migration 003, Query API README): "Try the demo" users are ordinary rows in
`users`, `subscriptions` and `company_grants`, so CDC grants and revokes their access like
anyone's; the hourly `demo-cleanup` job deletes them when they expire, and the `demo_*` rows
cascade.

`company_id` values refer to `silver.companies` in the `tributary` database; there is no
cross-database foreign key. Tests: `tests/integration/test_app_schema.py` (needs
`APP_TEST_DATABASE_URL`).
