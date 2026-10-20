"""Layer 2 on its own: the API wired to an authorizer that allows EVERYTHING, against real
Postgres. Row-level security must still hide what authz.user_access doesn't grant.
Skipped unless TEST_DATABASE_URL is set; every test is rolled back."""

import os
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from query_api.app import DERIVED_METRICS, METRICS, create_app
from query_api.store import Store

from .conftest import SECRET

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

A, B = "9000000001", "9000000002"  # test companies


class AllowAll:
    def can(self, *_):
        return True

    def readable(self, _):
        return {A: {"financials", "filings"}, B: {"financials", "filings"}}


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def client():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        for cid in (A, B):
            conn.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                         " VALUES (%s, %s, %s)", (cid, f"Test {cid}", cid))
            conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                         (cid, cid))
            conn.execute(
                "INSERT INTO gold.quarterly_financials (company_id, metric, period_end, value,"
                " unit, value_source, source_tag, accession_no, filed, source_accessions,"
                " ingested_at) VALUES (%s, 'revenue', '2026-06-30', 1, 'USD', 'reported',"
                " 'us-gaap:Revenues', %s, '2026-08-01', ARRAY[%s], now())",
                (cid, f"{cid}-26-000001", f"{cid}-26-000001"))
            conn.execute(
                "INSERT INTO gold.derived_financials (company_id, span, metric, period_start,"
                " period_end, value, unit, value_source, inputs, accession_no, filed,"
                " source_accessions, ingested_at) VALUES (%s, 'ttm', 'net_margin', '2025-07-01',"
                " '2026-06-30', 0.25, 'ratio', 'computed', '{}', %s, '2026-08-01', ARRAY[%s],"
                " now())", (cid, f"{cid}-26-000001", f"{cid}-26-000001"))
            conn.execute(
                "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed,"
                " document_name, url, content_gzip, content_sha256, size_bytes)"
                " VALUES (%s, %s, '10-K', '2026-02-01', 'd.htm', 'u', '\\x00', 'x', 1)",
                (f"{cid}-26-000002", cid))
            conn.execute(
                "INSERT INTO silver.filing_sections (accession_no, section_key, parser,"
                " parser_version, item, title, text, char_start, char_end)"
                " VALUES (%s, '1A', 'python', 't', '1A', 'Risk Factors', 'secret risks', 0, 12)",
                (f"{cid}-26-000002",))
        conn.execute("UPDATE silver.companies SET sic = '2080', sic_description = 'Beverages',"
                     " exchanges = '{NYSE}' WHERE company_id = %s", (A,))
        conn.execute("DELETE FROM authz.user_access WHERE user_id LIKE 't-%'")
        conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier) VALUES"
                     " ('t-free', %s, 'financials'), ('t-pro', %s, 'financials'),"
                     " ('t-pro', %s, 'filings')", (A, A, A))
        c = TestClient(create_app(AllowAll(), Store(OneConnection(conn)), SECRET))
        c.app_conn = conn
        yield c


def financials(client, token, user, company, query=""):
    r = client.get(f"/v1/companies/{company}/financials?{query}", headers=token(user))
    assert r.status_code == 200
    return r.json()["rows"]


def sections(client, token, user, company):
    r = client.get(f"/v1/companies/{company}/sections/1A", headers=token(user))
    assert r.status_code == 200
    return r.json()["sections"]


def test_granted_rows_visible(client, token):
    assert len(financials(client, token, "t-free", A)) == 1
    assert sections(client, token, "t-pro", A)[0]["text"] == "secret risks"


def test_ungranted_company_hidden_even_when_layer_one_allows(client, token):
    assert financials(client, token, "t-free", B) == []
    assert sections(client, token, "t-pro", B) == []


@pytest.mark.parametrize("query", ["view=ttm", "view=ttm&basis=calendar",
                                   "basis=calendar&metrics=revenue"])
def test_ttm_and_calendar_views_respect_rls(client, token, query):
    assert len(financials(client, token, "t-free", A, query)) == 1
    assert financials(client, token, "t-free", B, query) == []
    assert financials(client, token, "t-nobody", A, query) == []


