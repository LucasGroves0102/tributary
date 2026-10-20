# app database

FR7, week 2. The application's own database (`app`), separate from the analytics database
(`tributary`): users, organizations, plans, subscriptions, company grants and watchlists.
In production this would belong to a different service, so other components learn about
changes through Debezium CDC ([streaming/debezium](../../streaming/debezium)), never by querying
it directly. [authz-sync](../../services/authz-sync) turns those changes into OpenFGA tuples and
`authz.user_access`.

| Table | Streamed by CDC |
|---|---|
| `users` (with optional `org_id`), `subscriptions` (user → plan), `plan_tiers` (plan → tier), `company_grants` (company → user or organization) | yes |
| `organizations`, `plans`, `watchlists` | no |

Watchlists are preferences only and never grant access.

- `migrations/`: numbered schema migrations, applied by `make migrate`
- `seed.sql`: demo users covering every access path (`make seed-app`): alice (free; NVDA,
  AAPL; watches TSLA), bob (pro; org acme: NVDA, MSFT), carol (pro; no grants), dave (free; acme)

`company_id` values refer to `silver.companies` in the `tributary` database; there is no
cross-database foreign key. Tests: `tests/integration/test_app_schema.py` (needs
`APP_TEST_DATABASE_URL`).
