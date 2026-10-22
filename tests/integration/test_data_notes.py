"""The restatement check and the data-notes grants (db/migrations/023_data_notes.sql).
Skipped unless TEST_DATABASE_URL is set; every test is rolled back."""

import os
from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

CO = "9000000231"
Q_START, Q_END = date(2025, 1, 1), date(2025, 3, 31)


def fact(conn, accession, filed, value, tag="Revenues", unit="USD", start=Q_START,
         metric="revenue"):
    conn.execute(
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '10-Q',"
        " %s)", (CO, metric, tag, unit, start, Q_END, value, accession, filed))


def gold(conn, accession, filed, value, source="reported", metric="revenue", start=Q_START,
         unit="USD"):
    conn.execute(
        "INSERT INTO gold.quarterly_financials (company_id, metric, period_start, period_end,"
        " value, unit, value_source, source_tag, accession_no, form, filed, source_accessions,"
        " ingested_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 'us-gaap:Revenues', %s, '10-Q', %s,"
        " ARRAY[%s], now())", (CO, metric, start, Q_END, value, unit, source, accession, filed,
                               accession))


def check(conn):
    conn.execute("SELECT ops.check_financials(%s::text[])", ([CO],))
    return conn.execute(
        "SELECT metric, period_end, severity, detail FROM ops.quality_findings"
        " WHERE company_id = %s AND check_name = 'restatement'", (CO,)).fetchall()


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                  " VALUES (%s, 'Test restated', %s)", (CO, CO))
        c.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)", (CO, CO))
        yield c


def test_restated_quarter_is_an_info_finding(conn):
    fact(conn, "orig", date(2025, 5, 1), 100)
    fact(conn, "restated", date(2026, 5, 1), 110)
    gold(conn, "restated", date(2026, 5, 1), 110)
    [(metric, end, severity, detail)] = check(conn)
    assert (metric, end, severity) == ("revenue", Q_END, "info")
    assert detail == {"original_accession": "orig", "original_filed": "2025-05-01",
                      "restated_accession": "restated", "restated_filed": "2026-05-01",
                      "change": 0.1}
    # info doesn't count as an error or a warning
    assert conn.execute("SELECT errors, warnings FROM ops.quality_companies"
                        " WHERE company_id = %s", (CO,)).fetchone() == (0, 0)
    assert conn.execute("SELECT findings FROM ops.quality_summary"
                        " WHERE check_name = 'restatement'").fetchone()[0] >= 1


@pytest.mark.parametrize("case", ["same_value", "tiny_change", "other_tag", "derived",
                                  "first_filing", "zero_original"])
def test_no_restatement(conn, case):
    original = {"tiny_change": 109.9, "zero_original": 0}.get(case, 110)
    fact(conn, "orig", date(2025, 5, 1), original,
         tag="SalesRevenueNet" if case == "other_tag" else "Revenues")
    fact(conn, "later", date(2026, 5, 1), 110)
    if case == "first_filing":
        gold(conn, "orig", date(2025, 5, 1), 110)
    else:
        gold(conn, "later", date(2026, 5, 1), 110,
             source="derived" if case == "derived" else "reported")
    assert check(conn) == []


def test_restated_balance_sheet_instant(conn):
    fact(conn, "orig", date(2025, 5, 1), 50, tag="Revenues", start=None, metric="cash")
    fact(conn, "later", date(2026, 5, 1), 40, tag="Revenues", start=None, metric="cash")
    gold(conn, "later", date(2026, 5, 1), 40, metric="cash", start=None)
    [(metric, _, _, detail)] = check(conn)
    assert (metric, detail["change"]) == ("cash", -0.2)


def test_findings_readable_by_query_api_only_under_rls(conn):
    fact(conn, "orig", date(2025, 5, 1), 100)
    gold(conn, "restated", date(2026, 5, 1), 110)
    check(conn)
    conn.execute("DELETE FROM authz.user_access WHERE user_id LIKE 't-%'")
    conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier) VALUES"
                 " ('t-fin', %s, 'financials'), ('t-doc', %s, 'filings')", (CO, CO))
    conn.execute("SET LOCAL ROLE query_api")

    def visible(user):
        conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        return (conn.execute("SELECT count(*) FROM ops.quality_findings WHERE company_id = %s",
                             (CO,)).fetchone()[0],
                conn.execute("SELECT count(*) FROM ops.quality_companies WHERE company_id = %s",
                             (CO,)).fetchone()[0])

    assert visible("t-fin") == (1, 1)
    assert visible("t-doc") == (0, 0)  # filings tier only
    assert visible("") == (0, 0)
    for table in ("ops.quality_runs", "silver.financial_facts"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(f"SELECT * FROM {table} LIMIT 1")
    conn.execute("RESET ROLE")
