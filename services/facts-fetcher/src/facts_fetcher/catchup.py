"""Catch-up at the end of the hourly segments job (migration 071): companies whose gold or quality
checks an older rule built are rebuilt or re-checked, oldest build first, in batches, within a time
budget. No SEC requests.

First, the screener's stored figures (migration 078): companies with none (every company after the
deploy that adds them, then new companies) or read live since a future period began
(`gold.screen_backlog`), SCREEN_BATCH a transaction under their gold locks. Logged, not a kind.

Kinds, in this order (the cheap ones first, so a long gold catch-up never holds them up):

- check_segments: `ops.recheck('check_segments', batch)`, nothing re-derived.
- check_financials: `ops.recheck('check_financials', batch)`, only companies whose gold is current
  (a gold rebuild runs the check itself).
- gold: the financials-rebuild path for the batch's CIKs: their stored companyfacts payloads
  re-projected with the current metric tags, silver, then `gold.refresh_financials` of the companies
  and of `gold.companies_for_ciks` (co-registrants that borrow the CIKs' filings).
- known: gold's history (migration 086): `gold.rebuild_known(batch)` replays each company's views
  as of every filing date at the current rules, only companies whose gold is current
  (ops.company_backlog). Not paused on deploy (the owner's call). A view per filing date: Apple's
  70 dates about 16 minutes before migration 161, about 1.5 after.

The versions are gold.rules_version() (gold and known), ops.check_financials_version() and
ops.check_segments_version(); every refresh stamps ops.company_builds (gold.refresh_financials,
gold.refresh_segments, ops.recheck), so the nightly jobs' and a hand-run rebuild's work counts too.
Each batch commits alone and gold writes take gold.lock_companies, so stopping at any point leaves
whole companies and a nightly refresh of the same company waits for the batch instead of
interleaving with it. A batch that raises is retried one company at a time; a company that still
raises is recorded (ops.company_builds.failed_version) and left for the next version. A deadlock or
lock timeout ends the kind's turn: the next run tries again.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import psycopg
import psycopg.errors

from facts_fetcher.db import (
    company_ids,
    latest_raw,
    metric_tags,
    refresh_gold,
    refresh_silver,
    set_facts,
)
from facts_fetcher.payload import reproject

log = logging.getLogger("facts_fetcher.catchup")

KINDS = ("check_segments", "check_financials", "gold", "known")
BATCH = {"check_segments": 100, "check_financials": 100, "gold": 25, "known": 5}
SCREEN_BATCH = 200  # companies' stored screener figures a transaction (078)
SECONDS = 1200
JOB_SECONDS = 3000  # the hourly job's whole run, so the next hour's is never skipped (Forbid)
TRANSIENT = (psycopg.errors.TransactionRollback, psycopg.errors.LockNotAvailable)

Build = Callable[[psycopg.Connection, str, Sequence[str]], None]


@dataclass
class Result:
    kind: str
    version: int
    behind: int = 0
    failed: int = 0
    oldest: date | None = None
    done: int = 0
    paused: bool = False


def build(conn: psycopg.Connection, kind: str, companies: Sequence[str]) -> None:
    """Rebuild (gold), replay (known) or check these companies again in one transaction; the
    SQL stamps them."""
    with conn.transaction():
        if kind == "known":
            conn.execute("SELECT gold.rebuild_known(%s)", (list(companies),))
            return
        if kind != "gold":
            conn.execute("SELECT ops.recheck(%s, %s)", (kind, list(companies)))
            return
        ciks = [r[0] for r in conn.execute(
            "SELECT cik FROM silver.company_ciks WHERE company_id = ANY(%s) ORDER BY cik",
            (list(companies),))]
        tags = metric_tags(conn)
        for cik, sha, raw_gzip in latest_raw(conn, ciks):
            set_facts(conn, cik, sha, reproject(raw_gzip, tags))
        refresh_silver(conn, ciks)
        refresh_gold(conn, sorted({*companies, *company_ids(conn, ciks)}))


def catch_up(conn: psycopg.Connection, kind: str, deadline: float, *,
             clock: Callable[[], float] = time.monotonic, batch: int | None = None,
             build: Build = build) -> Result:
    started = clock()
    with conn.transaction():
        version = conn.execute("SELECT ops.company_rule_version(%s)", (kind,)).fetchone()[0]  # type: ignore[index]
        last = conn.execute("SELECT version FROM ops.catchup_runs WHERE kind = %s"
                            " ORDER BY run_at DESC LIMIT 1", (kind,)).fetchone()
        row = conn.execute("SELECT paused FROM ops.catchup_begin(%s, %s)",
                           (kind, str(version))).fetchone()
    r = Result(kind, version, paused=bool(row and row[0]))
    if last is None or last[0] != str(version):
        # a new version: its backlog now, so the status never says "current, 0 behind" while
        # the first run works through it
        record(conn, r, 0.0)
    seen: set[str] = set()
    while not r.paused and clock() < deadline:
        with conn.transaction():
            ids = [x[0] for x in conn.execute(
                "SELECT company_id FROM ops.company_backlog(%s) WHERE NOT failed"
                # the epoch: inputs changed (ops.rebuild_soon, 076), before the oldest builds
                " ORDER BY (built_at = 'epoch') IS TRUE DESC, built_at NULLS FIRST,"
                " company_id LIMIT %s",
                (kind, batch or BATCH[kind]))]
        if not ids or seen.issuperset(ids):  # nothing left, or rebuilding changed nothing
            break
        seen.update(ids)
        try:
            build(conn, kind, ids)
            r.done += len(ids)
            continue
        except psycopg.Error as e:
            if conn.broken:
                raise
            if isinstance(e, TRANSIENT):
                log.warning("catch-up %s: %s; the next run goes on", kind, e)
                break
            log.warning("catch-up %s: a batch of %d raised (%s); one at a time", kind, len(ids), e)
        if not one_at_a_time(conn, kind, version, ids, build, r):
            break
    record(conn, r, clock() - started)
    log.info("catch-up %s %s: %d done, %d behind, %d failed%s", kind, version, r.done, r.behind,
             r.failed, " (paused)" if r.paused else "")
    return r


def record(conn: psycopg.Connection, r: Result, seconds: float) -> None:
    """Measure the kind's backlog into `r` and record a run (ops.catchup_runs)."""
    with conn.transaction():
        r.behind, r.failed, r.oldest = conn.execute(  # type: ignore[misc]
            "SELECT count(*) FILTER (WHERE NOT failed), count(*) FILTER (WHERE failed),"
            " min(built_at)::date FROM ops.company_backlog(%s)", (r.kind,)).fetchone()
        conn.execute("SELECT ops.catchup_record(%s, %s, %s::bigint, 0, %s::bigint, %s::date,"
                     " %s::bigint, %s::real)",
                     (r.kind, str(r.version), r.behind, r.failed, r.oldest, r.done, seconds))


