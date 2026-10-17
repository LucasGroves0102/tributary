"""Consume loop: filings.raw → bronze.filings, offsets committed after the DB commit."""

import logging
import os
import signal
import threading

import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException

from bronze_loader.db import insert_filings
from bronze_loader.rows import to_row

TOPIC = "filings.raw"
GROUP_ID = "bronze-loader"

log = logging.getLogger("bronze_loader")


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
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

    with psycopg.connect(dsn) as conn:
        log.info("consuming %s → bronze.filings", TOPIC)
        try:
            while not stop.is_set():
                msgs = consumer.consume(num_messages=batch_size, timeout=1.0)
                if not msgs:
                    continue
                rows = []
                for m in msgs:
                    if m.error():
                        if m.error().code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
                            continue  # topic not created yet
                        raise KafkaException(m.error())
                    try:
                        rows.append(to_row(m.value(), m.partition(), m.offset()))
                    except (ValueError, KeyError) as e:
                        # TODO: route to a dead-letter topic instead of dropping
                        log.error("skipping malformed message at %d:%d: %s",
                                  m.partition(), m.offset(), e)
                inserted = insert_filings(conn, rows)
                consumer.commit(asynchronous=False)
                log.info("batch of %d: %d new, %d already present",
                         len(msgs), inserted, len(rows) - inserted)
        finally:
            consumer.close()
    log.info("stopped")


if __name__ == "__main__":
    main()
