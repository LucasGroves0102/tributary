# monitoring

Metrics, logs and alerts for the server and Tributary, in the `monitoring` namespace. An
umbrella chart over upstream charts (versions pinned in `Chart.yaml` / `Chart.lock`):

| Component | Chart | Does |
|---|---|---|
| Prometheus, Alertmanager, Grafana, node-exporter, kube-state-metrics | `kube-prometheus-stack` | metrics (15 days, 20Gi), alerts to Discord, dashboards |
| Loki | `loki` (single binary, filesystem) | logs, 14 days |
| Alloy | `alloy` | ships every pod's logs, Kubernetes events and the host journal (Postgres, k3s, sshd, ufw, pg-backup, pg-restore-test) to Loki |
| postgres-exporter | `prometheus-postgres-exporter` | host Postgres health (login `monitoring`, role `pg_monitor`) |
| ops-exporter | `prometheus-postgres-exporter` (alias) | metrics from the `ops` views (`files/ops-exporter-queries.yaml`); only with `opsDatabase.enabled` |
| kafka-exporter | `prometheus-kafka-exporter` | topic offsets and consumer-group lag (SCRAM user `kafka-exporter` on 9094, describe-only) |

Prometheus selects PodMonitors, ServiceMonitors and rules from every namespace and release, so
the tributary chart's PodMonitors are scraped without a `release` label. k3s runs etcd, the
scheduler, controller manager and kube-proxy in-process, so those targets and their default
rules are off.

## Tributary additions

- `templates/rules.yaml`: `KafkaConsumerLagGrowing`, `FilingsNotArriving` (no new
  `filings.raw` message in 2 hours, weekdays 14:00-21:00 UTC), `CompanyFactsBehindEdgar` (needs
  `opsDatabase.enabled`), `PostgresDown`,
  `ReplicationSlotRetainingWal` (a slot holding over 10 GB of WAL), `PostgresConnectionsHigh`,
  `DiskUsageHigh`, `DiskSpaceUnder100GB` (a warning: runbook below), `DiskSpaceLow` (under 50
  GB free), and for the document crawl
  `DocCrawlPausedForDisk` and `DocCrawlStalled` (its `tributary_doc_crawl_*` metrics), and
  `CatchupStalled` (a kind of stored data an older rule built hasn't moved in a day, a paused kind
  never; migration 071;
  `tributary_catchup_*{kind}`, which doc-crawler reports from `ops.catchup_status`). Alertmanager
  sends every alert to Discord except `info` severity, `Watchdog` and `InfoInhibitor`.
- `dashboards/*.json`: loaded by the Grafana sidecar (`tributary-freshness.json`, FR11).
- `templates/ops-database.yaml`: with `opsDatabase.enabled`, a Grafana Postgres datasource
  (uid `tributary`) on the `ops` views, used by the freshness dashboard, and the ops-exporter
  queries (`files/ops-exporter-queries.yaml`): `tributary_companyfacts_*{company}` from
  `ops.companyfacts_lag`, `tributary_financials_quality_*{check_name,severity}` from
  `ops.quality_summary` (one series per check and severity since 072), `tributary_doc_crawl_*{form}` from `ops.doc_crawl_progress`, and
  `tributary_catchup_*{kind}` from `ops.catchup_status`.
  `opsDatabase.enabled` is off in the chart and in hermes' release (it needs the `grafana_reader`
  login and the `grafana-postgres` secret, below), so on hermes those panels are empty and
  `CompanyFactsBehindEdgar` has no data. The public Data status page (Query API
  `GET /v1/status/data`) reports freshness, coverage and data quality from the `ops` schema
  instead.

## Disk space

`DiskSpaceUnder100GB` (warning): plan before `DiskSpaceLow` (50 GB, critical) and the crawl's own
50 GB guard. On hermes, read-only: `df -h /`; the biggest tables (`SELECT relname,
pg_size_pretty(pg_total_relation_size(oid)) FROM pg_class ORDER BY
pg_total_relation_size(oid) DESC LIMIT 10`: `gold.section_search` leads, then
`bronze.filing_documents`); `ops.doc_crawl_status` (`projected_stored_bytes`, what is still to
come); backups in `deploy/host/pg-backup.sh`'s directory (14 days kept). To stop growth while you
decide: `UPDATE ops.doc_crawl_control SET paused = true` (the crawl only; the live doc-fetcher
keeps storing new filings).

## Prerequisites

Secrets in `monitoring`, created from [deploy/secrets](../../secrets): `grafana-admin`
(`admin-user`, `admin-password`), `alertmanager-discord` (`webhook-url`), `postgres-exporter`
(`password`), with `opsDatabase.enabled` `grafana-postgres` (`password` of the
`grafana_reader` login, which also needs the `ops_reader` role and a `pg_hba` line on the host),
and for kafka-exporter over SASL `kafka-exporter-kafka` (`username`, `password`; phase B of
the [Kafka authentication rollout](../platform/README.md#kafka-authentication-saslscram):
`prometheus-kafka-exporter.kafkaServer` on port 9094 and `sasl.enabled` + `sasl.scram.enabled`,
set in the hermes release values).
No backup alerts: hermes runs without backups since 2026-11-24 ([deploy/host](../../host)); the
backup and restore-test alerts went with them.

## Install

Flux installs it from [deploy/flux/releases/monitoring.yaml](../../flux/releases). By hand:

```bash
helm dependency build deploy/helm/monitoring
helm upgrade --install monitoring deploy/helm/monitoring --namespace monitoring
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80
```

CI runs `helm lint --strict` and kubeconform on the rendered chart. It caches `charts/` per
`Chart.lock` and downloads the dependencies (three tries) only when that changes.
