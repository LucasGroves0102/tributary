"""Consume filings.raw → fetch 10-K/10-Q/8-K documents → bronze → documents.fetched.

Which filings (`Coverage.scope`):
- tracked companies: every 10-K, 10-Q and 8-K event, whatever its source (feed, daily index, seed);
- every other operating company (gold financials, the document crawl's set): new filings from the
  live feed, so they're stored within minutes instead of at the crawl's next plan (up to
  CRAWL_PLAN_HOURS later, H10). 8-Ks only when one of their items (read from the filing index,
  the first request anyway) is in `FETCH_8K_ITEMS`; an index that lists no items is fetched.

Everything else (amendments, the daily index's late filings, the 8-Ks left out here, anything a
fetch error dropped) is the document crawl's, which skips whatever this stored first.

Insider filings (Forms 3/4/5, #117; `ownership.py`, FETCH_INSIDERS): each one in the live feed
under an operating or tracked company's CIK as issuer is fetched (one request), stored in bronze
and parsed into silver; `doc-fetcher-insiders` backfills a bounded window.
"""

import json
import logging
import os
import signal
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass

import httpx
import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException, Producer
from prometheus_client import Counter

from doc_fetcher import ownership
from doc_fetcher.db import (
    already_fetched,
    insert_document,
    insert_exhibit,
    operating_ciks,
    tracked_ciks,
)
from doc_fetcher.index import parse_exhibits, parse_index, parse_items
from tributary_kafka import client_config
from tributary_sec import sec_client
from tributary_telemetry import CONSUMED, SKIPPED, configure_logging, count_delivery, serve_metrics

IN_TOPIC = "filings.raw"
OUT_TOPIC = "documents.fetched"
GROUP_ID = "doc-fetcher"
FORMS = {"10-K", "10-Q", "8-K"}
TRACKED_REFRESH_SECONDS = 300
# Results, Reg FD and other events, and the material events (deals, debt, leadership,
# impairments, auditor, non-reliance). Keep in step with the crawl's CRAWL_8K_ITEMS in
# deploy/helm/tributary/values.yaml, so the live path and the crawl store the same 8-Ks.
MATERIAL_8K_ITEMS = ("1.01", "1.02", "1.03", "1.05", "2.01", "2.02", "2.03", "2.04", "2.05",
                     "2.06", "3.01", "3.02", "3.03", "4.01", "4.02", "5.01", "5.02", "5.03",
                     "7.01", "8.01")

log = logging.getLogger("doc_fetcher")

DOCUMENTS = Counter("tributary_documents_fetched_total",
                    "Primary documents fetched and stored in bronze", ["form"])
PASSED = Counter("tributary_documents_passed_total",
                 "Filings of operating companies read but not stored: 8-Ks with none of the"
                 " fetched items", ["form"])
INSIDERS = Counter("tributary_insider_filings_fetched_total",
                   "Insider filings (Forms 3/4/5) fetched from the live feed and stored", ["form"])


def _on(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "true").strip().lower() not in ("false", "0", "no")


@dataclass(frozen=True)
class Config:
    operating: bool = True                            # FETCH_OPERATING
    eight_k_items: frozenset[str] | None = frozenset(MATERIAL_8K_ITEMS)  # None: every 8-K
    insiders: bool = True                             # FETCH_INSIDERS

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Config":
        items = env.get("FETCH_8K_ITEMS", ",".join(MATERIAL_8K_ITEMS))
        return cls(
            operating=_on(env, "FETCH_OPERATING"),
            eight_k_items=(None if items.strip().lower() == "all"
                           else frozenset(i.strip() for i in items.split(",") if i.strip())),
            insiders=_on(env, "FETCH_INSIDERS"),
        )


@dataclass(frozen=True)
class Coverage:
    """Which companies' filings this fetches; re-read every TRACKED_REFRESH_SECONDS."""
    tracked: frozenset[str]
    operating: frozenset[str]

    @classmethod
    def load(cls, conn: psycopg.Connection, config: Config) -> "Coverage":
        return cls(frozenset(tracked_ciks(conn)),
                   frozenset(operating_ciks(conn)) if config.operating else frozenset())

    @property
    def issuers(self) -> frozenset[str]:
        """Whose insider filings are stored: every tracked and operating company's."""
        return self.tracked | self.operating

    def scope(self, event: dict) -> str | None:
        """'all': a tracked company's filing; 'live': another operating company's filing from
        the live feed (8-Ks filtered by item); None: not ours (the crawl's, or nobody's)."""
        if event["form_type"] not in FORMS:
            return None
        if event["cik"] in self.tracked:
            return "all"
        if event["cik"] in self.operating and event.get("source", "feed") == "feed":
            return "live"
        return None


