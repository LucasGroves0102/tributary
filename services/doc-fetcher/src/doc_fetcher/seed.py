"""Publish recent 10-K/10-Q/8-K filings of tracked companies to filings.raw (history load)."""

import json
import logging
import os
from datetime import date, timedelta

import psycopg
from confluent_kafka import Producer

from doc_fetcher.db import tracked_ciks
from tributary_kafka import client_config
from tributary_sec import acceptance_time, sec_client
from tributary_telemetry import configure_logging

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
LIMITS = {"10-K": 2, "10-Q": 4}  # most recent N...
MAX_AGE_DAYS = 730             # ...within the last 2 years (skips retired predecessor CIKs)
EIGHT_K_DAYS = 180

log = logging.getLogger("doc_fetcher.seed")


def select_filings(cik: str, submissions: dict, today: date) -> list[dict]:
    """filings.raw events for the filings to seed, from a submissions payload."""
    recent = submissions["filings"]["recent"]
    counts = dict.fromkeys(LIMITS, 0)
    events = []
    for i, form in enumerate(recent["form"]):  # newest first
        filed = date.fromisoformat(recent["filingDate"][i])
        if form in LIMITS:
            if counts[form] >= LIMITS[form] or filed < today - timedelta(days=MAX_AGE_DAYS):
                continue
            counts[form] += 1
        elif form != "8-K" or filed < today - timedelta(days=EIGHT_K_DAYS):
            continue
        accession = recent["accessionNumber"][i]
        accepted = acceptance_time(recent["acceptanceDateTime"][i])
        if accepted is None:
            continue
        events.append({
            "accession_no": accession,
            "cik": cik,
            "company_name": submissions["name"],
            "role": "Filer",
            "form_type": form,
            "filed_at": accepted.isoformat(),
            "url": (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{accession.replace('-', '')}/{accession}-index.htm"),
            "source": "seed",
        })
    return events


def main() -> None:
    configure_logging()
    producer = Producer(client_config({"enable.idempotence": True}))
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        ciks = sorted(tracked_ciks(conn))
    with sec_client(os.environ["SEC_USER_AGENT"]) as client:
        for cik in ciks:
            resp = client.get(SUBMISSIONS_URL.format(cik=cik))
            resp.raise_for_status()
            events = select_filings(cik, resp.json(), date.today())
            for e in events:
                producer.produce("filings.raw", key=cik.encode(), value=json.dumps(e).encode())
            log.info("%s: %d filings", cik, len(events))
    producer.flush(10)


if __name__ == "__main__":
    main()
