"""Insider filings (Forms 3, 4 and 5; #117): the SEC's ownershipDocument XML, read from the filing's
full submission text (`<accession>.txt`, one request), stored as filed in
bronze.ownership_documents and parsed into silver.insider_filings / silver.insider_transactions by
silver.refresh_insider_filings (migrations 054, 059), in one transaction per filing. Idempotent on
the accession: storing a filing again replaces its XML and re-derives its silver rows.

Three ways in, all through libs/sec (User-Agent, shared rate limit):
- the live doc-fetcher (`main.py`): each new Form 3/4/5 in the feed whose filer is an operating or
  tracked company and isn't the reporting owner (the feed lists a Form 4 once per filer) is queued
  in ops.insider_queue (`enqueue`) and fetched by `drain` when the consumer is idle, so 10-Ks,
  10-Qs and 8-Ks go first;
- `doc-fetcher-insiders` (`backfill`): the Forms 3/4/5 of operating companies in the submissions
  index over the last INSIDERS_DAYS days that bronze doesn't have, newest first, at most
  INSIDERS_LIMIT a run, at the caller's SEC class (Helm: the daily `insiders-catchup`, 7 days, and
  the one-off `insiders-backfill`, 90).

One filing never stops the others (review B3), and an outage never loses one (pre-ship review):
- a parse that fails leaves the filing in bronze with an `ops.insider_findings` row
  (`parse_failed`) and no silver rows;
- a transient error (no connection, a timeout, a 5xx, a 429 or the SEC's rate-limit 403:
  `transient`) never fails a filing: the queue retries it with a wait that doubles from
  RETRY_BASE up to RETRY_CAP and then repeats, however long the outage lasts; the catch-up and
  the backfill log it and take the filing again on their next run;
- a 404 within NOT_FOUND_GRACE of the feed queueing the filing is EDGAR lagging the feed, retried
  like a transient error; after that it counts as gone;
- a filing that can't be stored (gone from EDGAR, no ownershipDocument, a value Postgres
  refuses), or that hit another error MAX_ATTEMPTS times, is kept in ops.insider_queue as `failed`
  with its error. The live queue never takes it again; the daily catch-up (and the backfill)
  retakes it once a run while its filing date is inside the run's window, after every filing
  never tried."""

import hashlib
import logging
import os
import re
import time
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import psycopg

from tributary_sec import sec_client
from tributary_sec.client import is_throttle
from tributary_telemetry import configure_logging

INSIDER_FORMS = frozenset({"3", "4", "5", "3/A", "4/A", "5/A"})
# The root as filed: bare, with attributes (xmlns:xsi ...), or namespaced (a prefix, default xmlns)
_DOCUMENT = re.compile(
    rb"<(?:([A-Za-z_][\w.-]*):)?ownershipDocument(?=[\s>/])[^>]*>.*?</(?:\1:)?ownershipDocument\s*>",
    re.S)
_EASTERN = ZoneInfo("America/New_York")
MAX_ATTEMPTS = 5          # a queued filing's other (not transient) errors before it's kept failed
RETRY_BASE = timedelta(minutes=2)      # the first retry's wait; it doubles each attempt ...
RETRY_CAP = timedelta(hours=3)         # ... up to this, then repeats: an outage never fails one
NOT_FOUND_GRACE = timedelta(hours=24)  # a 404 this soon after the feed queued it: EDGAR lagging

log = logging.getLogger("doc_fetcher.ownership")


class Unstorable(Exception):
    """A filing no attempt will store: gone from EDGAR, or without an ownershipDocument."""


class NotOnEdgar(Unstorable):
    """A 404: gone, or not there yet (EDGAR can lag the feed: NOT_FOUND_GRACE)."""


def transient(error: BaseException) -> bool:
    """An error a later attempt gets past: no connection, a timeout, the SEC down (5xx) or
    throttling (429, its rate-limit 403). Never a reason to fail a filing."""
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500 or is_throttle(error.response)
    return isinstance(error, httpx.TransportError)


def submission_url(cik: str, accession: str) -> str:
    """The filing's full submission text: every document of it, the XML included."""
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{accession}.txt")