def main() -> None:
    configure_logging()
    dsn = os.environ["DATABASE_URL"]
    config = Config.from_env()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    consumer = Consumer(client_config({
        "group.id": GROUP_ID,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    }))
    consumer.subscribe([IN_TOPIC])
    producer = Producer(client_config({"enable.idempotence": True}))
    serve_metrics()

    with psycopg.connect(dsn, autocommit=True) as conn, \
            sec_client(os.environ["SEC_USER_AGENT"]) as client:
        coverage, refreshed = Coverage.load(conn, config), time.monotonic()
        issuers = coverage.issuers if config.insiders else frozenset()
        log.info("consuming %s; %d tracked CIKs, %d operating CIKs", IN_TOPIC,
                 len(coverage.tracked), len(coverage.operating))
        try:
            while not stop.is_set():
                if time.monotonic() - refreshed > TRACKED_REFRESH_SECONDS:
                    coverage, refreshed = Coverage.load(conn, config), time.monotonic()
                    issuers = coverage.issuers if config.insiders else frozenset()
                msg = consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
                        continue  # topic not created yet: nothing has been produced to it
                    raise KafkaException(msg.error())
                CONSUMED.labels(IN_TOPIC).inc()
                try:
                    event = json.loads(msg.value())
                    if ownership.wanted(event, issuers):
                        handle_insider(conn, client, event)
                    scope = coverage.scope(event)
                    if scope is not None:
                        handle(conn, client, producer, event,
                               eight_k_items=config.eight_k_items if scope == "live" else None)
                except (ValueError, KeyError, httpx.HTTPError) as e:
                    # TODO: route to a dead-letter topic instead of dropping
                    reason = "sec" if isinstance(e, httpx.HTTPError) else "invalid"
                    SKIPPED.labels(IN_TOPIC, reason).inc()
                    log.error("skipping %d:%d: %s", msg.partition(), msg.offset(), e)
                consumer.commit(msg, asynchronous=False)
        finally:
            producer.flush(10)
            consumer.close()
    log.info("stopped")


def handle(conn: psycopg.Connection, client: httpx.Client, producer: Producer, event: dict,
           eight_k_items: frozenset[str] | None = None) -> bool:
    """Fetch, store and announce one filing; False when it was stored already or is an 8-K
    with none of `eight_k_items` (None: every 8-K)."""
    accession, cik, form = event["accession_no"], event["cik"], event["form_type"]
    if already_fetched(conn, accession):
        return False
    index_resp = client.get(event["url"])
    index_resp.raise_for_status()
    if form == "8-K" and eight_k_items is not None:
        items = parse_items(index_resp.content)
        if items and not items & eight_k_items:
            PASSED.labels(form).inc()
            log.info("%s %s %s: items %s not fetched", cik, form, accession,
                     ",".join(sorted(items)), extra={"cik": cik, "form": form,
                                                     "accession_no": accession})
            return False
    index = parse_index(index_resp.content, form)
    doc_resp = client.get(index.url)
    doc_resp.raise_for_status()
    digest = insert_document(conn, accession, cik, form, index, doc_resp.content)
    if form == "8-K":
        fetch_exhibits(conn, client, accession, index_resp.content)
    producer.produce(
        OUT_TOPIC,
        key=cik.encode(),
        value=json.dumps({
            "accession_no": accession, "cik": cik, "form": form,
            "document_name": index.document_name, "url": index.url,
            "content_sha256": digest, "size_bytes": len(doc_resp.content),
        }).encode(),
        on_delivery=count_delivery,
    )
    producer.flush(10)
    DOCUMENTS.labels(form).inc()
    log.info("%s %s %s: %d KB", cik, form, accession, len(doc_resp.content) // 1024,
             extra={"cik": cik, "form": form, "accession_no": accession,
                    "size_bytes": len(doc_resp.content)})
    return True


def handle_insider(conn: psycopg.Connection, client: httpx.Client, event: dict) -> bool:
    """One insider filing from the feed: stored once (idempotent on the accession)."""
    accession, form = event["accession_no"], event["form_type"]
    if ownership.stored(conn, accession):
        return False
    done = ownership.fetch(conn, client, accession, event["cik"], form,
                           ownership.filed_on(event["filed_at"]))
    if done:
        INSIDERS.labels(form).inc()
    return done


def fetch_exhibits(conn: psycopg.Connection, client: httpx.Client, accession: str,
                   index_html: bytes) -> int:
    exhibits = parse_exhibits(index_html)
    for exhibit in exhibits:
        resp = client.get(exhibit.url)
        resp.raise_for_status()
        insert_exhibit(conn, accession, exhibit, resp.content)
    return len(exhibits)


if __name__ == "__main__":
    main()
