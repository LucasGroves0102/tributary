"""Bulk submissions load against a real Postgres; skipped unless TEST_DATABASE_URL is set.
Every test runs inside a transaction that is rolled back."""

import json
import os
from datetime import UTC, date, datetime

import pytest

from company_loader.build import build
from company_loader.bulk import run
from company_loader.db import load

from .conftest import write_zip

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

APPLE, INSIDER = "0000320193", "0001801661"
NIGHT1, NIGHT2 = datetime(2026, 10, 17, 7, tzinfo=UTC), datetime(2026, 10, 18, 7, tzinfo=UTC)


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("DELETE FROM bronze.submission_filings; DELETE FROM bronze.submissions;"
                  " DELETE FROM bronze.submissions_loads; DELETE FROM bronze.filings;"
                  " DELETE FROM silver.company_aliases; DELETE FROM silver.company_ciks;"
                  " DELETE FROM silver.companies")
        c.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                  " VALUES (%s, 'Apple Inc.', %s)", (APPLE, APPLE))
        c.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                  (APPLE, APPLE))
        yield c


def load_zip(conn, path, snapshot, max_ciks=500):
    with path.open("rb") as f:
        return run(conn, f, snapshot, max_ciks)


def count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def test_loads_history_and_metadata(conn, bulk_zip):
    counts = load_zip(conn, bulk_zip, NIGHT1)
    assert counts | {"filings": 0} == {"scanned": 3, "unchanged": 0, "loaded": 3, "filings": 0,
                                       "companies_changed": 1, "complete": True}
    assert count(conn, "SELECT count(*) FROM bronze.submission_filings WHERE cik = %s",
                 APPLE) == 5
    apple = conn.execute(
        "SELECT sic, sic_description, tickers, exchanges, fiscal_year_end,"
        " state_of_incorporation, filer_categories, entity_type FROM silver.companies"
        " WHERE company_id = %s", (APPLE,)).fetchone()
    assert apple == ("3571", "Electronic Computers", ["AAPL"], ["Nasdaq"], "0926", "CA",
                     ["Large accelerated filer"], "operating")
    names = conn.execute("SELECT alias, valid_from FROM silver.company_aliases"
                         " WHERE cik = %s ORDER BY alias", (APPLE,)).fetchall()
    assert ("APPLE COMPUTER INC", date(1994, 1, 26)) in names and len(names) == 4
    assert count(conn, "SELECT finished_at IS NOT NULL FROM bronze.submissions_loads"
                       " WHERE snapshot = %s", NIGHT1)


def test_rerun_is_a_no_op(conn, bulk_zip):
    load_zip(conn, bulk_zip, NIGHT1)
    assert load_zip(conn, bulk_zip, NIGHT1)["loaded"] == 0
    assert load_zip(conn, bulk_zip, NIGHT2)["unchanged"] == 3  # new snapshot, same content
    assert count(conn, "SELECT count(*) FROM silver.company_aliases") == 4


def test_runs_are_bounded_and_resume(conn, bulk_zip):
    first = load_zip(conn, bulk_zip, NIGHT1, max_ciks=2)
    assert (first["loaded"], first["complete"]) == (2, False)
    assert count(conn, "SELECT finished_at FROM bronze.submissions_loads") is None
    second = load_zip(conn, bulk_zip, NIGHT1, max_ciks=2)
    assert (second["loaded"], second["unchanged"], second["complete"]) == (1, 2, True)
    assert count(conn, "SELECT runs FROM bronze.submissions_loads") == 2
    assert count(conn, "SELECT ciks_loaded FROM bronze.submissions_loads") == 3


def test_changed_cik_converges(conn, tmp_path, bulk_entries):
    load_zip(conn, write_zip(tmp_path / "1.zip", bulk_entries), NIGHT1)
    apple = json.loads(bulk_entries["CIK0000320193.json"])
    recent = apple["filings"]["recent"]
    for col in recent.values():
        del col[0]  # the Form 144 is gone
    recent["form"][0] = "10-K/A"  # and the 10-K was amended in place
    apple["exchanges"] = ["Nasdaq", "NYSE", "Nasdaq"]
    changed = {**bulk_entries, "CIK0000320193.json": json.dumps(apple).encode()}
    counts = load_zip(conn, write_zip(tmp_path / "2.zip", changed), NIGHT2)
    assert (counts["loaded"], counts["unchanged"]) == (1, 2)
    forms = conn.execute("SELECT form FROM bronze.submission_filings WHERE cik = %s"
                         " ORDER BY filing_date DESC", (APPLE,)).fetchall()
    assert [f for (f,) in forms][:2] == ["8-K", "10-K/A"] and len(forms) == 4
    assert count(conn, "SELECT exchanges FROM silver.companies") == ["Nasdaq", "NYSE"]


def test_filings_view_prefers_the_live_feed(conn, bulk_zip):
    load_zip(conn, bulk_zip, NIGHT1)
    feed_time = datetime(2026, 10, 17, 1, 56, 48, tzinfo=UTC)
    for acc, cik, filed_at, source in (
        ("0001801661-26-000124", INSIDER, feed_time, "feed"),
        ("0000320193-26-000018", APPLE, datetime(2026, 1, 29, 5, tzinfo=UTC), "daily-index"),
    ):
        conn.execute(
            "INSERT INTO bronze.filings (accession_no, cik, form_type, filed_at, payload,"
            " kafka_partition, kafka_offset) VALUES (%s, %s, '4', %s, %s, 0, 0)",
            (acc, cik, filed_at, json.dumps({"source": source})))
    rows = {acc: (source, accepted) for acc, source, accepted in conn.execute(
        "SELECT accession_no, source, accepted_at FROM silver.filings")}
    assert len(rows) == 6  # no duplicates across sources
    assert rows["0001801661-26-000124"] == ("feed", feed_time)
    # the history's acceptance time matches the feed's for the same filing
    assert count(conn, "SELECT accepted_at FROM bronze.submission_filings"
                       " WHERE accession_no = '0001801661-26-000124'") == feed_time
    source, accepted = rows["0000320193-26-000018"]
    assert source == "daily-index" and accepted.minute != 0  # from the history, not midnight
    assert {s for s, _ in rows.values()} == {"feed", "daily-index", "submissions"}


def test_company_loader_keeps_bulk_metadata(conn, bulk_zip, inputs):
    load_zip(conn, bulk_zip, NIGHT1)
    load(conn, build(**{**inputs, "submissions": {}}), date(2026, 10, 18))
    assert conn.execute("SELECT sic, fiscal_year_end FROM silver.companies"
                        " WHERE company_id = %s", (APPLE,)).fetchone() == ("3571", "0926")
    assert count(conn, "SELECT count(*) FROM silver.company_aliases"
                       " WHERE cik = %s AND source = 'sec_submissions'", APPLE) == 4
