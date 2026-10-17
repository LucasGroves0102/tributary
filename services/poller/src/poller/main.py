"""Poll loop: fetch feed → skip already-seen entries → publish new filings."""

import logging
import os
import signal
import threading
from collections import deque
from collections.abc import Iterable

import httpx

from poller.edgar import Filing, fetch_feed, parse_feed
from poller.producer import FilingProducer
from poller.ratelimit import RateLimiter

log = logging.getLogger("poller")


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
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    user_agent = os.environ["SEC_USER_AGENT"]
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    interval = float(os.environ.get("POLL_INTERVAL_SECONDS", "30"))

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    limiter = RateLimiter(max_per_second=5)
    producer = FilingProducer(bootstrap)
    seen = SeenSet()
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}

    with httpx.Client(headers=headers, timeout=20.0) as client:
        log.info("polling EDGAR every %.0fs → %s", interval, bootstrap)
        while not stop.is_set():
            try:
                limiter.wait()
                filings = new_filings(parse_feed(fetch_feed(client)), seen)
                for f in filings:
                    producer.publish(f)
                producer.flush()
                log.info("published %d new filings", len(filings))
            except httpx.HTTPStatusError as e:
                # 403 usually means a missing/invalid User-Agent; 429 means slow down
                log.error("EDGAR returned %s", e.response.status_code)
            except (httpx.TransportError, ValueError) as e:
                log.error("poll failed: %s", e)
            stop.wait(interval)

    producer.flush()
    log.info("stopped")


if __name__ == "__main__":
    main()
