"""Platform setup run by Helm hooks: `setup.py openfga` (store + model), `setup.py connector` and
`setup.py kafka` (SCRAM users + ACLs from kafka-acls.yaml)."""

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
ADMIN = "admin"  # super user; its SCRAM credential is created inside the Kafka pod
MECHANISM = "SCRAM-SHA-512"
ITERATIONS = 8192


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


Acl = tuple[str, str, str, str, str]  # (resource, name, pattern, principal, operation)
OPERATIONS = {"read", "write", "create", "delete", "alter", "describe", "describe_configs",
              "alter_configs", "idempotent_write", "cluster_action", "all"}


def load_acl_spec(path: str) -> dict:
    """kafka-acls.yaml, or the JSON the chart renders from it (the setup image has no PyYAML)."""
    with open(path) as f:
        if path.endswith(".json"):
            return json.load(f)
        import yaml  # CI and local runs read the YAML directly

        return yaml.safe_load(f)


def kafka_users(spec: dict) -> list[str]:
    users = sorted(spec["principals"])
    if ADMIN in users:
        raise ValueError(f"{ADMIN} is the super user; don't list it")
    return users


def kafka_acls(spec: dict) -> set[Acl]:
    """Every ACL the spec grants (all ALLOW, any host)."""
    acls: set[Acl] = set()
    for user, grants in spec["principals"].items():
        principal = f"User:{user}"
        unknown = set(grants or {}) - {"produce", "consume", "groups", "acls"}
        if unknown:
            raise ValueError(f"{user}: unknown keys {sorted(unknown)}")
        grants = grants or {}
        for topic in grants.get("produce", []):
            acls |= {("topic", topic, "literal", principal, op) for op in ("write", "create")}
        for topic in grants.get("consume", []):
            acls.add(("topic", topic, "literal", principal, "read"))
        for group in grants.get("groups", []):
            acls.add(("group", group, "literal", principal, "read"))
        for acl in grants.get("acls", []):
            resource, pattern = acl["resource"], acl.get("pattern", "literal")
            if resource not in ("topic", "group", "cluster") or pattern not in ("literal",
                                                                               "prefixed"):
                raise ValueError(f"{user}: bad ACL {acl}")
            if resource == "cluster" and acl["name"] != "kafka-cluster":
                raise ValueError(f"{user}: the cluster resource is named kafka-cluster")
            for op in acl["operations"]:
                if op not in OPERATIONS:
                    raise ValueError(f"{user}: unknown operation {op}")
                acls.add((resource, acl["name"], pattern, principal, op))
    return acls


def acl_diff(current: set[Acl], wanted: set[Acl]) -> tuple[set[Acl], set[Acl]]:
    """(to delete, to create)."""
    return current - wanted, wanted - current


def read_password(directory: str, user: str) -> str:
    path = os.path.join(directory, f"kafka-password-{user}")
    try:
        with open(path) as f:
            password = f.read().strip()
    except FileNotFoundError:
        sys.exit(f"{path} missing: add kafka-password-{user} to the platform secret")
    if len(password) < 16:
        sys.exit(f"kafka-password-{user} is shorter than 16 characters")
    return password


def kafka() -> None:
    from confluent_kafka import KafkaException
    from confluent_kafka.admin import (
        AclBinding,
        AclBindingFilter,
        AclOperation,
        AclPermissionType,
        AdminClient,
        ResourcePatternType,
        ResourceType,
        ScramCredentialInfo,
        ScramMechanism,
        UserScramCredentialDeletion,
        UserScramCredentialUpsertion,
    )

    restypes = {"topic": ResourceType.TOPIC, "group": ResourceType.GROUP,
                "cluster": ResourceType.BROKER}
    names = {v: k for k, v in restypes.items()}

    def binding(acl: Acl, cls: type = AclBinding):
        resource, name, pattern, principal, op = acl
        return cls(restypes[resource], name, ResourcePatternType[pattern.upper()], principal, "*",
                   AclOperation[op.upper()], AclPermissionType.ALLOW)

    spec = load_acl_spec(os.environ["KAFKA_ACLS"])
    users, wanted = kafka_users(spec), kafka_acls(spec)
    secrets = os.environ.get("KAFKA_PASSWORDS_DIR", "/secrets")
    passwords = {u: read_password(secrets, u) for u in users}
    admin = AdminClient({
        "bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"],
        "security.protocol": "SASL_PLAINTEXT",
        "sasl.mechanism": MECHANISM,
        "sasl.username": ADMIN,
        "sasl.password": read_password(secrets, ADMIN),
    })
    # The admin user appears once the broker's sidecar has created it.
    for attempt in range(60):
        try:
            admin.list_topics(timeout=10)
            break
        except KafkaException as e:
            if attempt == 59:
                sys.exit(f"cannot connect to Kafka as {ADMIN}: {e}")
            time.sleep(5)

    existing = admin.describe_user_scram_credentials().result(timeout=30)
    stale = [UserScramCredentialDeletion(user, info.mechanism)
             for user, desc in existing.items() if user not in passwords and user != ADMIN
             for info in desc.scram_credential_infos]
    upserts = [UserScramCredentialUpsertion(
        user, ScramCredentialInfo(ScramMechanism.SCRAM_SHA_512, ITERATIONS), password.encode())
        for user, password in passwords.items()]
    for future in admin.alter_user_scram_credentials(stale + upserts).values():
        future.result(timeout=30)
    print(f"SCRAM users: {len(upserts)} set, {len(stale)} stale credentials deleted")

    any_acl = AclBindingFilter(ResourceType.ANY, None, ResourcePatternType.ANY, None, None,
                               AclOperation.ANY, AclPermissionType.ANY)
    current: set[Acl] = set()
    foreign = []  # DENY or host-specific ACLs: never written by this file, deleted as they are
    for b in admin.describe_acls(any_acl).result(timeout=30):
        if b.permission_type == AclPermissionType.ALLOW and b.host == "*" and b.restype in names:
            current.add((names[b.restype], b.name, b.resource_pattern_type.name.lower(),
                         b.principal, b.operation.name.lower()))
        else:
            foreign.append(AclBindingFilter(b.restype, b.name, b.resource_pattern_type,
                                            b.principal, b.host, b.operation, b.permission_type))
    delete, create = acl_diff(current, wanted)
    filters = [binding(a, AclBindingFilter) for a in sorted(delete)] + foreign
    # deletes first, so a changed grant never widens access in between
    if filters:
        for future in admin.delete_acls(filters).values():
            future.result(timeout=30)
    if create:
        for future in admin.create_acls([binding(a) for a in sorted(create)]).values():
            future.result(timeout=30)
    print(f"ACLs: {len(wanted)} wanted, {len(create)} created, {len(filters)} deleted")


if __name__ == "__main__":
    {"openfga": openfga, "connector": connector, "kafka": kafka}[sys.argv[1]]()
