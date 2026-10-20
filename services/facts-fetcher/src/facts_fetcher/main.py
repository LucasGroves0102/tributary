"""Batch job: SEC company facts → bronze → silver → gold, one transaction per company."""

import argparse
import logging
import os

import psycopg

from facts_fetcher.db import refresh_gold, refresh_silver, store_payload, tracked_ciks
from facts_fetcher.sec import fetch_company_facts
from tributary_sec import sec_client
from tributary_telemetry import configure_logging

log = logging.getLogger("facts_fetcher")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true",
                        help="skip the SEC; re-derive silver and gold from stored bronze")
    args = parser.parse_args()
    configure_logging()
    dsn = os.environ["DATABASE_URL"]

    with psycopg.connect(dsn) as conn:
        companies = tracked_ciks(conn)
        conn.commit()
        if not companies:
            log.warning("no tracked companies; run the company-loader first")
            return
        if args.rebuild:
            for company_id, ciks in companies.items():
                with conn.transaction():
                    for cik in ciks:
                        refresh_silver(conn, cik)
                    log.info("%s: %d gold rows", company_id, refresh_gold(conn, company_id))
            return

        with sec_client(os.environ["SEC_USER_AGENT"]) as client:
            for company_id, ciks in companies.items():
                payloads = {cik: fetch_company_facts(client, cik) for cik in ciks}
                with conn.transaction():
                    new_facts = 0
                    changed = False
                    for cik, raw in payloads.items():
                        if raw is None:
                            log.info("%s: no XBRL facts for CIK %s", company_id, cik)
                        elif store_payload(conn, cik, raw):
                            changed = True
                            new_facts += refresh_silver(conn, cik)
                    if changed:
                        rows = refresh_gold(conn, company_id)
                        log.info("%s: %d new facts, %d gold rows", company_id, new_facts, rows)
                    else:
                        log.info("%s: unchanged", company_id)


if __name__ == "__main__":
    main()
