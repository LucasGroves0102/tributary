"""Platform setup run by Helm hooks: `setup.py openfga` (store + model) and `setup.py connector`."""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

STORE = "tributary"
MODEL = "authz/model.fga"
CONNECTOR = "streaming/debezium/app-connector.json"


def wait_for(url: str, attempts: int = 60) -> None:
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=5):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(2)
    sys.exit(f"{url} not reachable")


def fga(*args: str) -> dict:
    out = subprocess.run(["fga", *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def prune(value: Any) -> Any:
    """Drop empty fields: the server fills in defaults ("", {}) that the DSL leaves out."""
    if isinstance(value, dict):
        value = {k: prune(v) for k, v in value.items()}
        return {k: v for k, v in value.items() if v not in ("", {}, [], None)}
    if isinstance(value, list):
        return [prune(v) for v in value]
    return value


def model_json(model: dict) -> dict:
    return prune({k: model.get(k) for k in ("schema_version", "type_definitions", "conditions")})


def openfga() -> None:
    wait_for(os.environ["FGA_API_URL"] + "/healthz")
    stores = [s for s in fga("store", "list")["stores"] if s["name"] == STORE]
    if stores:
        store_id = stores[0]["id"]
    else:
        store_id = fga("store", "create", "--name", STORE)["store"]["id"]
        print(f"created store {STORE}: {store_id}")
    wanted = model_json(fga("model", "transform", "--file", MODEL, "--input-format", "fga"))
    try:
        current = fga("model", "get", "--store-id", store_id, "--format", "json")
    except subprocess.CalledProcessError:
        current = {}
    if model_json(current) == wanted:
        print(f"store {store_id}: model unchanged")
        return
    written = fga("model", "write", "--store-id", store_id, "--file", MODEL)
    print(f"store {store_id}: wrote model {written['authorization_model_id']}")


def connector() -> None:
    url = os.environ["CONNECT_URL"]
    wait_for(url + "/connectors")
    with open(CONNECTOR) as f:
        config = f.read()
    # Connect resolves ${env:...} from its own environment, so the password never leaves its pod.
    config = config.replace("${POSTGRES_USER}", os.environ["POSTGRES_USER"])
    config = config.replace("${POSTGRES_PASSWORD}", "${env:POSTGRES_PASSWORD}")
    req = urllib.request.Request(url + "/connectors/app/config", data=config.encode(), method="PUT",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).close()
    state = ""
    for _ in range(30):
        with urllib.request.urlopen(url + "/connectors/app/status", timeout=10) as r:
            status = json.load(r)
        state = " ".join([status["connector"]["state"], *(t["state"] for t in status["tasks"])])
        if state == "RUNNING RUNNING":
            print("connector app: running")
            return
        time.sleep(2)
    sys.exit(f"connector app not running: {state}")


if __name__ == "__main__":
    {"openfga": openfga, "connector": connector}[sys.argv[1]]()
