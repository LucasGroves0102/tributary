# app database

FR7, week 2. The application's own database (`app`), separate from the analytics database
(`tributary`): users, organizations, plans, subscriptions, company grants, watchlists, saved
work (workspaces, theses, models, reports, thesis checks), view preferences, and each user's own
market-data connection and prices ("Connect your data", Query API README).
In production this would belong to a different service, so other components learn about
changes through Debezium CDC ([streaming/debezium](../../streaming/debezium)), never by querying
it directly. [authz-sync](../../services/authz-sync) turns those changes into OpenFGA tuples and
`authz.user_access`.

| Table | Streamed by CDC |
|---|---|
| `users` (with optional `org_id`), `subscriptions` (user → plan), `plan_tiers` (plan → tier), `company_grants` (company → user or organization) | yes |
| `public_catalogs` (migration 008: catalog → public or not; the open-access switch, below) | yes |
| `organizations`, `plans`, `watchlists` | no |
| `workspaces`, `workspace_items` (saved charts, comps sets, screens; reports the agent writes, migration 005; theses and revenue models the user writes, migration 009: state up to 128 KiB for reports and theses, 16 KiB otherwise), `recent_filings` (migration 004; owner-only, forced RLS on `app.user_id`). Migration 012 adds `workspaces.project` (NULL for a plain folder; for an analysis project the company, peers, "How it makes money" notes and steps marked done, up to 16 KiB), covered by the same forced RLS | no: they grant nothing |
| `user_views` (per-user layout of Home, the screener and the watchlist; migration 006; owner-only, forced RLS) | no: it grants nothing |
| `thesis_checks` (migration 010: the history of "Check my thesis" results per thesis, written only by agent-api through the Query API; owner-only, forced RLS, `(item_id, user_id)` references the thesis and cascades) | no: it grants nothing |
| `data_connections` (migration 011: a user's own market-data API key per provider, envelope-encrypted with a master key that is never in this database, plus `last4`), `market_prices` (end-of-day prices fetched with that key, per user), `market_price_fetches` (last fetch per user and company: the once-a-day window); owner-only, forced RLS on `app.user_id`; prices and fetches reference the connection and cascade (disconnect deletes them) | no: they grant nothing |
| `demo_sandboxes` (sandbox user → expiry), `demo_creations` (creation rate limit, HMAC of the client address), `demo_agent_usage` / `demo_agent_daily` (assistant question caps) | no: they grant nothing |
| `github_accounts` (migration 007: GitHub id → `gh-<id>` user, login, first/last sign-in) | no: identity, not access |
| `password_accounts` (migration 013: owner-created username → `pw-<username>` user, scrypt hash, own daily assistant cap, disabled, lockout, `sessions_valid_after`, first/last sign-in) | no: identity, not access |

Watchlists are preferences only and never grant access.

- `migrations/`: numbered schema migrations, applied by `make migrate`
- `seed.sql`: demo users covering every access path (`make seed-app`): alice (free; NVDA,
  AAPL; watches TSLA), bob (pro; org acme: NVDA, MSFT), carol (pro; no grants), dave (free; acme).
  With the catalog public they all read every company; their grants and plans are the test
  personas for private grants and tiers (what they read with the catalog closed). Production
  doesn't seed them or let anyone sign in as them (`demo.enabled: false`).

Demo sandboxes (migration 003, Query API README): "Try the demo" users are ordinary rows in
`users`, `subscriptions` and (only while the catalog is closed) `company_grants`, so CDC grants
and revokes their access like anyone's; the hourly `demo-cleanup` job deletes them when they expire, and the `demo_*` rows
cascade.

GitHub sign-in (migration 007, Query API README "GitHub sign-in"): the first sign-in writes the
same `users` / `subscriptions` / `watchlists` / workspace rows (and, while the catalog is
closed, `company_grants`) as a sandbox,
plus `github_accounts`, in one transaction; CDC grants the access. These users have no
`demo_sandboxes` row, so `demo-cleanup` never deletes them, but their assistant questions count
in `demo_agent_usage` / `demo_agent_daily` (rows older than 90 days are pruned). No GitHub
access token or email address is stored; `users.email` is GitHub's no-reply form.

Password accounts (migration 013, Query API README "Password accounts"): no signup; the owner's
`query-api-account create` writes the same `users` / `subscriptions` / `watchlists` / workspace
rows (and, while the catalog is closed, `company_grants`) as a GitHub sign-in, plus
`password_accounts`, in one transaction; CDC grants the access. `user_id` is `pw-<username>`
(a CHECK ties them), `users.email` is `<username>@password.invalid`. Only a self-describing
scrypt hash is stored (`scrypt$n=..,r=..,p=..$salt$key`, CHECKed), never the password.
`questions_per_day` (0-1000) is the account's own assistant cap, counted in `demo_agent_usage`
but not in the demo's shared `demo_agent_daily`. Like `github_accounts` it isn't streamed and
has no row-level security: sign-in must read it before there is a user id, and only the Query
API's login reads it.

`company_id` values refer to `silver.companies` in the `tributary` database; there is no
cross-database foreign key. Tests: `tests/integration/test_app_schema.py` (needs
`APP_TEST_DATABASE_URL`).

## Open access

`public_catalogs` holds one row per catalog, `('sec', public)`. `catalog:sec` is every company
with gold financials or parsed documents (authz-sync keeps that list). While the row says
public, every signed-in user (named users, sandboxes, GitHub accounts: anyone with a JWT) reads
every catalog company within their plan's tiers: authz-sync writes
`catalog:sec#reader@user:*` to OpenFGA and `authz.public_access` rows for RLS. A request
without a user reads nothing, as before. Company grants keep working for anything outside the
catalog. Migration 008 ships the row **off**.

Open it (on hermes: `sudo -u postgres psql -d app`), one transaction:

```sql
BEGIN;
UPDATE public_catalogs SET public = true WHERE catalog_id = 'sec';
-- every plan reads both tiers (until now free = financials only)
INSERT INTO plan_tiers (plan_id, tier) VALUES ('free', 'filings') ON CONFLICT DO NOTHING;
COMMIT;
```

Close it again (incident switch; leaves plan tiers as they are):

```sql
UPDATE public_catalogs SET public = false WHERE catalog_id = 'sec';
```

Both reach OpenFGA and `authz.public_access` through CDC in well under a second
(`tests/propagation`). The plan-tier insert ships with the flip, not with migration 008, so
deploying the migration changes no one's access; it only affects the free plan (alice, dave),
since sandboxes and GitHub accounts are on pro.

On a deployment where the connector already ran before 008 (hermes), Debezium does not
snapshot the new table, so `app.public.public_catalogs` appears with the row's first update.
authz-sync treats the missing topic as "nothing public" and picks the topic up when it appears.
To create it before the flip (and check the connector streams the table):

```sql
UPDATE public_catalogs SET public = public WHERE catalog_id = 'sec';  -- no-op change event
SELECT tablename FROM pg_publication_tables WHERE pubname = 'app_debezium';  -- lists it
```
