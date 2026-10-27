# deploy

Everything that runs Tributary on the server: a single machine running k3s, with Postgres on
the host and the public site behind a Cloudflare Tunnel. Local development uses
`docker-compose.yml` at the repo root instead.

| Path | What |
|---|---|
| [`helm/tributary/`](helm/tributary) | chart for the Tributary services, CronJobs, migrations hook, NetworkPolicies, PodMonitors (namespace `tributary`) |
| [`helm/platform/`](helm/platform) | Kafka, Kafka Connect + Debezium, OpenFGA (namespace `platform`) |
| [`helm/monitoring/`](helm/monitoring) | Prometheus, Alertmanager, Grafana, Loki, Alloy and exporters (namespace `monitoring`) |
| [`platform-setup/`](platform-setup) | image for the platform chart's hooks: OpenFGA store + model, Debezium connector |
| [`flux/`](flux) | GitOps: Flux applies the charts, secrets and image updates from `main` |
| [`secrets/`](secrets) | Kubernetes Secrets, encrypted with SOPS + age |
| [`terraform/`](terraform) | Flux bootstrap on a fresh cluster; Cloudflare tunnel, DNS, HTTPS and rate limiting |
| [`host/`](host) | systemd units and scripts on the server itself: nightly Postgres backup and weekly restore test |

```
push to main → CI (lint, tests, images to GHCR, tag main-<run>-<sha>)
            → Flux image automation commits the new tag → Flux upgrades the Helm releases
internet → Cloudflare (TLS, rate limit) → tunnel → cloudflared → web (nginx) → query-api / agent-api
```

Tributary, platform and cloudflared pods run as non-root, and those namespaces only allow the
traffic their NetworkPolicies list. CI lints and renders all three charts, validates
the Flux manifests with kubeconform, and checks Terraform formatting and validity.
