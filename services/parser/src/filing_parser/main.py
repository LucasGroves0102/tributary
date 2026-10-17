"""Consume documents.fetched → parse sections → silver.filing_sections."""

import argparse
import json
import logging
import os
import signal
import threading
import time

import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException

from filing_parser.db import all_accessions, load_document, write_sections
from filing_parser.sections import parse

TOPIC = "documents.fetched"
GROUP_ID = "parser-python"

log = logging.getLogger("parser")


def parse_one(conn: psycopg.Connection, accession_no: str) -> None:
    form, content = load_document(conn, accession_no)
    t0 = time.perf_counter()
    sections = parse(content, form)
    ms = (time.perf_counter() - t0) * 1000
    write_sections(conn, accession_no, sections)
    log.info("%s %s: %d sections in %.0f ms (%d KB)",
             accession_no, form, len(sections), ms, len(content) // 1024)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reparse-all", action="store_true",
                    help="re-parse every stored document without Kafka, then exit")
    args = ap.parse_args()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        if args.reparse_all:
            for accession_no in all_accessions(conn):
                parse_one(conn, accession_no)
            return
        consume(conn)


def consume(conn: psycopg.Connection) -> None:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    consumer = Consumer({
        "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        "group.id": GROUP_ID,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe([TOPIC])
    log.info("consuming %s", TOPIC)
    try:
        while not stop.is_set():
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
                    continue  # topic not created yet: nothing has been produced to it
                raise KafkaException(msg.error())
            try:
                parse_one(conn, json.loads(msg.value())["accession_no"])
            except (ValueError, KeyError) as e:
                # TODO: route to a dead-letter topic instead of dropping
                log.error("skipping %d:%d: %s", msg.partition(), msg.offset(), e)
            consumer.commit(msg, asynchronous=False)
    finally:
        consumer.close()
    log.info("stopped")


if __name__ == "__main__":
    main()
