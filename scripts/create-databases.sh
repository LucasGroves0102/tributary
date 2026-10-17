#!/usr/bin/env bash
# Create the app and openfga databases next to the analytics database, if missing.
set -euo pipefail
: "${DATABASE_URL:?DATABASE_URL must be set}"
for _ in $(seq 1 30); do psql "$DATABASE_URL" -qAtc "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done
for db in app openfga; do
    if [[ -z "$(psql "$DATABASE_URL" -qAtc "SELECT 1 FROM pg_database WHERE datname = '$db'")" ]]; then
        psql "$DATABASE_URL" -qc "CREATE DATABASE $db"
        echo "created database $db"
    fi
done
