# host

Files installed directly on the server, outside Kubernetes. Postgres 16 runs on the host (with
`wal_level=logical` for Debezium) and holds the `tributary`, `app` and `openfga` databases.

| File | Does |
|---|---|
| `pg-backup.sh` | logical backup as the `postgres` user: roles (`pg_dumpall --globals-only`) plus one custom-format dump per database, each checked with `pg_restore --list` before the backup directory is published, with `<db>.rowcounts` (every table's row count in the dump's own snapshot); deletes backups older than `KEEP_DAYS`; writes `pg_backup_last_success_timestamp_seconds` and `pg_backup_size_bytes` for node-exporter's textfile collector |
| `pg-backup.service`, `pg-backup.timer` | systemd units: run `/usr/local/bin/pg-backup` nightly at 05:00 New York, after the 02:00-04:00 CronJobs (`backfill`, `companies`, `financials`) and alongside the bulk loads that start at 05:00 and 05:30 (each dump is one consistent snapshot), sandboxed to the backup and textfile directories |
| `pg-restore-test.sh` | restores the latest full backup (`--slim` or `RESTORE_FROM=slim`: the latest nightly) into a throwaway Postgres instance (initdb in a temp directory, socket only, never the live cluster): `globals.sql`, then each database; checks every table and its exact row count against `<db>.rowcounts` (tables in `<db>.schema-only-tables` must be empty); always stops and deletes the instance; writes `pg_restore_test_last_result`, `pg_restore_test_last_success_timestamp_seconds` and friends for node-exporter |
| `pg-restore-test.service`, `pg-restore-test.timer` | systemd units: run `/usr/local/bin/pg-restore-test` every Sunday at 11:00 New York, six hours after the weekly full backup starts; sandboxed like the backup, writing only `/var/lib/postgresql/restore-test` (the throwaway instance) and the textfile directory |

Settings (env, with defaults): `BACKUP_DIR` (`/var/backups/postgres`), `KEEP_DAYS` (14),
`DATABASES` (`tributary app openfga`), `TEXTFILE_DIR`
(`/var/lib/node_exporter/textfile_collector`), `FULL_WEEKDAY` (7, Sunday), `FULL_KEEP` (1),
`SLIM_EXCLUDE_DATA` and `FULL_EXCLUDE_DATA` (space-separated tables; defaults in the script),
`COMPRESS` (`zstd:1`, `pg_dump --compress`). The restore test also reads
`RESTORE_FROM` (`full`), `FREE_SPACE_FACTOR` (2), `RESTORE_JOBS` (2, `pg_restore --jobs`),
`RESTORE_TEST_DIR` (`/var/lib/postgresql/restore-test`; the systemd unit's `ReadWritePaths` must
match), `RESTORE_TEST_PORT` (54329) and `PG_BIN` (the highest `/usr/lib/postgresql/*/bin`).

Nightly dumps are slim: the document crawl added ~30-40 GB over its first five years (more since
its window became 11 years), and 14 full copies would not fit on the disk. Tributary's document subtree (`bronze.filing_documents` and every table with a foreign
key to it, the search/diff/segment tables derived from them, and the crawl/index checkpoints) is
dumped schema only; the tables are listed in each backup's `tributary.schema-only-tables`. Once a
week a full dump goes to `$BACKUP_DIR/full`, and the newest `FULL_KEEP` (1) are kept; the old
one is deleted only after the new one is published, so there is always a full dump. Restoring a
slim dump gives every financial, filer and access table, with the document tables empty: restart
the crawl (`ops.doc_crawl_plan`, then `doc-crawler` + `parser-backfill`), then the `segments` and
`section-index` jobs, or restore the latest full dump instead.

Full dumps hold every table's data except what the `section-index` job derives from the parsed
sections: `gold.section_search`, `gold.section_diffs`, `gold.section_changes` and its checkpoint
`ops.section_index` (~35 GB, listed in `tributary.schema-only-tables`). After restoring one, run
the `section-index` job until `ops.section_index_status` has caught up (about 1 h of indexing and
~4 h of diffs for ~265k filings; see the job's comment in the chart's `values.yaml`), then
`REINDEX` `gold.section_search_tsv`. Search and "What changed" are empty until then; every other
screen works at once.

Dumps use `zstd:1` compression (Postgres 16). Most of the document bodies are already gzip, and
`pg_dump`'s default gzip recompressing them on one core took most of the first full dump's ~70
minutes (38 GB, 2026-11-02). Older gzip dumps restore the same way.

## Install

From a checkout of `main` on the server (the owner runs these; nothing here installs itself):

```bash
sudo install -m 0755 deploy/host/pg-backup.sh /usr/local/bin/pg-backup
sudo install -m 0755 deploy/host/pg-restore-test.sh /usr/local/bin/pg-restore-test
sudo install -d -o postgres -g postgres -m 0700 /var/lib/postgresql/restore-test
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

### Restore test: a throwaway instance

The restore never touches the live cluster. `wal_level=logical` there means a 54 GB restore
would push all of its WAL through Debezium's `app_debezium` slot (`max_slot_wal_keep_size` does
not cap it), so the script runs its own Postgres instead: `initdb` into a fresh
`run.XXXXXX` directory under `RESTORE_TEST_DIR` (same disk as the data and backups), started with
`pg_ctl` on a unix socket inside that directory only (`listen_addresses = ''`, port 54329),
`wal_level = minimal`, `max_wal_senders = 0`, `fsync`, `synchronous_commit`, `full_page_writes`
and autovacuum off, 256 MB shared buffers. It restores `globals.sql` (roles; the
`CREATE ROLE postgres` error is expected and only counted), then each database, compares, drops
it, and goes on. On any exit, including a failure or `TimeoutStartSec=8h`, an EXIT trap runs
`pg_ctl stop -m immediate` and deletes the directory. It refuses to start unless that
filesystem has `FREE_SPACE_FACTOR` (2) times the backup's size free.

Row counts are exact: pg-backup counts every table in an exported snapshot and dumps that same
snapshot (`pg_dump --snapshot`), so the restore must match to the row, whatever changed in the
live database since.

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
