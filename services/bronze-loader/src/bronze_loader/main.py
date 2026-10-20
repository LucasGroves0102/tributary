"""Consume loop: filings.raw → bronze.filings, offsets committed after the DB commit."""

import logging
import os
import signal
import threading

import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException, Message
from prometheus_client import Counter

from bronze_loader.db import insert_filings
from bronze_loader.rows import FilingRow, to_row
from tributary_telemetry import CONSUMED, SKIPPED, configure_logging, serve_metrics

TOPIC = "filings.raw"
GROUP_ID = "bronze-loader"

log = logging.getLogger("bronze_loader")

FILINGS = Counter("tributary_bronze_filings_total",
                  "filings.raw events written to bronze.filings (inserted) or already there "
                  "(duplicate)", ["result"])


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
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    batch_size = int(os.environ.get("BATCH_SIZE", "500"))

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": GROUP_ID,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",  # a new group starts from the beginning
        }
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
                inserted = insert_filings(conn, rows)
                consumer.commit(asynchronous=False)
                FILINGS.labels("inserted").inc(inserted)
                FILINGS.labels("duplicate").inc(len(rows) - inserted)
                log.info("batch of %d: %d new, %d already present",
                         len(msgs), inserted, len(rows) - inserted,
                         extra={"messages": len(msgs), "inserted": inserted})
        finally:
            consumer.close()
    log.info("stopped")


if __name__ == "__main__":
    main()
