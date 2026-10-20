"""Batch job: SEC reference data + curated files → silver company identity."""

import logging
import os
from datetime import date

import psycopg

from company_loader import curated
from company_loader.build import Lineage, build, parse_tickers, tracked_ciks
from company_loader.db import load
from company_loader.sec import fetch_submissions, fetch_tickers
from tributary_sec import sec_client
from tributary_telemetry import configure_logging

log = logging.getLogger("company_loader")


def main() -> None:
    configure_logging()
    user_agent = os.environ["SEC_USER_AGENT"]
    dsn = os.environ["DATABASE_URL"]

    successions = curated.successions()
    tracked = curated.tracked_tickers()

    with sec_client(user_agent) as client:
        tickers = parse_tickers(fetch_tickers(client))
        log.info("company_tickers.json: %d rows", len(tickers))
        to_fetch = sorted(tracked_ciks(tickers, tracked, Lineage(successions)))
        submissions = {}
        for cik in to_fetch:
            sub = fetch_submissions(client, cik)
            if sub is None:
                log.warning("no submissions record for CIK %s", cik)
                continue
            submissions[cik] = sub
        log.info("fetched submissions for %d CIKs", len(submissions))

    snap = build(tickers, submissions, successions, curated.ticker_history(), tracked)
    with psycopg.connect(dsn) as conn:
        counts = load(conn, snap, date.today())
    log.info("loaded: %s", counts)


if __name__ == "__main__":
    main()
