"""Check rendered manifests for alert email's egress (values: alertEmail.enabled, alertEmail.port).

Usage: helm template ... | python3 check_alert_email.py on|off [port]
off: no allow-smtp policy (the default: email is off, allow-internet opens 443 only).
on:  allow-smtp opens TCP `port` (default 587) to public addresses only, for pods labelled
     tributary.io/smtp, and only the alerts job carries that label.
CI runs it in the chart job; it needs only PyYAML."""

import sys

import yaml

PRIVATE = {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10"}


def pod_labels(doc):
    spec = (doc or {}).get("spec") or {}
    template = (spec.get("jobTemplate", {}).get("spec", spec)).get("template", {})
    return (template.get("metadata") or {}).get("labels") or {}


def main(mode: str, port: int) -> int:
    errors = []
    docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
    smtp = [d for d in docs
            if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "allow-smtp"]
    labelled = sorted(f"{d['kind']}/{d['metadata']['name']}" for d in docs
                      if pod_labels(d).get("tributary.io/smtp") == "true")
    if labelled != ["CronJob/alerts"]:
        errors.append(f"tributary.io/smtp on {labelled}, want only CronJob/alerts")
    if mode == "off" and smtp:
        errors.append("allow-smtp rendered with alertEmail off")
    if mode == "on":
        if len(smtp) != 1:
            errors.append("allow-smtp missing with alertEmail on")
        else:
            spec = smtp[0]["spec"]
            if spec["podSelector"] != {"matchLabels": {"tributary.io/smtp": "true"}}:
                errors.append(f"allow-smtp selects {spec['podSelector']}")
            (rule,) = spec["egress"]
            if rule["ports"] != [{"protocol": "TCP", "port": port}]:
                errors.append(f"allow-smtp ports {rule['ports']}, want TCP {port}")
            (block,) = rule["to"]
            ip = block["ipBlock"]
            if ip["cidr"] != "0.0.0.0/0" or set(ip["except"]) != PRIVATE:
                errors.append(f"allow-smtp reaches {block}")
    internet = [d for d in docs if d["kind"] == "NetworkPolicy"
                and d["metadata"]["name"] == "allow-internet"]
    if [p for d in internet for r in d["spec"]["egress"] for p in r["ports"]] != [
            {"protocol": "TCP", "port": 443}]:
        errors.append("allow-internet opens more than 443")
    for e in errors:
        print("check_alert_email:", e, file=sys.stderr)
    if not errors:
        print(f"check_alert_email {mode}: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 587))