def ownership_xml(raw: bytes) -> str | None:
    """The <ownershipDocument> element of a full submission, as filed; None when it has none.
    A namespace on the root (a prefix, or a default xmlns) is taken off the elements that carry
    it, since silver's XPath reads unqualified names; anything else stays as filed."""
    m = _DOCUMENT.search(raw)
    if m is None:
        return None
    try:
        xml = m.group(0).decode("utf-8")
    except UnicodeDecodeError:
        xml = m.group(0).decode("latin-1")
    return _unqualified(xml, m.group(1).decode() if m.group(1) else None)


def _unqualified(xml: str, prefix: str | None) -> str:
    root_end = xml.index(">") + 1
    root, rest = xml[:root_end], xml[root_end:]
    if prefix is not None:
        p = re.escape(prefix)
        root = re.sub(rf"\s+xmlns:{p}\s*=\s*(\"[^\"]*\"|'[^']*')", "", root)
        root, rest = (re.sub(rf"(</?){p}:", r"\1", s) for s in (root, rest))
        root, rest = (re.sub(rf"(\s){p}:([\w.-]+\s*=)", r"\1\2", s) for s in (root, rest))
    # a default namespace puts every unprefixed element in it: drop the declaration(s)
    if re.search(r"\sxmlns\s*=", xml):
        root, rest = (re.sub(r"\s+xmlns\s*=\s*(\"[^\"]*\"|'[^']*')", "", s) for s in (root, rest))
    return root + rest


def filed_on(filed_at: str) -> date:
    """EDGAR's filing date (US Eastern) of an acceptance time."""
    return datetime.fromisoformat(filed_at.replace("Z", "+00:00")).astimezone(_EASTERN).date()


def stored(conn: psycopg.Connection, accession: str) -> bool:
    return conn.execute("SELECT 1 FROM bronze.ownership_documents WHERE accession_no = %s",
                        (accession,)).fetchone() is not None


_BRONZE = (
    "INSERT INTO bronze.ownership_documents (accession_no, cik, form, filed, url, xml,"
    " content_sha256) VALUES (%s, %s, %s, %s, %s, %s, %s)"
    " ON CONFLICT (accession_no) DO UPDATE SET cik = EXCLUDED.cik, form = EXCLUDED.form,"
    " filed = EXCLUDED.filed, url = EXCLUDED.url, xml = EXCLUDED.xml,"
    " content_sha256 = EXCLUDED.content_sha256, fetched_at = now()")
_DEQUEUE = "DELETE FROM ops.insider_queue WHERE accession_no = %s"


def store(conn: psycopg.Connection, accession: str, cik: str, form: str, filed: date, url: str,
          xml: str) -> int:
    """Bronze, then silver, in one transaction; the number of silver filings (0: the issuer
    isn't a company here, the XML isn't well formed, or parsing failed). When parsing raises,
    the filing is stored in bronze alone with a `parse_failed` finding (one bad filing must not
    stop the consumer or the backfill); a connection error still raises."""
    params = (accession, cik, form, filed, url, xml, hashlib.sha256(xml.encode()).hexdigest())
    try:
        with conn.transaction():
            conn.execute(_BRONZE, params)
            row = conn.execute("SELECT silver.refresh_insider_filings(ARRAY[%s])",
                               (accession,)).fetchone()
            conn.execute(_DEQUEUE, (accession,))
        return row[0] if row else 0
    except psycopg.OperationalError:
        raise
    except psycopg.DatabaseError as e:
        error = f"{type(e).__name__}: {e}"
    log.error("%s %s %s: parse failed, stored in bronze only: %s", cik, form, accession, error,
              extra={"cik": cik, "form": form, "accession_no": accession})
    with conn.transaction():
        conn.execute(_BRONZE, params)
        conn.execute("DELETE FROM silver.insider_filings WHERE accession_no = %s", (accession,))
        conn.execute(
            "INSERT INTO ops.insider_findings (accession_no, kind, detail)"
            " VALUES (%s, 'parse_failed', left(%s, 500))"
            " ON CONFLICT (accession_no, kind) DO UPDATE SET detail = EXCLUDED.detail,"
            " found_at = now()", (accession, error))
        conn.execute(_DEQUEUE, (accession,))
    return 0


