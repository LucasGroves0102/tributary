"""Poll loop: walk the feed's pages back to what the last poll saw → publish new filings.

The feed lists filings newest first, 100 to a page. One page used to be all a poll read, so a
burst of more than 100 filer rows between two polls (13F and 10-Q deadlines, the 4 pm ET
close), a poll that failed, or an entry EDGAR listed late below entries already seen was
skipped until the nightly daily-index check (6.6% of filings from 2026-10-20 to 2026-11-27,
36% on the 13F and 10-Q deadline of 2026-11-16). A poll now reads pages until one is *settled*:

- it holds an entry an earlier poll saw (the overlap; not needed on the first poll),
- it holds no new entry below such an entry (a late listing: more may sit deeper), and
- its oldest entry was accepted at or before the horizon: LOOKBACK before the newest entry the
  last finished poll saw (on the first poll, STARTUP_LOOKBACK before the feed's own time),

or until the feed ends, or a page repeats the last (the feed stopped paging), or MAX_PAGES.
A quiet poll is still one request. A walk cut short at MAX_PAGES is counted
(`tributary_poller_unsettled_polls_total`): what it skipped is the hourly recheck's
(`poller-recheck`). A walk that fails part way publishes what it found and keeps the last
horizon, so the next poll walks back over the gap.
"""

import logging
import os
import signal
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

import httpx
from prometheus_client import Counter, Gauge

from poller.edgar import PAGE_SIZE, Filing, fetch_feed, parse_page
from poller.producer import FilingProducer
from tributary_kafka import bootstrap_servers
from tributary_sec import sec_client
from tributary_telemetry import configure_logging, serve_metrics

log = logging.getLogger("poller")

POLLS = Counter("tributary_poller_polls_total", "Feed polls by result (ok, http_error, failed)",
                ["result"])
LAST_SUCCESS = Gauge("tributary_poller_last_success_timestamp_seconds",
                     "Unix time of the last poll that fetched the feed and published it")
PAGES = Counter("tributary_poller_feed_pages_total", "Feed pages fetched")
UNSETTLED = Counter("tributary_poller_unsettled_polls_total",
                    "Polls that stopped at POLL_MAX_PAGES (or a feed that stopped paging) before "
                    "reaching what the last poll saw: filings in between wait for the recheck")

MAX_PAGES = 20
LOOKBACK = timedelta(seconds=120)
STARTUP_LOOKBACK = timedelta(minutes=10)


class Publisher(Protocol):
    def publish(self, filing: Filing) -> None: ...
    def flush(self, timeout: float = 10.0) -> None: ...


class SeenSet:
    """Bounded memory of recently published (accession_no, cik) keys. Lost on restart, so
    a restart can re-publish up to STARTUP_LOOKBACK of entries; consumers must be
    idempotent on the key anyway (Kafka delivery is at-least-once)."""

    def __init__(self, maxlen: int = 50_000) -> None:
        self._order: deque[tuple[str, str]] = deque()
        self._keys: set[tuple[str, str]] = set()
        self._maxlen = maxlen

    def __contains__(self, key: tuple[str, str]) -> bool:
        return key in self._keys

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


@dataclass(frozen=True)
class WalkConfig:
    max_pages: int = MAX_PAGES
    lookback: timedelta = LOOKBACK
    startup_lookback: timedelta = STARTUP_LOOKBACK
    page_size: int = PAGE_SIZE

    @classmethod
    def from_env(cls) -> "WalkConfig":
        return cls(
            max_pages=int(os.environ.get("POLL_MAX_PAGES", MAX_PAGES)),
            lookback=timedelta(seconds=float(os.environ.get(
                "POLL_LOOKBACK_SECONDS", LOOKBACK.total_seconds()))),
            startup_lookback=timedelta(seconds=float(os.environ.get(
                "POLL_STARTUP_LOOKBACK_SECONDS", STARTUP_LOOKBACK.total_seconds()))))


DEFAULT_WALK = WalkConfig()


@dataclass
class FeedState:
    """What the poller remembers between polls."""

    seen: SeenSet = field(default_factory=SeenSet)
    newest: datetime | None = None  # the newest acceptance time the last finished walk saw


