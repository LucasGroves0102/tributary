"""Insider filings (#117): real NVIDIA Form 3/4/4/A full submissions (fetched 2026-11-09, gzip) →
bronze.ownership_documents → silver (migration 054's XMLTABLE parse). The DB tests run against a
migrated Postgres when TEST_DATABASE_URL is set; each is rolled back."""

import gzip
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from doc_fetcher import ownership
from doc_fetcher.main import Config, Coverage, drain_insiders

FIXTURES = Path(__file__).parent / "fixtures" / "ownership"
NVDA = "0001045810"
KRESS = "0001588670-26-000014"   # CFO: tax withholding, three sales, holdings by trusts
STEVENS = "0001199039-26-000014"  # director: seven sales by a trust
COXE = "0001197647-26-000013"    # director: a gift, 10b5-1
PARKER_3 = "0002152188-26-000003"  # Form 3, nothing held
PARKER_4A = "0002152188-26-000007"  # 4/A: an award


def raw(accession: str) -> bytes:
    return gzip.decompress((FIXTURES / f"{accession}.txt.gz").read_bytes())


def test_the_ownership_document_comes_out_of_the_full_submission():
    xml = ownership.ownership_xml(raw(KRESS))
    assert xml is not None and xml.startswith("<ownershipDocument>")
    assert xml.endswith("</ownershipDocument>") and "Kress Colette" in xml
    assert ownership.ownership_xml(b"<SEC-DOCUMENT>a paper filing's scan</SEC-DOCUMENT>") is None
    assert ownership.submission_url(NVDA, KRESS) == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000158867026000014/"
        "0001588670-26-000014.txt")
    # acceptance at 21:30 New York is 01:30 UTC the next day: the filing date is New York's
    assert ownership.filed_on("2026-09-19T01:30:00+00:00") == date(2026, 9, 18)


def test_a_namespaced_or_attributed_root_is_read():
    """The SEC's root is bare in practice; one with attributes, a default namespace or a prefix
    must still be found, and come out with unqualified names (silver's XPath reads those)."""
    bare = ownership.ownership_xml(raw(KRESS))
    body = bare.removeprefix("<ownershipDocument>").removesuffix("</ownershipDocument>")
    xsi = ('<ownershipDocument xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
           ' xsi:noNamespaceSchemaLocation="x.xsd">' + body + "</ownershipDocument>")
    assert ownership.ownership_xml(b"<XML>" + xsi.encode() + b"</XML>") == xsi  # as filed
    default = '<ownershipDocument xmlns="urn:sec:own">' + body + "</ownershipDocument>"
    assert ownership.ownership_xml(default.encode()) == bare
    prefixed = ('<o:ownershipDocument xmlns:o="urn:sec:own">'
                + body.replace("<", "<o:").replace("<o:/", "</o:")
                + "</o:ownershipDocument>")
    assert ownership.ownership_xml(prefixed.encode()) == bare
    assert ownership.ownership_xml(b"<ownershipDocumentation>x</ownershipDocumentation>") is None


def test_the_feed_line_under_the_issuer_is_the_one_taken():
    ciks = frozenset({NVDA})
    issuer = {"accession_no": KRESS, "cik": NVDA, "form_type": "4", "role": "Issuer",
              "source": "feed"}
    assert ownership.wanted(issuer, ciks)
    assert not ownership.wanted({**issuer, "role": "Reporting"}, ciks)  # the insider's own line
    assert not ownership.wanted({**issuer, "cik": "0000320193"}, ciks)  # not a company here
    assert not ownership.wanted({**issuer, "form_type": "10-Q"}, ciks)
    assert not ownership.wanted({**issuer, "source": "daily-index"}, ciks)  # the backfill's
    assert ownership.wanted({**issuer, "form_type": "4/A"}, ciks)
    assert Config.from_env({"FETCH_INSIDERS": "false"}).insiders is False
    assert Coverage(frozenset({"a"}), frozenset({"b"})).issuers == {"a", "b"}