def fetch(conn: psycopg.Connection, client: httpx.Client, accession: str, cik: str, form: str,
          filed: date) -> bool:
    """One filing: fetch, store, parse. Raises Unstorable when no attempt ever will (gone, no
    XML), httpx errors when the SEC didn't answer, psycopg errors when bronze refused it."""
    url = submission_url(cik, accession)
    resp = client.get(url)
    if resp.status_code == 404:
        raise NotOnEdgar("not on EDGAR (404)")
    resp.raise_for_status()
    xml = ownership_xml(resp.content)
    if xml is None:
        raise Unstorable("no ownershipDocument (a paper filing?)")
    store(conn, accession, cik, form, filed, url, xml)
    log.info("%s %s %s: insider filing stored", cik, form, accession,
             extra={"cik": cik, "form": form, "accession_no": accession})
    return True


def wanted(event: dict, ciks: Iterable[str] | frozenset[str]) -> bool:
    """A live feed event the doc-fetcher stores: a Form 3/4/5 under its issuer's CIK (not the
    reporting owner's line), for a company in `ciks`."""
    return (event["form_type"] in INSIDER_FORMS and event.get("role") != "Reporting"
            and event.get("source", "feed") == "feed" and event["cik"] in ciks)


def enqueue(conn: psycopg.Connection, event: dict) -> None:
    """Queue a live feed filing for `drain` (once: a queued or failed accession stays as it is)."""
    conn.execute(
        "INSERT INTO ops.insider_queue (accession_no, cik, form, filed, source)"
        " VALUES (%s, %s, %s, %s, 'feed') ON CONFLICT (accession_no) DO NOTHING",
        (event["accession_no"], event["cik"], event["form_type"], filed_on(event["filed_at"])))


def give_up(conn: psycopg.Connection, accession: str, cik: str, form: str, filed: date,
            error: str, source: str = "catch-up") -> None:
    """Keep a filing no attempt will store as failed, with its error: the live queue never
    fetches it again; the catch-up retakes it once a run while it is inside its window."""
    conn.execute(
        "INSERT INTO ops.insider_queue (accession_no, cik, form, filed, source, status, attempts,"
        " last_error) VALUES (%s, %s, %s, %s, %s, 'failed', 1, left(%s, 500))"
        " ON CONFLICT (accession_no) DO UPDATE SET status = 'failed',"
        " attempts = ops.insider_queue.attempts + 1, last_error = EXCLUDED.last_error,"
        " not_before = NULL", (accession, cik, form, filed, source, error))


def backoff(attempts: int) -> timedelta:
    """The wait after a queued filing's `attempts`-th failed try (0 = the first): RETRY_BASE
    doubling, at most RETRY_CAP (2, 4, 8 ... 128 minutes, then every 3 hours)."""
    return min(RETRY_BASE * 2 ** min(attempts, 16), RETRY_CAP)


def _retry_later(conn: psycopg.Connection, accession: str, attempts: int,
                 error: BaseException) -> None:
    """Try a queued filing again after `backoff`. A transient error, or a 404 inside its grace,
    keeps it pending however long the outage lasts; any other error fails it at MAX_ATTEMPTS."""
    conn.execute(
        "UPDATE ops.insider_queue SET attempts = least(attempts + 1, 32000),"
        " last_error = left(%s, 500),"
        " status = CASE WHEN NOT %s AND attempts + 1 >= %s THEN 'failed' ELSE 'pending' END,"
        " not_before = now() + %s::interval WHERE accession_no = %s",
        (f"{type(error).__name__}: {error}", transient(error) or isinstance(error, NotOnEdgar),
         MAX_ATTEMPTS, backoff(attempts), accession))


def attempt(conn: psycopg.Connection, client: httpx.Client, accession: str, cik: str, form: str,
            filed: date, fresh: bool = False) -> bool:
    """`fetch` for one queued or backfilled filing, its failure recorded instead of raised: an
    unstorable filing or one bronze refuses is kept as failed. Transient errors (SEC, network)
    raise for the caller to retry; so does a lost database connection, and a 404 while `fresh`
    (the feed queued it under NOT_FOUND_GRACE ago: EDGAR can lag the feed)."""
    try:
        return fetch(conn, client, accession, cik, form, filed)
    except (httpx.HTTPError, psycopg.OperationalError):
        raise
    except (Unstorable, psycopg.DatabaseError, ValueError) as e:
        if fresh and isinstance(e, NotOnEdgar):
            raise
        log.warning("%s %s %s: not stored: %s", cik, form, accession, e,
                    extra={"cik": cik, "form": form, "accession_no": accession})
        give_up(conn, accession, cik, form, filed, f"{type(e).__name__}: {e}")
        return False


