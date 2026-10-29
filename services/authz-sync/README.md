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

It reconciles as soon as a CDC event arrives: it polls for the first message (waking every
0.5 s when idle), then lingers 50 ms for the rest of that app transaction's events, and fetches
with `fetch.wait.max.ms` 100. Revocations reach OpenFGA and `authz.user_access` in 0.11–0.17 s
in CI ([report](../../tests/propagation/report.md)).

Logs the delay from the app database commit to applied permissions for each change.

Upstream: the [app database](../../db/app) streamed by [Debezium](../../streaming/debezium).
Downstream: the [Query API](../query-api) checks OpenFGA (layer 1) and reads `authz.user_access`
through row-level security (layer 2). The model it writes against is
[authz/model.fga](../../authz/model.fga).

| | |
|---|---|
| Input | `app.public.users`, `app.public.subscriptions`, `app.public.plan_tiers`, `app.public.company_grants` (compacted; waits until all exist) |
| Output | OpenFGA tuples in store `tributary`; `authz.user_access` rows `(user_id, company_id, tier)` |
| Metrics | `tributary_authz_reconciles_total`, `tributary_authz_changes_total{kind}`, `tributary_authz_propagation_seconds`, `tributary_authz_caught_up` |

## Run

Needs `make up`, `make migrate`, `make authz` and `make cdc` first.

```bash
make up-all                                       # in docker-compose
set -a; . ./.env; set +a; .venv/bin/authz-sync    # locally
make propagation-test                             # revoke live and time propagation
```

Config (env): `DATABASE_URL`, `FGA_API_URL` (both required), `FGA_API_TOKEN` (OpenFGA
preshared key, when enabled), `KAFKA_BOOTSTRAP_SERVERS` (default `localhost:9092`),
`METRICS_PORT`, `LOG_LEVEL`, and the Kafka authentication variables of [libs/kafka](../../libs/kafka) (SASL/SCRAM on k3s).

## Tests

`pytest services/authz-sync`: state rebuilding and desired-permission computation are pure
(`state.py`); the reconciler is tested against a fake OpenFGA client and Postgres when
`TEST_DATABASE_URL` is set. The live end-to-end check is [tests/propagation](../../tests/propagation).
