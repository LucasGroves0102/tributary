"""Poll loop: fetch feed → skip already-seen entries → publish new filings."""

import logging
import os
import signal
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import Protocol

import httpx
from prometheus_client import Counter, Gauge

from poller.edgar import Filing, fetch_feed, parse_feed
from poller.producer import FilingProducer
from tributary_sec import sec_client
from tributary_telemetry import configure_logging, serve_metrics

log = logging.getLogger("poller")

POLLS = Counter("tributary_poller_polls_total", "Feed polls by result (ok, http_error, failed)",
                ["result"])
LAST_SUCCESS = Gauge("tributary_poller_last_success_timestamp_seconds",
                     "Unix time of the last poll that fetched the feed and published it")


class Publisher(Protocol):
    def publish(self, filing: Filing) -> None: ...
    def flush(self, timeout: float = 10.0) -> None: ...


class SeenSet:
    """Bounded memory of recently published (accession_no, cik) keys. Lost on restart, so
    a restart can re-publish up to one feed's worth of entries; consumers must be
    idempotent on the key anyway (Kafka delivery is at-least-once)."""

    def __init__(self, maxlen: int = 10_000) -> None:
        self._order: deque[tuple[str, str]] = deque()
        self._keys: set[tuple[str, str]] = set()
        self._maxlen = maxlen

    def add_if_new(self, key: tuple[str, str]) -> bool:
        if key in self._keys:
            return False
        self._keys.add(key)
        self._order.append(key)
        if len(self._order) > self._maxlen:
            self._keys.discard(self._order.popleft())
        return True


def new_filings(filings: Iterable[Filing], seen: SeenSet) -> list[Filing]:
    return [f for f in filings if seen.add_if_new(f.key)]


def main() -> None:
    configure_logging()
    user_agent = os.environ["SEC_USER_AGENT"]
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    interval = float(os.environ.get("POLL_INTERVAL_SECONDS", "30"))

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    producer = FilingProducer(bootstrap)
    seen = SeenSet()

    serve_metrics()
    with sec_client(user_agent) as client:
        log.info("polling EDGAR every %.0fs → %s", interval, bootstrap)
        while not stop.is_set():
            poll_once(client, producer, seen)
            stop.wait(interval)

    producer.flush()
    log.info("stopped")


def poll_once(client: httpx.Client, producer: Publisher, seen: SeenSet,
              clock: Callable[[], float] = time.time) -> None:
    """Fetch the feed once and publish what's new. Errors are logged, not raised."""
    try:
        filings = new_filings(parse_feed(fetch_feed(client)), seen)
        for f in filings:
            producer.publish(f)
        producer.flush()
    except httpx.HTTPStatusError as e:
        # 403 usually means a missing/invalid User-Agent; 429 means slow down
        POLLS.labels("http_error").inc()
        log.error("EDGAR returned %s", e.response.status_code,
                  extra={"status": e.response.status_code})
        return
    except (httpx.TransportError, ValueError) as e:
        POLLS.labels("failed").inc()
        log.error("poll failed: %s", e)
        return
    POLLS.labels("ok").inc()
    LAST_SUCCESS.set(clock())
    log.info("published %d new filings", len(filings), extra={"filings": len(filings)})


if __name__ == "__main__":
    main()
