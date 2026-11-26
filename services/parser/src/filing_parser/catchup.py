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
2. check_sections: ops.check_sections (migration 072, the parser-output checks) on every stored
   document no check of ops.check_sections_version() has seen, oldest filing first, SECTIONS_BATCH
   documents a transaction, for up to SECTIONS_SECONDS of the budget. section-index itself checks
   the filings it indexes; this covers the rest once per version (and documents with no sections).
3. statements: statements.py (migration 074) on every stored 10-K/10-Q with tables that the current
   statements.READER hasn't read (ops.statement_backlog), oldest filing first, STATEMENTS_BATCH a
   transaction, for up to STATEMENTS_SECONDS of the budget. section-index reads what it indexes;
   this covers the rest once per READER. Each read makes its company's financial check due
   (ops.company_backlog), so the facts-fetcher catch-up compares the lines with gold within the
   hour.
4. releases: releases.py (migration 165) on every stored 6-K with tables that the current
   releases.READER hasn't read (ops.release_backlog), oldest filing first, RELEASES_BATCH a
   transaction, for up to RELEASES_SECONDS of the budget. section-index reads what it indexes;
   this covers the rest once per READER. Each read makes its company's gold due
   (ops.company_backlog), so the facts-fetcher catch-up rebuilds it within the hour.
5. passages: index.refresh_passages for the rest of `seconds` (filings whose passages an older
   SEARCH_RULE built, oldest first), under the lock section-index and section-passages share.
6. diffs: measured only; the run itself recomputes diffs of an older diff.ALGORITHM.

Each kind records a row in ops.catchup_runs (read ops.catchup_status). `UPDATE ops.catchup SET
paused = true WHERE kind = ...` stops a kind's work and keeps measuring it. Everything is keyed on
stored versions, so a stopped run loses nothing and a second run redoes nothing.

