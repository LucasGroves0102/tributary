"""Kafka authentication and ACLs against a live broker: every principal of
deploy/helm/platform/files/kafka-acls.yaml can do what the file grants and nothing else, and a
client without valid SCRAM credentials gets nothing at all.

Runs in CI (job propagation) against tests/propagation/compose.ci.yml, whose Kafka is configured
like hermes with all SASL flags on, after `setup.py kafka` created the users and ACLs. Needs
E2E=1, KAFKA_BOOTSTRAP_SERVERS (the SASL listener) and KAFKA_PASSWORDS_DIR
(kafka-password-<user> files, as in the platform secret)."""

import importlib.util
import os
import time
import uuid
from pathlib import Path

import pytest
from confluent_kafka import (
    OFFSET_BEGINNING,
    Consumer,
    ConsumerGroupTopicPartitions,
    KafkaError,
    KafkaException,
    Producer,
)
from confluent_kafka import TopicPartition as TP
from confluent_kafka.admin import AdminClient

ROOT = Path(__file__).resolve().parents[2]
PASSWORDS = os.environ.get("KAFKA_PASSWORDS_DIR", "")
pytestmark = pytest.mark.skipif(not (os.environ.get("E2E") and PASSWORDS),
                                reason="needs E2E=1 and KAFKA_PASSWORDS_DIR (live Kafka with SASL)")

_spec = importlib.util.spec_from_file_location("platform_setup",
                                               ROOT / "deploy/platform-setup/setup.py")
assert _spec and _spec.loader
setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup)
SPEC = setup.load_acl_spec(str(ROOT / "deploy/helm/platform/files/kafka-acls.yaml"))
ACLS = setup.kafka_acls(SPEC)
USERS = setup.kafka_users(SPEC)
BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
# The pipeline topics, and one CDC topic (Debezium has filled it by the time this runs).
TOPICS = ["filings.raw", "documents.fetched", "documents.backfill", "app.public.users"]
SEEDED = TOPICS[:3]
IMPLIES = {"describe": {"read", "write", "delete", "alter", "describe"}}


def allowed(user: str, resource: str, name: str, op: str) -> bool:
    ops = IMPLIES.get(op, {op}) | {"all"}
    return any(p == f"User:{user}" and r == resource and o in ops
               and (n == "*" or (pattern == "literal" and n == name)
                    or (pattern == "prefixed" and name.startswith(n)))
               for r, n, pattern, p, o in ACLS)


def password(user: str) -> str:
    return (Path(PASSWORDS) / f"kafka-password-{user}").read_text().strip()


def config(user: str, secret: str | None = None, **extra) -> dict:
    return {"bootstrap.servers": BOOTSTRAP, "broker.address.family": "v4",
            "security.protocol": "SASL_PLAINTEXT", "sasl.mechanism": "SCRAM-SHA-512",
            "sasl.username": user,
            "sasl.password": secret if secret is not None else password(user), **extra}


def groups(user: str) -> list[str]:
    return (SPEC["principals"][user] or {}).get("groups", [])


@pytest.fixture(scope="module", autouse=True)
def seeded():
    """One message in each pipeline topic, written as the super user admin."""
    producer = Producer(config("admin"))
    for topic in SEEDED:
        producer.produce(topic, key=b"acl-test", value=b'{"acl_test": true}')
    assert producer.flush(30) == 0


def produce(user: str, topic: str) -> KafkaError | None:
    errors: list[KafkaError | None] = []
    producer = Producer(config(user, **{"message.timeout.ms": 15000}))
    # a tombstone in a CDC topic (authz-sync skips them), a harmless test event elsewhere
    value = None if topic.startswith("app.") else b'{"acl_test": true}'
    producer.produce(topic, key=b"acl-test", value=value,
                     on_delivery=lambda err, _msg: errors.append(err))
    producer.flush(25)
    assert len(errors) == 1, f"{user} → {topic}: no delivery report"
    return errors[0]


def group_of(user: str) -> str:
    return (groups(user) or [f"acl-test-{user}"])[0]


def fetch(user: str, topic: str, seconds: float) -> bool:
    """Whether the user's consumer receives a message from the topic within `seconds`."""
    consumer = Consumer(config(user, **{"group.id": group_of(user),
                                        "enable.auto.commit": False}))
    try:
        consumer.assign([TP(topic, 0, OFFSET_BEGINNING)])
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            msg = consumer.poll(0.5)
            if msg is not None and msg.error() is None:
                return True
        return False
    finally:
        consumer.close()


