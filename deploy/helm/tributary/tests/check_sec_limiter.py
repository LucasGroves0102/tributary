"""Check rendered manifests for the SEC limiter login (values: secLimiter.dedicatedLogin).

Usage: helm template ... | python3 check_sec_limiter.py on|off
on:  every `uses: sec` container reads SEC_LIMITER_DATABASE_URL from sec-limiter-database-url,
     and containers without DATABASE_URL (sec but not postgres) reference no database-url.
off: SEC_LIMITER_DATABASE_URL comes from database-url (the behaviour before the flag).
CI runs it in the chart job; it needs only PyYAML."""

import sys

import yaml


def containers(doc):
    spec = (doc or {}).get("spec") or {}
    template = (spec.get("jobTemplate", {}).get("spec", spec)).get("template", {})
    return template.get("spec", {}).get("containers", [])


def secret_keys(container):
    return {e["name"]: e["valueFrom"]["secretKeyRef"]["key"]
            for e in container.get("env", []) if "secretKeyRef" in e.get("valueFrom", {})}


def main(mode: str) -> int:
    errors, checked, without_db = [], 0, 0
    for doc in yaml.safe_load_all(sys.stdin):
        for c in containers(doc):
            keys = secret_keys(c)
            if "SEC_LIMITER_DATABASE_URL" not in keys:
                continue
            checked += 1
            name = f"{doc['kind']}/{doc['metadata']['name']}"
            want = "sec-limiter-database-url" if mode == "on" else "database-url"
            if keys["SEC_LIMITER_DATABASE_URL"] != want:
                errors.append(f"{name}: SEC_LIMITER_DATABASE_URL from "
                              f"{keys['SEC_LIMITER_DATABASE_URL']}, want {want}")
            if mode == "on" and "DATABASE_URL" not in keys:
                without_db += 1
                if "database-url" in keys.values():
                    errors.append(f"{name}: uses sec without postgres but gets database-url")
    if not checked or (mode == "on" and not without_db):
        errors.append(f"nothing checked ({checked} sec containers, {without_db} without postgres)")
    print("\n".join(errors) or f"ok: {checked} sec containers ({without_db} without postgres)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
