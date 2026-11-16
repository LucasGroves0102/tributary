"""The extremes report (migration 072): for every screener value column, the top and bottom 20
companies as the screener sorts them (gold.latest_reading, readings applied), each with what
explains it: the checks that have a finding on the metric or its inputs in the year to its period,
a quarter derived from year-to-date figures, or the company's own figure a period earlier (a year
for TTM) of the same sign within 3x (`in_line`: Amazon's revenue is large, not wrong). One nothing
explains is flagged `review`: an extreme no reading, check or data note accounts for is the
likeliest wrong number a reader meets first. ops.refresh_extremes rebuilds ops.extremes; the
nightly financials-bulk job runs it after its rounds (refresh). ops.extremes_report adds the names,
for ops_reader and this CLI only: it names companies, so it is never public.

    make extremes                # the rows flagged for review
    make extremes ALL=1          # every row
    make extremes REFRESH=1      # recompute first (a couple of minutes on production)
"""

import argparse
import logging
import os
import time
from collections.abc import Iterable
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger("facts_fetcher.extremes")

TOP = 20


def refresh(conn: psycopg.Connection, n: int = TOP) -> int:
    """Rebuild ops.extremes in one transaction; returns the rows flagged for review."""
    with conn.transaction():
        row = conn.execute("SELECT ops.refresh_extremes(%s)", (n,)).fetchone()
    return int(row[0]) if row else 0


def refresh_quietly(conn: psycopg.Connection) -> None:
    """refresh() for a nightly job whose own work is done: a failure is logged, never the job's,
    unless the connection broke."""
    started = time.monotonic()
    try:
        flagged = refresh(conn)
    except psycopg.Error:
        if conn.broken:
            raise
        log.exception("extremes report not refreshed; the next night tries again")
        return
    log.info("extremes report refreshed: %d rows to review in %.0fs", flagged,
             time.monotonic() - started)


def rows(conn: psycopg.Connection, everything: bool = False,
         column: str | None = None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT column_key, side, rank, company, company_id, value, unit, period_end,"
            " accession_no, status, prev_value, findings, review, refreshed_at"
            " FROM ops.extremes_report"
            " WHERE (%(all)s OR review) AND (%(column)s::text IS NULL OR column_key = %(column)s)"
            " ORDER BY column_key, side DESC, rank",
            {"all": everything, "column": column})
        return cur.fetchall()


def figure(value: Decimal, unit: str | None) -> str:
    """A compact figure: 1.23B, 45.6M, 0.1234, with the unit when it isn't a ratio."""
    v = float(value)
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= size:
            text = f"{v / size:.3g}{suffix}"
            break
    else:
        text = f"{v:.4g}"
    return f"{text} {unit}" if unit and unit not in ("pure", "ratio") else text


def render(found: Iterable[dict]) -> str:
    lines = []
    for r in found:
        why = ", ".join(r["findings"]) or ("REVIEW" if r["review"] else "")
        before = "" if r["prev_value"] is None else figure(r["prev_value"], r["unit"])
        lines.append(f"{r['column_key']:<34} {r['side']:<6} {r['rank']:>2}  "
                     f"{(r['company'] or '')[:36]:<36} {figure(r['value'], r['unit']):>16}  "
                     f"{before:>16}  "
                     f"{r['period_end']}  {r['accession_no'] or '':<20}  "
                     f"{r['status'] or '':<6} {why}")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="recompute the report first")
    parser.add_argument("--all", action="store_true", help="every row, not only those to review")
    parser.add_argument("--column", help="one column, e.g. revenue_growth_ttm")
    args = parser.parse_args(None if argv is None else list(argv))
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        if args.refresh:
            log.info("%d rows to review", refresh(conn))
        found = rows(conn, args.all, args.column)
    if not found:
        print("nothing to review" if not args.all else "no report yet: run with --refresh")
        return
    print(f"refreshed {found[0]['refreshed_at']:%Y-%m-%d %H:%M %Z}; "
          f"{sum(r['review'] for r in found)} of {len(found)} rows to review")
    print(render(found))


if __name__ == "__main__":
    main()