def screen(conn: psycopg.Connection, deadline: float, *,
           clock: Callable[[], float] = time.monotonic, batch: int = SCREEN_BATCH) -> int:
    """The screener's stored figures for the companies in gold.screen_backlog (078), `batch` a
    transaction (gold.refresh_screen_backlog takes their gold locks), until none is left or the
    deadline. Returns the companies done."""
    done = 0
    while clock() < deadline:
        with conn.transaction():
            n = conn.execute("SELECT gold.refresh_screen_backlog(%s)", (batch,)).fetchone()[0]  # type: ignore[index]
        done += n
        if n < batch:
            break
    log.info("screener figures: %d companies stored", done)
    return done


def one_at_a_time(conn: psycopg.Connection, kind: str, version: int, ids: Sequence[str],
                  build: Build, r: Result) -> bool:
    """After a batch raised: each company alone; one that still raises is recorded as failed at
    this version. False when a deadlock or lock timeout ends the kind's turn."""
    for company in ids:
        try:
            build(conn, kind, [company])
            r.done += 1
        except psycopg.Error as e:
            if conn.broken:
                raise
            if isinstance(e, TRANSIENT):
                log.warning("catch-up %s: %s; the next run goes on", kind, e)
                return False
            log.error("catch-up %s: %s failed at version %s: %s", kind, company, version, e,
                      extra={"company_id": company})
            with conn.transaction():
                conn.execute("SELECT ops.build_failed(%s, %s, %s, %s)",
                             (kind, company, version, f"{type(e).__name__}: {e}"))
    return True


def budget(seconds: float, elapsed: float) -> float:
    """The catch-up's seconds after a segments run that took `elapsed`: at most `seconds`, none
    past JOB_SECONDS."""
    return max(0.0, min(seconds, JOB_SECONDS - elapsed))


def run(conn: psycopg.Connection, seconds: float = SECONDS, *,
        clock: Callable[[], float] = time.monotonic, batch: int | None = None,
        build: Build = build) -> list[Result]:
    """Every kind within `seconds` in all. A kind that raises is logged and left for the next run;
    a lost connection stops the job."""
    deadline = clock() + seconds
    try:
        screen(conn, deadline, clock=clock)
    except psycopg.Error:
        if conn.broken:
            raise
        log.exception("screener figures stopped; the next run goes on")
    results = []
    for kind in KINDS:
        try:
            results.append(catch_up(conn, kind, deadline, clock=clock, batch=batch, build=build))
        except psycopg.Error:
            if conn.broken:
                raise
            log.exception("catch-up %s stopped; the next run tries again", kind)
    return results
