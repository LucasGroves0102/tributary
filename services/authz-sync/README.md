# authz-sync

FR7/FR8, week 2. Keeps access in sync with the app database, through Debezium CDC only:

```
app.public.{users,subscriptions,company_grants,plan_tiers} ─▶ authz-sync ─┬─▶ OpenFGA tuples
                                                                        └─▶ authz.user_access
```

It rebuilds the current app state from the compacted CDC topics (always replayed from the
beginning, so no consumer offsets), computes the **desired** permissions from the whole
state, and applies only the difference, revocations first. Recomputing from state rather
than applying per-event deltas makes it immune to cross-topic ordering (a subscription
arriving before its plan's tiers) and safe to replay.

It doesn't reconcile until it has caught up to the end of every topic at startup, so a
restart never briefly revokes access that a later event would restore.

Logs the delay from the app database commit to applied permissions for each change.

Config (env): `DATABASE_URL`, `FGA_API_URL` (both required), `KAFKA_BOOTSTRAP_SERVERS`,
`LOG_LEVEL`.