DSN = os.environ.get("TEST_DATABASE_URL")
db = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def conn():
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(DSN, autocommit=True) as c:
        tx = c.transaction(force_rollback=True)
        tx.__enter__()
        try:
            c.execute("TRUNCATE bronze.ownership_documents CASCADE")
            c.execute("INSERT INTO silver.companies (company_id, name, current_cik) VALUES"
                      " (%s, 'NVIDIA CORP', %s) ON CONFLICT (company_id) DO NOTHING", (NVDA, NVDA))
            c.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)"
                      " ON CONFLICT DO NOTHING", (NVDA, NVDA))
            yield c
        finally:
            tx.__exit__(None, None, None)


def store_all(c) -> None:
    for acc in (KRESS, STEVENS, COXE, PARKER_3, PARKER_4A):
        form = "3" if acc == PARKER_3 else "4/A" if acc == PARKER_4A else "4"
        assert ownership.store(c, acc, NVDA, form, date(2026, 9, 20), "u",
                               ownership.ownership_xml(raw(acc))) == 1


@db
def test_silver_reads_owners_roles_and_every_line(conn):
    store_all(conn)
    owners = conn.execute("SELECT owners, aff10b5_one, period_of_report FROM silver.insider_filings"
                          " WHERE accession_no = %s", (KRESS,)).fetchone()
    assert owners[0] == [{"cik": "0001588670", "name": "Kress Colette", "director": False,
                          "officer": True, "title": "EVP & Chief Financial Officer",
                          "ten_percent": False, "other": False, "other_text": None}]
    assert owners[1:] == (True, date(2026, 9, 16))
    lines = conn.execute(
        "SELECT line, kind, code, shares, price, acquired_disposed, shares_after, direct_indirect,"
        " nature FROM silver.insider_transactions WHERE accession_no = %s ORDER BY line",
        (KRESS,)).fetchall()
    assert lines[0] == (1, "transaction", "F", Decimal("40747"), Decimal("212.17"), "D",
                        Decimal("800021"), "D", None)
    assert [x[2] for x in lines[:4]] == ["F", "S", "S", "S"]
    assert lines[4][:2] == (5, "holding") and lines[4][8] == "By Trust"
    assert len(lines) == 12
    gift = conn.execute("SELECT code, shares, price, nature FROM silver.insider_transactions"
                        " WHERE accession_no = %s AND line = 1", (COXE,)).fetchone()
    assert gift == ("G", Decimal("500000"), Decimal("0"), "By Trust")
    award = conn.execute("SELECT code, acquired_disposed, shares FROM silver.insider_transactions"
                         " WHERE accession_no = %s", (PARKER_4A,)).fetchall()
    assert award == [("A", "A", Decimal("160418"))]
    # a Form 3 that holds nothing is a filing with no lines
    assert conn.execute("SELECT count(*) FROM silver.insider_transactions WHERE accession_no = %s",
                        (PARKER_3,)).fetchone() == (0,)


@db
def test_storing_again_is_idempotent_and_bad_or_foreign_documents_stay_out(conn):
    store_all(conn)
    before = conn.execute("SELECT count(*) FROM silver.insider_transactions").fetchone()
    store_all(conn)
    assert conn.execute("SELECT count(*) FROM silver.insider_transactions").fetchone() == before
    # an issuer that isn't a company here, and XML that isn't well formed: bronze only, no error
    other = ownership.ownership_xml(raw(KRESS)).replace("0001045810", "0000000001")
    assert ownership.store(conn, "x-1", "0000000001", "4", date(2026, 9, 20), "u", other) == 0
    assert ownership.store(conn, "x-2", NVDA, "4", date(2026, 9, 20), "u",
                           "<ownershipDocument><issuer>") == 0
    assert conn.execute("SELECT count(*) FROM bronze.ownership_documents").fetchone() == (7,)