def test_derived_financials_hidden_without_a_grant(client):
    conn = client.app_conn
    with conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute("SELECT set_config('app.user_id', 't-free', true)")
        cur.execute("SELECT DISTINCT company_id FROM gold.derived_financials"
                    " WHERE company_id IN (%s, %s)", (A, B))
        assert cur.fetchall() == [(A,)]
        cur.execute("SELECT DISTINCT company_id FROM gold.financials"
                    " WHERE company_id IN (%s, %s)", (A, B))
        assert cur.fetchall() == [(A,)]
        cur.execute("RESET ROLE")


def test_tier_enforced_by_database(client, token):
    assert sections(client, token, "t-free", A) == []  # free plan: no filings rows


def test_unknown_user_sees_nothing(client, token):
    assert financials(client, token, "t-nobody", A) == []


def test_filings_index_respects_rls(client, token):
    r = client.get(f"/v1/companies/{B}/filings", headers=token("t-pro"))
    assert r.json()["filings"] == []


def test_latest_filings_respect_rls(client, token):
    r = client.get("/v1/filings/latest", headers=token("t-pro"))
    assert [f["company_id"] for f in r.json()["filings"]] == [A]
    assert client.get("/v1/filings/latest", headers=token("t-free")).json()["filings"] == []


def test_metric_list_matches_database():
    with psycopg.connect(DSN) as conn:
        assert {m for (m,) in conn.execute("SELECT metric FROM silver.metrics")} == set(METRICS)
        assert {m for (m,) in conn.execute("SELECT metric FROM gold.derived_metrics")} == \
            set(DERIVED_METRICS)


def test_company_profile_is_public_metadata(client, token):
    me = client.get("/v1/me", headers=token("t-nobody")).json()
    assert {c["company_id"]: c["industry"] for c in me["companies"]} == {A: "Beverages", B: None}
    r = client.get(f"/v1/companies/search?q={A}", headers=token("t-nobody")).json()
    assert r["results"][0] | {"access": None} == {
        "company_id": A, "name": f"Test {A}", "matched_on": "cik", "matched_alias": A,
        "is_current": True, "sic": "2080", "industry": "Beverages", "exchanges": ["NYSE"],
        "access": None}


def screen(client, token, user, query=""):
    r = client.get(f"/v1/screener?q=Test%20900000000&columns=revenue_q&columns=net_margin_ttm"
                   f"&sort=company_id&{query}", headers=token(user))
    assert r.status_code == 200, r.text
    return {row["company_id"]: row["values"] for row in r.json()["rows"]}


def test_screener_values_respect_rls(client, token):
    """Layer 1 allows everything here; RLS alone must keep B's values out."""
    assert screen(client, token, "t-free") == {
        A: {"revenue_q": "1", "net_margin_ttm": "0.25"},
        B: {"revenue_q": None, "net_margin_ttm": None}}
    assert screen(client, token, "t-nobody") == {
        A: {"revenue_q": None, "net_margin_ttm": None},
        B: {"revenue_q": None, "net_margin_ttm": None}}


def test_screener_value_filters_never_match_hidden_values(client, token):
    assert list(screen(client, token, "t-free", "where=revenue_q:gte:0")) == [A]
    assert screen(client, token, "t-nobody", "where=revenue_q:gte:0") == {}
    assert list(screen(client, token, "t-free", "where=revenue_q:lt:0")) == []


class Watching:
    def list(self, user_id):
        return [{"company_id": c, "added_at": None} for c in (A, B)]


def test_watchlist_values_respect_rls(client, token):
    """Watching B grants nothing: with layer 1 allowing everything, RLS still hides B's values
    while its metadata (public) is listed."""
    store = Store(OneConnection(client.app_conn))
    c = TestClient(create_app(AllowAll(), store, SECRET, watchlists=Watching()))
    r = c.get("/v1/watchlist?columns=revenue_q", headers=token("t-free"))
    assert r.status_code == 200, r.text
    assert {row["company_id"]: row["values"] for row in r.json()["companies"]} == {
        A: {"revenue_q": "1"}, B: {"revenue_q": None}}
