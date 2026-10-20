#!/usr/bin/env bash
# Copy the latest local backup to R2 as one age-encrypted tar. Runs as the postgres user from
# pg-backup-offsite.service, which passes the R2 keys in as systemd credentials.
set -euo pipefail
cd /

dir="${BACKUP_DIR:-/var/backups/postgres}"
: "${R2_ENDPOINT:?}" "${R2_BUCKET:?}" "${AGE_RECIPIENT:?}"
creds="${CREDENTIALS_DIRECTORY:?}"

latest="$(find "$dir" -mindepth 1 -maxdepth 1 -type d -name '*Z' | sort | tail -1)"
[[ -n "$latest" ]] || { echo "no backups in $dir" >&2; exit 1; }
name="$(basename "$latest").tar.age"

# Encrypt to a local file first, so a failure never leaves a truncated object in the bucket.
umask 077
staged="$dir/$name.partial"
trap 'rm -f "$staged"' EXIT
tar -C "$dir" -cf - "$(basename "$latest")" | age -r "$AGE_RECIPIENT" >"$staged"

export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT"
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
RCLONE_CONFIG_R2_ACCESS_KEY_ID="$(<"$creds/r2-access-key-id")"
RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$(<"$creds/r2-secret-access-key")"
export RCLONE_CONFIG_R2_ACCESS_KEY_ID RCLONE_CONFIG_R2_SECRET_ACCESS_KEY

size="$(stat -c %s "$staged")"
rclone --config /dev/null copyto "$staged" "r2:$R2_BUCKET/$name"
remote="$(rclone --config /dev/null size --json "r2:$R2_BUCKET/$name" | sed -E 's/.*"bytes":([0-9]+).*/\1/')"
[[ "$remote" == "$size" ]] || { echo "uploaded $remote bytes, expected $size" >&2; exit 1; }
echo "offsite r2:$R2_BUCKET/$name: $size bytes"

textfile="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
if [[ -d "$textfile" && -w "$textfile" ]]; then
    {
        echo "# HELP pg_backup_offsite_last_success_timestamp_seconds Unix time of the last backup copied to R2."
        echo "# TYPE pg_backup_offsite_last_success_timestamp_seconds gauge"
        echo "pg_backup_offsite_last_success_timestamp_seconds $(date +%s)"
        echo "# HELP pg_backup_offsite_size_bytes Size of the last encrypted backup copied to R2."
        echo "# TYPE pg_backup_offsite_size_bytes gauge"
        echo "pg_backup_offsite_size_bytes $size"
    } >"$textfile/pg_backup_offsite.prom.tmp"
    chmod 0644 "$textfile/pg_backup_offsite.prom.tmp"
    mv "$textfile/pg_backup_offsite.prom.tmp" "$textfile/pg_backup_offsite.prom"
fi
