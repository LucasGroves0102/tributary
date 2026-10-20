"""ops freshness views (db/migrations/010_ops.sql). Skipped unless TEST_DATABASE_URL is set;
every test is rolled back."""

import json
import os

import pytest

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

VISA, MSFT, OTHER = "9000000101", "9000000102", "9000000103"  # OTHER is untracked


def filing(conn, acc, cik, form, filed_at, source="feed", ingested_at=None, role="Filer"):
    payload = {"accession_no": acc, "cik": cik, "role": role, "form_type": form,
               "filed_at": filed_at}
    if source:
        payload["source"] = source
    conn.execute(
        "INSERT INTO bronze.filings (accession_no, cik, form_type, filed_at, payload,"
        " kafka_partition, kafka_offset, ingested_at)"
        " VALUES (%s, %s, %s, %s, %s, 0, 0, coalesce(%s, now()))",
        (acc, cik, form, filed_at, json.dumps(payload), ingested_at))


def fact(conn, cik, acc, form, filed, ingested_at="2026-08-01T00:00:00Z"):
    conn.execute(
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed, ingested_at)"
        " VALUES (%s, 'revenue', 'Revenues', 'USD', '2026-01-01', '2026-03-31', 1, %s, %s,"
        " %s, %s)", (cik, acc, form, filed, ingested_at))


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        for cid, tracked in ((VISA, True), (MSFT, True), (OTHER, False)):
            c.execute("INSERT INTO silver.companies (company_id, name, current_cik, tracked)"
                      " VALUES (%s, %s, %s, %s)", (cid, f"Test {cid}", cid, tracked))
            c.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                      (cid, cid))
        yield c


def test_latency_per_live_filing(conn):
    acc = "test-ops-10q"
    filing(conn, acc, VISA, "10-Q", "2026-07-28T22:08:12Z",
           ingested_at="2026-07-28T22:08:42Z")
    filing(conn, acc, OTHER, "10-Q", "2026-07-28T22:08:12Z",
           ingested_at="2026-07-28T22:09:12Z", role="Filed by")
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name,"
        " url, content_gzip, content_sha256, size_bytes, fetched_at) VALUES (%s, %s, '10-Q',"
        " '2026-07-29', 'd.htm', 'u', '\\x00', 'x', 1, '2026-07-28T22:09:00Z')", (acc, VISA))
    for key, parsed_at in (("I-1", "2026-07-28T22:10:12Z"), ("I-2", "2026-07-28T22:10:30Z")):
        conn.execute(
            "INSERT INTO silver.filing_sections (accession_no, section_key, parser,"
            " parser_version, item, title, text, char_start, char_end, parsed_at)"
            " VALUES (%s, %s, 'python', 't', '1', 't', 'text', 0, 4, %s)",
            (acc, key, parsed_at))
    fact(conn, VISA, acc, "10-Q", "2026-07-29", ingested_at="2026-07-29T08:08:12Z")

    rows = conn.execute(
        "SELECT company_id, form, bronze_lag_s, document_at IS NOT NULL, sections_lag_s,"
        " facts_lag_s, queryable_lag_s FROM ops.filing_latency WHERE accession_no = %s",
        (acc,)).fetchall()
    assert rows == [(VISA, "10-Q", 30.0, True, 120.0, 36000.0, 120.0)]


def test_latency_excludes_backfill_and_skips_untracked_stages(conn):
    filing(conn, "test-ops-backfill", VISA, "8-K", "2026-07-28T04:00:00Z",
           source="daily-index")
    filing(conn, "test-ops-seed", VISA, "10-K", "2025-02-01T21:00:00Z", source=None)
    filing(conn, "test-ops-other", OTHER, "8-K", "2026-07-28T15:00:00Z",
           ingested_at="2026-07-28T15:00:20Z")

    rows = conn.execute(
        "SELECT accession_no, company_id, bronze_lag_s, queryable_lag_s"
        " FROM ops.filing_latency WHERE accession_no LIKE 'test-ops-%'").fetchall()
    assert rows == [("test-ops-other", None, 20.0, None)]


def test_filings_daily_counts_filings_not_filers(conn):
    filing(conn, "test-ops-4", VISA, "4", "2026-07-28T15:00:00Z", role="Issuer")
    filing(conn, "test-ops-4", OTHER, "4", "2026-07-28T15:00:00Z", role="Reporting")
    filing(conn, "test-ops-8k", OTHER, "8-K", "2026-07-28T23:00:00Z")  # 7pm ET: same day

    rows = conn.execute(
        "SELECT form, filings, tracked_filings FROM ops.filings_daily"
        " WHERE day = '2026-07-28' AND form IN ('4', '8-K') ORDER BY form").fetchall()
    assert rows == [("4", 1, 1), ("8-K", 1, 0)]


def test_companyfacts_lag_flags_filings_the_api_has_not_reflected(conn):
    # VISA: companyfacts stops at the April 10-Q; EDGAR has the July one
    fact(conn, VISA, "test-ops-v-q2", "10-Q", "2026-04-29")
    fact(conn, VISA, "test-ops-v-proxy", "DEF 14A", "2026-10-01")  # not a periodic report
    filing(conn, "test-ops-v-q2", VISA, "10-Q", "2026-04-28T20:05:00Z")
    filing(conn, "test-ops-v-q3", VISA, "10-Q", "2026-07-28T22:08:12Z")
    filing(conn, "test-ops-v-old", VISA, "10-K", "2014-11-14T21:00:00Z", source=None)
    conn.execute("INSERT INTO bronze.company_facts (cik, payload_sha256, payload, fetched_at,"
                 " last_checked_at) VALUES (%s, 'x', '{}', '2026-05-01', '2026-10-17')", (VISA,))
    # MSFT: up to date
    fact(conn, MSFT, "test-ops-m-q1", "10-Q", "2026-07-29")
    filing(conn, "test-ops-m-q1", MSFT, "10-Q", "2026-07-29T20:00:00Z")

    rows = {r[0]: r[1:] for r in conn.execute(
        "SELECT company_id, missing_filings, newest_missing_accession, facts_latest_accession,"
        " companyfacts_checked_at > oldest_missing_at, behind_seconds > 0"
        " FROM ops.companyfacts_lag WHERE company_id IN (%s, %s, %s)",
        (VISA, MSFT, OTHER))}
    assert rows == {
        VISA: (1, "test-ops-v-q3", "test-ops-v-q2", True, True),
        MSFT: (0, None, "test-ops-m-q1", None, False),
    }


def test_ops_reader_sees_views_only(conn):
    conn.execute("SET LOCAL ROLE ops_reader")
    for view in ("filing_latency", "filings_daily", "filings_daily_coverage",
                 "companyfacts_lag"):
        conn.execute(f"SELECT * FROM ops.{view} LIMIT 1")
    for table in ("bronze.filings", "bronze.filing_documents", "silver.filing_sections",
                  "gold.quarterly_financials"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
