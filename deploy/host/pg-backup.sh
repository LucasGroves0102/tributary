#!/usr/bin/env bash
# Logical backup of the host Postgres: roles plus one custom-format dump per database, each
# checked readable before the backup is published. Runs as the postgres user (peer auth).
#
# Nightly dumps are slim: tributary's document subtree (bodies, everything with a foreign key to
# them, the tables derived from them, and the crawl/index checkpoints) is dumped as schema only.
# All of it is rebuilt from the SEC by re-running the crawl, and excluding the whole subtree keeps
# the restore's foreign keys valid. On FULL_WEEKDAY a full dump goes to $dir/full instead, and the
# newest FULL_KEEP of those are kept.
set -euo pipefail
cd /

dir="${BACKUP_DIR:-/var/backups/postgres}"
keep_days="${KEEP_DAYS:-14}"
read -ra databases <<<"${DATABASES:-tributary app openfga}"
full_weekday="${FULL_WEEKDAY:-7}"  # date +%u: 1 = Monday ... 7 = Sunday
full_keep="${FULL_KEEP:-2}"
slim_exclude=(bronze.filing_documents bronze.filing_exhibits bronze.filing_xbrl
              silver.filing_sections silver.segment_facts gold.segment_financials
              gold.section_search gold.section_diffs gold.section_changes ops.section_index
              ops.doc_crawl_targets)
if [[ -n "${SLIM_EXCLUDE_DATA:-}" ]]; then read -ra slim_exclude <<<"$SLIM_EXCLUDE_DATA"; fi

full=false
[[ "$(date +%u)" == "$full_weekday" ]] && full=true
if $full; then dir="$dir/full"; fi

umask 077
out="$dir/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$out.partial"
pg_dumpall --globals-only --file="$out.partial/globals.sql"
for db in "${databases[@]}"; do
    args=()
    if ! $full && [[ "$db" == tributary ]]; then
        for table in "${slim_exclude[@]}"; do args+=(--exclude-table-data="$table"); done
        printf '%s\n' "${slim_exclude[@]}" >"$out.partial/$db.schema-only-tables"
    fi
    pg_dump --format=custom "${args[@]}" --file="$out.partial/$db.dump" "$db"
    pg_restore --list "$out.partial/$db.dump" >/dev/null
done
mv "$out.partial" "$out"

if $full; then
    find "$dir" -mindepth 1 -maxdepth 1 -type d -name '*Z' | sort | head -n -"$full_keep" | xargs -r rm -rf
else
    find "$dir" -mindepth 1 -maxdepth 1 -type d -name '*Z' -mtime +"$keep_days" -exec rm -rf {} +
fi
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
