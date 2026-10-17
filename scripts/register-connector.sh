#!/usr/bin/env bash
# Register (or update) the Debezium connector from streaming/debezium/app-connector.json.
set -euo pipefail
: "${POSTGRES_USER:=tributary}" "${POSTGRES_PASSWORD:=tributary}"
url="${CONNECT_URL:-http://localhost:8083}"
for _ in $(seq 1 60); do curl -sf "$url/connectors" >/dev/null && break; sleep 2; done
config="$(sed -e "s/\${POSTGRES_USER}/$POSTGRES_USER/" -e "s/\${POSTGRES_PASSWORD}/$POSTGRES_PASSWORD/" \
    "$(dirname "$0")/../streaming/debezium/app-connector.json")"
curl -sf -X PUT -H "Content-Type: application/json" --data "$config" "$url/connectors/app/config" >/dev/null
for _ in $(seq 1 30); do
    state="$(curl -sf "$url/connectors/app/status" | python3 -c 'import json,sys
s = json.load(sys.stdin); print(s["connector"]["state"], *[t["state"] for t in s["tasks"]])')" || true
    [[ "$state" == "RUNNING RUNNING" ]] && { echo "connector app: running"; exit 0; }
    sleep 2
done
echo "connector app not running: $state" >&2; exit 1
