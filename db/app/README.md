# app database

FR7, week 2. The application's own database (`app`), separate from the analytics database
(`tributary`): users, organizations, plans, subscriptions, company grants, watchlists, saved
work (workspaces, theses, models, reports, thesis checks), view preferences, filing alerts (what
alerts, the alerts themselves, red lines, webhooks and their deliveries), and each user's own
market-data connection and prices ("Connect your data", Query API README).
In production this would belong to a different service, so other components learn about
changes through Debezium CDC ([streaming/debezium](../../streaming/debezium)), never by querying
it directly. [authz-sync](../../services/authz-sync) turns those changes into OpenFGA tuples and
the `authz` tables row-level security reads (`user_access`, `user_tiers`, `public_access`).

| Table | Streamed by CDC |
|---|---|
| `users` (with optional `org_id`), `subscriptions` (user → plan), `plan_tiers` (plan → tier), `company_grants` (company → user or organization) | yes |
| `public_catalogs` (migration 008: catalog → public or not; the open-access switch, below) | yes |
| `debezium_heartbeat` (migration 002: Debezium writes it every few seconds so its replication slot advances while the app tables are idle) | yes |
| `organizations`, `plans` | no |
| `watchlists` (companies a user follows; owner-only, forced RLS on `app.user_id` since migration 045) | no: watching grants nothing |
| `workspaces`, `workspace_items` (saved charts, comps sets, screens; reports the agent writes, migration 005; theses and models the user writes, migration 009; own metrics (a saved formula, at most 20 per user), migration 022: state up to 128 KiB for reports and theses, 64 KiB for models since migration 015 (F2's per-year rates, adjustments and price), 16 KiB otherwise), `recent_filings` (migration 004; owner-only, forced RLS on `app.user_id`). Migration 012 adds `workspaces.project` (NULL for a plain folder; for an analysis project the company, peers, "How it makes money" notes and steps marked done, up to 16 KiB), covered by the same forced RLS | no: they grant nothing |
| `user_views` (per-user layout of Home, the screener and the watchlist; migration 006; owner-only, forced RLS) | no: it grants nothing |
| `thesis_checks` (migration 010: the history of "Check my thesis" results per thesis, written only by agent-api through the Query API; owner-only, forced RLS, `(item_id, user_id)` references the thesis and cascades) | no: it grants nothing |
| `item_versions` (migration 014: the version of a thesis, or since F2 of a model, each save replaced, the newest 30 per item, written by the Query API in the save's transaction; owner-only, forced RLS, `(item_id, user_id)` references the item and cascades) | no: it grants nothing |
| `data_connections` (migration 011: a user's own market-data API key per provider, envelope-encrypted with a master key that is never in this database, plus `last4`), `market_prices` (end-of-day prices fetched with that key, per user), `market_price_fetches` (last fetch per user and company: the once-a-day window); owner-only, forced RLS on `app.user_id`; prices and fetches reference the connection and cascade (disconnect deletes them) | no: they grant nothing |
| `alert_settings` (forms alerted per followed company), `notifications` (one per user, filing and company: form, dates, an 8-K's items, the key figures and red-line results as read for that user, pending / ready / skipped, read), `red_lines` (a formula-language rule, operator and constant per company), `webhooks` (an https URL, format, a random salt the signing secret is derived from: no secret stored), `webhook_deliveries` (the log, with retries; `(webhook_id, user_id)` and `(notification_id, user_id)` reference their rows), `alert_checks` (when the alerts job last looked; since migration 018 rewritten only when it changes or hourly); migration 016, owner-only, forced RLS on `app.user_id`, written by the Query API's routes and its `query-api-alerts` job as the user. Migration 017: `alert_settings.other_8ks` (8-Ks whose only items are 7.01/8.01 alert too; false by default and for every earlier row) and `webhooks.stalls` / `stalled_until` (consecutive stalled sends; the job leaves the webhook out until then); catalog-only ADD COLUMNs, an instant's ACCESS EXCLUSIVE on each table (lock_timeout 5 s) | no: they grant nothing |
| `watches` (migration 024: a user's watch on one of their saved screens: companies entering, leaving, up to 5 lines (a column above or below a value, for one company or any in it), the screener columns it was shown with; what the alerts job saw last as the owner: company ids only (`members`, at most 500; per line `near`), a `basis` hash, when and how it checked), `screen_alerts` (one per watch per check that found something: the screen's name and what entered, left or crossed, at most 20 companies listed), `webhook_deliveries.screen_alert_id` (a delivery carries a filing notification or a screen alert, never both; neither: a test send); owner-only, forced RLS on `app.user_id`; a watch references its screen as `(item_id, user_id)` and goes with it, an alert with its watch; written by the Query API's routes and the alerts job as the user | no: they grant nothing |
| `alert_runs` (migration 018: one row, when the alerts job's last run started and whether it checked every user it planned; "last checked" reads the newer of it and the user's `alert_checks`; no user data, so no row-level security; CREATE TABLE only) | no: it grants nothing |
| `api_keys` (migration 019: a user's personal API keys: name, the key's SHA-256 and first 8 characters (never the key), scopes `{read}` or `{read,write}`, created / last used / expires (at most 366 days) / revoked; CHECKs: no `demo-` user, name 1-60 characters, prefix `trb_` + 4, a 32-byte hash) and `api_key_usage` (per key per UTC day: requests, refused for a limit, and the current minute's count; `(key_id, user_id)` references the key and cascades); owner-only, forced RLS on `app.user_id`, plus `key_holder` on `api_keys`: a transaction that sets `app.api_key_hash` reads that one key's row (SELECT only), the Query API's lookup before it knows the user; written only by the Query API (Query API README, "API keys") | no: they grant nothing beyond what the key's user reads |
| `oauth_clients` (migration 025: apps that may ask to connect: one registered by dynamic registration (`trc_` id, an optional secret's SHA-256) or one identified by its client ID metadata document (the https URL, the document's name and redirect URIs cached with `fresh_until`); no user data, no RLS), `oauth_grants` (a user's approval of an app: scopes `{read}`, the MCP server's URL as resource, a unique `consent_hash` so a request is approved once, revoked; CHECK: no `demo-` user), `oauth_codes` (single-use, 60 s, bound to grant, redirect URI and an S256 challenge), `oauth_tokens` (access `tro_` 1 h, refresh `trr_` 30 days and rotated: SHA-256 and first 8 characters only) and `oauth_usage` (the API-key limits per grant); grants, codes, tokens and usage owner-only with forced RLS on `app.user_id`, plus `code_holder` / `token_holder`: a transaction that sets `app.oauth_code_hash` or `app.oauth_token_hash` reads that one row (SELECT only); written only by the Query API (Query API README, "Connected apps (OAuth)") | no: a connected app reads nothing its user doesn't |
| `demo_sandboxes` (sandbox user → expiry), `demo_creations` (creation rate limit, HMAC of the client address), `demo_agent_usage` / `demo_agent_daily` (assistant question caps) | no: they grant nothing |
| `github_accounts` (migration 007: GitHub id → `gh-<id>` user, login, first/last sign-in) | no: identity, not access |
| `password_accounts` (migration 013: owner-created username → `pw-<username>` user, scrypt hash, own daily assistant cap, disabled, lockout, `sessions_valid_after`, first/last sign-in) | no: identity, not access |
| `portfolio_holdings`, `portfolio_lots`, `portfolio_sells`, `journal_entries` (migration 021: a user's holdings by company, or by symbol for a fund Tributary doesn't cover; each buy and sale; their own last typed or imported price; an optional link to their Analysis project or thesis (ON DELETE SET NULL of that column); the decision journal by company, kept as written after 24 hours by a trigger (`restrict_violation`; cascades from a deleted user pass); an account label never holds five digits in a row or six in all); owner-only, forced RLS on `app.user_id`; 021 also lets `notifications.sources` say `portfolio` | no: they grant nothing |

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
with gold financials or a stored filing document (`authz.catalog_companies()`; authz-sync keeps that
list). While the row says public, every signed-in user (named users, sandboxes, GitHub and password
accounts: anyone with a JWT) reads
every catalog company within their plan's tiers: authz-sync writes
`catalog:sec#reader@user:*` to OpenFGA and `authz.public_access` rows for RLS. A request
without a user reads nothing, as before. Company grants keep working for anything outside the
catalog. Migration 008 ships the row **off**; production runs with it open and both plans
reading both tiers (checked 2026-11-09).

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
since sandboxes, GitHub and password accounts are on pro.

On a deployment where the connector already ran before 008 (hermes), Debezium does not
snapshot the new table, so `app.public.public_catalogs` appears with the row's first update.
authz-sync treats the missing topic as "nothing public" and picks the topic up when it appears.
To create it before the flip (and check the connector streams the table):

```sql
UPDATE public_catalogs SET public = public WHERE catalog_id = 'sec';  -- no-op change event
SELECT tablename FROM pg_publication_tables WHERE pubname = 'app_debezium';  -- lists it
```
