# tributary

Helm chart for the Tributary services on k3s. Kafka, Kafka Connect and OpenFGA run separately
in the `platform` namespace; Postgres runs on the host.

## Prerequisites

- Postgres reachable from pods at `hostAddress` (default `10.42.0.1`, the k3s bridge), with the
  `tributary`, `app` and `openfga` databases and a `query_api` role granted to the login.
- In the release namespace:
  - Secret `tributary` with keys `database-url`, `app-database-url`, `jwt-secret`,
    `anthropic-api-key`, `sec-user-agent`, and `openfga-api-token` when `openfga.auth.enabled`.
  - Secret `ghcr` (type `kubernetes.io/dockerconfigjson`) with a token that can read packages.

## Install

```bash
helm upgrade --install tributary deploy/helm/tributary \
  --namespace tributary --set image.tag=<commit sha>
```

Each install or upgrade first runs the `migrate` Job (`db/Dockerfile`), which applies `db/`
migrations and, when `demo.enabled`, seeds the demo users. A failed migration stops the release.

Fill a new database once the services are up:

```bash
kubectl -n tributary create job --from=cronjob/companies companies-1
kubectl -n tributary create job --from=cronjob/financials financials-1
kubectl -n tributary create job --from=cronjob/seed-documents seed-documents-1
```

## Components

`services` become Deployments and `jobs` become CronJobs. Each lists what it `uses`; that
decides its environment variables, which Secret keys it can read, and its network access:

| uses | Env | Network |
|---|---|---|
| `postgres` | `DATABASE_URL` | host Postgres |
| `kafka` | `KAFKA_BOOTSTRAP_SERVERS` | Kafka in `platform` |
| `openfga` | `FGA_API_URL`, `FGA_API_TOKEN` when `openfga.auth.enabled` | OpenFGA in `platform` |
| `sec` | `SEC_USER_AGENT` | HTTPS to public addresses |
| `anthropic` | `ANTHROPIC_API_KEY` | HTTPS to public addresses |
| `jwt` | `QUERY_API_JWT_SECRET` | |
| `query-api` | `QUERY_API_URL` | |
| `demo` | `DEMO_USERS` when `demo.enabled` | |

A service with a `port` gets a Service, health probes, and an ingress policy admitting only the
components in its `from` list and pods in its `fromNamespaces`. Everything else is denied.

Every pod runs as non-root with a read-only root filesystem, no capabilities, the
`RuntimeDefault` seccomp profile and no service account token.
