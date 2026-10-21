# propagation

End-to-end tests against the running stack (`make up`, `make cdc`, `make seed-app`,
`make authz`, authz-sync running). Skipped unless `E2E=1`. Run: `make propagation-test`.

`test_revocation.py` measures the BRD success metric: access revoked in the app database →
gone from OpenFGA **and** `authz.user_access` in under 30 seconds. Cases: a revoked grant
(alice), a Pro → Free downgrade (bob: filings only), and a deleted user (a throwaway user in
acme with a direct grant; the delete cascades to their subscription and grants, and every tuple
naming them and every `user_access` row must go). It restores what it changes.

CI runs them on every push (`.github/workflows/ci.yml`, job `propagation`) against
`compose.ci.yml`: the same Kafka (KRaft), Kafka Connect + Debezium and OpenFGA images as hermes,
pinned by digest, and Postgres with `wal_level=logical`. The job migrates both databases, seeds
the demo users, bootstraps OpenFGA (`scripts/authz-bootstrap.sh` with
`COMPOSE="docker compose -f tests/propagation/compose.ci.yml"`), registers the connector, starts
authz-sync on the runner, waits for `tributary_authz_caught_up 1`, then runs the tests; on failure
it prints authz-sync's log, the connector status and each container's log.
