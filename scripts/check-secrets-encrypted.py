"""Fail if any file in deploy/secrets has a plaintext secret value or lacks SOPS metadata."""

import sys
from pathlib import Path

import yaml

failed = False
for path in sorted(Path("deploy/secrets").glob("*.yaml")):
    for doc in yaml.safe_load_all(path.read_text()):
        if not doc:
            continue
        values = {**doc.get("data", {}), **doc.get("stringData", {})}
        plain = [k for k, v in values.items() if not str(v).startswith("ENC[")]
        if "sops" not in doc or plain:
            print(f"{path}: not encrypted with sops {plain or ''}")
            failed = True
sys.exit(failed)
