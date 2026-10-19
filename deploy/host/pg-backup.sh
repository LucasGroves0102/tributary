#!/usr/bin/env bash
# Logical backup of the host Postgres: roles plus one custom-format dump per database, each
# checked readable before the backup is published. Runs as the postgres user (peer auth).
set -euo pipefail
cd /

dir="${BACKUP_DIR:-/var/backups/postgres}"
keep_days="${KEEP_DAYS:-14}"
read -ra databases <<<"${DATABASES:-tributary app openfga}"

umask 077
out="$dir/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$out.partial"
pg_dumpall --globals-only --file="$out.partial/globals.sql"
for db in "${databases[@]}"; do
    pg_dump --format=custom --file="$out.partial/$db.dump" "$db"
    pg_restore --list "$out.partial/$db.dump" >/dev/null
done
mv "$out.partial" "$out"

find "$dir" -mindepth 1 -maxdepth 1 -type d -name '*Z' -mtime +"$keep_days" -exec rm -rf {} +
find "$dir" -mindepth 1 -maxdepth 1 -type d -name '*.partial' -mmin +1440 -exec rm -rf {} +
echo "backup $out: $(du -sh "$out" | cut -f1)"

# For the PostgresBackupMissing alert: node-exporter reads *.prom files from this directory.
textfile="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
if [[ -d "$textfile" && -w "$textfile" ]]; then
    {
        echo "# HELP pg_backup_last_success_timestamp_seconds Unix time of the last successful backup."
        echo "# TYPE pg_backup_last_success_timestamp_seconds gauge"
        echo "pg_backup_last_success_timestamp_seconds $(date +%s)"
        echo "# HELP pg_backup_size_bytes Size of the last successful backup."
        echo "# TYPE pg_backup_size_bytes gauge"
        echo "pg_backup_size_bytes $(du -sb "$out" | cut -f1)"
    } >"$textfile/pg_backup.prom.tmp"
    chmod 0644 "$textfile/pg_backup.prom.tmp"
    mv "$textfile/pg_backup.prom.tmp" "$textfile/pg_backup.prom"
fi
