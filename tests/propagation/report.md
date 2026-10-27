# Revocation propagation (FR8)

2026-10-27 00:23 UTC, CI job `propagation`,
[run 40426065085](https://github.com/LucasGroves0102/tributary/actions/runs/40426065085/job/121932014285)
(commit `46267f5`, branch `claims`). App database change committed → gone from (or present in)
OpenFGA **and** `authz.user_access`, measured by `test_revocation.py` and `test_sandbox.py`
against `compose.ci.yml` (Kafka with SASL/SCRAM + ACLs, Debezium, OpenFGA, Postgres, authz-sync
on the runner).

| | |
|---|---|
| Measurements | 22: 16 revocations (three scenarios 5 times, plus the sandbox cleanup) and 6 grants |
| **Slowest revocation** | **0.17 s** |
| Median revocation | 0.16 s (revoked grant), 0.11 s (Pro → Free), 0.11 s (deleted user) |
| BRD target | under 30 s (enforced) |
| Regression guard | under 3 s (enforced) |
| Before this change, same job | 0.48-0.96 s ([run 40424756272](https://github.com/LucasGroves0102/tributary/actions/runs/40424756272/job/121927618230)); 0.90-1.01 s in earlier runs |

| Scenario | Runs | Median s | Max s | All s |
|---|---|---|---|---|
| Revoked grant (alice loses Nvidia) | 5 | 0.16 | 0.17 | 0.16, 0.16, 0.16, 0.17, 0.16 |
| Re-grant (restoring it) | 5 | 0.17 | 0.17 | 0.16, 0.16, 0.17, 0.17, 0.17 |
| Pro → Free (bob loses filings, keeps financials) | 5 | 0.11 | 0.16 | 0.16, 0.11, 0.11, 0.11, 0.11 |
| Deleted user (cascade: every tuple and row) | 5 | 0.11 | 0.17 | 0.11, 0.11, 0.17, 0.17, 0.11 |
| Demo sandbox granted | 1 | 0.18 | 0.18 | 0.18 |
| Demo sandbox revoked by cleanup | 1 | 0.13 | 0.13 | 0.13 |

## What changed

The pipeline was idle-waiting, not working: fixed waits added up to about a second.

| Where | Before | After |
|---|---|---|
| Debezium `poll.interval.ms` ([app-connector.json](../../streaming/debezium/app-connector.json)): the connector sleeps this long when the replication stream is idle, and again before handing a batch to Kafka | 500 ms (default) | 50 ms |
| authz-sync `consume(500, timeout=0.5)`: blocks until 500 messages **or** the whole timeout, so a lone revocation waited 0.5 s | up to 500 ms | returns on the first message, then lingers 50 ms for the rest of its transaction (`next_batch`) |
| authz-sync `fetch.wait.max.ms` (an idle fetch's hold on the broker; data ends a fetch at once either way) | 500 ms | 100 ms |

Both ship to production unchanged: the connector JSON is baked into the `platform-setup` image
(the `setup-connector` hook PUTs it), and authz-sync's consumer settings are in its code (built
with `tributary_kafka.client_config`). Replay semantics, deletes-first and the startup catch-up
are untouched. Idle cost: Debezium reads the replication socket every 50 ms and authz-sync's
consumer fetches every 100 ms instead of every 500 ms.

## How to read the numbers

`wait_until` polls every 50 ms, and each poll is an OpenFGA check plus a Postgres query, so the
figures are upper bounds with ~55 ms resolution (they cluster at 0.11 s and 0.16-0.17 s: two and
three polls). One CI run on a shared GitHub runner; production runs the same images and
settings but hasn't been measured with this script. The job prints every measurement in its
summary on each push (`PROPAGATION_RESULTS`, `conftest.py`); CI logs expire, this file doesn't.
