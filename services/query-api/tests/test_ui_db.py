"""UI data over real Postgres (migration 017): expected filing dates, filing recaps, flags and
peers, through the API with both layers. Skipped unless TEST_DATABASE_URL is set; every test is
rolled back."""

import os
from contextlib import contextmanager
from datetime import date

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.store import Store

from .conftest import SECRET

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

C, W, S = "9100000001", "9100000002", "9100000003"  # calendar-year, 52-week, stale filer

# (company, form, period end, filed)
REPORTS = [
    (C, "10-K", "2024-12-31", "2025-02-14"),
    (C, "10-Q", "2025-03-31", "2025-04-30"),
    (C, "10-Q", "2025-06-30", "2025-07-31"),
    (C, "10-Q", "2025-09-30", "2025-10-30"),
    (C, "10-K", "2025-12-31", "2026-02-13"),
    (C, "10-Q", "2026-03-31", "2026-04-30"),
    (C, "10-Q", "2026-06-30", "2026-07-30"),
    (C, "10-Q/A", "2026-06-30", "2026-08-10"),  # amendments never count as the latest period
    (W, "10-K", "2024-09-28", "2024-11-01"),
    (W, "10-K", "2025-09-27", "2025-10-31"),
    (W, "10-Q", "2026-06-27", "2026-08-01"),
    (S, "10-Q", "2023-09-30", "2023-11-01"),
]


class AllowAll:
    def can(self, *_):
        return True

    def readable(self, _):
        return {}


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        for cid, fye, categories in ((C, "1231", ["Large accelerated filer"]),
                                     (W, "0927", None), (S, "1231", None)):
            conn.execute("INSERT INTO silver.companies (company_id, name, current_cik,"
                         " fiscal_year_end, filer_categories, sic)"
                         " VALUES (%s, %s, %s, %s, %s, '3571')",
                         (cid, f"UI Test {cid}", cid, fye, categories))
            conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                         (cid, cid))
        for i, (cid, form, period, filed) in enumerate(REPORTS):
            conn.execute("INSERT INTO bronze.submission_filings (accession_no, cik, form,"
                         " filing_date, report_date) VALUES (%s, %s, %s, %s, %s)",
                         (f"{cid}-00-{i:06d}", cid, form, filed, period))
        conn.execute("DELETE FROM authz.user_access WHERE user_id LIKE 't-%'")
        yield conn


def client(conn, today=date(2026, 8, 15)):
    return TestClient(create_app(AllowAll(), Store(OneConnection(conn)), SECRET,
                                 today=lambda: today))


