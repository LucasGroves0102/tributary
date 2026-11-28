"""Hourly recheck (`poller-recheck`): the filings EDGAR's latest-filings feed lists for today
and the previous filing day, against bronze; republishes only the missing ones.

Why the feed: it is the one EDGAR listing of today's filings with exact acceptance times and
the filer roles (the daily index only appears after the day; the full index is quarterly and
nightly). What the live poller misses is timing, not form types: bursts deeper than its walk,
its outages, and filings EDGAR lists late below entries already seen (accepted after 5:30 pm
ET, many are listed only the next morning, deep in the feed). So this walks every page back to
the start of the previous filing day (a weekday with filings in bronze: Friday on a Monday)
and compares each (accession_no, cik) row with bronze.filings and bronze.submission_filings
(completeness.missing, the nightly check's lookup). Entries accepted in the last GRACE are left
to the poller, so a filing it is publishing right now isn't taken from it.

Republished events carry `source: recheck` with the feed's own acceptance time and role, so
freshness metrics stay honest: only `feed` rows count as caught live and in latency, and the
status page shows these as caught later the same hour or day. Each run is recorded in
ops.filing_rechecks (migration 169), including how far back the feed reached: `complete` is
false when the feed ended (or stopped paging, or MAX_PAGES) before the window's start. The
nightly daily-index check (`poller-backfill`) stays the final word.
"""

import argparse
import logging
import os
import time as clock_time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta

import httpx
import psycopg

from poller import completeness
from poller.daily_index import EASTERN
from poller.edgar import PAGE_SIZE, Filing, fetch_feed, parse_page
from poller.main import Publisher
from poller.producer import FilingProducer
from tributary_sec import sec_client
from tributary_telemetry import configure_logging

log = logging.getLogger("poller.recheck")

MAX_PAGES = 400  # 40,000 filer rows: two deadline days (the busiest, 2026-11-16, had ~16,000)
GRACE = timedelta(minutes=10)
KEEP_DAYS = 90  # ops.filing_rechecks rows kept

# The last weekday before `today` (ET) with a filing in bronze: the window's first day. An
# index scan backwards from today's start (filings_filed_at) that skips weekend strays.
_PREVIOUS_DAY = """
    SELECT (filed_at AT TIME ZONE 'America/New_York')::date
    FROM bronze.filings
    WHERE filed_at < %s
      AND extract(isodow FROM filed_at AT TIME ZONE 'America/New_York') < 6
    ORDER BY filed_at DESC
    LIMIT 1
"""

_RECORD = """
    INSERT INTO ops.filing_rechecks (checked_at, window_start, reached_back_to, complete, pages,
                                     listed, missing, republished)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""
_PRUNE = "DELETE FROM ops.filing_rechecks WHERE checked_at < %s"


@dataclass
class Listing:
    """The feed's entries back to `start`, newest first, one per (accession_no, cik)."""

    start: datetime
    filings: list[Filing] = field(default_factory=list)
    pages: int = 0
    reached_back_to: datetime | None = None  # the oldest acceptance time listed
    complete: bool = False  # the walk reached `start`


@dataclass(frozen=True)
class Result:
    listing: Listing
    listed: int  # filer rows in the window, outside the grace period
    missing: list[Filing]

    @property
    def missing_accessions(self) -> int:
        return len({f.accession_no for f in self.missing})


def previous_weekday(day: date) -> date:
    day -= timedelta(days=1)
    while day.isoweekday() > 5:
        day -= timedelta(days=1)
    return day


def window_start(conn: psycopg.Connection | None, now: datetime) -> datetime:
    """Midnight ET of the previous filing day: the last weekday before today with a filing in
    bronze (a holiday has none), else the previous weekday."""
    today = datetime.combine(now.astimezone(EASTERN).date(), time(), EASTERN)
    day = None
    if conn is not None:
        row = conn.execute(_PREVIOUS_DAY, (today,)).fetchone()
        day = row[0] if row else None
    return datetime.combine(day or previous_weekday(today.date()), time(), EASTERN)


