"""Runs against a real Postgres with migrations applied; skipped unless TEST_DATABASE_URL is set."""

import json
import os

import pytest

from bronze_loader.db import insert_filings
from bronze_loader.rows import to_row

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


def event(acc: str, cik: str) -> bytes:
    return json.dumps({
        "accession_no": acc, "cik": cik, "company_name": "Co", "role": "Issuer",
        "form_type": "8-K", "filed_at": "2026-10-16T12:00:00-04:00", "url": "u",
    }).encode()


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c:
        c.execute("DELETE FROM bronze.filings WHERE accession_no LIKE 'test-%'")
        c.commit()
        yield c
        c.execute("DELETE FROM bronze.filings WHERE accession_no LIKE 'test-%'")
        c.commit()


def test_insert_is_idempotent(conn):
    rows = [to_row(event("test-1", "1"), 0, 0), to_row(event("test-1", "2"), 0, 1)]
    assert insert_filings(conn, rows) == 2
    assert insert_filings(conn, rows) == 0  # replay after a crash: no duplicates
    count = conn.execute(
        "SELECT count(*) FROM bronze.filings WHERE accession_no = 'test-1'"
    ).fetchone()[0]
    assert count == 2


def test_payload_round_trips(conn):
    insert_filings(conn, [to_row(event("test-2", "1"), 0, 0)])
    payload = conn.execute(
        "SELECT payload FROM bronze.filings WHERE accession_no = 'test-2'"
    ).fetchone()[0]
    assert payload["company_name"] == "Co"
