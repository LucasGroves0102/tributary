"""POST /v1/sheets/values against Postgres with the catalog closed: Store's SQL as query_api under
RLS (resolve_companies, sheet_readings, and the reads it shares with the single routes), two
users, layer 2 on its own (an authorizer that allows everything) and both layers together.
Skipped unless TEST_DATABASE_URL is set; every test is rolled back."""

import os
from contextlib import contextmanager
from datetime import date

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.authorizer import Access
from query_api.store import Store

from .conftest import SECRET
from .test_api import FakeAuthorizer

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

A, B = "9000000041", "9000000042"  # test companies, tickers TSTA and TSTB
TODAY = date(2026, 11, 17)


class AllowAll:
    def can(self, *_):
        return True

    def access(self, user_id):
        return Access(frozenset(), {A: {"financials"}, B: {"financials"}})


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        for cid, ticker, value in ((A, "TSTA", 1000), (B, "TSTB", 2000)):
            conn.execute("INSERT INTO silver.companies (company_id, name, current_cik,"
                         " fiscal_year_end, sic) VALUES (%s, %s, %s, '1231', '3674')",
                         (cid, f"Test {ticker}", cid))
            conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                         (cid, cid))
            conn.execute("INSERT INTO silver.company_aliases (company_id, cik, alias_type,"
                         " alias, source) VALUES (%s, %s, 'ticker', %s, 'sec_tickers')",
                         (cid, cid, ticker))
            for end, start, filed, n in (("2026-06-30", "2026-04-01", "2026-08-01", 1),
                                         ("2026-03-31", "2026-01-01", "2026-05-01", 2)):
                acc = f"{cid}-26-00000{n}"
                conn.execute(
                    "INSERT INTO gold.quarterly_financials (company_id, metric, period_start,"
                    " period_end, value, unit, value_source, source_tag, accession_no, form,"
                    " filed, source_accessions, ingested_at) VALUES (%s, 'revenue', %s, %s, %s,"
                    " 'USD', 'reported', 'us-gaap:Revenues', %s, '10-Q', %s, ARRAY[%s], now())",
                    (cid, start, end, value + n, acc, filed, acc))
            conn.execute(
                "INSERT INTO gold.derived_financials (company_id, span, metric, period_start,"
                " period_end, value, unit, value_source, inputs, accession_no, form, filed,"
                " source_accessions, ingested_at) VALUES (%s, 'ttm', 'net_margin', '2025-07-01',"
                " '2026-06-30', 0.25, 'pure', 'computed', '{}', %s, '10-Q', '2026-08-01',"
                " ARRAY[%s], now())", (cid, f"{cid}-26-000001", f"{cid}-26-000001"))
        # 086: gold's history, believed from the filing date (as of reads replay it)
        conn.execute(
            "INSERT INTO gold.financials_known (company_id, span, metric, period_end, known_from,"
            " period_start, value, unit, value_source, source_tag, accession_no, form, filed,"
            " source_accessions, ingested_at)"
            " SELECT company_id, 'quarter', metric, period_end, filed, period_start, value, unit,"
            " value_source, source_tag, accession_no, form, filed, source_accessions, ingested_at"
            " FROM gold.quarterly_financials WHERE company_id IN (%s, %s)", (A, B))
        conn.execute("SELECT ops.stamp_builds('known', %s, gold.rules_version())", ([A, B],))
        conn.execute("INSERT INTO gold.known_companies (company_id, dates, facts, ids,"
                     " replayed_at) VALUES (%s, '{}', '{}', '{}', now()),"
                     " (%s, '{}', '{}', '{}', now())", (A, B))
        # the catalog closed; t-free reads A, t-other reads B, both with the financials tier
        conn.execute("DELETE FROM authz.public_access")
        conn.execute("DELETE FROM authz.user_access WHERE user_id LIKE 't-%'")
        conn.execute("DELETE FROM authz.user_tiers WHERE user_id LIKE 't-%'")
        conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier) VALUES"
                     " ('t-free', %s, 'financials'), ('t-other', %s, 'financials')", (A, B))
        conn.execute("INSERT INTO authz.user_tiers (user_id, tier) VALUES"
                     " ('t-free', 'financials'), ('t-other', 'financials')")
        yield conn


