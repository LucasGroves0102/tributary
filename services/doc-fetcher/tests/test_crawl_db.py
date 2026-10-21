"""The crawl queue in Postgres (db/migrations/020_doc_crawl.sql, doc_fetcher.crawl_db). Skipped
unless TEST_DATABASE_URL is set; every test is rolled back."""

import gzip
import os
from datetime import date, timedelta

import pytest

from doc_fetcher import crawl_db
from doc_fetcher.crawl_fetch import Fetched
from doc_fetcher.db import Blob
from doc_fetcher.index import Exhibit, FilingIndex

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

TRACKED, OPERATING, NO_GOLD = "9000000201", "9000000202", "9000000203"
SINCE = date(2021, 10, 20)
ITEMS = ["2.02", "7.01", "8.01"]
LEASE = timedelta(minutes=30)


def submission(conn, acc, cik, form, filed, items=None, primary="doc.htm"):
    conn.execute(
        "INSERT INTO bronze.submission_filings (accession_no, cik, form, filing_date,"
        " report_date, primary_document, items) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (acc, cik, form, filed, filed - timedelta(days=30), primary, items))


@pytest.fixture
def conn():
    with psycopg.connect(DSN, autocommit=True) as c, c.transaction(force_rollback=True):
        for cid, tracked in ((TRACKED, True), (OPERATING, False), (NO_GOLD, False)):
            c.execute("INSERT INTO silver.companies (company_id, name, current_cik, tracked)"
                      " VALUES (%s, %s, %s, %s)", (cid, f"Test {cid}", cid, tracked))
            c.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                      (cid, cid))
        for cid in (TRACKED, OPERATING):
            c.execute(
                "INSERT INTO gold.quarterly_financials (company_id, metric, period_start,"
                " period_end, value, unit, value_source, source_tag, accession_no, form, filed,"
                " source_accessions, ingested_at) VALUES (%s, 'revenue', '2026-01-01',"
                " '2026-03-31', 1, 'USD', 'reported', 'us-gaap:X', 'x', '10-Q', '2026-04-30',"
                " ARRAY['x'], now())", (cid,))
        yield c


def queued(conn):
    return {r[0]: r[1:] for r in conn.execute(
        "SELECT accession_no, cik, form, priority, status FROM ops.doc_crawl_targets"
        " WHERE accession_no LIKE 'tc-%'")}


def test_plan_selects_operating_companies_recent_filings(conn):
    s = submission
    s(conn, "tc-10k", TRACKED, "10-K", date(2026, 2, 1))
    s(conn, "tc-10q", OPERATING, "10-Q", date(2025, 8, 1))
    s(conn, "tc-10ka", OPERATING, "10-K/A", date(2025, 4, 1))
    s(conn, "tc-10qa", OPERATING, "10-Q/A", date(2024, 4, 1))
    s(conn, "tc-8k-results", OPERATING, "8-K", date(2025, 7, 30), items="2.02,9.01")
    s(conn, "tc-8k-unknown", OPERATING, "8-K", date(2025, 7, 30), items=None)
    s(conn, "tc-8k-officer", OPERATING, "8-K", date(2025, 7, 30), items="5.02,9.01")
    s(conn, "tc-form4", OPERATING, "4", date(2025, 7, 30))
    s(conn, "tc-old", OPERATING, "10-K", date(2021, 3, 1))
    s(conn, "tc-no-gold", NO_GOLD, "10-K", date(2026, 3, 1))
    s(conn, "tc-stored", OPERATING, "10-Q", date(2026, 5, 1))
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name,"
        " url, content_gzip, content_sha256, size_bytes) VALUES ('tc-stored', %s, '10-Q',"
        " '2026-05-01', 'd.htm', 'u', '\\x00', 'x', 1)", (OPERATING,))
    # one filing, two filers: queued once, under the tracked company
    s(conn, "tc-joint", OPERATING, "10-K", date(2026, 3, 2))
    s(conn, "tc-joint", TRACKED, "10-K", date(2026, 3, 2))

    assert crawl_db.plan(conn, SINCE, ITEMS) >= 7
    assert queued(conn) == {
        "tc-10k": (TRACKED, "10-K", 0, "pending"),
        "tc-10q": (OPERATING, "10-Q", 1, "pending"),
        "tc-10ka": (OPERATING, "10-K/A", 1, "pending"),
        "tc-10qa": (OPERATING, "10-Q/A", 1, "pending"),
        "tc-8k-results": (OPERATING, "8-K", 1, "pending"),
        "tc-8k-unknown": (OPERATING, "8-K", 1, "pending"),
        "tc-joint": (TRACKED, "10-K", 0, "pending"),
    }
    assert crawl_db.plan(conn, SINCE, ITEMS) == 0  # idempotent
    crawl_db.plan(conn, SINCE, None)  # every 8-K
    assert "tc-8k-officer" in queued(conn)
    assert conn.execute("SELECT planned_at IS NOT NULL FROM ops.doc_crawl_control").fetchone()[0]


