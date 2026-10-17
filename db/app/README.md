# app database

FR7, week 2. The application's own database (`app`), separate from the analytics database
(`tributary`): users, organizations, plans, subscriptions, company grants and watchlists.
In production this would belong to a different service, so other components learn about
changes through Debezium CDC (step 6), never by querying it directly.

- `migrations/`: numbered schema migrations, applied by `make migrate`
- `seed.sql`: demo users covering every access path (`make seed-app`)

`company_id` values refer to `silver.companies` in the `tributary` database; there is no
cross-database foreign key.