@db
def test_the_live_path_fetches_once(conn):
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, content=raw(KRESS))

    client = httpx.Client(transport=httpx.MockTransport(respond))
    event = {"accession_no": KRESS, "cik": NVDA, "form_type": "4", "role": "Issuer",
             "source": "feed", "filed_at": "2026-09-18T20:05:00+00:00"}
    ownership.enqueue(conn, event)
    ownership.enqueue(conn, event)
    assert drain_insiders(conn, client, 60.0) == 1
    ownership.enqueue(conn, event)  # the same filing again (its other line, a replay)
    assert drain_insiders(conn, client, 60.0) == 0  # stored: no second request
    assert requests == ["/Archives/edgar/data/1045810/000158867026000014/0001588670-26-000014.txt"]
    assert conn.execute("SELECT count(*) FROM ops.insider_queue").fetchone() == (0,)
    assert conn.execute("SELECT filed FROM bronze.ownership_documents").fetchone() == (
        date(2026, 9, 18),)


@db
def test_the_backfill_takes_operating_issuers_missing_filings_newest_first(conn):
    conn.execute("INSERT INTO gold.quarterly_financials (company_id, metric, period_start,"
                 " period_end, value, unit, value_source, source_tag, accession_no, filed,"
                 " source_accessions, ingested_at) VALUES (%s, 'revenue', '2026-04-27',"
                 " '2026-07-26', 1, 'USD', 'reported', 't', 'a', '2026-08-26', '{a}', now())",
                 (NVDA,))
    for acc, cik, filed in ((KRESS, NVDA, "2026-09-18"), (KRESS, "0001588670", "2026-09-18"),
                            (STEVENS, NVDA, "2026-09-08"), (COXE, NVDA, "2025-01-02")):
        conn.execute("INSERT INTO bronze.submission_filings (accession_no, cik, form,"
                     " filing_date) VALUES (%s, %s, '4', %s)", (acc, cik, filed))
    todo = ownership.pending(conn, date(2026, 1, 1), 10)
    assert [(a, c) for a, c, _, _ in todo] == [(KRESS, NVDA), (STEVENS, NVDA)]
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=raw(r.url.path.rsplit("/", 1)[-1][:-4]))))
    assert ownership.backfill(conn, client, date(2026, 11, 9), 365, 10) == 2
    assert ownership.pending(conn, date(2026, 1, 1), 10) == []


def kress_with(old: str, new: str) -> str:
    xml = ownership.ownership_xml(raw(KRESS))
    assert xml.count(old) >= 1
    return xml.replace(old, new, 1)


@db
def test_a_date_that_does_not_exist_is_null_with_a_finding(conn):
    """Review B3: silver.xml_date('2026-02-30') raised out of range, which aborted the filing's
    transaction and crash-looped the live consumer on the same event."""
    assert conn.execute("SELECT silver.xml_date('2026-02-30'), silver.xml_date('2026-09-16-05:00'),"
                        " silver.xml_date('0000-01-01'), silver.xml_date('n/a')").fetchone() == (
        None, date(2026, 9, 16), None, None)
    xml = kress_with("<periodOfReport>2026-09-16", "<periodOfReport>2026-02-30")
    xml = xml.replace("<transactionDate>\n                <value>2026-09-16",
                      "<transactionDate>\n                <value>2026-09-31", 1)
    assert ownership.store(conn, KRESS, NVDA, "4", date(2026, 9, 20), "u", xml) == 1
    assert conn.execute("SELECT period_of_report FROM silver.insider_filings").fetchone() == (None,)
    assert conn.execute("SELECT count(*) FROM silver.insider_transactions").fetchone() == (12,)
    finding = conn.execute("SELECT detail FROM ops.insider_findings WHERE accession_no = %s"
                           " AND kind = 'bad_date'", (KRESS,)).fetchone()[0]
    assert finding == "periodOfReport 2026-02-30; line 1 transactionDate 2026-09-31"
    assert conn.execute("SELECT transaction_date FROM silver.insider_transactions WHERE line = 1"
                        ).fetchone() == (None,)
    # storing it again with a good date clears the finding
    store_all(conn)
    assert conn.execute("SELECT count(*) FROM ops.insider_findings").fetchone() == (0,)


