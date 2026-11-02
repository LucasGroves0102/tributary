"""Completeness check against a real Postgres; skipped unless TEST_DATABASE_URL is set.
Every test runs inside a transaction that is rolled back."""

import os
from datetime import date
from pathlib import Path

import pytest

from poller import completeness
from poller.daily_index import parse_master

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

MASTER = (Path(__file__).parent / "fixtures" / "master.20261016.idx").read_bytes()
DAY = date(2026, 10, 16)
FORM4 = "0001213900-26-110436"  # insider 0002042022 and issuer 0002058414


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("DELETE FROM bronze.filings; DELETE FROM bronze.submission_filings;"
                  " DELETE FROM ops.filing_completeness")
        yield c


def store_feed(conn, accession, cik):
    conn.execute("INSERT INTO bronze.filings (accession_no, cik, form_type, filed_at, payload,"
                 " kafka_partition, kafka_offset) VALUES (%s, %s, '4', now(), '{}', 0, 0)"
                 " ON CONFLICT DO NOTHING", (accession, cik))


def store_history(conn, accession, cik):
    conn.execute("INSERT INTO bronze.submission_filings (accession_no, cik, form, filing_date)"
                 " VALUES (%s, %s, '4', %s)", (accession, cik, DAY))


def test_everything_missing_from_an_empty_database(conn):
    filings = parse_master(MASTER)
    absent = completeness.missing(conn, filings)
    assert [f.key for f in absent] == list(dict.fromkeys(f.key for f in filings))


def test_a_filer_row_in_either_bronze_table_counts_as_present(conn):
    filings = parse_master(MASTER)
    store_feed(conn, FORM4, "0002042022")      # the insider, from the live feed
    store_history(conn, FORM4, "0002058414")   # the issuer, from submissions.zip
    absent = {f.key for f in completeness.missing(conn, filings)}
    assert (FORM4, "0002042022") not in absent
    assert (FORM4, "0002058414") not in absent
    assert len(absent) == len({f.key for f in filings}) - 2


def test_one_missing_filer_of_two_is_republished_alone(conn):
    filings = parse_master(MASTER)
    store_feed(conn, FORM4, "0002042022")
    absent = [f for f in completeness.missing(conn, filings) if f.accession_no == FORM4]
    assert [f.key for f in absent] == [(FORM4, "0002058414")]


def test_record_counts_accessions_and_rows_and_overwrites_the_day(conn):
    filings = parse_master(MASTER)
    completeness.record(conn, DAY, filings, completeness.missing(conn, filings))
    for f in filings:
        if f.key != (FORM4, "0002058414"):
            store_feed(conn, f.accession_no, f.cik)
    completeness.record(conn, DAY, filings, completeness.missing(conn, filings))
    row = conn.execute("SELECT indexed, missing, republished FROM ops.filing_completeness_status"
                       " WHERE day = %s", (DAY,)).fetchone()
    assert row == (len({f.accession_no for f in filings}), 1, 1)
