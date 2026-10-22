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
    `openfga-api-token` (the key `setup-openfga` sends), and with `kafka.sasl.enabled`:
    `kafka-password-admin` and `kafka-password-<principal>` for every principal in
    `files/kafka-acls.yaml` ([Kafka authentication](#kafka-authentication-saslscram)).
  - Secret `ghcr` (type `kubernetes.io/dockerconfigjson`) for the setup image.

## Install

Flux installs it from [deploy/flux/releases/platform.yaml](../../flux/releases), with the setup
image tag kept current by image automation. By hand:

```bash
helm upgrade --install platform deploy/helm/platform \
  --namespace platform --set images.setup.tag=<commit sha> --wait
```

| Hook | When | Does |
|---|---|---|
| `openfga-migrate` | before install/upgrade | OpenFGA schema migrations |
| `setup-openfga` | after | finds or creates the `tributary` store; writes `authz/model.fga` if it changed |
| `setup-kafka` | after, first (`kafka.sasl.enabled`) | makes Kafka's SCRAM users and ACLs match `files/kafka-acls.yaml` |
| `setup-connector` | after | registers the Debezium connector from `streaming/debezium/app-connector.json` |

The setup image (`deploy/platform-setup`) is built by CI with the model and connector config of
that commit. The connector's password is `${env:POSTGRES_PASSWORD}`, resolved inside the Connect
pod, so it is never stored in Kafka.

## Components

| | Kind | Port | Reachable from |
|---|---|---|---|
| Kafka | StatefulSet, 10Gi volume | 9092 (PLAINTEXT), 9094 (SASL, `kafka.sasl.enabled`) | Tributary pods labelled `tributary.io/kafka`, Connect, kafka-exporter in `monitoring`, `setup-kafka` |
| Kafka Connect | Deployment | 8083 | `setup-connector` |
| OpenFGA | Deployment | 8080 | Tributary pods labelled `tributary.io/openfga`, `setup-openfga` |

Kafka runs one broker in KRaft mode; its controller listens on localhost only. Every pod runs as
non-root with a read-only root filesystem (writable config copies live in `emptyDir` volumes), no
capabilities and the `RuntimeDefault` seccomp profile. Everything not listed above is denied.

## Kafka authentication (SASL/SCRAM)

Off by default (`kafka.sasl.*`, `kafka.plaintextListener`), so the chart renders exactly as
before until each phase below is switched on. With it on, every Kafka client logs in as its own
SCRAM user and may use only its own topics and groups: a second layer behind the NetworkPolicies,
so a pod that gets Kafka network access (a policy mistake, a compromised service) still can't
read the CDC topics or forge a grant.

**Listener.** `SASL` (SASL_PLAINTEXT, SCRAM-SHA-512) on `kafka.sasl.port` (9094), next to
today's `PLAINTEXT` on 9092 until phase C. No TLS, on purpose: Kafka and all its clients run on
one node, so the traffic never leaves hermes' pod bridge, and SCRAM is a salted
challenge-response that never sends the password, so even a capture on the node yields no
credential. Someone able to capture there is root on the node and can read the Secrets anyway;
the payloads are public SEC data and the app tables' CDC (IDs, plans, grants, no passwords). TLS
would add a CA, certificate rotation and truststores for four kinds of client for no gain on one
node; if Kafka ever spans nodes, use SASL_SSL (libs/kafka and parser-rs already accept
`KAFKA_SECURITY_PROTOCOL=SASL_SSL` and `KAFKA_SSL_CA_LOCATION`). Inter-broker traffic uses a
`BROKER` listener on `localhost:9095` (`kafka.sasl.internalPort`); the KRaft `CONTROLLER`
listener stays on `localhost:9093`. Neither is reachable from another pod.

**Authorizer.** `StandardAuthorizer` with `allow.everyone.if.no.acl.found=false`: no ACL, no
access. Super users: `admin` (setup-kafka's login) and `ANONYMOUS`, the principal of every
connection without SASL. ANONYMOUS covers the broker itself, the controller and scram-admin on
the localhost listeners and, until phase C, the existing PLAINTEXT clients, so phase A changes
nothing for them. After phase C nothing outside the Kafka pod can connect without SASL.

**Users and ACLs** come from [`files/kafka-acls.yaml`](files/kafka-acls.yaml). Two steps create
them, both idempotent:

- `scram-admin`, a second container in the Kafka pod (`files/kafka-admin.sh`, the Kafka image's
  `kafka-configs.sh`), creates the SCRAM user `admin` over `localhost:9095`, the only way in
  before any SCRAM user exists, and updates it when `kafka-password-admin` changes.
- The `setup-kafka` hook (`deploy/platform-setup/setup.py kafka`, confluent-kafka's admin API)
  logs in as `admin` after every release and makes the broker match the file: it upserts every
  listed user from `kafka-password-<user>`, deletes SCRAM users that aren't listed (except
  `admin`), deletes ACLs that aren't in the file (deletes first), then creates the missing ones.
  A missing or short (< 16 characters) password fails the hook, and with it the release.

| Principal | Client | Topics | Groups |
|---|---|---|---|
| `poller` | poller | write + create `filings.raw` | |
| `poller-backfill` | job `backfill` | write + create `filings.raw` | |
| `doc-fetcher-seed` | job `seed-documents` | write + create `filings.raw` | |
| `bronze-loader` | bronze-loader | read `filings.raw` | `bronze-loader` |
| `doc-fetcher` | doc-fetcher | read `filings.raw`; write + create `documents.fetched` | `doc-fetcher` |
| `doc-crawler` | doc-crawler | write + create `documents.backfill` | |
| `parser` | parser (Python or Rust) | read `documents.fetched` | `parser-python` |
| `parser-backfill` | parser-backfill | read `documents.backfill` | `parser-backfill` |
| `authz-sync` | authz-sync | read `app.public.{users,subscriptions,company_grants,plan_tiers}` | `authz-sync` |
| `connect` | Kafka Connect + Debezium | `connect.*`: read, write, create, describe, describe-configs; `app.*`, `__debezium-heartbeat.app`: write, create, describe | `connect` |
| `kafka-exporter` | kafka-exporter (`monitoring`) | describe `*` (+ describe groups `*`, cluster) | |
| `admin` | setup-kafka | super user | |

Read and write imply describe. Only `connect` can write the CDC topics, so no other client can
forge a grant for authz-sync. CI checks the whole matrix against a live broker
([tests/kafka_acls](../../../tests/kafka_acls)), and that the tributary chart's clients match the
file ([check_kafka_auth.py](../tributary/tests/check_kafka_auth.py)).

### Secrets

Passwords are random 64-hex-character strings, the same value in every namespace that needs
it. `scripts/kafka-passwords.sh` creates all of them with `sops` (needs the age key, sops ≥ 3.9;
never prints a value; reruns only add what's missing). By hand: `openssl rand -hex 32` per user,
then `sops deploy/secrets/<file>.yaml`.

| File | Secret (namespace) | Keys |
|---|---|---|
| `deploy/secrets/platform.yaml` | `platform` (platform) | `kafka-password-admin` and `kafka-password-<principal>` for all 11 principals above |
| `deploy/secrets/tributary.yaml` | `tributary` (tributary) | `kafka-password-` + `poller`, `poller-backfill`, `doc-fetcher-seed`, `bronze-loader`, `doc-fetcher`, `doc-crawler`, `parser`, `parser-backfill`, `authz-sync` |
| `deploy/secrets/monitoring-kafka-exporter.yaml` (new) | `kafka-exporter-kafka` (monitoring) | `username: kafka-exporter`, `password` |

### Rollout

Each step is a values change in `deploy/flux/releases/` (commit, then
`flux reconcile kustomization releases --with-source` or wait). The images must already contain
this change (the Rust parser needs librdkafka with OpenSSL for SCRAM).

0. **Secrets**: run `scripts/kafka-passwords.sh`, commit, and wait for the `secrets`
   Kustomization (`kubectl -n platform get secret platform -o json | jq '.data | keys'` lists the
   `kafka-password-*` keys).
1. **Phase A**, platform: `kafka: {sasl: {enabled: true}}`. Kafka restarts once (single broker:
   about a minute; producers buffer and retry, consumers resume from their committed offsets, as
   for any Kafka upgrade), then `setup-kafka` creates the users and ACLs. Every client still uses
   PLAINTEXT and is unaffected. Check:
   `kubectl -n platform exec kafka-0 -c scram-admin -- /opt/kafka/bin/kafka-acls.sh
   --bootstrap-server localhost:9095 --list` and `... kafka-configs.sh --bootstrap-server
   localhost:9095 --describe --entity-type users` (scram-admin has the tools and a small heap); `kubectl -n platform logs kafka-0 -c scram-admin` says
   `SCRAM user admin: set`.
2. **Phase B**, the clients, in any order and one at a time if you like (PLAINTEXT stays open):
   - platform `kafka.sasl.connect: true`: Connect restarts and logs in as `connect`; Debezium
     resumes from `connect.offsets` (the replication slot holds the WAL meanwhile). Check:
     `kubectl -n platform port-forward svc/connect 8083`, then
     `curl localhost:8083/connectors/app/status` shows `RUNNING` for connector and task.
   - tributary `kafka: {sasl: {enabled: true}}`: every `uses: kafka` pod restarts onto 9094 as
     its own user. Do it outside the CronJob windows (02:00 backfill), since a job running
     during the switch loses its egress to 9092.
   - monitoring, under `prometheus-kafka-exporter`: `kafkaServer:
     [kafka.platform.svc.cluster.local:9094]`, `sasl: {enabled: true, scram: {enabled: true}}`.

   Check that nothing is left on PLAINTEXT before phase C: established connections to local
   port 9092 (hex `2384`) are zero: `kubectl -n platform exec kafka-0 -c kafka -- cat
   /proc/net/tcp /proc/net/tcp6 | awk '$2 ~ /:2384$/ && $4 == "01"' | wc -l`; and consumer lag
   (`kafka_consumergroup_lag`) keeps moving with no `KafkaConsumerLagGrowing` alert.
3. **Phase C**, platform: `kafka.plaintextListener: false`. Kafka restarts once more without
   9092; the Service and NetworkPolicy drop it too. The chart refuses to render C without A and
   Connect's flag (B).

**Rollback**, in reverse: C → B: `plaintextListener: true` (a Kafka restart reopens 9092). B →
A: switch each client's flag off; it reconnects over PLAINTEXT, still open. A → off: only once
no client uses SASL, `kafka.sasl.enabled: false`; Kafka restarts without the SASL listener and
authorizer. The users and ACLs stay in the metadata log, inert, and are reused if A is turned on
again. A password change (edit the key in every file listed above, or delete it and rerun the
script) applies on the next release: `setup-kafka` updates the user, then restart the client.

Adding a Kafka client: an entry in `files/kafka-acls.yaml` (its `kafkaUser` in the tributary
chart, default the service/job name), its password (`scripts/kafka-passwords.sh`), and a row in
[streaming/topics.md](../../../streaming/topics.md).
