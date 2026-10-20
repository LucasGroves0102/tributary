# observability

FR11, week 4. This folder holds no configuration. Monitoring is deployed by the
[monitoring chart](../deploy/helm/monitoring) (Prometheus, Alertmanager, Grafana with the
freshness dashboard, Loki, Alloy, exporters); the freshness views it reads are in
[`db/migrations/010_ops.sql`](../db/migrations/010_ops.sql), and the services' metrics and JSON
logs come from [libs/telemetry](../libs/telemetry). OpenTelemetry tracing (agent → Query API →
Postgres/OpenFGA) is not built yet.
