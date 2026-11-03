"""Replay app CDC topics → app state → reconcile OpenFGA and the authz tables."""

import json
import logging
import os
import signal
import threading
import time
from collections.abc import Callable

import psycopg
from confluent_kafka import OFFSET_BEGINNING, Consumer, KafkaError, KafkaException, TopicPartition
from prometheus_client import Counter, Gauge, Histogram

from authz_sync.reconcile import Reconciler
from authz_sync.state import AppState, accept_catalog
from tributary_authz import FgaClient
from tributary_kafka import client_config
from tributary_telemetry import CONSUMED, configure_logging, serve_metrics

TABLES = ("users", "subscriptions", "plan_tiers", "company_grants")
TOPICS = [f"app.public.{t}" for t in TABLES]
# Created by the first change to public_catalogs after the table joined the connector (app
# migration 008: Debezium doesn't snapshot a table added to a running connector). Until it
# exists no catalog is public; it is picked up when it appears.
OPTIONAL_TOPICS = ["app.public.public_catalogs"]

log = logging.getLogger("authz_sync")

RECONCILES = Counter("tributary_authz_reconciles_total", "Reconciliations applied")
CHANGES = Counter("tributary_authz_changes_total",
                  "OpenFGA tuples and authz table rows added or deleted", ["kind"])
PROPAGATION = Histogram("tributary_authz_propagation_seconds",
                        "App DB commit to permissions applied in OpenFGA and Postgres",
                        buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 300))
CAUGHT_UP = Gauge("tributary_authz_caught_up",
                  "1 once the startup replay of the CDC topics has been reconciled")
CATALOG_COMPANIES = Gauge("tributary_authz_catalog_companies",
                          "Companies in catalog:sec (gold financials or a stored document)")
CATALOG_PUBLIC = Gauge("tributary_authz_catalog_public",
                       "1 while the app database's public_catalogs says catalog:sec is public")
CATALOG_REFUSED = Counter("tributary_authz_catalog_refused_total",
                          "Catalog loads refused for shrinking to under half the current set")

POLL_SECONDS = 0.5  # idle wake-up (stop checks); a message ends the wait at once
LINGER_SECONDS = 0.05  # after the first message, for the rest of its transaction
BATCH = 500
# How long the broker holds an empty fetch. Data ends a fetch at once either way; this only
# bounds how stale an idle connection's view can get (librdkafka default 500 ms).
FETCH_WAIT_MAX_MS = 100
# How often catalog membership is re-read from the Tributary DB: a company that just got its
# first data becomes readable to everyone within this long (env CATALOG_REFRESH_SECONDS).
CATALOG_REFRESH_SECONDS = 600
TOPIC_CHECK_SECONDS = 5  # how often to look for an optional topic that doesn't exist yet


def main() -> None:
    configure_logging()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    serve_metrics()

    consumer = Consumer(client_config({
        "group.id": "authz-sync",  # required by librdkafka; offsets are never committed
        "enable.auto.commit": False,
        "fetch.wait.max.ms": FETCH_WAIT_MAX_MS,
    }))
    partitions = wait_for_topics(consumer, stop)
    if partitions is None:
        return
    consumer.assign([TopicPartition(tp.topic, tp.partition, OFFSET_BEGINNING)
                     for tp in partitions])
    missing = [t for t in OPTIONAL_TOPICS if t not in {tp.topic for tp in partitions}]
    # state is only complete once every partition is read up to where it was at startup
    pending = {(tp.topic, tp.partition): consumer.get_watermark_offsets(tp)[1]
               for tp in partitions}
    pending = {k: high for k, high in pending.items() if high > 0}

    state = AppState()
    every = float(os.environ.get("CATALOG_REFRESH_SECONDS") or CATALOG_REFRESH_SECONDS)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        reconciler = Reconciler(FgaClient.from_env(), conn)
        catalog = Catalog(lambda: load_catalog(conn), every)
        catalog.refresh()
        caught_up = not pending
        CAUGHT_UP.set(int(caught_up))
        if caught_up:
            reconcile(state, reconciler, catalog.companies, "startup (empty topics)")
        next_topic_check = time.monotonic() + TOPIC_CHECK_SECONDS
        try:
            while not stop.is_set():
                msgs = next_batch(consumer)
                oldest_commit_ms = None
                for m in msgs:
                    if m.error():
                        if m.error().code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
                            continue
                        raise KafkaException(m.error())
                    CONSUMED.labels(m.topic()).inc()
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
                        CAUGHT_UP.set(1)
                        reconcile(state, reconciler, catalog.companies, "startup replay")
                elif msgs:
                    reconcile(state, reconciler, catalog.companies, f"{len(msgs)} events",
                              oldest_commit_ms)
                if missing and time.monotonic() >= next_topic_check:
                    missing = assign_new_topics(consumer, missing)
                    next_topic_check = time.monotonic() + TOPIC_CHECK_SECONDS
                if caught_up and catalog.due() and catalog.refresh():
                    reconcile(state, reconciler, catalog.companies, "catalog changed")
        finally:
            consumer.close()
    log.info("stopped")


