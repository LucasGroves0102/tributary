#!/usr/bin/env bash
# Apply <dir>/*.sql in order, each exactly once, recorded in public.schema_migrations.
# Usage: migrate.sh [dir] [database-url]   (defaults: db/migrations, $DATABASE_URL)
set -euo pipefail

dir="${1:-$(dirname "$0")/../db/migrations}"
url="${2:-${DATABASE_URL:?DATABASE_URL must be set}}"
psql_() { PGOPTIONS="-c client_min_messages=warning" psql "$url" -v ON_ERROR_STOP=1 -qAt "$@"; }

psql_ -c "CREATE TABLE IF NOT EXISTS public.schema_migrations (
            version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"

for file in "$dir"/*.sql; do
    version="$(basename "$file")"
    if [[ -n "$(psql_ -c "SELECT 1 FROM public.schema_migrations WHERE version = '$version'")" ]]; then
        continue
    fi
    echo "applying $version"
    # migration and its bookkeeping row commit together or not at all
    psql_ -1 -f "$file" -c "INSERT INTO public.schema_migrations (version) VALUES ('$version')"
done
echo "migrations up to date: $dir"
