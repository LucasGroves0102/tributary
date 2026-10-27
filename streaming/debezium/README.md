# debezium

FR7, week 2. Kafka Connect + Debezium stream the `app` database's access tables into Kafka:
`app.public.users`, `app.public.subscriptions`, `app.public.company_grants`,
`app.public.plan_tiers`. `authz-sync` consumes them.

- `app-connector.json`: connector config; `make cdc` registers or updates it (idempotent PUT).
  `${POSTGRES_USER}` / `${POSTGRES_PASSWORD}` are filled from `.env` at registration.
  `poll.interval.ms` is 50 (Debezium's default is 500): the connector sleeps that long both
  when the replication stream is idle and before handing a batch to Kafka, so it sets most of
  the revocation latency (tests/propagation/report.md). Polling an idle stream every 50 ms
  costs next to no CPU.
- Topics are **compacted**: Kafka keeps the latest event per row forever, so replaying a topic
  from the beginning always reconstructs the table's current state.
- Values are plain JSON envelopes: `{"before": {...}, "after": {...}, "op": "c|u|d|r", ...}`.
  Deletes carry the full `before` row because the tables use `REPLICA IDENTITY FULL`; each
  delete is followed by a tombstone (null value) that lets compaction drop the row.

Connect's REST API is on host port 8083: `curl localhost:8083/connectors/app/status`.