def plan_three(conn):
    submission(conn, "tc-a", OPERATING, "10-Q", date(2026, 5, 1))
    submission(conn, "tc-b", OPERATING, "10-K", date(2026, 2, 1))
    submission(conn, "tc-c", TRACKED, "10-Q", date(2024, 5, 1))
    conn.execute("DELETE FROM ops.doc_crawl_targets")  # only this test's targets
    crawl_db.plan(conn, SINCE, ITEMS)


def test_claim_order_lease_and_backoff(conn):
    plan_three(conn)
    first = crawl_db.claim(conn, 2, LEASE)
    # tracked companies first, then most recent first
    assert [t.accession_no for t in first] == ["tc-c", "tc-a"]
    assert first[0].primary_document == "doc.htm"
    assert first[0].report_date == date(2024, 4, 1)
    assert [t.accession_no for t in crawl_db.claim(conn, 5, LEASE)] == ["tc-b"]
    assert crawl_db.claim(conn, 5, LEASE) == []

    # a crashed run's claims come back once the lease expires
    conn.execute("UPDATE ops.doc_crawl_targets SET claimed_at = now() - interval '1 hour'"
                 " WHERE accession_no = 'tc-a'")
    assert [t.accession_no for t in crawl_db.claim(conn, 5, LEASE)] == ["tc-a"]

    crawl_db.retry_later(conn, "tc-a", "ReadTimeout", timedelta(minutes=10))
    crawl_db.release(conn, ["tc-b"])
    assert [t.accession_no for t in crawl_db.claim(conn, 5, LEASE)] == ["tc-b"]
    conn.execute("UPDATE ops.doc_crawl_targets SET not_before = now() - interval '1 s'"
                 " WHERE accession_no = 'tc-a'")
    again = crawl_db.claim(conn, 5, LEASE)
    assert [(t.accession_no, t.attempts) for t in again] == [("tc-a", 1)]

    crawl_db.finish(conn, "tc-a", "failed", "HTTPStatusError: 404")
    crawl_db.finish(conn, "tc-b", "skipped", "Unusable: over 100 MB")
    assert crawl_db.remaining(conn) == 1  # tc-c, still claimed
    assert queued(conn)["tc-a"][3] == "failed"


def test_store_publish_and_ops_views(conn):
    plan_three(conn)
    targets = {t.accession_no: t for t in crawl_db.claim(conn, 5, LEASE)}
    doc = Blob.of(b"<html>10-Q</html>")
    index = FilingIndex(date(2026, 5, 1), None, "doc.htm", "https://sec/doc.htm")
    ex = Exhibit("EX-99.1", "PRESS RELEASE", "pr.htm", "https://sec/pr.htm")
    fetched = Fetched(index, doc, [(ex, Blob.of(b"<html>press</html>"))], 3)
    crawl_db.store(conn, targets["tc-a"], fetched)
    crawl_db.store(conn, targets["tc-a"], fetched)  # a retried batch: no duplicate rows

    stored = conn.execute("SELECT content_gzip, size_bytes FROM bronze.filing_documents"
                          " WHERE accession_no = 'tc-a'").fetchone()
    assert gzip.decompress(stored[0]) == b"<html>10-Q</html>"
    assert conn.execute("SELECT count(*) FROM bronze.filing_exhibits"
                        " WHERE accession_no = 'tc-a'").fetchone()[0] == 1

    events = crawl_db.unpublished(conn, 10)
    assert [e["accession_no"] for e in events] == ["tc-a"]
    assert events[0] == {"accession_no": "tc-a", "cik": OPERATING, "form": "10-Q",
                         "document_name": "doc.htm", "url": "https://sec/doc.htm",
                         "content_sha256": doc.sha256, "size_bytes": doc.size}
    crawl_db.mark_published(conn, ["tc-a"])
    assert crawl_db.unpublished(conn, 10) == []

    assert crawl_db.already_stored(conn, ["tc-a", "tc-b"]) == {"tc-a"}
    crawl_db.mark_already_stored(conn, ["tc-b"])
    crawl_db.finish(conn, "tc-c", "failed", "HTTPStatusError: 404")
    crawl_db.report(conn, "running", 300 * 10**9, 50 * 10**9)
    assert crawl_db.control(conn).paused is False

    conn.execute("SET LOCAL ROLE ops_reader")  # what Grafana and ops-exporter log in as
    progress = {r[0]: r[1:] for r in conn.execute(
        "SELECT form, targets, done, remaining, failed, files, raw_bytes, stored_bytes"
        " FROM ops.doc_crawl_progress WHERE year = 2026")}
    raw = doc.size + len(b"<html>press</html>")
    assert progress["10-Q"][:6] == (1, 1, 0, 0, 2, raw)
    assert progress["10-K"][:4] == (1, 1, 0, 0)  # already stored by doc-fetcher
    status = conn.execute(
        "SELECT state, disk_free_bytes, targets, done, remaining, failed, unpublished,"
        " raw_bytes, finished_last_hour, compression_ratio IS NOT NULL,"
        " projected_stored_bytes >= stored_bytes FROM ops.doc_crawl_status").fetchone()
    assert status == ("running", 300 * 10**9, 3, 2, 0, 1, 0, raw, 3, True, True)
    conn.execute("RESET ROLE")