@db
def test_a_repeated_element_takes_the_first_with_a_finding(conn):
    """Review B3: a column path matching two nodes raised 'more than one value returned by column
    XPath expression'."""
    xml = kress_with("<transactionCode>F</transactionCode>",
                     "<transactionCode>F</transactionCode><transactionCode>S</transactionCode>")
    xml = xml.replace("<periodOfReport>2026-09-16</periodOfReport>",
                      "<periodOfReport>2026-09-16</periodOfReport>"
                      "<periodOfReport>2026-09-17</periodOfReport>")
    assert ownership.store(conn, KRESS, NVDA, "4", date(2026, 9, 20), "u", xml) == 1
    assert conn.execute("SELECT period_of_report FROM silver.insider_filings").fetchone() == (
        date(2026, 9, 16),)
    assert conn.execute("SELECT code FROM silver.insider_transactions WHERE line = 1").fetchone() \
        == ("F",)
    assert conn.execute("SELECT detail FROM ops.insider_findings WHERE kind = 'several_values'"
                        ).fetchone() == ("issuer, period or a reporting owner; lines 1",)


@db
def test_a_parse_that_fails_keeps_bronze_and_records_it(conn):
    """Whatever else Postgres refuses while parsing: the filing is stored in bronze alone with a
    parse_failed finding, and the caller goes on (the transaction rolls back only the parse)."""
    conn.execute("ALTER FUNCTION silver.refresh_insider_filings(text[])"
                 " RENAME TO refresh_insider_filings_real")
    conn.execute("CREATE FUNCTION silver.refresh_insider_filings(p text[]) RETURNS int"
                 " LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'more than one value'"
                 " USING ERRCODE = 'cardinality_violation'; END $$")
    xml = ownership.ownership_xml(raw(KRESS))
    assert ownership.store(conn, KRESS, NVDA, "4", date(2026, 9, 20), "u", xml) == 0
    assert conn.execute("SELECT count(*) FROM bronze.ownership_documents").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM silver.insider_filings").fetchone() == (0,)
    kind, detail = conn.execute("SELECT kind, detail FROM ops.insider_findings").fetchone()
    assert kind == "parse_failed" and detail.startswith("CardinalityViolation")


def queue(conn) -> dict:
    return {r[0]: r[1:] for r in conn.execute(
        "SELECT accession_no, status, attempts, not_before IS NOT NULL, last_error IS NOT NULL"
        " FROM ops.insider_queue ORDER BY accession_no")}


@db
def test_queued_feed_filings_are_drained_and_one_failure_never_stops_the_rest(conn):
    """Priority (10-K/Q/8-K first): the consumer only queues a Form 3/4/5, then drains the queue
    when idle. A 404 just after the feed waits for EDGAR, a 503 retries later, and the next one
    is still stored; a 404 a day after the feed is gone, kept as failed."""
    feed = {"cik": NVDA, "form_type": "4", "role": "Issuer", "source": "feed",
            "filed_at": "2026-09-18T20:05:00+00:00"}
    for acc in ("0000000001-26-000001", "0000000002-26-000002", KRESS):
        ownership.enqueue(conn, {**feed, "accession_no": acc})
        conn.execute("UPDATE ops.insider_queue SET queued_at = clock_timestamp()"
                     " WHERE accession_no = %s", (acc,))
    ownership.enqueue(conn, {**feed, "accession_no": KRESS})  # the feed again: queued once

    def respond(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if name.startswith("0000000001"):
            return httpx.Response(404)
        if name.startswith("0000000002"):
            return httpx.Response(503)
        return httpx.Response(200, content=raw(KRESS))

    stored_forms: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(respond))
    assert ownership.drain(conn, client, 60.0, on_stored=stored_forms.append) == 1
    assert stored_forms == ["4"]
    assert queue(conn) == {"0000000001-26-000001": ("pending", 1, True, True),
                           "0000000002-26-000002": ("pending", 1, True, True)}
    assert conn.execute("SELECT count(*) FROM silver.insider_filings").fetchone() == (1,)
    assert ownership.drain(conn, client, 60.0) == 0  # both wait for their backoff
    # a day on, the 404 is still there: gone, kept as failed (the 503 is still retried)
    conn.execute("UPDATE ops.insider_queue SET not_before = now(),"
                 " queued_at = now() - interval '25 hours'")
    assert ownership.drain(conn, client, 60.0) == 0
    assert queue(conn) == {"0000000001-26-000001": ("failed", 2, False, True),
                           "0000000002-26-000002": ("pending", 2, True, True)}


