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
from prometheus_client import Counter, Histogram

from filing_parser.db import all_accessions, load_document, load_exhibits, write_sections
from filing_parser.sections import exhibit_section, parse
from tributary_telemetry import CONSUMED, SKIPPED, configure_logging, serve_metrics

TOPIC = "documents.fetched"
GROUP_ID = "parser-python"

log = logging.getLogger("parser")

PARSED = Counter("tributary_documents_parsed_total", "Documents parsed into sections", ["form"])
PARSE_FAILURES = Counter("tributary_parse_failures_total", "Documents the parser raised on",
                         ["form"])
PARSE_SECONDS = Histogram("tributary_parse_duration_seconds",
                          "Time to split one document (and its exhibits) into sections",
                          ["form"], buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))


def parse_one(conn: psycopg.Connection, accession_no: str) -> None:
    form, content = load_document(conn, accession_no)
    t0 = time.perf_counter()
    try:
        sections = parse(content, form)
        sections += [exhibit_section(t, d, raw)
                     for t, d, raw in load_exhibits(conn, accession_no)]
    except Exception:
        PARSE_FAILURES.labels(form).inc()
        raise
    seconds = time.perf_counter() - t0
    write_sections(conn, accession_no, sections)
    PARSED.labels(form).inc()
    PARSE_SECONDS.labels(form).observe(seconds)
    log.info("%s %s: %d sections in %.0f ms (%d KB)",
             accession_no, form, len(sections), seconds * 1000, len(content) // 1024,
             extra={"accession_no": accession_no, "form": form, "sections": len(sections),
                    "parse_ms": round(seconds * 1000)})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reparse-all", action="store_true",
                    help="re-parse every stored document without Kafka, then exit")
    args = ap.parse_args()
    configure_logging()
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
    serve_metrics()
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
            CONSUMED.labels(TOPIC).inc()
            try:
                parse_one(conn, json.loads(msg.value())["accession_no"])
            except (ValueError, KeyError) as e:
                # TODO: route to a dead-letter topic instead of dropping
                SKIPPED.labels(TOPIC, "invalid").inc()
                log.error("skipping %d:%d: %s", msg.partition(), msg.offset(), e)
            consumer.commit(msg, asynchronous=False)
    finally:
        consumer.close()
    log.info("stopped")


if __name__ == "__main__":
    main()
