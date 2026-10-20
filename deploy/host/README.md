# host

Files installed directly on the server, outside Kubernetes. Postgres 16 runs on the host (with
`wal_level=logical` for Debezium) and holds the `tributary`, `app` and `openfga` databases.

| File | Does |
|---|---|
| `pg-backup.sh` | logical backup as the `postgres` user: roles (`pg_dumpall --globals-only`) plus one custom-format dump per database, each checked with `pg_restore --list` before the backup directory is published; deletes backups older than `KEEP_DAYS`; writes `pg_backup_last_success_timestamp_seconds` and `pg_backup_size_bytes` for node-exporter's textfile collector |
| `pg-backup.service`, `pg-backup.timer` | systemd units: run `/usr/local/bin/pg-backup` nightly at 05:00 New York, after the nightly CronJobs, sandboxed to the backup and textfile directories |
| `pg-restore-test.sh` | restores the latest backup of each database into a scratch database, compares tables and row counts with the live one, then drops the copies |

Settings (env, with defaults): `BACKUP_DIR` (`/var/backups/postgres`), `KEEP_DAYS` (14),
`DATABASES` (`tributary app openfga`), `TEXTFILE_DIR`
(`/var/lib/node_exporter/textfile_collector`).

## Install

```bash
sudo install -m 0755 deploy/host/pg-backup.sh /usr/local/bin/pg-backup
sudo install -m 0644 deploy/host/pg-backup.service deploy/host/pg-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pg-backup.timer
sudo -u postgres deploy/host/pg-restore-test.sh    # verify a restore
```

## Postgres settings

`/etc/postgresql/16/main/conf.d/tributary.conf` sets `wal_level = logical`,
`listen_addresses = '*'` (pods reach it over the k3s bridge; `pg_hba.conf` limits who) and
`max_slot_wal_keep_size = 20GB`. The last one caps how much WAL a replication slot may hold:
Debezium's slot only advances on app-database traffic (it writes a heartbeat every 10 s to
keep it moving), and a stuck slot would otherwise keep every database's WAL until the disk
fills. Past the cap the slot is invalidated and Debezium re-snapshots the app tables. Reload
after a change: `sudo -u postgres psql -c "SELECT pg_reload_conf()"`. The
`ReplicationSlotRetainingWal` alert fires above 10 GB.

The `PostgresBackupMissing` alert ([deploy/helm/monitoring](../helm/monitoring)) fires when no
backup succeeded in 26 hours. Alloy ships the unit's journal to Loki.
