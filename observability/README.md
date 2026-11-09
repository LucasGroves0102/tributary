# observability

FR11, week 4. This folder holds no configuration. Monitoring is deployed by the
[monitoring chart](../deploy/helm/monitoring) (Prometheus, Alertmanager, Grafana with the
freshness dashboard, Loki, Alloy, exporters); the freshness views it reads are in
[`db/migrations/010_ops.sql`](../db/migrations/010_ops.sql), and the services' metrics and JSON
logs come from [libs/telemetry](../libs/telemetry). The public Data status page (web `#/status`,
Query API `GET /v1/status/data`, aggregates only, from the functions of migrations 026 and 028) shows feed
freshness, latency, completeness, coverage and data-quality checks without a sign-in, and an
outside check from GitHub Actions ([deploy/uptime](../deploy/uptime)) reads the web app, both
APIs' health and `/v1/status` (feed freshness).
OpenTelemetry tracing (agent → Query API → Postgres/OpenFGA) is not built.
