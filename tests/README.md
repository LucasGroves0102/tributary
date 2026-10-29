# tests

Cross-component tests. Unit tests live next to each component (`services/*/tests`,
`libs/*/tests`); `make test` (pytest over `libs`, `services` and `tests`) runs everything.

| Folder | What | Runs in CI |
|---|---|---|
| [`adversarial/`](adversarial) | FR10: 59 attacks + 4 controls against the agent, 0 leaks | replays recordings |
| [`citations/`](citations) | citation-accuracy eval: 32 questions, every figure traced | replays recordings |
| [`integration/`](integration) | app schema and ops views against real Postgres | yes (`TEST_DATABASE_URL`, `APP_TEST_DATABASE_URL`) |
| [`golden/`](golden) | gold vs figures printed in five 10-Ks (149 figures, 12 derived metrics) | yes (`TEST_DATABASE_URL`; the test is in `services/facts-fetcher/tests`) |
| [`propagation/`](propagation) | live revocation timing against the full stack (`E2E=1`, `make propagation-test`) | yes (job `propagation`, `compose.ci.yml`; fails above 3 s) |
| [`kafka_acls/`](kafka_acls) | every Kafka principal of `deploy/helm/platform/files/kafka-acls.yaml` can do what it grants and nothing else, over SASL/SCRAM | yes (job `propagation`, `E2E=1`) |
| [`platform_setup/`](platform_setup) | the users and ACLs `deploy/platform-setup/setup.py kafka` derives from `kafka-acls.yaml` | yes |

The live adversarial and citation runs call the Anthropic API and need the stack running
(`make up-all` or at least the Query API); the recorded runs need neither.
