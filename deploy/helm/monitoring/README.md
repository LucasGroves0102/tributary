# monitoring

Metrics, logs and alerts for the server and Tributary, in the `monitoring` namespace. An
umbrella chart over upstream charts (versions pinned in `Chart.yaml` / `Chart.lock`):

| Component | Chart | Does |
|---|---|---|
| Prometheus, Alertmanager, Grafana, node-exporter, kube-state-metrics | `kube-prometheus-stack` | metrics (15 days, 20Gi), alerts to Discord, dashboards |
| Loki | `loki` (single binary, filesystem) | logs, 14 days |
| Alloy | `alloy` | ships every pod's logs, Kubernetes events and the host journal (Postgres, k3s, sshd, ufw, pg-backup) to Loki |
| postgres-exporter | `prometheus-postgres-exporter` | host Postgres health (login `monitoring`, role `pg_monitor`) |
| ops-exporter | `prometheus-postgres-exporter` (alias) | metrics from the `ops` views (`files/ops-exporter-queries.yaml`); only with `opsDatabase.enabled` |
| kafka-exporter | `prometheus-kafka-exporter` | topic offsets and consumer-group lag (SCRAM user `kafka-exporter`, describe-only, once Kafka SASL is on) |

Prometheus selects PodMonitors, ServiceMonitors and rules from every namespace and release, so
the tributary chart's PodMonitors are scraped without a `release` label. k3s runs etcd, the
scheduler, controller manager and kube-proxy in-process, so those targets and their default
rules are off.

## Tributary additions

- `templates/rules.yaml`: `KafkaConsumerLagGrowing`, `FilingsNotArriving`,
  `CompanyFactsBehindEdgar`, `PostgresBackupMissing`, `PostgresRestoreTestFailed`,
  `PostgresRestoreTestMissing` (no passing weekly restore test in 8 days), `PostgresDown`,
  `PostgresConnectionsHigh`, `DiskUsageHigh`, `DiskSpaceLow` (under 50 GB free), and for the
  document crawl `DocCrawlPausedForDisk` and `DocCrawlStalled` (its `tributary_doc_crawl_*`
  metrics). Alertmanager sends every alert to Discord
  except `info` severity, `Watchdog` and `InfoInhibitor`.
- `dashboards/*.json`: loaded by the Grafana sidecar (`tributary-freshness.json`, FR11).
- `templates/ops-database.yaml`: with `opsDatabase.enabled`, a Grafana Postgres datasource
  (uid `tributary`) on the `ops` views, used by the freshness dashboard, and the ops-exporter
  queries (`tributary_doc_crawl_*{form}`: crawl progress from `ops.doc_crawl_progress`).

## Prerequisites

Secrets in `monitoring`, created from [deploy/secrets](../../secrets): `grafana-admin`
(`admin-user`, `admin-password`), `alertmanager-discord` (`webhook-url`), `postgres-exporter`
(`password`), with `opsDatabase.enabled` `grafana-postgres` (`password` of the
`grafana_reader` login, which also needs the `ops_reader` role and a `pg_hba` line on the host),
and for kafka-exporter over SASL `kafka-exporter-kafka` (`username`, `password`; phase B of
the [Kafka authentication rollout](../platform/README.md#kafka-authentication-saslscram): set
`prometheus-kafka-exporter.kafkaServer` to port 9094 and `sasl.enabled` + `sasl.scram.enabled`
in the release values).
`PostgresBackupMissing` and the two restore-test alerts read the textfile metrics written by
[deploy/host/pg-backup.sh and pg-restore-test.sh](../../host).

## Install

Flux installs it from [deploy/flux/releases/monitoring.yaml](../../flux/releases). By hand:

```bash
helm dependency build deploy/helm/monitoring
helm upgrade --install monitoring deploy/helm/monitoring --namespace monitoring
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80
```

CI runs `helm lint --strict` and kubeconform on the rendered chart.
