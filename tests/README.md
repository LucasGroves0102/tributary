# tests

Cross-component tests. Unit tests live next to each component (`services/*/tests`,
`libs/*/tests`); `make test` (pytest over `libs`, `services` and `tests`) runs everything.

| Folder | What | Runs in CI |
|---|---|---|
| [`adversarial/`](adversarial) | FR10: 59 attacks + 4 controls against the agent, 0 leaks | replays recordings |
| [`citations/`](citations) | citation-accuracy eval: 32 questions, every figure traced | replays recordings |
| [`integration/`](integration) | app schema and ops views against real Postgres | yes (`TEST_DATABASE_URL`, `APP_TEST_DATABASE_URL`) |
| [`golden/`](golden) | gold vs figures printed in five 10-Ks (149 figures, 12 derived metrics) | yes (`TEST_DATABASE_URL`; the test is in `services/facts-fetcher/tests`) |
| [`propagation/`](propagation) | live revocation timing against the full stack | no (`E2E=1`, `make propagation-test`) |

The live adversarial and citation runs call the Anthropic API and need the stack running
(`make up-all` or at least the Query API); the recorded runs need neither.
