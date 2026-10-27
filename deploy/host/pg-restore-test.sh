#!/usr/bin/env bash
# Restore test, weekly via pg-restore-test.timer: restore the latest full backup of each
# database into a scratch database, check it holds exactly the tables and row counts that
# pg-backup.sh counted in the dump's own snapshot (<db>.rowcounts), then drop the scratch copies.
# With --slim (or RESTORE_FROM=slim) it tests the latest nightly instead; tables listed in its
# <db>.schema-only-tables must then be empty. Runs as the postgres user (peer auth). Writes the
# pg_restore_test_* metrics for node-exporter's textfile collector, pass or fail.
set -euo pipefail
cd /
export PGOPTIONS="-c client_min_messages=warning"

dir="${BACKUP_DIR:-/var/backups/postgres}"
read -ra databases <<<"${DATABASES:-tributary app openfga}"
from="${RESTORE_FROM:-full}"
[[ "${1:-}" == --slim ]] && from=slim
free_factor="${FREE_SPACE_FACTOR:-2}"  # free space on the data disk, x the backup's size
jobs="${RESTORE_JOBS:-2}"
textfile="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
started="$(date +%s)"
latest=""
rows_total=0
scratch=()

# Same query as pg-backup.sh's count_rows: "schema.table rows" for every table, ordered.
counts() {
    psql -X -qAt -F ' ' -v ON_ERROR_STOP=1 -d "$1" -c "
        SELECT n.nspname || '.' || c.relname,
               (xpath('/row/n/text()', query_to_xml(
                   format('SELECT count(*) AS n FROM %I.%I', n.nspname, c.relname),
                   false, true, '')))[1]::text::bigint
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg_temp%'
        ORDER BY 1"
}

# shellcheck disable=SC2329  # invoked by finish
write_metrics() {
    local ok=$1 prom="$textfile/pg_restore_test.prom" last_success=0 backup_ts=0 name
    [[ -d "$textfile" && -w "$textfile" ]] || return 0
    if [[ "$ok" == 1 ]]; then
        last_success="$(date +%s)"
    elif [[ -f "$prom" ]]; then
        last_success="$(awk '$1 == "pg_restore_test_last_success_timestamp_seconds" {print $2}' \
            "$prom")"
    fi
    if [[ -n "$latest" ]]; then  # backup directories are named by their UTC start time
        name="$(basename "$latest")"
        backup_ts="$(date -u -d "${name:0:4}-${name:4:2}-${name:6:2} ${name:9:2}:${name:11:2}:${name:13:2}" +%s)"
    fi
    {
        echo "# HELP pg_restore_test_last_result 1 if the last restore test passed, 0 if it failed."
        echo "# TYPE pg_restore_test_last_result gauge"
        echo "pg_restore_test_last_result $ok"
        echo "# HELP pg_restore_test_last_run_timestamp_seconds Unix time the last restore test finished."
        echo "# TYPE pg_restore_test_last_run_timestamp_seconds gauge"
        echo "pg_restore_test_last_run_timestamp_seconds $(date +%s)"
        echo "# HELP pg_restore_test_last_success_timestamp_seconds Unix time of the last passing restore test."
        echo "# TYPE pg_restore_test_last_success_timestamp_seconds gauge"
        echo "pg_restore_test_last_success_timestamp_seconds ${last_success:-0}"
        echo "# HELP pg_restore_test_backup_timestamp_seconds Start time of the backup the last test restored."
        echo "# TYPE pg_restore_test_backup_timestamp_seconds gauge"
        echo "pg_restore_test_backup_timestamp_seconds $backup_ts"
        echo "# HELP pg_restore_test_duration_seconds How long the last restore test took."
        echo "# TYPE pg_restore_test_duration_seconds gauge"
        echo "pg_restore_test_duration_seconds $(($(date +%s) - started))"
        echo "# HELP pg_restore_test_rows Rows restored and checked by the last restore test."
        echo "# TYPE pg_restore_test_rows gauge"
        echo "pg_restore_test_rows $rows_total"
    } >"$prom.tmp"
    chmod 0644 "$prom.tmp"
    mv "$prom.tmp" "$prom"
}

# Always drop the scratch databases and record the result, however the script ends.
# shellcheck disable=SC2329  # invoked by the EXIT trap
finish() {
    local status=$?
    trap - EXIT
    for db in "${scratch[@]}"; do dropdb --if-exists --force "$db" || status=1; done
    write_metrics "$([[ $status == 0 ]] && echo 1 || echo 0)" || true
    exit "$status"
}
trap finish EXIT
trap 'exit 130' INT TERM

case "$from" in
    full) search="$dir/full" ;;
    slim) search="$dir" ;;
    *) echo "RESTORE_FROM=$from: expected full or slim" >&2; exit 2 ;;
esac
latest="$(find "$search" -mindepth 1 -maxdepth 1 -type d -name '*Z' 2>/dev/null | sort | tail -1)"
[[ -n "$latest" ]] || { echo "no $from backups in $search" >&2; exit 1; }

data_dir="$(psql -X -qAt -d postgres -c 'SHOW data_directory')"
free="$(df -B1 --output=avail "$data_dir" | tail -1 | tr -d ' ')"
size="$(du -sb "$latest" | cut -f1)"
if ((free < free_factor * size)); then
    echo "only $free bytes free on the data disk ($data_dir); the restore needs ${free_factor}x" \
        "the backup ($size bytes)" >&2
    exit 1
fi
echo "restoring $latest ($size bytes; $free free)"

failed=0
for db in "${databases[@]}"; do
    expected="$latest/$db.rowcounts"
    if [[ ! -f "$expected" ]]; then
        echo "$db: no $db.rowcounts in $latest (made before pg-backup recorded them)" >&2
        failed=1
        continue
    fi
    # tables dumped as schema only (slim backups) must come back empty
    empty="$latest/$db.schema-only-tables"
    [[ -f "$empty" ]] || empty=/dev/null
    want="$(awk 'FILENAME == ARGV[1] {skip[$1] = 1; next}
                 {print $1, ($1 in skip ? 0 : $2)}' "$empty" "$expected")"

    db_scratch="restore_test_$db"
    scratch+=("$db_scratch")
    dropdb --if-exists --force "$db_scratch"
    createdb "$db_scratch"
    pg_restore --exit-on-error --jobs="$jobs" --dbname="$db_scratch" "$latest/$db.dump"
    got="$(counts "$db_scratch")"
    dropdb "$db_scratch"

    rows="$(awk '{s += $2} END {print s + 0}' <<<"$got")"
    rows_total=$((rows_total + rows))
    if [[ "$got" == "$want" ]]; then
        echo "$db: $(wc -l <<<"$got") tables, $rows rows: every table and row count matches the dump"
    else
        echo "$db: restored tables or row counts differ from the dump (< dump, > restored):" >&2
        diff <(echo "$want") <(echo "$got") | head -40 >&2 || true
        failed=1
    fi
done
exit "$failed"
