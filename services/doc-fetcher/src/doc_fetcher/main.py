"""Consume filings.raw → fetch tracked 10-K/10-Q/8-K documents → bronze → documents.fetched."""

import json
import logging
import os
import signal
import threading
import time

import httpx
import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException, Producer

from doc_fetcher.db import already_fetched, insert_document, tracked_ciks
from doc_fetcher.index import parse_index
from tributary_sec import sec_client

IN_TOPIC = "filings.raw"
OUT_TOPIC = "documents.fetched"
GROUP_ID = "doc-fetcher"
FORMS = {"10-K", "10-Q", "8-K"}
TRACKED_REFRESH_SECONDS = 300

log = logging.getLogger("doc_fetcher")


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    dsn = os.environ["DATABASE_URL"]
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    consumer = Consumer({
        "bootstrap.servers": bootstrap,
        "group.id": GROUP_ID,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe([IN_TOPIC])
    producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True})

    with psycopg.connect(dsn, autocommit=True) as conn, \
            sec_client(os.environ["SEC_USER_AGENT"]) as client:
        tracked, refreshed = tracked_ciks(conn), time.monotonic()
        log.info("consuming %s; %d tracked CIKs", IN_TOPIC, len(tracked))
        try:
            while not stop.is_set():
                if time.monotonic() - refreshed > TRACKED_REFRESH_SECONDS:
                    tracked, refreshed = tracked_ciks(conn), time.monotonic()
                msg = consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
                        continue  # topic not created yet: nothing has been produced to it
                    raise KafkaException(msg.error())
                try:
                    event = json.loads(msg.value())
                    if event["form_type"] in FORMS and event["cik"] in tracked:
                        handle(conn, client, producer, event)
                except (ValueError, KeyError, httpx.HTTPError) as e:
                    # TODO: route to a dead-letter topic instead of dropping
                    log.error("skipping %d:%d: %s", msg.partition(), msg.offset(), e)
                consumer.commit(msg, asynchronous=False)
        finally:
            producer.flush(10)
            consumer.close()
    log.info("stopped")


def handle(conn: psycopg.Connection, client: httpx.Client, producer: Producer, event: dict) -> None:
    accession, cik, form = event["accession_no"], event["cik"], event["form_type"]
    if already_fetched(conn, accession):
        return
    index_resp = client.get(event["url"])
    index_resp.raise_for_status()
    index = parse_index(index_resp.content, form)
    doc_resp = client.get(index.url)
    doc_resp.raise_for_status()
    digest = insert_document(conn, accession, cik, form, index, doc_resp.content)
    producer.produce(
        OUT_TOPIC,
        key=cik.encode(),
        value=json.dumps({
            "accession_no": accession, "cik": cik, "form": form,
            "document_name": index.document_name, "url": index.url,
            "content_sha256": digest, "size_bytes": len(doc_resp.content),
        }).encode(),
    )
    producer.flush(10)
    log.info("%s %s %s: %d KB", cik, form, accession, len(doc_resp.content) // 1024)


if __name__ == "__main__":
    main()
