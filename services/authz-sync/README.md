# authz-sync

FR7/FR8, week 2. Keeps access in sync with the app database, through Debezium CDC only:

```
app.public.{users,subscriptions,company_grants,plan_tiers,public_catalogs} ─▶ authz-sync ─┬─▶ OpenFGA tuples
tributary: authz.catalog_companies() (every 10 min) ──────────────────────────▶          ├─▶ authz.user_access, authz.user_tiers
                                                                                          └─▶ authz.public_access
```

It rebuilds the current app state from the compacted CDC topics (always replayed from the
beginning, so no consumer offsets), computes the **desired** permissions from the whole
state, and applies only the difference, revocations first. Recomputing from state rather
than applying per-event deltas makes it immune to cross-topic ordering (a subscription
arriving before its plan's tiers) and safe to replay.

What it writes:

| From | OpenFGA | Postgres (`authz.*`, read by RLS) |
|---|---|---|
| users, organizations, plans | `member`, `subscriber` tuples | `user_tiers (user, tier)` |
| `company_grants` (private grants) | `viewer` and `granted` tuples (+ the company's tier pointers) | `user_access (user, company, tier)` |
| catalog membership: `authz.catalog_companies()`, companies with gold financials or a stored document | `company:<id>#catalog@catalog:sec` + tier pointers, whether or not the catalog is public | - |
| `public_catalogs` `('sec', true)` (the open-access switch) | `catalog:sec#reader@user:*` | `public_access (company, tier)`: every catalog company x both tiers |

Opening or closing the catalog is therefore one tuple plus `public_access` (~12,000 rows, one
INSERT or DELETE statement), applied like any other change: OpenFGA first (deletes first),
then the three Postgres tables in one transaction. How to flip it: [db/app](../../db/app/README.md),
"Open access". Catalog membership is re-read every `CATALOG_REFRESH_SECONDS` (default 600), so a
company that gets its first data becomes readable within ten minutes; a load that fails, or that
would shrink the catalog to under half (a gold rebuild mid-way), is ignored and counted in
`tributary_authz_catalog_refused_total`. The `app.public.public_catalogs` topic is optional: on a
deployment where Debezium was already running, it only appears with the row's first update, and
authz-sync starts reading it (from the beginning) within 5 s of that.

It doesn't reconcile until it has caught up to the end of every topic at startup, so a
restart never briefly revokes access that a later event would restore.

It reconciles as soon as a CDC event arrives: it polls for the first message (waking every
0.5 s when idle), then lingers 50 ms for the rest of that app transaction's events, and fetches
with `fetch.wait.max.ms` 100. Revocations reach OpenFGA and `authz.user_access` in 0.11–0.17 s
in CI ([report](../../tests/propagation/report.md)).

Logs the delay from the app database commit to applied permissions for each change.

Upstream: the [app database](../../db/app) streamed by [Debezium](../../streaming/debezium).
Downstream: the [Query API](../query-api) checks OpenFGA (layer 1) and reads the `authz` tables
through row-level security (layer 2, `authz.readable_companies`, db/migrations/030). The model it writes against is
[authz/model.fga](../../authz/model.fga).

| | |
|---|---|
| Input | `app.public.users`, `app.public.subscriptions`, `app.public.plan_tiers`, `app.public.company_grants` (compacted; waits until all exist), `app.public.public_catalogs` (optional, see above); `authz.catalog_companies()` in the Tributary DB |
| Output | OpenFGA tuples in store `tributary`; `authz.user_access` `(user_id, company_id, tier)`, `authz.user_tiers` `(user_id, tier)`, `authz.public_access` `(company_id, tier)` |
| Metrics | `tributary_authz_reconciles_total`, `tributary_authz_changes_total{kind}` (`tuples_*`, `access_*`, `tiers_*`, `public_*`), `tributary_authz_propagation_seconds`, `tributary_authz_caught_up`, `tributary_authz_catalog_companies`, `tributary_authz_catalog_public`, `tributary_authz_catalog_refused_total` |

## Run

Needs `make up`, `make migrate`, `make authz` and `make cdc` first.

```bash
make up-all                                       # in docker-compose
set -a; . ./.env; set +a; .venv/bin/authz-sync    # locally
make propagation-test                             # revoke live and time propagation
```

Config (env): `DATABASE_URL`, `FGA_API_URL` (both required), `FGA_API_TOKEN` (OpenFGA
preshared key, when enabled), `CATALOG_REFRESH_SECONDS` (default 600),
`KAFKA_BOOTSTRAP_SERVERS` (default `localhost:9092`),
`METRICS_PORT`, `LOG_LEVEL`, and the Kafka authentication variables of [libs/kafka](../../libs/kafka) (SASL/SCRAM on k3s).

## Tests

`pytest services/authz-sync`: state rebuilding and desired-permission computation are pure
(`state.py`); the reconciler is tested against a fake OpenFGA client and Postgres when
`TEST_DATABASE_URL` is set. The live end-to-end check is [tests/propagation](../../tests/propagation).