def as_query_api(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute("SELECT set_config('app.user_id', 't-nobody', true)")
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.execute("RESET ROLE")
        return rows


def test_filer_category_and_deadlines(conn):
    assert as_query_api(conn, """
        SELECT gold.filer_category('{Large accelerated filer}'),
               gold.filer_category('{Accelerated filer,Smaller reporting company}'),
               gold.filer_category('{Non-accelerated filer,Smaller reporting company}'),
               gold.filer_category('{Smaller reporting company}'),
               gold.filer_category(NULL),
               gold.filing_deadline_days('large_accelerated', '10-K'),
               gold.filing_deadline_days('accelerated', '10-K'),
               gold.filing_deadline_days('non_accelerated', '10-Q'),
               gold.filing_deadline_days(NULL, '10-Q'),
               gold.next_business_day('2027-02-13'), gold.next_business_day('2027-02-14'),
               gold.next_business_day('2027-02-12')""") == [(
        "large_accelerated", "accelerated", "non_accelerated", "non_accelerated", None,
        60, 75, 45, None, date(2027, 2, 15), date(2027, 2, 15), date(2027, 2, 12))]


def expected(conn, token, company, today=date(2026, 8, 15)):
    r = client(conn, today).get(f"/v1/companies/{company}/expected-filings",
                                headers=token("t-nobody"))
    assert r.status_code == 200, r.text
    return [{k: e[k] for k in ("form", "period_end", "expected_date", "deadline", "basis",
                               "typical_lag_days", "lag_samples", "status")}
            for e in r.json()["expected"]]


def test_expected_from_history_and_deadlines(conn, token):
    assert expected(conn, token, C) == [
        # median 10-Q lag 30 days; deadline 40 days (large accelerated)
        {"form": "10-Q", "period_end": "2026-09-30", "expected_date": "2026-10-30",
         "deadline": "2026-11-09", "basis": "history", "typical_lag_days": 30,
         "lag_samples": 5, "status": "upcoming"},
        # median 10-K lag 44 days lands on a Saturday; deadline 60 days
        {"form": "10-K", "period_end": "2026-12-31", "expected_date": "2027-02-15",
         "deadline": "2027-03-01", "basis": "history", "typical_lag_days": 44,
         "lag_samples": 2, "status": "upcoming"}]


def test_overdue_after_the_deadline(conn, token):
    assert [e["status"] for e in expected(conn, token, C, date(2026, 11, 20))] == \
        ["overdue", "upcoming"]


def test_52_week_year_without_filer_category(conn, token):
    assert expected(conn, token, W) == [
        {"form": "10-K", "period_end": "2026-09-26", "expected_date": "2026-10-30",
         "deadline": None, "basis": "history", "typical_lag_days": 34, "lag_samples": 2,
         "status": "upcoming"},
        # one 10-Q of history and no category: no estimate, said so
        {"form": "10-Q", "period_end": "2026-12-26", "expected_date": None, "deadline": None,
         "basis": None, "typical_lag_days": 35, "lag_samples": 1, "status": "upcoming"}]


def test_stale_filers_get_no_estimate(conn, token):
    assert expected(conn, token, S) == []


def test_last_reports_include_amendments(conn):
    last = Store(OneConnection(conn)).last_reports("t-nobody", [C, W])
    assert (last[C]["form"], str(last[C]["period_end"])) == ("10-Q/A", "2026-06-30")
    assert last[W]["form"] == "10-Q"


R = "9100000004"
ACC = f"{R}-26-000002"
MDNA = ("We recorded a goodwill impairment of $5 million and restructuring charges. "
        "Impairment testing continues.")


@pytest.fixture
def recap_conn(conn):
    conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, fiscal_year_end)"
                 " VALUES (%s, 'UI Test R', %s, '1231')", (R, R))
    conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)", (R, R))
    for period, value, acc in (("2025-06-30", 100, f"{R}-25-000002"),
                               ("2026-03-31", 120, f"{R}-26-000001"),
                               ("2026-06-30", 130, ACC)):
        conn.execute(
            "INSERT INTO gold.quarterly_financials (company_id, metric, period_start, period_end,"
            " value, unit, value_source, source_tag, accession_no, form, filed,"
            " source_accessions, ingested_at) VALUES (%s, 'revenue', %s::date - 90, %s, %s,"
            " 'USD', 'reported', 'us-gaap:Revenues', %s, '10-Q', %s::date + 30, ARRAY[%s], now())",
            (R, period, period, value, acc, period, acc))
    conn.execute("INSERT INTO bronze.submission_filings (accession_no, cik, form, filing_date,"
                 " report_date) VALUES (%s, %s, '10-Q', '2026-07-30', '2026-06-30')", (ACC, R))
    conn.execute("INSERT INTO bronze.filing_documents (accession_no, cik, form, filed,"
                 " period_of_report, document_name, url, content_gzip, content_sha256,"
                 " size_bytes) VALUES (%s, %s, '10-Q', '2026-07-30', '2026-06-30', 'q.htm',"
                 " 'https://www.sec.gov/q.htm', '\\x00', 'x', 1)", (ACC, R))
    conn.execute("INSERT INTO silver.filing_sections (accession_no, section_key, parser,"
                 " parser_version, part, item, title, text, char_start, char_end)"
                 " VALUES (%s, 'I-2', 'python', 't', 'I', '2', 'MD&A', %s, 1000, %s)",
                 (ACC, MDNA, 1000 + len(MDNA)))
    conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier) VALUES"
                 " ('t-free', %s, 'financials'), ('t-pro', %s, 'financials'),"
                 " ('t-pro', %s, 'filings')", (R, R, R))
    return conn


def recap(conn, token, user):
    r = client(conn).get(f"/v1/filings/{ACC}/recap?metrics=revenue", headers=token(user))
    assert r.status_code == 200, r.text
    return r.json()


def test_recap_vs_prior_and_year_ago(recap_conn, token):
    body = recap(recap_conn, token, "t-free")
    assert (body["form"], body["period_end"]) == ("10-Q", "2026-06-30")
    [rev] = body["metrics"]
    assert (rev["metric"], rev["span"], rev["current"]["value"]) == ("revenue", "quarter", "130")
    assert rev["current"]["fiscal_period"] == "FY2026 Q2"
    assert rev["current"]["citation"]["accession_no"] == ACC
    assert (rev["prior_quarter"]["value"], rev["prior_quarter"]["change"],
            rev["prior_quarter"]["pct_change"]) == ("120", "10", "0.083333")
    assert (rev["year_ago"]["value"], rev["year_ago"]["pct_change"]) == ("100", "0.300000")
    assert body["flags"] == []  # layer 1 allows all here; RLS has no filings rows for t-free


def test_recap_and_flags_respect_rls(recap_conn, token):
    assert recap(recap_conn, token, "t-nobody")["metrics"] == []
    flags = recap(recap_conn, token, "t-pro")["flags"]
    assert [(f["flag"], f["matches"], f["citation"]["char_start"]) for f in flags] == [
        ("impairment", 2, 1023), ("restructuring", 1, 1052)]
    assert "goodwill impairment" in flags[0]["snippet"]
