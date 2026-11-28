"""Trust as a number (migration 168): ops.refresh_trust counts, over the company pages' quarterly
gold figures, those filed since inline XBRL, those whose source fact links to the line its filing
prints (the gate, 167), those of them that agree, and the figures on a fact the filing prints
otherwise; and the gate's own fact counts. One ops.trust_runs row per refresh; the public status
page reads the latest (ops.public_trust, counts only). The nightly financials-bulk job refreshes it
after its rounds, as it does the extremes report and the coverage sweep.

    python -m facts_fetcher.trust     # refresh now and print the run
"""

import logging
import os
import time

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger("facts_fetcher.trust")


def refresh(conn: psycopg.Connection) -> dict:
    """Count and record one run in its own transaction; returns it."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM ops.trust_runs WHERE run_at = ops.refresh_trust()")
        return cur.fetchone() or {}


def refresh_quietly(conn: psycopg.Connection) -> None:
    """refresh() for a nightly job whose own work is done: a failure is logged, never the job's,
    unless the connection broke."""
    started = time.monotonic()
    try:
        run = refresh(conn)
    except psycopg.Error:
        if conn.broken:
            raise
        log.exception("trust numbers not refreshed; the next night tries again")
        return
    log.info("trust numbers refreshed: %s of %s reported figures linked, %s agree, %s on a"
             " disagreeing fact, in %.0fs", run.get("linked"), run.get("reported"),
             run.get("agreeing"), run.get("disagreeing"), time.monotonic() - started)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        print(refresh(conn))


if __name__ == "__main__":
    main()
