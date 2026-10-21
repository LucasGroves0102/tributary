# platform-setup

Image for the [platform chart](../helm/platform)'s post-install/upgrade hooks. CI builds it as
`tributary-platform-setup` from the repo root with the OpenFGA CLI, `authz/model.fga` and
`streaming/debezium/app-connector.json` of that commit.

| Command | Hook | Does |
|---|---|---|
| `setup.py openfga` | `setup-openfga` | waits for OpenFGA, finds or creates the `tributary` store, writes `model.fga` as a new model only if it differs from the current one |
| `setup.py connector` | `setup-connector` | waits for Kafka Connect, registers or updates the `app` connector (PUT), waits until it and its task are running |
| `setup.py kafka` | `setup-kafka` (`kafka.sasl.enabled`) | logs in to Kafka as the super user `admin`, upserts every SCRAM user of `kafka-acls.yaml` and deletes unlisted ones, then deletes ACLs not in the file and creates the missing ones |

Config (env, set by the chart): `FGA_API_URL`, `FGA_API_TOKEN` (when OpenFGA auth is enabled),
`CONNECT_URL`, `POSTGRES_USER`, and for `kafka`: `KAFKA_BOOTSTRAP_SERVERS` (the SASL listener),
`KAFKA_ACLS` (the chart's JSON rendering of `deploy/helm/platform/files/kafka-acls.yaml`; a
`.yaml` path works where PyYAML is installed, as in CI) and `KAFKA_PASSWORDS_DIR` (files
`kafka-password-<user>`, mounted from the platform secret). The base image is Debian slim, not
Alpine, for confluent-kafka's wheels. Tests: `pytest tests/platform_setup`. The connector's `${POSTGRES_PASSWORD}` placeholder is replaced
with `${env:POSTGRES_PASSWORD}`, which Connect resolves from its own environment, so the
password is never sent to or stored in Kafka.

Local equivalents: `make authz` and `make cdc` ([scripts](../../scripts)).

```bash
docker build -f deploy/platform-setup/Dockerfile .
```