@db
def test_the_drain_stops_at_its_budget(conn):
    for acc in (KRESS, STEVENS):
        ownership.enqueue(conn, {"accession_no": acc, "cik": NVDA, "form_type": "4",
                                 "filed_at": "2026-09-18T20:05:00+00:00"})
    ticks = iter([0.0, 0.0, 99.0])
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=raw(r.url.path.rsplit("/", 1)[-1][:-4]))))
    assert ownership.drain(conn, client, 10.0, clock=lambda: next(ticks)) == 1
    assert len(queue(conn)) == 1


@db
def test_the_backfill_records_a_filing_it_cannot_store_and_goes_on(conn):
    """Review B3: the backfill caught only HTTP errors and worked newest first, so it died at the
    same filing every run. Now a refused filing is kept as failed and the run goes on; the next
    run tries it once more, after the filings never tried (pre-ship review)."""
    conn.execute("INSERT INTO gold.quarterly_financials (company_id, metric, period_start,"
                 " period_end, value, unit, value_source, source_tag, accession_no, filed,"
                 " source_accessions, ingested_at) VALUES (%s, 'revenue', '2026-04-27',"
                 " '2026-07-26', 1, 'USD', 'reported', 't', 'a', '2026-08-26', '{a}', now())",
                 (NVDA,))
    for acc, filed in ((KRESS, "2026-09-18"), (STEVENS, "2026-09-08")):
        conn.execute("INSERT INTO bronze.submission_filings (accession_no, cik, form,"
                     " filing_date) VALUES (%s, %s, '4', %s)", (acc, NVDA, filed))

    def respond(request: httpx.Request) -> httpx.Response:
        accession = request.url.path.rsplit("/", 1)[-1][:-4]
        body = raw(accession)
        if accession == KRESS:  # the newest: a NUL byte Postgres text refuses
            body = body.replace(b"Kress Colette", b"Kress\x00Colette")
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    assert ownership.backfill(conn, client, date(2026, 11, 9), 365, 10) == 1
    assert queue(conn)[KRESS][:2] == ("failed", 1)
    assert [a for a, *_ in ownership.pending(conn, date(2026, 1, 1), 10)] == [KRESS]
    assert ownership.backfill(conn, client, date(2026, 11, 9), 365, 10) == 0
    assert queue(conn)[KRESS][:2] == ("failed", 2)  # refused again, recorded, the run went on