class Walk:
    """One poll's pages, newest first. `found` keeps the new entries in feed order."""

    def __init__(self, state: FeedState, config: WalkConfig) -> None:
        self.state, self.config = state, config
        self.found: dict[tuple[str, str], Filing] = {}
        self.pages = 0
        self.top: datetime | None = None
        self.settled = False

    def run(self, client: httpx.Client) -> None:
        state, config = self.state, self.config
        horizon = state.newest - config.lookback if state.newest is not None else None
        overlapped = False
        last_keys: set[tuple[str, str]] = set()
        for n in range(config.max_pages):
            page = parse_page(fetch_feed(client, start=n * config.page_size,
                                         count=config.page_size))
            self.pages += 1
            newest = max((f.filed_at for f in page.filings), default=None)
            if newest is not None and (self.top is None or newest > self.top):
                self.top = newest
            if horizon is None:  # the first poll since start: nothing seen to overlap
                start = page.updated or self.top
                horizon = start - config.startup_lookback if start else None
            keys = {f.key for f in page.filings}
            if n and keys and keys <= last_keys:
                log.warning("feed page %d repeats the last: not paging", n + 1)
                return  # EDGAR ignored `start`: unsettled
            last_keys = keys
            seen_above = late = False
            for f in page.filings:
                if f.key in self.found:
                    continue  # the feed moved down between two pages of this walk
                if f.key in state.seen:
                    seen_above = overlapped = True
                    continue
                late = late or seen_above
                self.found[f.key] = f
            if page.entries < config.page_size:
                self.settled = True  # the feed's end: nothing deeper
                return
            oldest = min((f.filed_at for f in page.filings), default=None)
            if ((overlapped or state.newest is None) and not late and oldest is not None
                    and horizon is not None and oldest <= horizon):
                self.settled = True
                return

    def new(self) -> list[Filing]:
        """The new entries, oldest first (publish order)."""
        return list(reversed(self.found.values()))


def main() -> None:
    configure_logging()
    user_agent = os.environ["SEC_USER_AGENT"]
    bootstrap = bootstrap_servers()
    interval = float(os.environ.get("POLL_INTERVAL_SECONDS", "30"))
    config = WalkConfig.from_env()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    producer = FilingProducer()
    state = FeedState()

    serve_metrics()
    with sec_client(user_agent) as client:
        log.info("polling EDGAR every %.0fs (up to %d pages) → %s", interval, config.max_pages,
                 bootstrap)
        while not stop.is_set():
            poll_once(client, producer, state, config=config)
            stop.wait(interval)

    producer.flush()
    log.info("stopped")


def poll_once(client: httpx.Client, producer: Publisher, state: FeedState,
              clock: Callable[[], float] = time.time,
              config: WalkConfig = DEFAULT_WALK) -> list[Filing]:
    """Walk the feed once and publish what's new (returned, oldest first). Errors are logged,
    not raised; what the walk found before one is still published."""
    walk = Walk(state, config)
    result = "ok"
    try:
        walk.run(client)
    except httpx.HTTPStatusError as e:
        # 403 usually means a missing/invalid User-Agent; 429 means slow down
        result = "http_error"
        log.error("EDGAR returned %s", e.response.status_code,
                  extra={"status": e.response.status_code})
    except (httpx.TransportError, ValueError, ET.ParseError) as e:  # ParseError: not XML
        result = "failed"
        log.error("poll failed: %s", e)
    filings = walk.new()
    PAGES.inc(walk.pages)
    for f in filings:
        producer.publish(f)
        state.seen.add_if_new(f.key)
    producer.flush()
    POLLS.labels(result).inc()
    if result != "ok":
        return filings  # keep the last horizon: the next poll walks back over the gap
    if not walk.settled:
        UNSETTLED.inc()
        log.warning("stopped after %d feed pages without reaching the last poll's entries",
                    walk.pages, extra={"pages": walk.pages})
    if walk.top is not None and (state.newest is None or walk.top > state.newest):
        state.newest = walk.top
    LAST_SUCCESS.set(clock())
    log.info("published %d new filings from %d feed pages", len(filings), walk.pages,
             extra={"filings": len(filings), "pages": walk.pages})
    return filings


if __name__ == "__main__":
    main()
