"""doc-crawler: fetch 10-K/10-Q/8-K documents (+ 8-K EX-99.x) of every operating company.

A long-running worker (Helm `services.doc-crawler`, shipped at replicas 0). Each round:

1. pause if the operator set ops.doc_crawl_control.paused, or free disk is under the guard;
2. every `plan_hours`, queue new targets (ops.doc_crawl_plan: companies with gold financials,
   last `years` years) — so after the backfill it keeps up with new filings of every company;
3. publish documents.backfill for stored documents whose event isn't confirmed (outbox);
4. claim a batch (tracked companies first, then most recent first), fetch it with `workers`
   threads through sec_client (caller class from SEC_CALLER_CLASS: bulk), store each filing and
   its done mark in one transaction, then publish.

Everything is keyed by accession: re-running, crashing or redeploying at any point loses at most
the in-flight batch's work (its claims expire after `lease`) and never duplicates rows.
"""

import json
import logging
import os
import shutil
import signal
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

import httpx
import psycopg
from confluent_kafka import Producer
from prometheus_client import Counter, Gauge

from doc_fetcher import crawl_db
from doc_fetcher.crawl_fetch import Fetched, Target, Unusable, fetch_target
from tributary_sec import sec_client
from tributary_telemetry import configure_logging, count_delivery, serve_metrics

log = logging.getLogger("doc_fetcher.crawl")

STATES = ("running", "idle", "paused", "disk_low", "erroring", "stopped")

FILINGS = Counter("tributary_doc_crawl_filings_total",
                  "Crawl targets handled, by form and outcome", ["form", "outcome"])
FILES = Counter("tributary_doc_crawl_files_total", "Files stored (documents and exhibits)")
BYTES = Counter("tributary_doc_crawl_bytes_total", "Bytes stored, uncompressed (raw) and gzip",
                ["kind"])
PUBLISHED = Counter("tributary_doc_crawl_events_published_total",
                    "documents.backfill events confirmed by Kafka")
REMAINING = Gauge("tributary_doc_crawl_remaining_filings", "Targets pending or claimed")
DISK_FREE = Gauge("tributary_doc_crawl_disk_free_bytes", "Free bytes on the node's disk")
MIN_FREE = Gauge("tributary_doc_crawl_min_free_bytes", "The disk guard's threshold")
STATE = Gauge("tributary_doc_crawl_state", "1 for the crawler's current state", ["state"])
LAST_PROGRESS = Gauge("tributary_doc_crawl_last_progress_timestamp_seconds",
                      "Unix time a target was last finished")


@dataclass(frozen=True)
class Config:
    topic: str = "documents.backfill"
    batch: int = 32
    workers: int = 4
    years: int = 5
    eight_k_items: tuple[str, ...] | None = ("2.02", "7.01", "8.01")
    min_free_bytes: int = 50 * 10**9
    disk_path: str = "/tmp"
    max_document_bytes: int = 100 * 2**20
    plan_hours: float = 6
    idle_seconds: float = 900
    pause_seconds: float = 300
    max_attempts: int = 4
    retry_base: timedelta = timedelta(minutes=10)
    lease: timedelta = timedelta(minutes=30)

    @classmethod
    def from_env(cls, env: dict[str, str] | os._Environ = os.environ) -> "Config":
        d = cls()
        items = env.get("CRAWL_8K_ITEMS", ",".join(d.eight_k_items or ()))
        return cls(
            topic=env.get("CRAWL_TOPIC", d.topic),
            batch=int(env.get("CRAWL_BATCH", d.batch)),
            workers=int(env.get("CRAWL_WORKERS", d.workers)),
            years=int(env.get("CRAWL_YEARS", d.years)),
            eight_k_items=(None if items.strip().lower() == "all"
                           else tuple(i.strip() for i in items.split(",") if i.strip())),
            min_free_bytes=int(float(env.get("CRAWL_MIN_FREE_GB", 50)) * 10**9),
            disk_path=env.get("CRAWL_DISK_PATH", d.disk_path),
            max_document_bytes=int(float(env.get("CRAWL_MAX_DOCUMENT_MB", 100)) * 2**20),
            plan_hours=float(env.get("CRAWL_PLAN_HOURS", d.plan_hours)),
            idle_seconds=float(env.get("CRAWL_IDLE_SECONDS", d.idle_seconds)),
            max_attempts=int(env.get("CRAWL_MAX_ATTEMPTS", d.max_attempts)),
        )


