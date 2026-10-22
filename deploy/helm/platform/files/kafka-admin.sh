# Creates the SCRAM user `admin` that setup-kafka connects as, and updates it when the password
# file changes (a mounted Secret updates in place). Runs in the Kafka pod (container scram-admin;
# in CI a container sharing the broker's network) against the broker's localhost-only listener,
# where the principal is ANONYMOUS, a super user there: the only way in before any SCRAM user
# exists. Every other user and ACL is setup-kafka's job (deploy/platform-setup/setup.py kafka).
# The password is on kafka-configs' command line, visible only inside this container's own
# process namespace.
set -u
file="${ADMIN_PASSWORD_FILE:-/secrets/kafka-password-admin}"
bootstrap="${BOOTSTRAP:-localhost:9095}"
applied=""
while true; do
  sum="$(cksum < "$file")"
  if [ "$sum" != "$applied" ]; then
    if /opt/kafka/bin/kafka-configs.sh --bootstrap-server "$bootstrap" --alter \
        --entity-type users --entity-name admin \
        --add-config "SCRAM-SHA-512=[iterations=8192,password=$(tr -d '\n' < "$file")]" \
        > /tmp/kafka-configs.log 2>&1; then
      applied="$sum"
      echo "SCRAM user admin: set"
    else
      echo "SCRAM user admin: not set yet (broker starting?), retrying"
      tail -n 2 /tmp/kafka-configs.log
    fi
  fi
  sleep "${INTERVAL:-20}"
done