def commit(user: str, group: str, topic: str) -> KafkaError | None:
    """Commit an offset (OffsetCommit: READ on the group, then on the topic) via the admin API,
    which reports authorization errors directly."""
    admin = AdminClient(config(user))
    request = [ConsumerGroupTopicPartitions(group, [TP(topic, 0, 0)])]
    try:
        result = admin.alter_consumer_group_offsets(request, request_timeout=15)[group].result()
    except KafkaException as e:
        return e.args[0]
    return next((tp.error for tp in result.topic_partitions or [] if tp.error), None)


def read(user: str, topic: str) -> KafkaError | None:
    """None if the user can read the topic, else the authorization error its commit gets."""
    if fetch(user, topic, 20):
        return None
    return commit(user, group_of(user), topic) or KafkaError(KafkaError._TIMED_OUT)


def auth_errors(conf: dict) -> list[KafkaError]:
    errors: list[KafkaError] = []
    admin = AdminClient(conf | {"error_cb": errors.append})
    with pytest.raises(KafkaException):
        admin.list_topics(timeout=8)
    admin.poll(1)  # serve error_cb
    return errors


@pytest.mark.parametrize("user,topic", [(u, t) for u in USERS for t in TOPICS])
def test_produce(user, topic):
    err = produce(user, topic)
    if allowed(user, "topic", topic, "write"):
        assert err is None, f"{user} may write {topic}: {err}"
    else:  # not delivered: an authorization error, or a timeout waiting for one
        assert err is not None, f"{user} must not write {topic}"


@pytest.mark.parametrize("user,topic", [(u, t) for u in USERS for t in TOPICS])
def test_read(user, topic):
    if allowed(user, "topic", topic, "read"):
        assert fetch(user, topic, 20), f"{user} may read {topic}, got nothing in 20 s"
        return
    assert not fetch(user, topic, 3), f"{user} must not read {topic}"
    err = commit(user, group_of(user), topic)
    want = (KafkaError.TOPIC_AUTHORIZATION_FAILED if groups(user)
            else KafkaError.GROUP_AUTHORIZATION_FAILED)  # no group of its own at all
    assert err is not None and err.code() == want, f"{user} on {topic}: {err}"


@pytest.mark.parametrize("user", [u for u in USERS if groups(u)])
def test_groups(user):
    """Commit offsets in the principal's own group; another principal's group is refused."""
    readable = [t for t in TOPICS if allowed(user, "topic", t, "read")]
    if readable:
        assert commit(user, groups(user)[0], readable[0]) is None
    other = "bronze-loader" if groups(user)[0] != "bronze-loader" else "doc-fetcher"
    err = commit(user, other, (readable or ["filings.raw"])[0])
    assert err is not None and err.code() == KafkaError.GROUP_AUTHORIZATION_FAILED, err


def test_the_safety_critical_rules():
    """Spelled out, so a careless edit of kafka-acls.yaml can't quietly pass the matrix."""
    assert produce("authz-sync", "app.public.users") is not None  # no forged grants
    assert not fetch("authz-sync", "filings.raw", 3)
    assert not fetch("poller", "filings.raw", 3)
    assert not fetch("kafka-exporter", "filings.raw", 3)
    assert fetch("parser", "documents.fetched", 20)
    assert read("authz-sync", "app.public.users") is None


def test_exporter_sees_topics_and_groups():
    admin = AdminClient(config("kafka-exporter"))
    assert set(SEEDED) <= set(admin.list_topics(timeout=10).topics)
    groups_seen = {g.group_id for g in admin.list_consumer_groups().result(timeout=10).valid}
    assert "bronze-loader" in groups_seen  # from test_groups' commits


def test_unauthenticated_clients_are_rejected():
    for conf in (config("poller", "wrong-" + uuid.uuid4().hex),
                 config("mallory-" + uuid.uuid4().hex[:8], uuid.uuid4().hex)):
        codes = {e.code() for e in auth_errors(conf)}
        assert KafkaError._AUTHENTICATION in codes, codes
    # PLAINTEXT against the SASL listener never gets a metadata response
    with pytest.raises(KafkaException):
        AdminClient({"bootstrap.servers": BOOTSTRAP}).list_topics(timeout=8)