def outage_client(failure: str, requests: list[str]) -> httpx.Client:
    def down(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if failure == "connect":
            raise httpx.ConnectError("connection refused", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(int(failure))
    return httpx.Client(transport=httpx.MockTransport(down))


def test_what_counts_as_transient():
    req = httpx.Request("GET", "https://www.sec.gov/x")

    def status(code: int, body: bytes = b"") -> httpx.HTTPStatusError:
        return httpx.HTTPStatusError("e", request=req,
                                     response=httpx.Response(code, content=body, request=req))
    assert all(ownership.transient(e) for e in (
        status(500), status(503), status(429),
        status(403, b"<h1>Request Rate Threshold Exceeded</h1>"),
        httpx.ConnectError("x", request=req), httpx.ReadTimeout("x", request=req)))
    assert not any(ownership.transient(e) for e in (
        status(404), status(403, b"undeclared automated tool"), status(400),
        ownership.Unstorable("no XML"), ValueError("x")))
    waits = [ownership.backoff(n) for n in range(12)]
    assert [w.total_seconds() / 60 for w in waits[:8]] == [2, 4, 8, 16, 32, 64, 128, 180]
    assert set(waits[7:]) == {ownership.RETRY_CAP}
    assert ownership.backoff(30000) == ownership.RETRY_CAP


@db
@pytest.mark.parametrize("outage, failure", [(timedelta(minutes=40), "connect"),
                                             (timedelta(minutes=40), "503"),
                                             (timedelta(hours=6), "timeout"),
                                             (timedelta(hours=6), "429")])
def test_an_outage_never_fails_a_queued_filing(conn, outage, failure):
    """Pre-ship review: after 5 attempts (2+4+8+16 minutes of backoff) any transient error failed a
    queued filing for good, and the catch-up skipped failed ones: an outage of about 30 minutes
    lost every Form 4 queued in it. Now the queue retries for as long as the outage lasts, one
    request per try, the wait capped at RETRY_CAP, and the filing lands once the SEC is back."""
    ownership.enqueue(conn, {"accession_no": KRESS, "cik": NVDA, "form_type": "4",
                             "filed_at": "2026-09-18T20:05:00+00:00"})
    requests: list[str] = []
    down = outage_client(failure, requests)
    elapsed = timedelta(0)
    while elapsed < outage:
        assert ownership.drain(conn, down, 60.0) == 0
        status, wait = conn.execute("SELECT status, not_before - now()"
                                    " FROM ops.insider_queue").fetchone()
        assert status == "pending" and timedelta(0) < wait <= ownership.RETRY_CAP
        assert ownership.drain(conn, down, 60.0) == 0  # inside its wait: no request
        elapsed += wait  # the wait passes
        conn.execute("UPDATE ops.insider_queue SET not_before = now(),"
                     " queued_at = queued_at - %s", (wait,))
    assert len(requests) == (5 if outage < timedelta(hours=1) else 8)
    assert queue(conn)[KRESS][:2] == ("pending", len(requests))
    up = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=raw(KRESS))))
    assert ownership.drain(conn, up, 60.0) == 1
    assert queue(conn) == {}
    assert conn.execute("SELECT count(*) FROM silver.insider_filings").fetchone() == (1,)


@db
def test_a_filing_failed_by_an_outage_or_edgars_lag_is_retaken_by_the_catch_up(conn):
    """A queued filing kept as failed (a 404 past its grace; or, before this fix, an outage)
    isn't taken by the live queue again, but the daily catch-up retakes it, after the filings it
    never tried, and stores it once EDGAR has it."""
    ownership.enqueue(conn, {"accession_no": KRESS, "cik": NVDA, "form_type": "4",
                             "filed_at": "2026-09-18T20:05:00+00:00"})
    conn.execute("UPDATE ops.insider_queue SET queued_at = now() - interval '25 hours'")
    requests: list[str] = []
    assert ownership.drain(conn, outage_client("404", requests), 60.0) == 0
    assert queue(conn)[KRESS][:2] == ("failed", 1)
    conn.execute("UPDATE ops.insider_queue SET not_before = now() - interval '1 day'")
    assert ownership.drain(conn, outage_client("404", requests), 60.0) == 0
    assert len(requests) == 1  # the live queue leaves it

    conn.execute("INSERT INTO gold.quarterly_financials (company_id, metric, period_start,"
                 " period_end, value, unit, value_source, source_tag, accession_no, filed,"
                 " source_accessions, ingested_at) VALUES (%s, 'revenue', '2026-04-27',"
                 " '2026-07-26', 1, 'USD', 'reported', 't', 'a', '2026-08-26', '{a}', now())",
                 (NVDA,))
    for acc, filed in ((KRESS, "2026-09-18"), (STEVENS, "2026-09-08")):
        conn.execute("INSERT INTO bronze.submission_filings (accession_no, cik, form,"
                     " filing_date) VALUES (%s, %s, '4', %s)", (acc, NVDA, filed))
    # never tried first, though older; then the failed one
    assert [a for a, *_ in ownership.pending(conn, date(2026, 1, 1), 10)] == [STEVENS, KRESS]
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=raw(r.url.path.rsplit("/", 1)[-1][:-4]))))
    assert ownership.backfill(conn, client, date(2026, 9, 25), 30, 10) == 2
    assert queue(conn) == {}
    assert ownership.pending(conn, date(2026, 1, 1), 10) == []
