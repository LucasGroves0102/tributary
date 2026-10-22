# tributary-kafka

Kafka client settings shared by every Python Kafka service (poller, bronze-loader, doc-fetcher,
parser, authz-sync). `client_config(settings)` returns a confluent-kafka config: connection and
authentication from the environment, plus the client's own settings (`group.id`,
`enable.idempotence`, ...). Settings may not override what the environment sets.

| Variable | Default | |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | |
| `KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` | or `SASL_PLAINTEXT`, `SASL_SSL` |
| `KAFKA_SASL_MECHANISM` | `SCRAM-SHA-512` | or `SCRAM-SHA-256` |
| `KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD` | | required with SASL, rejected without it |
| `KAFKA_SSL_CA_LOCATION` | system store | `SASL_SSL` only |

The Helm chart's `uses: kafka` sets them (per-client SCRAM user when `kafka.sasl.enabled`; see
[deploy/helm/tributary](../../deploy/helm/tributary/README.md)). The Rust parser reads the same
variables. Local runs (`make up`, PLAINTEXT) need none of them.

Tests: `pytest libs/kafka`.