class Catalog:
    """The company ids in catalog:sec, re-read every `every` seconds. A failed load, or one that
    state.accept_catalog refuses, keeps the current set."""

    def __init__(self, load: Callable[[], set[str]], every: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._load, self._every, self._clock = load, every, clock
        self.companies: frozenset[str] = frozenset()
        self._next = clock()

    def due(self) -> bool:
        return self._clock() >= self._next

    def refresh(self) -> bool:
        """Reload now; True if the set changed."""
        self._next = self._clock() + self._every
        try:
            loaded = frozenset(self._load())
        except psycopg.Error as e:
            log.warning("catalog load failed, keeping %d companies: %s", len(self.companies), e)
            return False
        if not accept_catalog(self.companies, loaded):
            CATALOG_REFUSED.inc()
            log.error("catalog load refused: %d companies, currently %d",
                      len(loaded), len(self.companies))
            return False
        changed = loaded != self.companies
        if changed:
            log.info("catalog: %d companies (%+d)", len(loaded),
                     len(loaded) - len(self.companies))
        self.companies = loaded
        CATALOG_COMPANIES.set(len(loaded))
        return changed


def load_catalog(conn: psycopg.Connection) -> set[str]:
    """db/migrations/030: every company with gold financials or a stored filing document."""
    with conn.cursor() as cur:
        cur.execute("SELECT company_id FROM authz.catalog_companies()")
        return {r[0] for r in cur.fetchall()}


def assign_new_topics(consumer: Consumer, missing: list[str]) -> list[str]:
    """Start reading the optional topics that now exist, from the beginning; return those
    still missing. incremental_assign leaves the topics already assigned where they are."""
    topics = consumer.list_topics(timeout=10).topics
    found = [t for t in missing if t in topics]
    if found:
        consumer.incremental_assign([TopicPartition(t, p, OFFSET_BEGINNING)
                                     for t in found for p in topics[t].partitions])
        log.info("reading new CDC topics: %s", found)
    return [t for t in missing if t not in found]


def next_batch(consumer: Consumer, timeout: float = POLL_SECONDS,
               linger: float = LINGER_SECONDS, limit: int = BATCH) -> list:
    """Wait up to `timeout` for one message, then up to `linger` for the rest of its burst.
    consume(n, timeout) alone blocks until n messages or the whole timeout, so a single
    revocation used to wait 0.5 s here. The linger keeps one app transaction's events (Debezium
    produces them together) in one reconcile, as the 0.5 s window did; neither is a guarantee,
    and the next event's reconcile converges either way."""
    first = consumer.poll(timeout)
    if first is None:
        return []
    return [first, *consumer.consume(num_messages=limit - 1, timeout=linger)]


def reconcile(state: AppState, reconciler: Reconciler, catalog: frozenset[str], why: str,
              since_commit_ms: int | None = None,
              clock: Callable[[], float] = time.time) -> None:
    counts = reconciler.apply(state.desired(catalog))
    CATALOG_PUBLIC.set(int(state.is_public()))
    RECONCILES.inc()
    for kind, n in counts.items():
        CHANGES.labels(kind).inc(n)
    extra: dict = dict(counts)
    if since_commit_ms is not None:  # app DB commit → permissions applied everywhere
        lag_ms = clock() * 1000 - since_commit_ms
        PROPAGATION.observe(lag_ms / 1000)
        why += f", applied {lag_ms:.0f} ms after app commit"
        extra["propagation_ms"] = round(lag_ms)
    log.info("reconciled (%s): %s", why, counts, extra=extra)


def wait_for_topics(consumer: Consumer, stop: threading.Event) -> list[TopicPartition] | None:
    """Debezium creates the topics on its first snapshot; wait until the required ones exist.
    The partitions returned include those of OPTIONAL_TOPICS that exist already."""
    while not stop.is_set():
        topics = consumer.list_topics(timeout=10).topics
        missing = [t for t in TOPICS if t not in topics]
        if not missing:
            present = TOPICS + [t for t in OPTIONAL_TOPICS if t in topics]
            return [TopicPartition(t, p) for t in present for p in topics[t].partitions]
        log.info("waiting for CDC topics: %s (run `make cdc`)", missing)
        stop.wait(5)
    return None


if __name__ == "__main__":
    main()