class Queue(Protocol):
    """crawl_db over one connection (PgQueue); a fake in tests."""
    def plan(self, since: date, eight_k_items: list[str] | None) -> int: ...
    def claim(self, limit: int, lease: timedelta) -> list[Target]: ...
    def already_stored(self, accessions: list[str]) -> set[str]: ...
    def mark_already_stored(self, accessions: list[str]) -> None: ...
    def store(self, target: Target, fetched: Fetched) -> None: ...
    def finish(self, accession_no: str, status: str, error: str) -> None: ...
    def retry_later(self, accession_no: str, error: str, delay: timedelta) -> None: ...
    def release(self, accessions: list[str]) -> None: ...
    def unpublished(self, limit: int) -> list[dict]: ...
    def mark_published(self, accessions: list[str]) -> None: ...
    def remaining(self) -> int: ...
    def control(self) -> crawl_db.Control: ...
    def report(self, state: str, disk_free: int | None, min_free: int) -> None: ...


class PgQueue:
    """Queue on one autocommit connection (crawl_db)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def plan(self, since: date, eight_k_items: list[str] | None) -> int:
        return crawl_db.plan(self.conn, since, eight_k_items)

    def claim(self, limit: int, lease: timedelta) -> list[Target]:
        return crawl_db.claim(self.conn, limit, lease)

    def already_stored(self, accessions: list[str]) -> set[str]:
        return crawl_db.already_stored(self.conn, accessions)

    def mark_already_stored(self, accessions: list[str]) -> None:
        crawl_db.mark_already_stored(self.conn, accessions)

    def store(self, target: Target, fetched: Fetched) -> None:
        crawl_db.store(self.conn, target, fetched)

    def finish(self, accession_no: str, status: str, error: str) -> None:
        crawl_db.finish(self.conn, accession_no, status, error)

    def retry_later(self, accession_no: str, error: str, delay: timedelta) -> None:
        crawl_db.retry_later(self.conn, accession_no, error, delay)

    def release(self, accessions: list[str]) -> None:
        crawl_db.release(self.conn, accessions)

    def unpublished(self, limit: int) -> list[dict]:
        return crawl_db.unpublished(self.conn, limit)

    def mark_published(self, accessions: list[str]) -> None:
        crawl_db.mark_published(self.conn, accessions)

    def remaining(self) -> int:
        return crawl_db.remaining(self.conn)

    def control(self) -> crawl_db.Control:
        return crawl_db.control(self.conn)

    def report(self, state: str, disk_free: int | None, min_free: int) -> None:
        crawl_db.report(self.conn, state, disk_free, min_free)


def classify(error: Exception) -> str:
    """skipped: the filing can't be used; failed: the SEC says it isn't there; retry: anything
    else (5xx, network, a throttle the shared limiter is already cooling down for, other 403s)."""
    if isinstance(error, Unusable):
        return "skipped"
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (404, 410):
        return "failed"
    return "retry"


def years_before(day: date, years: int) -> date:
    return date(day.year - years, day.month, min(day.day, 28))


def disk_free(path: str) -> int:
    """Free bytes on the filesystem holding `path`. In the pod, /tmp is an emptyDir on the node's
    disk, which on hermes is the same filesystem as host Postgres (/var/lib/postgresql)."""
    return shutil.disk_usage(path).free


class Crawler:
    def __init__(self, queue: Queue, fetch: Callable[[Target], Fetched], producer: Producer,
                 config: Config, *, free_bytes: Callable[[], int],
                 today: Callable[[], date] = date.today,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 stop: threading.Event | None = None) -> None:
        self.queue, self.fetch, self.producer, self.config = queue, fetch, producer, config
        self.free_bytes, self.today, self.clock, self.wall = free_bytes, today, clock, wall
        self.stop = stop or threading.Event()
        self.planned_at: float | None = None
        self.pool = ThreadPoolExecutor(max_workers=config.workers,
                                       thread_name_prefix="crawl-fetch")

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                self.stop.wait(self.round())
        finally:
            self.pool.shutdown(cancel_futures=True)
            self._state("stopped", None)

    def round(self) -> float:
        """One round; returns the seconds to wait before the next."""
        c = self.config
        if self.queue.control().paused:
            self._state("paused", None)
            return c.pause_seconds
        free = self.free_bytes()
        DISK_FREE.set(free)
        if free < c.min_free_bytes:
            log.warning("disk guard: %.1f GB free, under %.1f GB; crawl paused",
                        free / 1e9, c.min_free_bytes / 1e9)
            self._state("disk_low", free)
            return c.pause_seconds
        if self.planned_at is None or self.clock() - self.planned_at >= c.plan_hours * 3600:
            since = years_before(self.today(), c.years)
            items = list(c.eight_k_items) if c.eight_k_items is not None else None
            added = self.queue.plan(since, items)
            self.planned_at = self.clock()
            log.info("planned %d new targets since %s", added, since)
        self.publish()
        batch = self.queue.claim(c.batch, c.lease)
        REMAINING.set(self.queue.remaining())
        if not batch:
            self._state("idle", free)
            return c.idle_seconds
        self._state("running", free)
        ok, errors = self.crawl(batch)
        self.publish()
        REMAINING.set(self.queue.remaining())
        if ok == 0 and errors >= min(5, len(batch)):
            log.error("every fetch in the batch failed (%d); backing off", errors)
            self._state("erroring", free)
            return c.pause_seconds
        return 0

    def crawl(self, batch: list[Target]) -> tuple[int, int]:
        """Fetch and store a claimed batch; returns (stored, errors)."""
        stored = self.queue.already_stored([t.accession_no for t in batch])
        if stored:
            self.queue.mark_already_stored(sorted(stored))
            for t in batch:
                if t.accession_no in stored:
                    FILINGS.labels(t.form, "already_stored").inc()
        todo = [t for t in batch if t.accession_no not in stored]
        futures = [(t, self.pool.submit(self._fetch, t)) for t in todo]
        ok = errors = 0
        unattempted = []
        for target, future in futures:
            if self.stop.is_set() and future.cancel():
                unattempted.append(target.accession_no)
                continue
            result = future.result()
            if isinstance(result, Fetched):
                self.queue.store(target, result)
                ok += 1
                FILINGS.labels(target.form, "done").inc()
                FILES.inc(result.files)
                BYTES.labels("raw").inc(result.raw_bytes)
                BYTES.labels("stored").inc(result.stored_bytes)
            else:
                errors += not isinstance(result, Unusable)
                self._failed(target, result)
            LAST_PROGRESS.set(self.wall())
        if unattempted:
            self.queue.release(unattempted)
        return ok, errors

    def _fetch(self, target: Target) -> Fetched | Exception:
        try:
            return self.fetch(target)
        except Exception as e:  # noqa: BLE001 - one odd filing must not crash-loop the crawl
            return e

    def _failed(self, target: Target, error: Exception) -> None:
        outcome = classify(error)
        message = f"{type(error).__name__}: {error}"
        if outcome == "retry" and target.attempts + 1 >= self.config.max_attempts:
            outcome = "failed"
        if outcome == "retry":
            delay = self.config.retry_base * 2 ** target.attempts
            self.queue.retry_later(target.accession_no, message, delay)
        else:
            self.queue.finish(target.accession_no, outcome, message)
        FILINGS.labels(target.form, outcome).inc()
        log.warning("%s %s %s: %s", outcome, target.form, target.accession_no, message,
                    extra={"accession_no": target.accession_no, "form": target.form})

    def publish(self) -> int:
        """Publish documents.backfill for stored documents without a confirmed event."""
        events = self.queue.unpublished(500)
        if not events:
            return 0
        delivered: list[str] = []

        def on_delivery(err: Any, msg: Any, accession_no: str) -> None:
            count_delivery(err, msg)
            if err is None:
                delivered.append(accession_no)

        for e in events:
            value = json.dumps({**e, "source": "crawl"}).encode()
            self.producer.produce(
                self.config.topic, value=value, key=e["cik"].encode(),
                on_delivery=lambda err, msg, a=e["accession_no"]: on_delivery(err, msg, a))
        self.producer.flush(30)
        if delivered:
            self.queue.mark_published(delivered)
            PUBLISHED.inc(len(delivered))
        return len(delivered)

    def _state(self, state: str, free: int | None) -> None:
        for s in STATES:
            STATE.labels(s).set(1 if s == state else 0)
        MIN_FREE.set(self.config.min_free_bytes)
        try:
            self.queue.report(state, free, self.config.min_free_bytes)
        except psycopg.Error as e:  # reporting must never stop the crawl or its shutdown
            log.warning("could not report state %s: %s", state, e)


def main() -> None:
    configure_logging()
    config = Config.from_env()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    producer = Producer({
        "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        "enable.idempotence": True,
    })
    user_agent = os.environ["SEC_USER_AGENT"]
    local = threading.local()

    def fetch(target: Target) -> Fetched:
        # one client (and shared-limiter connection) per worker thread
        if not hasattr(local, "client"):
            local.client = sec_client(user_agent)
        return fetch_target(local.client, target, config.max_document_bytes)

    serve_metrics()
    log.info("crawling to %s with %d workers, %d per batch, guard %.0f GB free on %s",
             config.topic, config.workers, config.batch, config.min_free_bytes / 1e9,
             config.disk_path)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        crawler = Crawler(PgQueue(conn), fetch, producer, config,
                          free_bytes=lambda: disk_free(config.disk_path), stop=stop)
        crawler.run()
    producer.flush(10)
    log.info("stopped")


if __name__ == "__main__":
    main()
