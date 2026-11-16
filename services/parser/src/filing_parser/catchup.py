"""Catch-up in the hourly section-index job (migration 071): documents an older parser split and
passages an older rule built upgrade themselves, oldest filing first, a budget per run.

It runs after parser-index's own work is done (new and re-parsed filings first), so it never delays
a new filing:

1. parser: ops.parser_catchup re-queues up to PER_RUN documents whose sections an older
   PARSER_VERSION wrote, through the crawl's outbox (ops.doc_crawl_targets.published_at):
   doc-crawler publishes them to documents.backfill and parser-backfill parses them;
   documents.fetched (the live parser) never sees them. Only while the crawl has under CRAWL_BUSY
   targets of its own pending, the outbox holds under OUTBOX events and under IN_FLIGHT of the last
   day's requests wait for parser-backfill. A document is re-queued once per version: a crawl
   target published since the catch-up first saw the version counts as requested.
2. passages: index.refresh_passages for up to `seconds` (filings whose passages an older SEARCH_RULE
   built, oldest first), under the lock section-index and section-passages share.
3. diffs: measured only; the run itself recomputes diffs of an older diff.ALGORITHM.

Each kind records a row in ops.catchup_runs (read ops.catchup_status). `UPDATE ops.catchup SET
paused = true WHERE kind = ...` stops a kind's work and keeps measuring it. Everything is keyed on
stored versions, so a stopped run loses nothing and a second run redoes nothing.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import psycopg

from filing_parser import index
from filing_parser.diff import ALGORITHM
from filing_parser.sections import PARSER_VERSION

log = logging.getLogger("parser_index.catchup")

PER_RUN = 3000      # documents re-queued per run, at most
OUTBOX = 3000       # ... while the crawl's outbox holds fewer unpublished events
IN_FLIGHT = 6000    # ... and fewer of the last day's requests wait for parser-backfill
CRAWL_BUSY = 1000   # ... and the crawl has fewer targets of its own pending
SECONDS = 1200      # passages rebuilt per run, in seconds
JOB_SECONDS = 3000  # ... within 50 minutes of the job's start: the next hourly run is never skipped


def budget(seconds: float, elapsed: float) -> float:
    """The catch-up's seconds after a run that took `elapsed`: at most `seconds`, and none past
    JOB_SECONDS (the CronJob's Forbid policy would skip the next hour's run, new filings' too)."""
    return max(0.0, min(seconds, JOB_SECONDS - elapsed))


def parser_version() -> int:
    """PARSER_VERSION's number ("python-7" -> 7): parser-rs writes "rust-<the same number>"."""
    return int(PARSER_VERSION.rsplit("-", 1)[1])


@dataclass
class Result:
    kind: str
    version: str
    behind: int = 0
    in_flight: int = 0
    failed: int = 0
    oldest: date | None = None
    done: int = 0
    paused: bool = False


def begin(conn: psycopg.Connection, kind: str, version: str) -> tuple[bool, object]:
    """(paused, since when the catch-up has seen this version)."""
    row = conn.execute("SELECT paused, started_at FROM ops.catchup_begin(%s, %s)",
                       (kind, version)).fetchone()
    assert row is not None, f"no ops.catchup row for {kind}"
    return bool(row[0]), row[1]


def record(conn: psycopg.Connection, r: Result, seconds: float) -> None:
    conn.execute("SELECT ops.catchup_record(%s, %s, %s::bigint, %s::bigint, %s::bigint, %s::date,"
                 " %s::bigint, %s::real)",
                 (r.kind, r.version, r.behind, r.in_flight, r.failed, r.oldest, r.done, seconds))
    log.info("catch-up %s %s: %d behind, %d in flight, %d failed, %d done%s", r.kind, r.version,
             r.behind, r.in_flight, r.failed, r.done, " (paused)" if r.paused else "")


def parser(conn: psycopg.Connection, per_run: int = PER_RUN, *, version: int | None = None,
           outbox: int = OUTBOX, in_flight: int = IN_FLIGHT,
           crawl_busy: int = CRAWL_BUSY) -> Result:
    v = parser_version() if version is None else version
    started = time.monotonic()
    with conn.transaction():
        paused, since = begin(conn, "parser", str(v))
        row = conn.execute("SELECT * FROM ops.parser_catchup(%s, %s, %s, %s, %s, %s)",
                           (v, since, 0 if paused else per_run, outbox, in_flight,
                            crawl_busy)).fetchone()
        assert row is not None
        r = Result("parser", str(v), behind=row[0], in_flight=row[1], failed=row[2],
                   oldest=row[3], done=row[4], paused=paused)
        record(conn, r, time.monotonic() - started)
    return r


def passages(conn: psycopg.Connection, seconds: float, *,
             clock: Callable[[], float] = time.monotonic) -> Result:
    started = clock()
    with conn.transaction():
        paused, _ = begin(conn, "passages", index.SEARCH_RULE)
    r = Result("passages", index.SEARCH_RULE, paused=paused)
    if not paused and seconds > 0:
        r.done = index.refresh_passages(conn, deadline=started + seconds, clock=clock).filings
    with conn.transaction():
        r.behind, r.oldest = conn.execute(
            "SELECT count(*), min(filed) FROM ops.section_index"
            " WHERE passages_rule IS DISTINCT FROM %s", (index.SEARCH_RULE,)).fetchone()  # type: ignore[misc]
        record(conn, r, clock() - started)
    return r


def diffs(conn: psycopg.Connection, diffed: int) -> Result:
    """The run recomputes diffs of an older ALGORITHM itself: this only measures what is left."""
    with conn.transaction():
        paused, _ = begin(conn, "diffs", ALGORITHM)
        r = Result("diffs", ALGORITHM, paused=paused)
        r.behind, r.oldest = conn.execute(
            "SELECT count(*), min(filed) FROM ops.section_index"
            " WHERE form IN ('10-K', '10-Q') AND algorithm IS DISTINCT FROM %s",
            (ALGORITHM,)).fetchone()  # type: ignore[misc]
        r.done = diffed
        record(conn, r, 0)
    return r


def run(conn: psycopg.Connection, seconds: float = SECONDS, per_run: int = PER_RUN,
        diffed: int = 0, clock: Callable[[], float] = time.monotonic) -> list[Result]:
    """Every kind, after a parser-index run that finished its own work. A kind that raises is
    logged and left for the next run; a lost connection stops the job."""
    steps: list[Callable[[], Result]] = [
        lambda: parser(conn, per_run),
        lambda: passages(conn, seconds, clock=clock),
        lambda: diffs(conn, diffed),
    ]
    results = []
    for step in steps:
        try:
            results.append(step())
        except psycopg.Error:
            if conn.broken:
                raise
            log.exception("catch-up step failed; the next run tries again")
    return results
