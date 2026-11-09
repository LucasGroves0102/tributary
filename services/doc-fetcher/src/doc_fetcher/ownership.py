"""Insider filings (Forms 3, 4 and 5; #117): the SEC's ownershipDocument XML, read from the filing's
full submission text (`<accession>.txt`, one request), stored as filed in
bronze.ownership_documents and parsed into silver.insider_filings / silver.insider_transactions by
silver.refresh_insider_filings (migration 054), in one transaction per filing. Idempotent on the
accession: storing a filing again replaces its XML and re-derives its silver rows.

Two ways in, both through libs/sec (User-Agent, shared rate limit):
- the live doc-fetcher (`main.py`): each new Form 3/4/5 in the feed whose filer is an operating or
  tracked company and isn't the reporting owner (the feed lists a Form 4 once per filer);
- `doc-fetcher-insiders` (`backfill`): the Forms 3/4/5 of operating companies in the submissions
  index over the last INSIDERS_DAYS days that bronze doesn't have, newest first, at most
  INSIDERS_LIMIT a run, at the caller's SEC class (the Helm job: bulk).
A filing without an ownershipDocument (a paper filing's scan) is skipped and logged."""

import hashlib
import logging
import os
import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import psycopg

from tributary_sec import sec_client
from tributary_telemetry import configure_logging

INSIDER_FORMS = frozenset({"3", "4", "5", "3/A", "4/A", "5/A"})
_DOCUMENT = re.compile(rb"<ownershipDocument>.*?</ownershipDocument>", re.S)
_EASTERN = ZoneInfo("America/New_York")

log = logging.getLogger("doc_fetcher.ownership")


def submission_url(cik: str, accession: str) -> str:
    """The filing's full submission text: every document of it, the XML included."""
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{accession}.txt")


def ownership_xml(raw: bytes) -> str | None:
    """The <ownershipDocument> element of a full submission, as filed; None when it has none."""
    m = _DOCUMENT.search(raw)
    if m is None:
        return None
    try:
        return m.group(0).decode("utf-8")
    except UnicodeDecodeError:
        return m.group(0).decode("latin-1")


def filed_on(filed_at: str) -> date:
    """EDGAR's filing date (US Eastern) of an acceptance time."""
    return datetime.fromisoformat(filed_at.replace("Z", "+00:00")).astimezone(_EASTERN).date()


def stored(conn: psycopg.Connection, accession: str) -> bool:
    return conn.execute("SELECT 1 FROM bronze.ownership_documents WHERE accession_no = %s",
                        (accession,)).fetchone() is not None


def store(conn: psycopg.Connection, accession: str, cik: str, form: str, filed: date, url: str,
          xml: str) -> int:
    """Bronze, then silver, in one transaction; the number of silver filings (0: the issuer
    isn't a company here, or the XML isn't well formed)."""
    digest = hashlib.sha256(xml.encode()).hexdigest()
    with conn.transaction():
        conn.execute(
            "INSERT INTO bronze.ownership_documents (accession_no, cik, form, filed, url, xml,"
            " content_sha256) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (accession_no) DO UPDATE SET cik = EXCLUDED.cik, form = EXCLUDED.form,"
            " filed = EXCLUDED.filed, url = EXCLUDED.url, xml = EXCLUDED.xml,"
            " content_sha256 = EXCLUDED.content_sha256, fetched_at = now()",
            (accession, cik, form, filed, url, xml, digest))
        row = conn.execute("SELECT silver.refresh_insider_filings(ARRAY[%s])",
                           (accession,)).fetchone()
    return row[0] if row else 0


def fetch(conn: psycopg.Connection, client: httpx.Client, accession: str, cik: str, form: str,
          filed: date) -> bool:
    """One filing: fetch, store, parse. False when there's nothing to store (gone, or no XML)."""
    url = submission_url(cik, accession)
    resp = client.get(url)
    if resp.status_code == 404:
        log.warning("%s %s %s: not on EDGAR", cik, form, accession)
        return False
    resp.raise_for_status()
    xml = ownership_xml(resp.content)
    if xml is None:
        log.warning("%s %s %s: no ownershipDocument (a paper filing?)", cik, form, accession)
        return False
    store(conn, accession, cik, form, filed, url, xml)
    log.info("%s %s %s: insider filing stored", cik, form, accession,
             extra={"cik": cik, "form": form, "accession_no": accession})
    return True


def wanted(event: dict, ciks: Iterable[str] | frozenset[str]) -> bool:
    """A live feed event the doc-fetcher stores: a Form 3/4/5 under its issuer's CIK (not the
    reporting owner's line), for a company in `ciks`."""
    return (event["form_type"] in INSIDER_FORMS and event.get("role") != "Reporting"
            and event.get("source", "feed") == "feed" and event["cik"] in ciks)


def pending(conn: psycopg.Connection, since: date, limit: int) -> list[tuple[str, str, str, date]]:
    """Forms 3/4/5 of operating companies filed since `since` that bronze doesn't have, newest
    first: (accession, issuer CIK, form, filed). One row per accession."""
    return conn.execute(
        """
        SELECT accession_no, cik, form, filing_date FROM (
            SELECT DISTINCT ON (s.accession_no) s.accession_no, s.cik, s.form, s.filing_date
            FROM bronze.submission_filings s
            WHERE s.form = ANY(%(forms)s) AND s.filing_date >= %(since)s
              AND s.cik IN (SELECT k.cik FROM silver.company_ciks k WHERE EXISTS
                            (SELECT 1 FROM gold.quarterly_financials q
                             WHERE q.company_id = k.company_id))
              AND NOT EXISTS (SELECT 1 FROM bronze.ownership_documents o
                              WHERE o.accession_no = s.accession_no)
            ORDER BY s.accession_no, s.cik
        ) p
        ORDER BY filing_date DESC, accession_no DESC
        LIMIT %(limit)s
        """, {"forms": sorted(INSIDER_FORMS), "since": since, "limit": limit}).fetchall()


def backfill(conn: psycopg.Connection, client: httpx.Client, today: date, days: int,
             limit: int) -> int:
    """The bounded backfill: returns how many filings it stored."""
    todo = pending(conn, today - timedelta(days=days), limit)
    log.info("%d insider filings to fetch (last %d days, at most %d)", len(todo), days, limit)
    done = 0
    for i, (accession, cik, form, filed) in enumerate(todo, 1):
        try:
            done += fetch(conn, client, accession, cik, form, filed)
        except httpx.HTTPError as e:
            log.error("%s %s: %s", cik, accession, e)
        if i % 500 == 0:
            log.info("%d of %d done, %d stored", i, len(todo), done)
    return done


def main() -> None:
    configure_logging()
    days = int(os.environ.get("INSIDERS_DAYS", "90"))
    limit = int(os.environ.get("INSIDERS_LIMIT", "60000"))
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn, \
            sec_client(os.environ["SEC_USER_AGENT"]) as client:
        stored_n = backfill(conn, client, datetime.now(_EASTERN).date(), days, limit)
    log.info("insider backfill: %d filings stored", stored_n)


if __name__ == "__main__":
    main()
