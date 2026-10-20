# platform-setup

Image for the [platform chart](../helm/platform)'s post-install/upgrade hooks. CI builds it as
`tributary-platform-setup` from the repo root with the OpenFGA CLI, `authz/model.fga` and
`streaming/debezium/app-connector.json` of that commit.

| Command | Hook | Does |
|---|---|---|
| `setup.py openfga` | `setup-openfga` | waits for OpenFGA, finds or creates the `tributary` store, writes `model.fga` as a new model only if it differs from the current one |
| `setup.py connector` | `setup-connector` | waits for Kafka Connect, registers or updates the `app` connector (PUT), waits until it and its task are running |

Config (env, set by the chart): `FGA_API_URL`, `FGA_API_TOKEN` (when OpenFGA auth is enabled),
`CONNECT_URL`, `POSTGRES_USER`. The connector's `${POSTGRES_PASSWORD}` placeholder is replaced
with `${env:POSTGRES_PASSWORD}`, which Connect resolves from its own environment, so the
password is never sent to or stored in Kafka.

Local equivalents: `make authz` and `make cdc` ([scripts](../../scripts)).

```bash
docker build -f deploy/platform-setup/Dockerfile .
```
