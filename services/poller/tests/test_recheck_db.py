"""The hourly recheck against a real Postgres (migration 169); skipped unless TEST_DATABASE_URL
is set. Every test runs inside a transaction that is rolled back."""

import os
from datetime import datetime, timedelta

import pytest

from poller import completeness
from poller.daily_index import EASTERN
from poller.edgar import parse_page
from poller.recheck import record, run, window_start

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("DELETE FROM bronze.filings; DELETE FROM bronze.submission_filings;"
                  " DELETE FROM ops.filing_rechecks")
        yield c


def store(conn, accession, cik, filed_at, source="feed"):
    conn.execute("INSERT INTO bronze.filings (accession_no, cik, form_type, filed_at, payload,"
                 " kafka_partition, kafka_offset) VALUES (%s, %s, '4', %s,"
                 " jsonb_build_object('source', %s::text), 0, 0)",
                 (accession, cik, filed_at, source))


class FakeProducer:
    def __init__(self):
        self.published = []

    def publish(self, filing):
        self.published.append(filing)

    def flush(self, timeout=10.0):
        pass


def test_the_window_starts_on_the_last_weekday_with_filings(conn):
    store(conn, "9100000001-26-000001", "0000000001", "2026-11-20T21:30:00Z")  # Friday
    store(conn, "9100000001-26-000002", "0000000001", "2026-11-22T15:00:00Z")  # a Sunday stray
    monday = datetime(2026, 11, 23, 15, 0, tzinfo=EASTERN)
    assert window_start(conn, monday) == datetime(2026, 11, 20, tzinfo=EASTERN)
    # the day after Thanksgiving: Thursday had no filings, so Wednesday's evening is in
    store(conn, "9100000001-26-000003", "0000000001", "2026-11-25T22:00:00Z")
    friday = datetime(2026, 11, 27, 9, 0, tzinfo=EASTERN)
    assert window_start(conn, friday) == datetime(2026, 11, 25, tzinfo=EASTERN)


def test_a_run_republishes_the_rows_bronze_lacks_and_records_itself(conn, feed):
    listed = parse_page(feed().page(0, 100)).filings
    for f in listed[1:]:
        if f.key != listed[25].key:
            store(conn, f.accession_no, f.cik, f.filed_at)
    start = datetime.fromisoformat("2026-10-16T00:00:00-04:00")
    now = datetime.fromisoformat("2026-10-16T22:00:00-04:00")
    producer = FakeProducer()
    result = run(feed().client(), producer, start, now,
                 lambda filings: completeness.missing(conn, filings))
    assert [f.key for f in producer.published] == [listed[25].key, listed[0].key]
    assert {f.source for f in producer.published} == {"recheck"}
    record(conn, now, result)
    record(conn, now - timedelta(days=91), result)  # pruned by the next record
    record(conn, now + timedelta(hours=1), result)
    conn.execute("SET LOCAL ROLE ops_reader")
    rows = conn.execute("SELECT checked_at, complete, pages, listed, missing, republished"
                        " FROM ops.filing_recheck_status ORDER BY checked_at").fetchall()
    assert [r[1:] for r in rows] == [(False, 1, 40, 2, 2)] * 2
    assert rows[0][0] == now
