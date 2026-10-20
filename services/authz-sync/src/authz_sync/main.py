"""Replay app CDC topics → app state → reconcile OpenFGA and authz.user_access."""

import json
import logging
import os
import signal
import threading
import time

import psycopg
from confluent_kafka import OFFSET_BEGINNING, Consumer, KafkaError, KafkaException, TopicPartition

from authz_sync.reconcile import Reconciler
from authz_sync.state import AppState
from tributary_authz import FgaClient

TABLES = ("users", "subscriptions", "plan_tiers", "company_grants")
TOPICS = [f"app.public.{t}" for t in TABLES]

log = logging.getLogger("authz_sync")


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    consumer = Consumer({
        "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        "group.id": "authz-sync",  # required by librdkafka; offsets are never committed
        "enable.auto.commit": False,
    })
    partitions = wait_for_topics(consumer, stop)
    if partitions is None:
        return
    consumer.assign([TopicPartition(tp.topic, tp.partition, OFFSET_BEGINNING)
                     for tp in partitions])
    # state is only complete once every partition is read up to where it was at startup
    pending = {(tp.topic, tp.partition): consumer.get_watermark_offsets(tp)[1]
               for tp in partitions}
    pending = {k: high for k, high in pending.items() if high > 0}

    state = AppState()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        reconciler = Reconciler(FgaClient.from_env(), conn)
        caught_up = not pending
        if caught_up:
            reconcile(state, reconciler, "startup (empty topics)")
        try:
            while not stop.is_set():
                msgs = consumer.consume(num_messages=500, timeout=0.5)
                oldest_commit_ms = None
                for m in msgs:
                    if m.error():
                        if m.error().code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
                            continue
                        raise KafkaException(m.error())
                    if m.value() is None:  # tombstone after a delete; already applied
                        continue
                    event = json.loads(m.value())
                    state.apply(m.topic().rsplit(".", 1)[-1], event)
                    commit_ms = event["source"]["ts_ms"]
                    oldest_commit_ms = min(oldest_commit_ms or commit_ms, commit_ms)
                if not caught_up:
                    # positions, not message offsets: compaction can remove the last offset
                    for tp in consumer.position([TopicPartition(t, p) for t, p in pending]):
                        if tp.offset >= pending[(tp.topic, tp.partition)]:
                            del pending[(tp.topic, tp.partition)]
                    if not pending:
                        caught_up = True
                        reconcile(state, reconciler, "startup replay")
                elif msgs:
                    reconcile(state, reconciler, f"{len(msgs)} events", oldest_commit_ms)
        finally:
            consumer.close()
    log.info("stopped")


def reconcile(state: AppState, reconciler: Reconciler, why: str,
              since_commit_ms: int | None = None) -> None:
    counts = reconciler.apply(state.desired_tuples(), state.desired_access())
    if since_commit_ms is not None:  # app DB commit → permissions applied everywhere
        why += f", applied {time.time() * 1000 - since_commit_ms:.0f} ms after app commit"
    log.info("reconciled (%s): %s", why, counts)


def wait_for_topics(consumer: Consumer, stop: threading.Event) -> list[TopicPartition] | None:
    """Debezium creates the topics on its first snapshot; wait until all exist."""
    while not stop.is_set():
        topics = consumer.list_topics(timeout=10).topics
        missing = [t for t in TOPICS if t not in topics]
        if not missing:
            return [TopicPartition(t, p) for t in TOPICS for p in topics[t].partitions]
        log.info("waiting for CDC topics: %s (run `make cdc`)", missing)
        stop.wait(5)
    return None


if __name__ == "__main__":
    main()
