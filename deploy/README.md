# deploy

Everything that runs Tributary on the server: a single machine running k3s, with Postgres on
the host and the public site behind a Cloudflare Tunnel. Local development uses
`docker-compose.yml` at the repo root instead.

| Path | What |
|---|---|
| [`helm/tributary/`](helm/tributary) | chart for the Tributary services, CronJobs, migrations hook, NetworkPolicies, PodMonitors and a read-only operator ServiceAccount (namespace `tributary`) |
| [`helm/platform/`](helm/platform) | Kafka, Kafka Connect + Debezium, OpenFGA (namespace `platform`) |
| [`helm/monitoring/`](helm/monitoring) | Prometheus, Alertmanager, Grafana, Loki, Alloy and exporters (namespace `monitoring`) |
| [`platform-setup/`](platform-setup) | image for the platform chart's hooks: OpenFGA store + model, Debezium connector, Kafka SCRAM users + ACLs |
| [`flux/`](flux) | GitOps: Flux applies the charts, secrets and image updates from `main` |
| [`secrets/`](secrets) | Kubernetes Secrets, encrypted with SOPS + age |
| [`terraform/`](terraform) | Flux bootstrap on a fresh cluster; Cloudflare tunnel, DNS, HTTPS and rate limiting |
| [`host/`](host) | systemd units and scripts on the server itself: nightly Postgres backup and weekly restore test into a throwaway Postgres instance |
| [`uptime/`](uptime) | outside uptime check run by GitHub Actions (scheduled every 5 min; GitHub runs it a few times a day): site, APIs and pipeline freshness; opens and closes `outage` issues |

```
push to main → CI (lint, tests, images to GHCR, tag main-<run>-<sha>)
            → Flux image automation commits the new tag → Flux upgrades the Helm releases
internet → Cloudflare (TLS, rate limit) → tunnel → cloudflared → web (nginx) → query-api / agent-api
```

Tributary, platform and cloudflared pods run as non-root, and those namespaces only allow the
traffic their NetworkPolicies list. Kafka accepts only SASL/SCRAM (port 9094), each client as its
own user limited by ACLs (`helm/platform/files/kafka-acls.yaml`). CI lints and renders all three charts
(the tributary chart also with each switch on and off: Kafka SASL, the SEC limiter login, alert
email egress; the platform chart in each Kafka SASL phase), validates them and the Flux manifests
with kubeconform, and checks Terraform formatting and validity. Its downloads
are cached so a flaky GitHub or registry doesn't fail a run: kubeconform's schemas, the monitoring
chart's dependencies (per `Chart.lock`, fetched with three tries on a miss) and each Terraform
module's providers (mirrored per `.terraform.lock.hcl` with three tries; `init -plugin-dir` then
reads only the mirror).
