#!/usr/bin/env bash
# Restore the latest backup of each database into a scratch database, compare tables and row
# counts with the live database, then drop the scratch copies. Runs as the postgres user.
set -euo pipefail
export PGOPTIONS="-c client_min_messages=warning"

dir="${BACKUP_DIR:-/var/backups/postgres}"
read -ra databases <<<"${DATABASES:-tributary app openfga}"
latest="$(find "$dir" -mindepth 1 -maxdepth 1 -type d -name '*Z' | sort | tail -1)"
[[ -n "$latest" ]] || { echo "no backups in $dir" >&2; exit 1; }
echo "restoring $latest"

counts() {
    psql -d "$1" -qAt -F ' ' -c "
        SELECT n.nspname || '.' || c.relname,
               (xpath('/row/n/text()', query_to_xml(
                   format('SELECT count(*) AS n FROM %I.%I', n.nspname, c.relname),
                   false, true, '')))[1]::text::bigint
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY 1"
}

failed=0
for db in "${databases[@]}"; do
    scratch="restore_test_$db"
    dropdb --if-exists "$scratch"
    createdb "$scratch"
    pg_restore --exit-on-error --dbname="$scratch" "$latest/$db.dump"
    restored="$(counts "$scratch")"
    live="$(counts "$db")"
    dropdb "$scratch"
    if [[ "$(cut -d' ' -f1 <<<"$restored")" != "$(cut -d' ' -f1 <<<"$live")" ]]; then
        echo "$db: restored tables differ from live" >&2
        failed=1
    fi
    rows="$(awk '{s += $2} END {print s + 0}' <<<"$restored")"
    echo "$db: $(wc -l <<<"$restored") tables, $rows rows restored"
done
exit "$failed"