def drain(conn: psycopg.Connection, client: httpx.Client, budget: float,
          clock: Callable[[], float] = time.monotonic,
          on_stored: Callable[[str], object] = lambda form: None) -> int:
    """Fetch queued live filings, oldest first, until the queue is empty or `budget` seconds
    have passed (a started fetch finishes). Returns how many were stored."""
    deadline, done = clock() + budget, 0
    while clock() < deadline:
        row = conn.execute(
            "SELECT accession_no, cik, form, filed, attempts,"
            "       queued_at > now() - %s::interval AS fresh FROM ops.insider_queue"
            " WHERE status = 'pending' AND coalesce(not_before, '-infinity') <= now()"
            " ORDER BY queued_at, accession_no LIMIT 1", (NOT_FOUND_GRACE,)).fetchone()
        if row is None:
            break
        accession, cik, form, filed, attempts, fresh = row
        if stored(conn, accession):
            conn.execute(_DEQUEUE, (accession,))
            continue
        try:
            if attempt(conn, client, accession, cik, form, filed, fresh=fresh):
                done += 1
                on_stored(form)
        except psycopg.OperationalError:
            raise
        except Exception as e:  # noqa: BLE001 - one filing must never stop the consumer (B3)
            log.warning("%s %s %s: retry later: %s", cik, form, accession, e,
                        extra={"cik": cik, "form": form, "accession_no": accession})
            _retry_later(conn, accession, attempts, e)
    return done


def pending(conn: psycopg.Connection, since: date, limit: int) -> list[tuple[str, str, str, date]]:
    """Forms 3/4/5 of operating companies filed since `since` that bronze doesn't have, newest
    first, those kept as failed after every one never tried (a run retakes each once: a failure
    an outage or EDGAR's lag caused still lands, and one that really can't be stored costs one
    request a run while it's inside the window): (accession, issuer CIK, form, filed). One row
    per accession."""
    return conn.execute(
        """
        SELECT accession_no, cik, form, filing_date FROM (
            SELECT DISTINCT ON (s.accession_no) s.accession_no, s.cik, s.form, s.filing_date,
                   EXISTS (SELECT 1 FROM ops.insider_queue f
                           WHERE f.accession_no = s.accession_no AND f.status = 'failed')
                       AS failed
            FROM bronze.submission_filings s
            WHERE s.form = ANY(%(forms)s) AND s.filing_date >= %(since)s
              AND s.cik IN (SELECT k.cik FROM silver.company_ciks k WHERE EXISTS
                            (SELECT 1 FROM gold.quarterly_financials q
                             WHERE q.company_id = k.company_id))
              AND NOT EXISTS (SELECT 1 FROM bronze.ownership_documents o
                              WHERE o.accession_no = s.accession_no)
            ORDER BY s.accession_no, s.cik
        ) p
        ORDER BY failed, filing_date DESC, accession_no DESC
        LIMIT %(limit)s
        """, {"forms": sorted(INSIDER_FORMS), "since": since, "limit": limit}).fetchall()


def backfill(conn: psycopg.Connection, client: httpx.Client, today: date, days: int,
             limit: int) -> int:
    """The bounded backfill (and the daily catch-up): returns how many filings it stored. One
    filing's failure is recorded or logged and the run goes on (review B3); a transient error is
    only logged, and the next run takes the filing again."""
    todo = pending(conn, today - timedelta(days=days), limit)
    log.info("%d insider filings to fetch (last %d days, at most %d)", len(todo), days, limit)
    done = failed = 0
    for i, (accession, cik, form, filed) in enumerate(todo, 1):
        try:
            done += attempt(conn, client, accession, cik, form, filed)
        except psycopg.OperationalError:
            raise
        except Exception as e:  # noqa: BLE001 - transient: the next run takes it again
            failed += 1
            log.error("%s %s: %s", cik, accession, e)
        if i % 500 == 0:
            log.info("%d of %d done, %d stored, %d errors", i, len(todo), done, failed)
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
