"""Check rendered manifests for Kafka SASL (values: kafka.sasl.enabled).

Usage: helm template ... | python3 check_kafka_auth.py on|off
on:  every Kafka client container connects over SASL_PLAINTEXT/SCRAM-SHA-512 as a user listed in
     deploy/helm/platform/files/kafka-acls.yaml, with the password from kafka-password-<user>;
     every listed principal (except platform ones) is used by some workload.
off: no container has SASL settings (the behaviour before the flag).
CI runs it in the chart job; it needs only PyYAML."""

import sys
from pathlib import Path

import yaml

ACLS = Path(__file__).resolve().parents[2] / "platform/files/kafka-acls.yaml"
PLATFORM_PRINCIPALS = {"connect", "kafka-exporter"}  # platform and monitoring namespaces


def containers(doc):
    spec = (doc or {}).get("spec") or {}
    template = (spec.get("jobTemplate", {}).get("spec", spec)).get("template", {})
    return template.get("spec", {}).get("containers", [])


def main(mode: str) -> int:
    principals = set(yaml.safe_load(ACLS.read_text())["principals"])
    errors, used, checked = [], set(), 0
    for doc in yaml.safe_load_all(sys.stdin):
        for c in containers(doc):
            env = {e["name"]: e for e in c.get("env", [])}
            if "KAFKA_BOOTSTRAP_SERVERS" not in env:
                continue
            checked += 1
            name = f"{doc['kind']}/{doc['metadata']['name']}"
            sasl = sorted(k for k in env if k.startswith(("KAFKA_SASL", "KAFKA_SECURITY")))
            bootstrap = env["KAFKA_BOOTSTRAP_SERVERS"]["value"]
            if mode == "off":
                if sasl:
                    errors.append(f"{name}: {', '.join(sasl)} set with kafka.sasl.enabled off")
                if not bootstrap.endswith(":9092"):
                    errors.append(f"{name}: bootstrap {bootstrap}")
                continue
            value = {k: e.get("value") for k, e in env.items()}
            user = value.get("KAFKA_SASL_USERNAME")
            if value.get("KAFKA_SECURITY_PROTOCOL") != "SASL_PLAINTEXT" \
                    or value.get("KAFKA_SASL_MECHANISM") != "SCRAM-SHA-512":
                errors.append(f"{name}: not SASL_PLAINTEXT + SCRAM-SHA-512")
            if not bootstrap.endswith(":9094"):
                errors.append(f"{name}: bootstrap {bootstrap}, want the SASL listener")
            if user not in principals:
                errors.append(f"{name}: user {user!r} not in {ACLS.name}")
            ref = env.get("KAFKA_SASL_PASSWORD", {}).get("valueFrom", {}).get("secretKeyRef", {})
            if ref.get("key") != f"kafka-password-{user}":
                errors.append(f"{name}: password from {ref.get('key')}, want kafka-password-{user}")
            used.add(user)
    if mode == "on":
        unused = principals - PLATFORM_PRINCIPALS - used
        if unused:
            errors.append(f"principals no workload uses: {sorted(unused)}")
    if not checked:
        errors.append("no Kafka clients rendered")
    print("\n".join(errors) or f"ok: {checked} Kafka client containers ({mode})")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
