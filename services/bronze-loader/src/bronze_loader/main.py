"""Consume loop: filings.raw → bronze.filings, offsets committed after the DB commit."""

import logging
import os
import signal
import threading
from collections import Counter as Tally
from collections.abc import Sequence
from datetime import datetime

import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException, Message
from prometheus_client import Counter, Histogram

from bronze_loader.db import insert_filings
from bronze_loader.rows import FilingRow, to_row
from tributary_kafka import client_config
from tributary_telemetry import CONSUMED, SKIPPED, configure_logging, serve_metrics

TOPIC = "filings.raw"
GROUP_ID = "bronze-loader"

log = logging.getLogger("bronze_loader")

# The event's `source` (streaming/topics.md); an event from before the field is the feed's.
SOURCES = ("feed", "recheck", "daily-index", "seed")
EXACT = ("feed", "recheck")  # sources whose filed_at is EDGAR's acceptance time

FILINGS = Counter("tributary_bronze_filings_total",
                  "filings.raw events written to bronze.filings (inserted) or already there "
                  "(duplicate), by the event's source", ["result", "source"])
LATENCY = Histogram(
    "tributary_bronze_filing_latency_seconds",
    "EDGAR acceptance to bronze.filings, once per filing (accession) per batch, for new rows "
    "with an exact acceptance time: source feed (the live poller) or recheck (the hourly "
    "recheck)", ["source"],
    buckets=(15, 30, 45, 60, 90, 120, 180, 240, 300, 450, 600, 900, 1200, 1800, 2700, 3600,
             7200, 14400, 28800, 86400))
for _s in (*SOURCES, "other"):  # every series from the start, so increase() sees the first
    FILINGS.labels("inserted", _s)
    FILINGS.labels("duplicate", _s)
for _s in EXACT:
    LATENCY.labels(_s)


def source_of(row: FilingRow) -> str:
    source = row.payload.get("source") or "feed"
    return source if source in SOURCES else "other"


def count(rows: Sequence[FilingRow], inserted: Sequence[tuple[FilingRow, datetime]]) -> int:
    """Count a batch's rows by result and source and observe the new ones' latency. Returns
    the rows inserted."""
    new = Tally(source_of(r) for r, _ in inserted)
    for source, n in Tally(source_of(r) for r in rows).items():
        FILINGS.labels("inserted", source).inc(new[source])
        FILINGS.labels("duplicate", source).inc(n - new[source])
    observed: set[str] = set()
    for row, ingested_at in inserted:
        source = source_of(row)
        if source in EXACT and row.accession_no not in observed:
            observed.add(row.accession_no)
            LATENCY.labels(source).observe((ingested_at - row.filed_at).total_seconds())
    return len(inserted)


def readable(msgs: list[Message]) -> list[Message]:
    """Messages carrying data. A topic not created yet is skipped; other errors raise."""
    out = []
    for m in msgs:
        err = m.error()
        if err is None:
            out.append(m)
        elif err.code() != KafkaError.UNKNOWN_TOPIC_OR_PART:
            raise KafkaException(err)
    return out


def to_rows(msgs: list[Message]) -> list[FilingRow]:
    """Rows for the well-formed messages; malformed ones are logged, counted and dropped."""
    CONSUMED.labels(TOPIC).inc(len(msgs))
    rows = []
    for m in msgs:
        try:
            rows.append(to_row(m.value(), m.partition(), m.offset()))
        except (ValueError, KeyError) as e:
            # TODO: route to a dead-letter topic instead of dropping
            SKIPPED.labels(TOPIC, "malformed").inc()
            log.error("skipping malformed message at %d:%d: %s", m.partition(), m.offset(), e)
    return rows


def main() -> None:
    configure_logging()
    dsn = os.environ["DATABASE_URL"]
    batch_size = int(os.environ.get("BATCH_SIZE", "500"))

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    consumer = Consumer(
        client_config(
            {
                "group.id": GROUP_ID,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",  # a new group starts from the beginning
            }
        )
    )
    consumer.subscribe([TOPIC])
    serve_metrics()

    with psycopg.connect(dsn) as conn:
        log.info("consuming %s → bronze.filings", TOPIC)
        try:
            while not stop.is_set():
                msgs = readable(consumer.consume(num_messages=batch_size, timeout=1.0))
                if not msgs:
                    continue  # nothing consumed, so no offset to commit
                rows = to_rows(msgs)
                new = insert_filings(conn, rows)
                consumer.commit(asynchronous=False)
                inserted = count(rows, new)
                log.info("batch of %d: %d new, %d already present",
                         len(msgs), inserted, len(rows) - inserted,
                         extra={"messages": len(msgs), "inserted": inserted})
        finally:
            consumer.close()
    log.info("stopped")


if __name__ == "__main__":
    main()
