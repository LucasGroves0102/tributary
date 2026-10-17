#!/usr/bin/env bash
# Find or create the "tributary" OpenFGA store, then write authz/model.fga as its latest model.
# Services look the store up by name, so no store ID needs to be configured anywhere.
set -euo pipefail
COMPOSE="${COMPOSE:-docker-compose}"
fga() { $COMPOSE --profile tools run --rm -T fga "$@" 2>/dev/null; }

store_id="$(fga store list | python3 -c '
import json, sys
stores = [s for s in json.load(sys.stdin).get("stores", []) if s["name"] == "tributary"]
print(stores[0]["id"] if stores else "")')"
if [[ -z "$store_id" ]]; then
    store_id="$(fga store create --name tributary | python3 -c '
import json, sys; print(json.load(sys.stdin)["store"]["id"])')"
    echo "created store tributary: $store_id"
fi
model_id="$(fga model write --store-id "$store_id" --file model.fga | python3 -c '
import json, sys; print(json.load(sys.stdin)["authorization_model_id"])')"
echo "store $store_id: model $model_id"
