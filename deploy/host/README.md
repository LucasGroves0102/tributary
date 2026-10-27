# host

Files installed directly on the server, outside Kubernetes. Postgres 16 runs on the host (with
`wal_level=logical` for Debezium) and holds the `tributary`, `app` and `openfga` databases.

| File | Does |
|---|---|
| `pg-backup.sh` | logical backup as the `postgres` user: roles (`pg_dumpall --globals-only`) plus one custom-format dump per database, each checked with `pg_restore --list` before the backup directory is published, with `<db>.rowcounts` (every table's row count in the dump's own snapshot); deletes backups older than `KEEP_DAYS`; writes `pg_backup_last_success_timestamp_seconds` and `pg_backup_size_bytes` for node-exporter's textfile collector |
| `pg-backup.service`, `pg-backup.timer` | systemd units: run `/usr/local/bin/pg-backup` nightly at 05:00 New York, after the nightly CronJobs, sandboxed to the backup and textfile directories |
| `pg-restore-test.sh` | restores the latest full backup (`--slim` or `RESTORE_FROM=slim`: the latest nightly) of each database into a scratch database `restore_test_<db>`, checks every table and its exact row count against `<db>.rowcounts` (tables in `<db>.schema-only-tables` must be empty), always drops the copies, and writes `pg_restore_test_last_result`, `pg_restore_test_last_success_timestamp_seconds` and friends for node-exporter |
| `pg-restore-test.service`, `pg-restore-test.timer` | systemd units: run `/usr/local/bin/pg-restore-test` every Sunday at 11:00 New York, six hours after the weekly full backup starts; sandboxed like the backup, writing only the textfile directory |

Settings (env, with defaults): `BACKUP_DIR` (`/var/backups/postgres`), `KEEP_DAYS` (14),
`DATABASES` (`tributary app openfga`), `TEXTFILE_DIR`
(`/var/lib/node_exporter/textfile_collector`), `FULL_WEEKDAY` (7, Sunday), `FULL_KEEP` (2),
`SLIM_EXCLUDE_DATA` (space-separated tables; default in the script). The restore test also reads
`RESTORE_FROM` (`full`), `FREE_SPACE_FACTOR` (2) and `RESTORE_JOBS` (2, `pg_restore --jobs`).

Nightly dumps are slim: the document crawl adds ~30-40 GB, and 14 full copies would not fit on
the disk. Tributary's document subtree (`bronze.filing_documents` and every table with a foreign
key to it, the search/diff/segment tables derived from them, and the crawl/index checkpoints) is
dumped schema only; the tables are listed in each backup's `tributary.schema-only-tables`. Once a
week a full dump goes to `$BACKUP_DIR/full`, and the newest `FULL_KEEP` are kept. Restoring a slim
dump gives every financial, filer and access table, with the document tables empty: restart the
crawl (`ops.doc_crawl_plan`, then `doc-crawler` + `parser-backfill`), then the `segments` and
`parser-index` jobs, or restore the latest full dump instead.

## Install

From a checkout of `main` on the server (the owner runs these; nothing here installs itself):

```bash
sudo install -m 0755 deploy/host/pg-backup.sh /usr/local/bin/pg-backup
sudo install -m 0755 deploy/host/pg-restore-test.sh /usr/local/bin/pg-restore-test
sudo install -m 0644 deploy/host/pg-backup.service deploy/host/pg-backup.timer \
    deploy/host/pg-restore-test.service deploy/host/pg-restore-test.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pg-backup.timer pg-restore-test.timer
```

The restore test needs a backup made by this `pg-backup` (with `.rowcounts`); older ones fail it.
To run it now instead of next Sunday, make a full backup first (what the timer does on Sundays;
it counts towards `FULL_KEEP`), then start the test and follow it:

```bash
sudo -u postgres env FULL_WEEKDAY="$(date +%u)" /usr/local/bin/pg-backup
sudo systemctl start pg-restore-test    # blocks until it finishes (hours for a full restore)
journalctl -u pg-restore-test -e
cat /var/lib/node_exporter/textfile_collector/pg_restore_test.prom
```

### Restore test: what it costs

The full restore writes about the database's size: it refuses to start unless the Postgres data
disk has `FREE_SPACE_FACTOR` (2) times the backup's size free, and the scratch databases are
dropped however it ends (an EXIT trap; `TimeoutStartSec=8h` stops a hung run, which counts as a
failure). It runs in the live cluster, so the restore's WAL also passes through Debezium's
replication slot: expect `ReplicationSlotRetainingWal` to fire briefly if decoding falls behind;
past `max_slot_wal_keep_size` (below) the slot is invalidated and Debezium re-snapshots the app
tables, which authz-sync handles by design. Row counts are exact: pg-backup counts every table in
an exported snapshot and dumps that same snapshot (`pg_dump --snapshot`), so the restore must
match to the row, regardless of what changed in the live database since.

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
backup succeeded in 26 hours; `PostgresRestoreTestFailed` when the last restore test failed, and
`PostgresRestoreTestMissing` when none passed in 8 days (including before the first run). Alloy ships the unit's journal to Loki.
