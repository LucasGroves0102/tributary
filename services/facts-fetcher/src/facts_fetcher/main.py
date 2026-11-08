"""Batch job: SEC company facts → bronze → silver → gold, one transaction per company."""

import argparse
import logging
import os
from collections.abc import Sequence

import psycopg

from facts_fetcher.db import (
    company_ids,
    latest_raw,
    metric_tags,
    refresh_gold,
    refresh_silver,
    set_facts,
    store_payload,
    tracked_ciks,
)
from facts_fetcher.payload import reproject
from facts_fetcher.sec import fetch_company_facts
from tributary_sec import sec_client
from tributary_telemetry import configure_logging

REBUILD_BATCH = 50  # CIKs per transaction

log = logging.getLogger("facts_fetcher")


def rebuild(conn: psycopg.Connection, only: Sequence[str] | None = None) -> None:
    """Re-project every CIK's latest stored payload with the current metric_tags, then
    re-derive silver and gold. Commits per batch of CIKs. `only`: just these CIKs, to look at a
    few companies before running everything."""
    with conn.transaction():
        tags = metric_tags(conn)
        ciks = [r[0] for r in conn.execute(
            "SELECT DISTINCT cik FROM bronze.company_facts"
            " WHERE %(only)s::text[] IS NULL OR cik = ANY(%(only)s) ORDER BY cik",
            {"only": list(only) if only else None}).fetchall()]
    facts = gold = 0
    for i in range(0, len(ciks), REBUILD_BATCH):
        batch = ciks[i:i + REBUILD_BATCH]
        with conn.transaction():
            for cik, sha, raw_gzip in latest_raw(conn, batch):
                set_facts(conn, cik, sha, reproject(raw_gzip, tags))
            facts += refresh_silver(conn, batch)
            gold += refresh_gold(conn, company_ids(conn, batch))
        log.info("%d/%d CIKs: %d new facts, %d gold rows", i + len(batch), len(ciks), facts, gold)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true",
                        help="skip the SEC; re-derive silver and gold from stored bronze")
    parser.add_argument("--ciks", default="",
                        help="with --rebuild: only these CIKs, comma-separated")
    args = parser.parse_args()
    configure_logging()
    dsn = os.environ["DATABASE_URL"]

    with psycopg.connect(dsn) as conn:
        if args.rebuild:
            rebuild(conn, [c.strip() for c in args.ciks.split(",") if c.strip()] or None)
            return
        companies = tracked_ciks(conn)
        conn.commit()
        if not companies:
            log.warning("no tracked companies; run the company-loader first")
            return

        with sec_client(os.environ["SEC_USER_AGENT"]) as client:
            for company_id, ciks in companies.items():
                payloads = {cik: fetch_company_facts(client, cik) for cik in ciks}
                with conn.transaction():
                    new = []
                    for cik, raw in payloads.items():
                        if raw is None:
                            log.info("%s: no XBRL facts for CIK %s", company_id, cik)
                        elif store_payload(conn, cik, raw):
                            new.append(cik)
                    if new:
                        facts = refresh_silver(conn, new)
                        rows = refresh_gold(conn, [company_id])
                        log.info("%s: %d new facts, %d gold rows", company_id, facts, rows)
                    else:
                        log.info("%s: unchanged", company_id)


if __name__ == "__main__":
    main()
