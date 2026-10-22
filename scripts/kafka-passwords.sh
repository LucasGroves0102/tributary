#!/usr/bin/env bash
# Create the Kafka SCRAM passwords (deploy/helm/platform README, "Kafka authentication") in the
# SOPS secrets without printing them: one random 64-hex-character password per principal of
# deploy/helm/platform/files/kafka-acls.yaml, plus admin, as kafka-password-<user> in
#   deploy/secrets/platform.yaml        all of them (setup-kafka sets every user from these)
#   deploy/secrets/tributary.yaml       the tributary chart's clients (not admin/connect/exporter)
#   deploy/secrets/monitoring-kafka-exporter.yaml   Secret kafka-exporter-kafka (username,
#                                       password) for kafka-exporter, created if missing
# A user's password is the same in every file. Existing keys are kept, so rerunning only adds
# what's missing (e.g. after adding a principal); to rotate one, delete its key in every file
# (sops <file>) and rerun. Needs sops >= 3.9 and the age private key; review with git diff
# (values stay encrypted) and commit.
set -euo pipefail
cd "$(dirname "$0")/.."
acls=deploy/helm/platform/files/kafka-acls.yaml
platform=deploy/secrets/platform.yaml
tributary=deploy/secrets/tributary.yaml
exporter=deploy/secrets/monitoring-kafka-exporter.yaml
not_tributary=" admin connect kafka-exporter "

get() { sops decrypt --extract "[\"stringData\"][\"$2\"]" "$1" 2>/dev/null || true; }
put() { sops set "$1" "[\"stringData\"][\"$2\"]" "\"$3\""; echo "$1: set $2"; }

for user in admin $(sed -n 's/^  \([a-z-]*\):.*/\1/p' "$acls"); do
    key="kafka-password-$user"
    value="$(get "$platform" "$key")"
    if [ -z "$value" ]; then
        value="$(openssl rand -hex 32)"
        put "$platform" "$key" "$value"
    fi
    case "$not_tributary" in *" $user "*) continue ;; esac
    [ "$(get "$tributary" "$key")" = "$value" ] || put "$tributary" "$key" "$value"
done

value="$(get "$platform" kafka-password-kafka-exporter)"
if [ ! -f "$exporter" ]; then
    umask 077
    tmp="$(mktemp)"
    trap 'rm -f "$tmp"' EXIT
    printf '%s\n' "apiVersion: v1" "kind: Secret" "metadata:" "    name: kafka-exporter-kafka" \
        "    namespace: monitoring" "type: Opaque" "stringData:" "    username: kafka-exporter" \
        "    password: \"$value\"" > "$tmp"
    sops encrypt --filename-override "$exporter" --input-type yaml --output-type yaml "$tmp" \
        > "$exporter"
    echo "$exporter: created"
elif [ "$(get "$exporter" password)" != "$value" ]; then
    put "$exporter" password "$value"
fi
python3 scripts/check-secrets-encrypted.py
