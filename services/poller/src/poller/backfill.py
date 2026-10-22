"""Publish every filing from EDGAR's daily master indexes to filings.raw.

Catches what the live poller missed: downtime, or bursts of more than 100 filings between
polls. Consumers are idempotent on (accession_no, cik), so re-publishing is harmless and
rows the poller already wrote keep their exact acceptance time.
"""

import argparse
import logging
import os
from datetime import date, timedelta

from poller.daily_index import available_days, fetch_master, parse_master
from poller.producer import FilingProducer
from tributary_sec import sec_client
from tributary_telemetry import configure_logging

log = logging.getLogger("poller.backfill")


def days_in_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def quarters(days: list[date]) -> set[tuple[int, int]]:
    return {(d.year, (d.month - 1) // 3 + 1) for d in days}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=1,
                    help="backfill this many days ending yesterday (default 1)")
    ap.add_argument("--start", type=date.fromisoformat, help="first day (YYYY-MM-DD)")
    ap.add_argument("--end", type=date.fromisoformat, help="last day (default yesterday)")
    args = ap.parse_args()
    configure_logging()
    end = args.end or date.today() - timedelta(days=1)
    start = args.start or end - timedelta(days=args.days - 1)
    wanted = days_in_range(start, end)

    producer = FilingProducer()
    total = 0
    with sec_client(os.environ["SEC_USER_AGENT"]) as client:
        available = set().union(*(available_days(client, y, q) for y, q in quarters(wanted)))
        for day in wanted:
            if day not in available:
                log.info("%s: no index (weekend, holiday, or not published yet)", day)
                continue
            filings = parse_master(fetch_master(client, day))
            for f in filings:
                producer.publish(f)
            producer.flush()
            total += len(filings)
            log.info("%s: published %d filings", day, len(filings))
    log.info("done: %d filings over %s..%s", total, start, end)


if __name__ == "__main__":
    main()