What the status says (migration 079): the first run of a new version records its backlog before it
works (else the status reads the old version's "current, 0 behind" for its whole budget), and a run
locked out by a hand-run job (section-passages holds parser-index's lock) records every kind as
`skipped: lock held` with its last count (`skipped`), measured only when that count is of another
version.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import psycopg

from filing_parser import index, releases, statements
from filing_parser.diff import ALGORITHM
from filing_parser.sections import PARSER_VERSION

log = logging.getLogger("parser_index.catchup")

PER_RUN = 3000      # documents re-queued per run, at most
OUTBOX = 3000       # ... while the crawl's outbox holds fewer unpublished events
IN_FLIGHT = 6000    # ... and fewer of the last day's requests wait for parser-backfill
CRAWL_BUSY = 1000   # ... and the crawl has fewer targets of its own pending
SECONDS = 1200      # passages rebuilt per run, in seconds (after the parser checks' share)
SECTIONS_SECONDS = 300  # documents checked per run, in seconds, out of SECONDS
SECTIONS_BATCH = 10000  # ... a transaction (2,000 took 0.8 s on hermes; 079's backlog 0.2 s)
STATEMENTS_SECONDS = 300  # filings' statement lines read per run, in seconds, out of SECONDS
STATEMENTS_BATCH = 100    # ... a transaction
RELEASES_SECONDS = 300    # 6-K results releases read per run, in seconds, out of SECONDS (165)
RELEASES_BATCH = 100      # ... a transaction
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


def record(conn: psycopg.Connection, r: Result, seconds: float, note: str | None = None) -> None:
    conn.execute("SELECT ops.catchup_record(%s, %s, %s::bigint, %s::bigint, %s::bigint, %s::date,"
                 " %s::bigint, %s::real, %s::text)",
                 (r.kind, r.version, r.behind, r.in_flight, r.failed, r.oldest, r.done, seconds,
                  note))
    log.info("catch-up %s %s: %d behind, %d in flight, %d failed, %d done%s%s", r.kind, r.version,
             r.behind, r.in_flight, r.failed, r.done, " (paused)" if r.paused else "",
             f" ({note})" if note else "")


def fresh(conn: psycopg.Connection, kind: str, version: str) -> bool:
    """No run of this kind recorded at this version yet: its first run records the backlog first."""
    row = conn.execute("SELECT version FROM ops.catchup_runs WHERE kind = %s"
                       " ORDER BY run_at DESC LIMIT 1", (kind,)).fetchone()
    return row is None or row[0] != version


def measure_passages(conn: psycopg.Connection, r: Result) -> Result:
    r.behind, r.oldest = conn.execute(  # type: ignore[misc]
        "SELECT count(*), min(filed) FROM ops.section_index"
        " WHERE passages_rule IS DISTINCT FROM %s", (index.SEARCH_RULE,)).fetchone()
    return r


def measure_sections(conn: psycopg.Connection, r: Result) -> Result:
    r.behind, r.oldest = conn.execute(  # type: ignore[misc]
        "SELECT count(*), min(filed) FROM ops.section_check_backlog(%s)",
        (int(r.version),)).fetchone()
    return r


def measure_statements(conn: psycopg.Connection, r: Result) -> Result:
    r.behind, r.oldest = conn.execute(  # type: ignore[misc]
        "SELECT count(*), min(filed) FROM ops.statement_backlog(%s)",
        (statements.READER,)).fetchone()
    return r


def measure_releases(conn: psycopg.Connection, r: Result) -> Result:
    r.behind, r.oldest = conn.execute(  # type: ignore[misc]
        "SELECT count(*), min(filed) FROM ops.release_backlog(%s)",
        (releases.READER,)).fetchone()
    return r


def measure_diffs(conn: psycopg.Connection, r: Result) -> Result:
    r.behind, r.oldest = conn.execute(  # type: ignore[misc]
        "SELECT count(*), min(filed) FROM ops.section_index"
        " WHERE form IN ('10-K', '10-Q') AND algorithm IS DISTINCT FROM %s",
        (ALGORITHM,)).fetchone()
    return r


def measure_parser(conn: psycopg.Connection, r: Result) -> Result:
    """ops.parser_catchup queueing nothing (limit 0): its counts only."""
    since = conn.execute("SELECT started_at FROM ops.catchup WHERE kind = 'parser'").fetchone()[0]  # type: ignore[index]
    row = conn.execute("SELECT * FROM ops.parser_catchup(%s, %s, 0, %s, %s, %s)",
                       (int(r.version), since, OUTBOX, IN_FLIGHT, CRAWL_BUSY)).fetchone()
    assert row is not None
    r.behind, r.in_flight, r.failed, r.oldest = row[0], row[1], row[2], row[3]
    return r


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
        new = fresh(conn, "passages", index.SEARCH_RULE)
        paused, _ = begin(conn, "passages", index.SEARCH_RULE)
        r = Result("passages", index.SEARCH_RULE, paused=paused)
        if new:
            record(conn, measure_passages(conn, r), 0.0)
    if not paused and seconds > 0:
        r.done = index.refresh_passages(conn, deadline=started + seconds, clock=clock).filings
    with conn.transaction():
        record(conn, measure_passages(conn, r), clock() - started)
    return r


def sections(conn: psycopg.Connection, seconds: float, *, batch: int = SECTIONS_BATCH,
             clock: Callable[[], float] = time.monotonic) -> Result:
    """Documents the current parser-output checks haven't seen, checked oldest filing first until
    `seconds` (clock time) run out; each batch commits alone, so a stopped run loses nothing."""
    started = clock()
    with conn.transaction():
        version = conn.execute("SELECT ops.check_sections_version()").fetchone()[0]  # type: ignore[index]
        new = fresh(conn, "check_sections", str(version))
        paused, _ = begin(conn, "check_sections", str(version))
        r = Result("check_sections", str(version), paused=paused)
        if new:
            record(conn, measure_sections(conn, r), 0.0)
    while not paused and clock() < started + seconds:
        with conn.transaction():
            ids = [x[0] for x in conn.execute(
                "SELECT accession_no FROM ops.section_check_backlog(%s)"
                " ORDER BY filed, accession_no LIMIT %s", (version, batch))]
            if ids:
                conn.execute("SELECT ops.check_sections(%s)", (ids,))
        if not ids:
            break
        r.done += len(ids)
    with conn.transaction():
        record(conn, measure_sections(conn, r), clock() - started)
    return r


def statement_lines(conn: psycopg.Connection, seconds: float, *, batch: int = STATEMENTS_BATCH,
                    clock: Callable[[], float] = time.monotonic) -> Result:
    """10-K/10-Q filings the current statements.READER hasn't read, read oldest filing first until
    `seconds` (clock time) run out; each batch commits alone."""
    started = clock()
    with conn.transaction():
        new = fresh(conn, "statements", statements.READER)
        paused, _ = begin(conn, "statements", statements.READER)
        r = Result("statements", statements.READER, paused=paused)
        if new:
            record(conn, measure_statements(conn, r), 0.0)
    while not paused and clock() < started + seconds:
        with conn.transaction():
            ids = [x[0] for x in conn.execute(
                "SELECT accession_no FROM ops.statement_backlog(%s)"
                " ORDER BY filed, accession_no LIMIT %s", (statements.READER, batch))]
            if ids:
                index.write_statements(conn, ids)
        if not ids:
            break
        r.done += len(ids)
    with conn.transaction():
        record(conn, measure_statements(conn, r), clock() - started)
    return r


def release_figures(conn: psycopg.Connection, seconds: float, *, batch: int = RELEASES_BATCH,
                    clock: Callable[[], float] = time.monotonic) -> Result:
    """6-Ks the current releases.READER hasn't read, read oldest filing first until `seconds`
    (clock time) run out; each batch commits alone."""
    started = clock()
    with conn.transaction():
        new = fresh(conn, "releases", releases.READER)
        paused, _ = begin(conn, "releases", releases.READER)
        r = Result("releases", releases.READER, paused=paused)
        if new:
            record(conn, measure_releases(conn, r), 0.0)
    while not paused and clock() < started + seconds:
        with conn.transaction():
            ids = [x[0] for x in conn.execute(
                "SELECT accession_no FROM ops.release_backlog(%s)"
                " ORDER BY filed, accession_no LIMIT %s", (releases.READER, batch))]
            if ids:
                index.write_releases(conn, ids)
        if not ids:
            break
        r.done += len(ids)
    with conn.transaction():
        record(conn, measure_releases(conn, r), clock() - started)
    return r


def diffs(conn: psycopg.Connection, diffed: int) -> Result:
    """The run recomputes diffs of an older ALGORITHM itself: this only measures what is left."""
    with conn.transaction():
        paused, _ = begin(conn, "diffs", ALGORITHM)
        r = Result("diffs", ALGORITHM, paused=paused)
        measure_diffs(conn, r)
        r.done = diffed
        record(conn, r, 0)
    return r


def skipped(conn: psycopg.Connection, note: str = "skipped: lock held") -> list[Result]:
    """parser-index found its lock held (a hand-run job does the work): every kind records a run
    that did nothing, with its last count (ops.catchup_skipped), measured only when the last count
    is of another version. A kind that raises is logged; a lost connection stops the job."""
    with conn.transaction():
        sections_version = str(conn.execute("SELECT ops.check_sections_version()").fetchone()[0])  # type: ignore[index]
    kinds: list[tuple[str, str, Callable[[psycopg.Connection, Result], Result]]] = [
        ("parser", str(parser_version()), measure_parser),
        ("check_sections", sections_version, measure_sections),
        ("statements", statements.READER, measure_statements),
        ("releases", releases.READER, measure_releases),
        ("passages", index.SEARCH_RULE, measure_passages),
        ("diffs", ALGORITHM, measure_diffs),
    ]
    results = []
    for kind, version, measure in kinds:
        try:
            with conn.transaction():
                paused, _ = begin(conn, kind, version)
                r = Result(kind, version, paused=paused)
                kept = conn.execute("SELECT ops.catchup_skipped(%s, %s, %s)",
                                    (kind, version, note)).fetchone()
                if kept is None or kept[0] is not True:
                    record(conn, measure(conn, r), 0.0, note)
                else:
                    log.info("catch-up %s %s: %s, the last count kept", kind, version, note)
            results.append(r)
        except psycopg.Error:
            if conn.broken:
                raise
            log.exception("catch-up %s: recording the skip failed", kind)
    return results


def run(conn: psycopg.Connection, seconds: float = SECONDS, per_run: int = PER_RUN,
        diffed: int = 0, clock: Callable[[], float] = time.monotonic,
        sections_seconds: float = SECTIONS_SECONDS,
        statements_seconds: float = STATEMENTS_SECONDS,
        releases_seconds: float = RELEASES_SECONDS) -> list[Result]:
    """Every kind, after a parser-index run that finished its own work: the parser checks get up to
    `sections_seconds` of `seconds`, the statement lines up to `statements_seconds`, the release
    figures up to `releases_seconds`, the passages what is left. A kind that raises is logged and
    left for the next run; a lost connection stops the job."""
    deadline = clock() + seconds
    steps: list[Callable[[], Result]] = [
        lambda: parser(conn, per_run),
        lambda: sections(conn, min(sections_seconds, max(0.0, deadline - clock())), clock=clock),
        lambda: statement_lines(conn, min(statements_seconds, max(0.0, deadline - clock())),
                                clock=clock),
        lambda: release_figures(conn, min(releases_seconds, max(0.0, deadline - clock())),
                                clock=clock),
        lambda: passages(conn, max(0.0, deadline - clock()), clock=clock),
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
