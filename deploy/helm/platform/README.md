# platform

Kafka, Kafka Connect with Debezium, and OpenFGA for the `tributary` chart. Install into the
`platform` namespace; Postgres runs on the host.

## Prerequisites

- Postgres reachable from pods at `hostAddress` (default `10.42.0.1`), `wal_level=logical`, with
  the `app` and `openfga` databases owned by the `tributary` login (which has `REPLICATION`).
- In the `platform` namespace:
  - Secret `platform` with keys `postgres-password` and `openfga-datastore-uri`
    (`postgres://tributary:<password>@postgres:5432/openfga?sslmode=disable`), and with
    `openfga.auth.enabled`: `openfga-preshared-keys` (comma-separated keys OpenFGA accepts) and
    `openfga-api-token` (the key `setup-openfga` sends).
  - Secret `ghcr` (type `kubernetes.io/dockerconfigjson`) for the setup image.

## Install

```bash
helm upgrade --install platform deploy/helm/platform \
  --namespace platform --set images.setup.tag=<commit sha> --wait
```

| Hook | When | Does |
|---|---|---|
| `openfga-migrate` | before install/upgrade | OpenFGA schema migrations |
| `setup-openfga` | after | finds or creates the `tributary` store; writes `authz/model.fga` if it changed |
| `setup-connector` | after | registers the Debezium connector from `streaming/debezium/app-connector.json` |

The setup image (`deploy/platform-setup`) is built by CI with the model and connector config of
that commit. The connector's password is `${env:POSTGRES_PASSWORD}`, resolved inside the Connect
pod, so it is never stored in Kafka.

## Components

| | Kind | Port | Reachable from |
|---|---|---|---|
| Kafka | StatefulSet, 10Gi volume | 9092 | Tributary pods labelled `tributary.io/kafka`, Connect |
| Kafka Connect | Deployment | 8083 | `setup-connector` |
| OpenFGA | Deployment | 8080 | Tributary pods labelled `tributary.io/openfga`, `setup-openfga` |

Kafka runs one broker in KRaft mode; its controller listens on localhost only. Every pod runs as
non-root with a read-only root filesystem (writable config copies live in `emptyDir` volumes), no
capabilities and the `RuntimeDefault` seccomp profile. Everything not listed above is denied.
