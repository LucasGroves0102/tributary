"""Store.alert_filings over real Postgres (the filing alerts job's read of the public filing
index): runs as query_api without a user, one row per filing, 8-K items from the submissions
history (NULL for a live-feed filing it doesn't have yet), originals of the asked forms only,
newest first. Skipped unless TEST_DATABASE_URL is set; rolled back."""

import os
from contextlib import contextmanager
from datetime import date

import pytest

from query_api.alert_job import SYSTEM
from query_api.store import Store

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

C, D = "9200000001", "9200000002"


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def store():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        for cid in (C, D):
            conn.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                         " VALUES (%s, %s, %s)", (cid, f"Alert Test {cid}", cid))
            conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                         (cid, cid))
        rows = [  # (accession, cik, form, filed, period, items)
            (f"{C}-26-000001", C, "10-Q", "2026-11-19", "2026-09-30", None),
            (f"{C}-26-000002", C, "8-K", "2026-11-18", "2026-11-17", "2.02,9.01"),
            (f"{C}-26-000003", C, "10-Q/A", "2026-11-19", "2026-06-30", None),
            (f"{C}-26-000004", C, "4", "2026-11-19", None, None),
            (f"{C}-26-000005", C, "8-K", "2026-10-01", "2026-10-01", "5.02"),
            (f"{D}-26-000001", D, "10-K", "2026-11-19", "2026-09-30", None),
        ]
        for acc, cik, form, filed, period, items in rows:
            conn.execute("INSERT INTO bronze.submission_filings (accession_no, cik, form,"
                         " filing_date, report_date, items) VALUES (%s, %s, %s, %s, %s, %s)",
                         (acc, cik, form, filed, period, items))
        # an 8-K the live feed has and the nightly submissions load doesn't yet
        conn.execute("INSERT INTO bronze.filings (accession_no, cik, form_type, filed_at,"
                     " payload, kafka_partition, kafka_offset) VALUES (%s, %s, '8-K',"
                     " '2026-11-19T21:05:00Z', '{\"source\": \"feed\"}', 0, 0)",
                     (f"{C}-26-000009", C))
        yield Store(OneConnection(conn))


def test_alert_filings_reads_the_index_without_a_user(store):
    rows = store.alert_filings(SYSTEM, [C], ["10-K", "10-Q", "8-K"], date(2026, 11, 17))
    got = [(r["accession_no"], r["form"], r["period_end"], r["items"]) for r in rows]
    assert got == [
        (f"{C}-26-000009", "8-K", None, None),
        (f"{C}-26-000001", "10-Q", date(2026, 9, 30), None),
        (f"{C}-26-000002", "8-K", date(2026, 11, 17), ["2.02", "9.01"]),
    ]
    assert all(r["company_id"] == C and r["cik"] == C for r in rows)
    assert store.alert_filings(SYSTEM, [], ["10-K"], date(2026, 1, 1)) == []
    only_k = store.alert_filings(SYSTEM, [C, D], ["10-K"], date(2026, 11, 1))
    assert [r["accession_no"] for r in only_k] == [f"{D}-26-000001"]