def walk(client: httpx.Client, start: datetime, max_pages: int = MAX_PAGES,
         page_size: int = PAGE_SIZE) -> Listing:
    """Every feed page until one lies wholly before `start`, the feed ends, it stops paging
    (a page repeating the last) or `max_pages`."""
    listing = Listing(start=start)
    found: dict[tuple[str, str], Filing] = {}
    last_keys: set[tuple[str, str]] = set()
    for n in range(max_pages):
        page = parse_page(fetch_feed(client, start=n * page_size, count=page_size))
        listing.pages += 1
        keys = {f.key for f in page.filings}
        if n and keys and keys <= last_keys:
            log.warning("feed page %d repeats the last: the feed stopped paging", n + 1)
            break
        last_keys = keys
        for f in page.filings:
            found.setdefault(f.key, f)
            if listing.reached_back_to is None or f.filed_at < listing.reached_back_to:
                listing.reached_back_to = f.filed_at
        if page.filings and all(f.filed_at < start for f in page.filings):
            listing.complete = True
            break
        if page.entries < page_size:  # the feed's end
            break
    listing.complete = listing.complete or (
        listing.reached_back_to is not None and listing.reached_back_to <= start)
    listing.filings = list(found.values())
    return listing


def run(client: httpx.Client, producer: Publisher, start: datetime, now: datetime,
        missing: Callable[[Sequence[Filing]], list[Filing]], max_pages: int = MAX_PAGES,
        grace: timedelta = GRACE) -> Result:
    """List the window, find the rows bronze lacks, publish them as `recheck`."""
    listing = walk(client, start, max_pages)
    cutoff = now - grace
    listed = [f for f in listing.filings if start <= f.filed_at <= cutoff]
    absent = [replace(f, source="recheck") for f in missing(listed)]
    for f in reversed(absent):  # oldest first
        producer.publish(f)
    producer.flush()
    return Result(listing=listing, listed=len(listed), missing=absent)


def record(conn: psycopg.Connection, now: datetime, result: Result) -> None:
    """One ops.filing_rechecks row per run (counts only); rows past KEEP_DAYS go."""
    listing = result.listing
    with conn.transaction():
        conn.execute(_RECORD, (now, listing.start, listing.reached_back_to, listing.complete,
                               listing.pages, result.listed, result.missing_accessions,
                               len(result.missing)))
        conn.execute(_PRUNE, (now - timedelta(days=KEEP_DAYS),))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES,
                    help=f"feed pages to read at most (default {MAX_PAGES})")
    ap.add_argument("--grace-minutes", type=float, default=GRACE.total_seconds() / 60,
                    help="leave entries accepted this recently to the live poller (default 10)")
    args = ap.parse_args()
    configure_logging()
    started = clock_time.monotonic()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn, \
            sec_client(os.environ["SEC_USER_AGENT"]) as client:
        now = datetime.now(UTC)
        start = window_start(conn, now)
        result = run(client, FilingProducer(), start, now,
                     lambda filings: completeness.missing(conn, filings),
                     max_pages=args.max_pages, grace=timedelta(minutes=args.grace_minutes))
        record(conn, now, result)
    listing = result.listing
    log.info("recheck since %s: %d pages, %d filer rows listed, %d missing (%d filings) "
             "republished; reached back to %s (%s) in %.0fs",
             start.isoformat(), listing.pages, result.listed, len(result.missing),
             result.missing_accessions,
             listing.reached_back_to.isoformat() if listing.reached_back_to else "nothing",
             "complete" if listing.complete else "INCOMPLETE",
             clock_time.monotonic() - started,
             extra={"pages": listing.pages, "listed": result.listed,
                    "missing": len(result.missing), "complete": listing.complete})


if __name__ == "__main__":
    main()