def client(conn, authorizer):
    return TestClient(create_app(authorizer, Store(OneConnection(conn)), SECRET,
                                 today=lambda: TODAY))


def values(c, token, user, asks):
    r = c.post("/v1/sheets/values", json={"requests": asks}, headers=token(user))
    assert r.status_code == 200, r.text
    return r.json()["values"]


ASKS = [{"company": t, "metric": m, "period": p}
        for t in ("TSTA", "TSTB")
        for m, p in (("revenue", "latest"), ("revenue", "TTM"), ("revenue", "CY2026 Q1"),
                     ("revenue", "2026-03-31"), ("net_margin", "TTM"), ("revenue", "FY"),
                     ("eps_diluted", "latest"))]


def test_each_user_reads_their_own_company_and_rls_hides_the_other(conn, token):
    """Layer 2 alone: the authorizer allows everything, RLS still decides."""
    c = client(conn, AllowAll())
    for user, mine, theirs in (("t-free", A, B), ("t-other", B, A)):
        got = values(c, token, user, ASKS)
        own = [g for g in got if g["company_id"] == mine]
        assert own[0]["status"] == "current" and own[0]["value"] in ("1001", "2001")
        assert own[0]["period_end"] == "2026-06-30" and own[0]["unit"] == "USD"
        assert own[0]["citation"]["accession_no"] == f"{mine}-26-000001"
        assert [g["value"] for g in own if g["metric"] == "net_margin"] == ["0.25"]
        # every figure of the other company: none, whatever the period
        other = [g for g in got if g["company_id"] == theirs]
        assert len(other) == len(ASKS) // 2
        assert all(g["value"] is None and g["citation"] is None for g in other), other
        assert {g["status"] for g in other} <= {"no_figure"}


def test_named_periods_and_readings_through_the_real_functions(conn, token):
    got = values(client(conn, AllowAll()), token, "t-free", ASKS[:7])
    assert [(g["status"], g["value"]) for g in got] == [
        ("current", "1001"),
        ("no_figure", None),        # no TTM of revenue on file
        ("ok", "1002"),             # CY2026 Q1
        ("ok", "1002"),             # the quarter ending that day
        ("current", "0.25"),        # net margin's TTM, gold.latest_reading
        ("no_figure", None),        # no fiscal year on file yet
        ("no_figure", None)]        # no EPS: per_share reads nothing
    assert got[2]["period"] == "CY2026 Q1"


def test_as_of_a_day_reads_what_was_filed_by_then(conn, token):
    c = client(conn, AllowAll())
    got = values(c, token, "t-free", [
        {"company": "TSTA", "metric": "revenue", "as_of": "2026-06-01"},
        {"company": "TSTA", "metric": "revenue", "as_of": "2026-06-01", "period": "FY2026 Q2"},
        {"company": "TSTA", "metric": "revenue", "as_of": "2026-06-01", "count": 4}])
    assert (got[0]["status"], got[0]["value"], got[0]["period_end"]) == (
        "current", "1002", "2026-03-31")  # the June quarter was filed in August
    assert got[0]["known_on"] == "2026-06-01"
    assert got[1]["status"] == "no_figure"
    assert [p["value"] for p in got[2]["points"]] == ["1002"]


def test_both_layers_unknown_and_unreadable_read_the_same(conn, token):
    authz = FakeAuthorizer({"t-free": {A: {"financials"}}})
    got = values(client(conn, authz), token, "t-free", [
        {"company": "TSTB", "metric": "revenue"},
        {"company": "NOSUCHCO", "metric": "revenue"},
        {"company": B, "metric": "revenue"},
        {"company": "TSTA", "metric": "revenue"}])
    strip = [{k: v for k, v in g.items() if k != "company"} for g in got[:3]]
    assert strip[0] == strip[1] == strip[2]
    assert strip[0]["status"] == "not_permitted" and strip[0]["company_id"] is None
    assert got[3]["value"] == "1001"
    assert sorted(authz.checks) == [("t-free", "financials", A), ("t-free", "financials", B)]
