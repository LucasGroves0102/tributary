"""Layer 2 on its own: the API wired to an authorizer that allows EVERYTHING, against real
Postgres. Row-level security must still hide what authz.user_access doesn't grant.
Skipped unless TEST_DATABASE_URL is set; every test is rolled back."""

import os
from contextlib import contextmanager
from datetime import date

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.authorizer import Access
from query_api.formula import CATALOG
from query_api.screener import Screen, compile_screen, parse_filter
from query_api.store import Store

from .conftest import SECRET

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

A, B = "9000000001", "9000000002"  # test companies


class AllowAll:
    def can(self, *_):
        return True

    def access(self, user_id):
        return Access(frozenset(), self.readable(user_id))

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
            conn.execute(
                "INSERT INTO gold.segment_financials (company_id, span, axis, member,"
                " member_label, metric, period_start, period_end, value, unit, value_source,"
                " source_tag, axis_qname, accession_no, filer_cik, filed, source_accessions,"
                " ingested_at) VALUES (%s, 'quarter', 'product_service', 'x:WidgetsMember',"
                " 'Widgets', 'revenue', '2026-04-01', '2026-06-30', 0.1, 'USD', 'reported',"
                " 'us-gaap:Revenues', 'srt:ProductOrServiceAxis', %s, %s, '2026-08-01',"
                " ARRAY[%s], now())", (cid, f"{cid}-26-000001", cid, f"{cid}-26-000001"))
        conn.execute("UPDATE silver.companies SET sic = '2080', sic_description = 'Beverages',"
                     " exchanges = '{NYSE}' WHERE company_id = %s", (A,))
        conn.execute("DELETE FROM authz.user_access WHERE user_id LIKE 't-%'")
        conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier) VALUES"
                     " ('t-free', %s, 'financials'), ('t-pro', %s, 'financials'),"
                     " ('t-pro', %s, 'filings')", (A, A, A))
        # plans' tiers (authz-sync); the catalog starts closed (no public_access rows)
        conn.execute("DELETE FROM authz.public_access")
        conn.execute("DELETE FROM authz.user_tiers WHERE user_id LIKE 't-%'")
        conn.execute("INSERT INTO authz.user_tiers (user_id, tier) VALUES ('t-free', 'financials'),"
                     " ('t-pro', 'financials'), ('t-pro', 'filings'),"
                     " ('t-reader', 'financials'), ('t-reader', 'filings')")
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


def test_filing_index_is_public_but_section_counts_respect_rls(client, token):
    """Layer 1 allows everything here: both companies' index rows are public metadata, but
    the parsed-section count comes back only where RLS lets the caller read sections."""
    conn = client.app_conn
    for cid in (A, B):
        conn.execute("INSERT INTO bronze.submission_filings (accession_no, cik, form,"
                     " filing_date, report_date, primary_document) VALUES"
                     " (%s, %s, '10-K', '2026-02-01', '2025-12-31', 'd.htm'),"
                     " (%s, %s, '8-K', '2026-03-01', NULL, 'e.htm')",
                     (f"{cid}-26-000002", cid, f"{cid}-26-000009", cid))

    def index(user, company, query=""):
        r = client.get(f"/v1/companies/{company}/filing-index?{query}", headers=token(user))
        assert r.status_code == 200, r.text
        return [(f["accession_no"], f["form"], f["sections"]) for f in r.json()["filings"]]

    assert index("t-pro", A) == [(f"{A}-26-000009", "8-K", 0), (f"{A}-26-000002", "10-K", 1)]
    assert index("t-pro", B) == [(f"{B}-26-000009", "8-K", 0), (f"{B}-26-000002", "10-K", 0)]
    assert index("t-free", A) == [(f"{A}-26-000009", "8-K", 0), (f"{A}-26-000002", "10-K", 0)]
    assert index("t-pro", A, "form=10-K") == [(f"{A}-26-000002", "10-K", 1)]
    assert index("t-pro", A, "to=2026-02-15") == [(f"{A}-26-000002", "10-K", 1)]
    assert index("t-pro", A, "limit=1&offset=1") == [(f"{A}-26-000002", "10-K", 1)]
    r = client.get(f"/v1/filing-index?company_id={A}&company_id={B}", headers=token("t-pro"))
    rows = r.json()["filings"]
    assert {f["company_id"] for f in rows} == {A, B}
    assert rows[-1]["period_of_report"] == "2025-12-31" and rows[0]["name"].startswith("Test")


def test_latest_filings_respect_rls(client, token):
    r = client.get("/v1/filings/latest", headers=token("t-pro"))
    assert [f["company_id"] for f in r.json()["filings"]] == [A]
    assert client.get("/v1/filings/latest", headers=token("t-free")).json()["filings"] == []


def test_data_notes_respect_rls(client, token):
    """Findings, derived quarters and "companyfacts behind EDGAR" for both companies; layer 1
    allows everything, RLS leaves only A to t-free (financials) and nothing to the others."""
    conn = client.app_conn
    run = conn.execute("INSERT INTO ops.quality_runs (companies) VALUES (2)"
                       " RETURNING run_id").fetchone()[0]
    for cid in (A, B):
        conn.execute(
            "INSERT INTO ops.quality_findings (company_id, check_name, metric, period_end,"
            " severity, detail, run_id) VALUES (%s, 'restatement', 'revenue', '2026-06-30',"
            " 'info', %s, %s)",
            (cid, f'{{"original_accession": "{cid}-26-000000", "original_filed": "2026-07-01",'
                  f' "restated_accession": "{cid}-26-000001", "restated_filed": "2026-08-01",'
                  ' "change": 0.02}', run))
        conn.execute("INSERT INTO ops.quality_companies (company_id, run_id, checked_at, errors,"
                     " warnings) VALUES (%s, %s, now(), 0, 0)", (cid, run))
        conn.execute(
            "INSERT INTO gold.quarterly_financials (company_id, metric, period_start, period_end,"
            " value, unit, value_source, source_tag, accession_no, filed, source_accessions,"
            " ingested_at) VALUES (%s, 'revenue', '2026-01-01', '2026-03-31', 1, 'USD',"
            " 'derived', 'us-gaap:Revenues', %s, '2026-05-01', ARRAY[%s], now())",
            (cid, f"{cid}-26-000003", f"{cid}-26-000003"))
        conn.execute("INSERT INTO bronze.submission_filings (accession_no, cik, form,"
                     " filing_date, report_date) VALUES (%s, %s, '10-Q', '2026-09-01',"
                     " '2026-07-31')", (f"{cid}-26-000010", cid))

    def notes(user, company):
        r = client.get(f"/v1/companies/{company}/data-notes", headers=token(user))
        assert r.status_code == 200, r.text
        return sorted((n["kind"], n["filings"][0]["accession_no"]) for n in r.json()["notes"])

    assert notes("t-free", A) == [("companyfacts_behind", f"{A}-26-000010"),
                                  ("derived_quarters", f"{A}-26-000003"),
                                  ("restatement", f"{A}-26-000000")]
    assert notes("t-free", B) == []
    assert notes("t-nobody", A) == []
    r = client.get("/v1/data-notes", headers=token("t-free"))
    assert {n["company_id"] for n in r.json()["notes"]} == {A}
    assert "derived_quarters" not in {n["kind"] for n in r.json()["notes"]}
    assert client.get("/v1/data-notes", headers=token("t-nobody")).json()["notes"] == []


RELEASE_TEXT = ("Acme Reports Third Quarter 2026 Results\n"
                "(1) Results include a $3.0 million benefit from tariff refunds received in the"
                " quarter, which the Company excludes from adjusted figures.\n"
                "Full Year 2026 Guidance\n"
                "•Revenue is expected to be $40 to $42 million.")


@pytest.fixture
def events(client):
    """For A and B: a 10-Q for Jun 30, an 8-K with Item 2.02 and its press release, an 8-K
    completing an acquisition after the quarter end, and an 8-K too new for the submissions
    history (bronze.filings only) whose parsed headings give its items. Layer 1 allows
    everything: what a caller sees of the sections is RLS's doing."""
    conn = client.app_conn
    for cid in (A, B):
        rows = [(f"{cid}-26-000020", "10-Q", "2026-08-05", "2026-06-30", None),
                (f"{cid}-26-000021", "8-K", "2026-08-04", "2026-08-04", "2.02,9.01"),
                (f"{cid}-26-000022", "8-K", "2026-08-20", "2026-08-18", "2.01,9.01")]
        for acc, form, filed, report, items in rows:
            conn.execute("INSERT INTO bronze.submission_filings (accession_no, cik, form,"
                         " filing_date, report_date, primary_document, items) VALUES"
                         " (%s, %s, %s, %s, %s, 'd.htm', %s)", (acc, cid, form, filed, report,
                                                                   items))
        conn.execute("INSERT INTO bronze.filings (accession_no, cik, form_type, filed_at, payload,"
                     " kafka_partition, kafka_offset) VALUES (%s, %s, '8-K',"
                     " '2026-08-25 20:05+00', '{}', 0, 0)", (f"{cid}-26-000023", cid))
        sections = [(f"{cid}-26-000021", "EX-99.1", RELEASE_TEXT),
                    (f"{cid}-26-000022", "2.01", "On August 18, 2026, Acme completed its"
                                                 " acquisition of Widgets Inc. for $50 million."),
                    (f"{cid}-26-000023", "2.03", "On August 25, 2026, Acme borrowed $20 million.")]
        for acc, key, text in sections:
            conn.execute(
                "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed,"
                " document_name, url, content_gzip, content_sha256, size_bytes)"
                " VALUES (%s, %s, '8-K', '2026-08-04', 'e.htm', 'u', '\\x00', 'x', 1)"
                " ON CONFLICT DO NOTHING", (acc, cid))
            conn.execute(
                "INSERT INTO silver.filing_sections (accession_no, section_key, parser,"
                " parser_version, item, title, text, char_start, char_end)"
                " VALUES (%s, %s, 'python', 't', %s, 'Item', %s, 0, %s)",
                (acc, key, key, text, len(text)))
    return TestClient(create_app(AllowAll(), Store(OneConnection(conn)), SECRET,
                                 today=lambda: date(2026, 11, 8)))


def test_events_index_is_public_but_excerpts_and_parsed_items_respect_rls(events, token):
    def get(user, company):
        r = events.get(f"/v1/companies/{company}/events", headers=token(user))
        assert r.status_code == 200, r.text
        return r.json()

    body = get("t-pro", A)
    assert body["since"] == "2026-06-30" and body["window"] == "since_report"
    assert [(e["accession_no"], e["items_from"]) for e in body["events"]] == [
        (f"{A}-26-000023", "parsed"), (f"{A}-26-000022", "index"), (f"{A}-26-000021", "index")]
    new, deal, _ = body["events"]
    assert [i["item"] for i in new["items"]] == ["2.03"]
    assert deal["excerpt"]["text"].startswith("On August 18, 2026, Acme completed")
    assert {s["metric"] for s in body["stale"]} == {
        "cash", "long_term_debt", "net_debt", "total_assets", "debt_to_equity"}
    # B: the index is public metadata, its text isn't readable (no grant): no excerpt, and the
    # newest 8-K's items can't be read from sections the caller can't see
    other = get("t-pro", B)
    assert [e["items_from"] for e in other["events"]] == [None, "index", "index"]
    assert all(e["excerpt"] is None and not e["sections_available"] for e in other["events"])
    assert {s["metric"] for s in other["stale"]} == {"cash", "long_term_debt", "net_debt",
                                                     "total_assets"}
    assert all(e["excerpt"] is None for e in get("t-free", A)["events"])  # no filings tier


def test_earnings_text_respects_rls(events, token):
    def quarter(user, company):
        r = events.get(f"/v1/companies/{company}/earnings?quarters=1", headers=token(user))
        assert r.status_code == 200, r.text
        [q] = r.json()["quarters"]
        return q

    q = quarter("t-pro", A)
    assert q["period_end"] == "2026-06-30" and q["report"]["accession_no"] == f"{A}-26-000020"
    assert q["release"]["accession_no"] == f"{A}-26-000021"
    assert q["release"]["section_key"] == "EX-99.1"
    assert q["headline"]["text"] == "Acme Reports Third Quarter 2026 Results"
    assert [o["heading"] for o in q["outlook"]] == ["Full Year 2026 Guidance"]
    assert [a["kind"] for a in q["adjustments"]] == ["note"]
    hidden = quarter("t-pro", B)  # the release is listed; its text isn't readable
    assert hidden["release"]["accession_no"] == f"{B}-26-000021"
    assert hidden["release"]["section_key"] is None and hidden["outlook"] == []
    assert quarter("t-free", A)["outlook"] == []


def test_model_runs_read_only_what_rls_allows(client, token):
    """F2: POST /v1/companies/{id}/model reads gold through Store.model_inputs under RLS; with
    layer one allowing everything, an ungranted company or user still reads no rows."""
    store = Store(OneConnection(client.app_conn))

    def inputs(user, company):
        return store.model_inputs(user, company, ["revenue", "net_margin"], 40, 12, 12,
                                  ["net_debt", "shares_outstanding"])

    mine = inputs("t-free", A)
    assert [r["metric"] for r in mine["quarters"]] == ["revenue"]
    assert [r["metric"] for r in mine["ttm"]] == ["net_margin"]
    for user, company in (("t-free", B), ("t-nobody", A)):
        hidden = inputs(user, company)
        assert hidden["ttm"] == hidden["quarters"] == hidden["annual"] == []
        assert all(r["value"] is None for r in hidden["latest"].values())
    state = {"companyId": B, "ytd": True, "assumptions": [{"growth": 5, "margin": 10}]}
    r = client.post(f"/v1/companies/{B}/model", json=state, headers=token("t-free"))
    assert r.status_code == 200 and r.json()["history"] == [] and r.json()["base_fy"] is None


def test_a_euro_companys_ev_is_in_euros_at_a_euro_price(client, token):
    """Pre-ship review: a euro company's net debt reads other_currency (056), which the model took
    for unusable, so EV was blank even at a price in euros. Store.model_inputs now gives the
    reading's unit and age (under RLS, as before); at a euro price EV is in euros, at a dollar
    price nothing is divided."""
    conn, acc = client.app_conn, f"{A}-26-000001"
    conn.execute("UPDATE gold.quarterly_financials SET unit = 'EUR' WHERE company_id = %s", (A,))
    conn.execute(
        "INSERT INTO gold.quarterly_financials (company_id, metric, period_end, value, unit,"
        " value_source, source_tag, accession_no, filed, source_accessions, ingested_at) VALUES"
        " (%s, 'shares_outstanding', '2026-06-30', 1000, 'shares', 'reported',"
        " 'dei:EntityCommonStockSharesOutstanding', %s, '2026-08-01', ARRAY[%s], now())",
        (A, acc, acc))
    conn.execute(
        "INSERT INTO gold.derived_financials (company_id, span, metric, period_end, value, unit,"
        " value_source, inputs, accession_no, filed, source_accessions, ingested_at) VALUES"
        " (%s, 'quarter', 'net_debt', '2026-06-30', 500, 'EUR', 'computed', '{}', %s,"
        " '2026-08-01', ARRAY[%s], now())", (A, acc, acc))
    store = Store(OneConnection(conn))
    latest = store.model_inputs("t-free", A, ["revenue"], 40, 12, 12,
                                ["net_debt", "shares_outstanding"])["latest"]
    debt = latest["net_debt"]
    assert (debt["status"], debt["unit"], debt["period_status"]) == ("other_currency", "EUR", None)
    hidden = store.model_inputs("t-free", B, ["revenue"], 40, 12, 12, ["net_debt"])["latest"]
    assert hidden["net_debt"]["value"] is None and hidden["net_debt"]["unit"] is None

    state = {"companyId": A, "assumptions": [{"growth": 5, "margin": 10}],
             "price": {"value": 2, "date": "2026-11-06", "currency": "EUR"}}
    r = client.post(f"/v1/companies/{A}/model", json=state, headers=token("t-free"))
    assert r.status_code == 200, r.text
    v = r.json()["valuation"]
    assert r.json()["currency"] == "EUR" and v["market_cap_currency"] == "EUR"
    assert (v["market_cap"], v["enterprise_value"], v["ev_note"]) == (2000, 2500, None)
    state["price"]["currency"] = "USD"
    v = client.post(f"/v1/companies/{A}/model", json=state, headers=token("t-free")).json()[
        "valuation"]
    assert v["enterprise_value"] is None
    assert v["currency_mismatch"] == {"price": "USD", "figures": "EUR"}


def test_metric_list_matches_database(client):
    served = client.get("/v1/metrics").json()
    with psycopg.connect(DSN) as conn:
        assert [m for (m,) in conn.execute("SELECT metric FROM silver.metrics ORDER BY 1")] \
            == served["metrics"]
        assert [m for (m,) in conn.execute("SELECT metric FROM gold.derived_metrics"
                                           " ORDER BY 1")] == served["derived_metrics"]
    assert "revenue" in served["metrics"] and "roe" in served["derived_metrics"]


def test_formula_catalog_matches_database():
    with psycopg.connect(DSN) as conn:
        units = dict(conn.execute("SELECT metric, unit FROM silver.metrics").fetchall())
        units |= dict(conn.execute("SELECT metric, unit FROM gold.derived_metrics").fetchall())
    assert set(CATALOG) == set(units)
    assert {m: unit for m, (unit, _) in CATALOG.items()} == units


def test_company_profile_is_public_metadata(client, token):
    me = client.get("/v1/me", headers=token("t-nobody")).json()
    assert {c["company_id"]: c["industry"] for c in me["companies"]} == {A: "Beverages", B: None}
    r = client.get(f"/v1/companies/search?q={A}", headers=token("t-nobody")).json()
    assert r["results"][0] | {"access": None} == {
        "company_id": A, "name": f"Test {A}", "matched_on": "cik", "matched_alias": A,
        "is_current": True, "sic": "2080", "industry": "Beverages", "exchanges": ["NYSE"],
        "tickers": None,
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


def segments(client, token, user, company, query=""):
    r = client.get(f"/v1/companies/{company}/segments?{query}", headers=token(user))
    assert r.status_code == 200, r.text
    return r.json()["rows"]


@pytest.mark.parametrize("query", ["", "axis=product_service&metric=revenue",
                                   "basis=calendar", "format=csv"])
def test_segments_respect_rls(client, token, query):
    if "csv" in query:
        r = client.get(f"/v1/companies/{A}/segments?{query}", headers=token("t-free"))
        assert r.text.count("x:WidgetsMember") == 1
        r = client.get(f"/v1/companies/{B}/segments?{query}", headers=token("t-free"))
        assert "x:WidgetsMember" not in r.text
        return
    rows = segments(client, token, "t-free", A, query)
    assert [(r["member_label"], r["value"]) for r in rows] == [("Widgets", "0.1")]
    assert rows[0]["citation"]["filing_url"].endswith(f"/{int(A)}/{A}26000001/")
    assert segments(client, token, "t-free", B, query) == []
    assert segments(client, token, "t-nobody", A, query) == []


def test_segment_rows_hidden_without_a_grant(client):
    with client.app_conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute("SELECT set_config('app.user_id', 't-free', true)")
        cur.execute("SELECT DISTINCT company_id FROM gold.segment_financials"
                    " WHERE company_id IN (%s, %s)", (A, B))
        assert cur.fetchall() == [(A,)]
        cur.execute("SELECT DISTINCT company_id FROM gold.segments"
                    " WHERE company_id IN (%s, %s)", (A, B))
        assert cur.fetchall() == [(A,)]
        cur.execute("RESET ROLE")


def test_segment_metric_list_matches_database(client, token):
    r = client.get(f"/v1/companies/{A}/segments?metric=salary", headers=token("t-free"))
    assert r.status_code == 422
    with psycopg.connect(DSN) as conn:
        names = [m for (m,) in conn.execute("SELECT metric FROM silver.segment_metrics")]
    assert {"revenue", "operating_income"} <= set(names)
    assert all(n in r.json()["detail"] for n in names)


@pytest.fixture
def texts(client):
    """Per company: a prior 10-K whose 1A said 'old risks', search passages for both filings,
    and the 1A diff (one rewritten paragraph) as parser-index writes it."""
    conn = client.app_conn
    for cid in (A, B):
        new, old = f"{cid}-26-000002", f"{cid}-25-000002"
        conn.execute(
            "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed,"
            " document_name, url, content_gzip, content_sha256, size_bytes)"
            " VALUES (%s, %s, '10-K', '2025-02-01', 'd.htm', 'u', '\\x00', 'x', 1)", (old, cid))
        conn.execute(
            "INSERT INTO silver.filing_sections (accession_no, section_key, parser,"
            " parser_version, item, title, text, char_start, char_end)"
            " VALUES (%s, '1A', 'python', 't', '1A', 'Risk Factors', 'old risks', 0, 9)",
            (old,))
        conn.execute("SELECT gold.refresh_section_search(ARRAY[%s, %s])", (new, old))
        conn.execute(
            "INSERT INTO gold.section_diffs (company_id, accession_no, section_key, basis, form,"
            " filed, parser, parsed_at, prior_accession_no, prior_form, prior_filed,"
            " prior_parser, prior_parsed_at, paragraphs, prior_paragraphs, unchanged,"
            " minor_edits, numbers_only, rewritten, new, removed, algorithm)"
            " SELECT %s, n.accession_no, '1A', 'prior', '10-K', '2026-02-01', n.parser,"
            " n.parsed_at, p.accession_no, '10-K', '2025-02-01', p.parser, p.parsed_at,"
            " 1, 1, 0, 0, 0, 1, 0, 0, 'test'"
            " FROM silver.filing_sections n, silver.filing_sections p"
            " WHERE n.accession_no = %s AND p.accession_no = %s", (cid, new, old))
        conn.execute(
            "INSERT INTO gold.section_changes (company_id, accession_no, section_key, basis,"
            " ordinal, kind, similarity, new_spans, prior_spans, words) VALUES"
            " (%s, %s, '1A', 'prior', 0, 'rewritten', 0.5, '{0,12}', '{0,9}',"
            " '[[\"r\", 0, 1, 0, 1], [\"e\", 1, 2, 1, 2]]')", (cid, new))
    return client


def changes(client, token, user, company, query=""):
    return client.get(f"/v1/companies/{company}/filings/{company}-26-000002/changes?{query}",
                      headers=token(user))


def test_section_changes_with_both_citations(texts, token):
    r = changes(texts, token, "t-pro", A)
    assert r.status_code == 200
    [s] = r.json()["sections"]
    assert s["stale"] is False and s["summary"]["rewritten"] == 1
    assert s["compared_to"]["accession_no"] == f"{A}-25-000002"
    [c] = s["changes"]
    assert (c["kind"], c["text"], c["prior_text"]) == ("rewritten", "secret risks", "old risks")
    assert c["words"] == [["-", "old"], ["+", "secret"], ["=", "risks"]]
    assert c["citation"]["char_end"] == 12 and c["prior_citation"]["char_end"] == 9


def test_material_view_reads_the_bases_and_changes_under_rls(texts, token):
    """F3: view=ranked asks gold.section_diffs for its bases and runs the pass, as query_api."""
    r = changes(texts, token, "t-pro", A, "view=ranked")
    assert r.status_code == 200
    body = r.json()
    assert body["view"] == "ranked" and body["basis"] == "prior"
    assert body["comparisons"] == [{"basis": "prior", "accession_no": f"{A}-25-000002",
                                    "form": "10-K", "filed": "2025-02-01",
                                    "period_of_report": None}]
    [s] = body["sections"]
    assert s["material"]["shown"] == len(s["changes"])
    # layer 1 allows everything here: RLS alone hides B's rows and the free plan's
    for user, company in (("t-pro", B), ("t-free", A), ("t-nobody", A)):
        assert changes(texts, token, user, company, "view=ranked").status_code == 404


def test_section_changes_hidden_without_filings_grant(texts, token):
    # layer 1 allows everything here: RLS alone hides B's and the free plan's rows
    for user, company in (("t-pro", B), ("t-free", A), ("t-nobody", A)):
        assert changes(texts, token, user, company).status_code == 404


def test_section_changes_go_stale_after_a_reparse(texts, token):
    texts.app_conn.execute("UPDATE silver.filing_sections SET parsed_at = now() + interval '1 s'"
                           " WHERE accession_no = %s", (f"{A}-25-000002",))
    [s] = changes(texts, token, "t-pro", A).json()["sections"]
    assert s["stale"] is True and s["changes"] == []


def test_section_change_rows_hidden_without_a_grant(texts):
    with texts.app_conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute("SELECT set_config('app.user_id', 't-pro', true)")
        for table in ("gold.section_diffs", "gold.section_changes", "gold.section_search"):
            cur.execute(f"SELECT DISTINCT company_id FROM {table}"  # noqa: S608
                        " WHERE company_id IN (%s, %s)", (A, B))
            assert cur.fetchall() == [(A,)], table
        cur.execute("SELECT set_config('app.user_id', 't-free', true)")
        cur.execute("SELECT count(*) FROM gold.section_search")
        assert cur.fetchone() == (0,)
        cur.execute("RESET ROLE")


def search(client, token, user, query):
    r = client.get(f"/v1/search?{query}", headers=token(user))
    assert r.status_code == 200, r.text
    return r.json()["results"]


def test_search_respects_rls(texts, token):
    # AllowAll lists A and B as readable; RLS still hides B
    hits = search(texts, token, "t-pro", "q=secret")
    assert [(h["company_id"], h["snippet"]) for h in hits] == [(A, "«secret» risks")]
    assert hits[0]["citation"]["char_end"] == 12
    assert search(texts, token, "t-pro", f"q=secret&company={B}") == []
    assert search(texts, token, "t-free", "q=risks") == []


def test_search_filters_and_pagination(texts, token):
    both = search(texts, token, "t-pro", "q=risks")
    assert [h["citation"]["accession_no"] for h in both] == [f"{A}-26-000002",
                                                            f"{A}-25-000002"]
    assert search(texts, token, "t-pro", "q=risks&from=2026-01-01")[0]["citation"][
        "filed"] == "2026-02-01"
    assert len(search(texts, token, "t-pro", "q=risks&to=2025-12-31")) == 1
    assert search(texts, token, "t-pro", "q=risks&form=10-Q") == []
    assert search(texts, token, "t-pro", "q=risks&section=7") == []
    r = texts.get("/v1/search?q=risks&limit=1", headers=token("t-pro")).json()
    assert r["has_more"] is True and len(r["results"]) == 1
    r = texts.get("/v1/search?q=risks&limit=1&offset=1", headers=token("t-pro")).json()
    assert r["has_more"] is False
    assert r["results"][0]["citation"]["accession_no"] == f"{A}-25-000002"


def test_search_ranks_denser_matches_first(texts, token):
    conn = texts.app_conn
    for acc, text in ((f"{A}-26-000003", "export controls on chips; export controls again"),
                      (f"{A}-26-000004", "export rules and, much later, some controls")):
        conn.execute(
            "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed,"
            " document_name, url, content_gzip, content_sha256, size_bytes)"
            " VALUES (%s, %s, '10-Q', '2026-05-01', 'd.htm', 'u', '\\x00', 'x', 1)", (acc, A))
        conn.execute(
            "INSERT INTO silver.filing_sections (accession_no, section_key, parser,"
            " parser_version, item, title, text, char_start, char_end)"
            " VALUES (%s, 'II-1A', 'python', 't', '1A', 'Risk Factors', %s, 100, 150)",
            (acc, text))
        conn.execute("SELECT gold.refresh_section_search(ARRAY[%s])", (acc,))
    hits = search(texts, token, "t-pro", "q=export+controls")
    assert [h["citation"]["accession_no"] for h in hits] == [f"{A}-26-000003",
                                                            f"{A}-26-000004"]
    assert hits[0]["rank"] > hits[1]["rank"]
    assert hits[0]["citation"]["char_start"] == 100  # section offset + passage offset
    # phrase search only matches adjacent words
    assert [h["citation"]["accession_no"] for h in
            search(texts, token, "t-pro", 'q="export controls"')] == [f"{A}-26-000003"]


def test_search_quoted_phrase_date_order_and_total(texts, token):
    """#80: a quoted phrase matches the words together only; #145: newest first and a total."""
    conn = texts.app_conn
    for acc, filed, text in (
            (f"{A}-25-000003", "2025-05-01", "export controls on chips; export controls again"),
            (f"{A}-26-000004", "2026-05-01", "new export controls"),
            (f"{A}-26-000005", "2026-06-01", "export rules and, much later, some controls")):
        conn.execute(
            "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed,"
            " document_name, url, content_gzip, content_sha256, size_bytes)"
            " VALUES (%s, %s, '10-Q', %s, 'd.htm', 'u', '\\x00', 'x', 1)", (acc, A, filed))
        conn.execute(
            "INSERT INTO silver.filing_sections (accession_no, section_key, parser,"
            " parser_version, item, title, text, char_start, char_end)"
            " VALUES (%s, 'II-1A', 'python', 't', '1A', 'Risk Factors', %s, 100, 150)",
            (acc, text))
        conn.execute("SELECT gold.refresh_section_search(ARRAY[%s])", (acc,))
    accs = lambda q: [h["citation"]["accession_no"]  # noqa: E731
                      for h in search(texts, token, "t-pro", q)]
    assert accs("q=export+controls") == [f"{A}-25-000003", f"{A}-26-000004", f"{A}-26-000005"]
    assert accs("q=%22export+controls%22") == [f"{A}-25-000003", f"{A}-26-000004"]
    assert accs("q=export+controls&sort=date") == [f"{A}-26-000005", f"{A}-26-000004",
                                                   f"{A}-25-000003"]
    body = texts.get("/v1/search?q=export+controls&limit=1&sort=date",
                     headers=token("t-pro")).json()
    assert body["total"] == 3 and body["has_more"] is True and body["sort"] == "date"
    body = texts.get("/v1/search?q=%22export+controls%22&limit=1&offset=1",
                     headers=token("t-pro")).json()
    assert body["total"] == 2 and body["has_more"] is False


def test_every_session_has_a_statement_timeout(client):
    """SET LOCAL ROLE skips role settings, so Store must set the timeout itself."""
    store = Store(OneConnection(client.app_conn))
    with store._session("t-free") as cur:
        cur.execute("SHOW statement_timeout")
        assert cur.fetchone()["statement_timeout"] == "5s"
    with pytest.raises(psycopg.errors.QueryCanceled), store._session("t-free") as cur:
        cur.execute("SELECT pg_sleep(6)")


# Open access (migration 030): the public catalog, as layer 2 sees it ---------------------------

def open_catalog(conn, *companies, tiers=("financials", "filings")):
    """What authz-sync writes while the app database's public_catalogs row says public."""
    conn.cursor().executemany(
        "INSERT INTO authz.public_access (company_id, tier) VALUES (%s, %s)",
        [(c, t) for c in companies for t in tiers])


def visible(conn, user, sql, params=()):
    """Rows `sql` returns as query_api with app.user_id = user (None: never set), inside a
    savepoint that is rolled back, so the setting can't leak into the next call."""
    with conn.transaction(force_rollback=True), conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        if user is not None:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        cur.execute(sql, params)
        return cur.fetchall()


RLS_TABLES = {  # every table with a granted_* policy, and how to find a company's rows
    "gold.quarterly_financials": "company_id", "gold.derived_financials": "company_id",
    "gold.segment_financials": "company_id", "gold.filing_sections": "company_id",
}


def test_public_catalog_readable_by_any_signed_in_user(client, token):
    open_catalog(client.app_conn, B)
    # t-reader has tiers but no grant at all; t-free has a grant on A only
    assert len(financials(client, token, "t-reader", B)) == 1
    assert sections(client, token, "t-reader", B)[0]["text"] == "secret risks"
    assert len(financials(client, token, "t-free", B)) == 1
    assert len(segments(client, token, "t-reader", B)) == 1
    for table, col in RLS_TABLES.items():
        assert visible(client.app_conn, "t-reader",
                       f"SELECT DISTINCT {col} FROM {table}"  # noqa: S608
                       f" WHERE {col} IN (%s, %s)", (A, B)) == [(B,)], table


INSIDER_XML = (  # an ownershipDocument as filed, cut down: one owner, one sale
    "<ownershipDocument><documentType>4</documentType><periodOfReport>2026-09-16</periodOfReport>"
    "<issuer><issuerCik>{cik}</issuerCik></issuer><reportingOwner><reportingOwnerId>"
    "<rptOwnerCik>0001588670</rptOwnerCik><rptOwnerName>Secret Insider</rptOwnerName>"
    "</reportingOwnerId><reportingOwnerRelationship><isOfficer>1</isOfficer>"
    "<officerTitle>CFO</officerTitle></reportingOwnerRelationship></reportingOwner>"
    "<nonDerivativeTable><nonDerivativeTransaction><securityTitle><value>Common</value>"
    "</securityTitle><transactionDate><value>2026-09-16</value></transactionDate>"
    "<transactionCoding><transactionCode>S</transactionCode></transactionCoding>"
    "<transactionAmounts><transactionShares><value>100</value></transactionShares>"
    "<transactionPricePerShare><value>2.5</value></transactionPricePerShare>"
    "<transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>"
    "</transactionAmounts></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>")


def add_insider_filings(conn):
    for cid in (A, B):
        conn.execute("INSERT INTO bronze.ownership_documents (accession_no, cik, form, filed, url,"
                     " xml, content_sha256) VALUES (%s, %s, '4', '2026-09-18', 'u', %s, 'x')",
                     (f"{cid}-26-000009", cid, INSIDER_XML.format(cik=cid)))
    conn.execute("SELECT silver.refresh_insider_filings(%s)",
                 ([f"{A}-26-000009", f"{B}-26-000009"],))


def test_insider_filings_respect_rls(client, token):
    """#117 (054): insider filings are filing content, the filings tier, RLS on company_id; the
    allow-all authorizer lists both companies, RLS still hides B's."""
    conn = client.app_conn
    add_insider_filings(conn)
    for table in ("silver.insider_filings", "silver.insider_transactions"):
        assert visible(conn, "t-pro", f"SELECT DISTINCT company_id FROM {table}") == [(A,)]  # noqa: S608
        assert visible(conn, "t-free", f"SELECT count(*) FROM {table}") == [(0,)]  # noqa: S608
        for user in (None, ""):
            assert visible(conn, user, f"SELECT count(*) FROM {table}") == [(0,)]  # noqa: S608
    r = client.get(f"/v1/companies/{A}/insiders", headers=token("t-pro"))
    assert r.status_code == 200, r.text
    [f] = r.json()["filings"]
    assert f["owners"] == [{"cik": "0001588670", "name": "Secret Insider", "roles": ["CFO"]}]
    assert f["lines"][0]["value"] == "250.0" and f["summary"] == {"sold": {"shares": "100",
                                                                           "value": "250.0"}}
    assert client.get(f"/v1/companies/{B}/insiders", headers=token("t-pro")).json()["filings"] == []
    open_catalog(conn, B)
    assert visible(conn, "t-reader", "SELECT company_id FROM silver.insider_filings") == [(B,)]


def test_insider_pages_never_skip_the_rest_of_a_day(client, token):
    """Pre-ship review: paging with before=<last day> lost the day's other filings. Five filings,
    three on one day, two a page: the cursor walks all five once, newest first, under RLS."""
    conn = client.app_conn
    days = ["2026-09-18", "2026-09-17", "2026-09-17", "2026-09-17", "2026-09-10"]
    accessions = [f"{A}-26-00002{n}" for n in range(5)]
    for acc, day in zip(accessions, days, strict=True):
        conn.execute("INSERT INTO bronze.ownership_documents (accession_no, cik, form, filed, url,"
                     " xml, content_sha256) VALUES (%s, %s, '4', %s, 'u', %s, 'x')",
                     (acc, A, day, INSIDER_XML.format(cik=A)))
    conn.execute("SELECT silver.refresh_insider_filings(%s)", (accessions,))
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        body = client.get(f"/v1/companies/{A}/insiders", params=params,
                          headers=token("t-pro")).json()
        seen += [(f["filed"], f["accession_no"]) for f in body["filings"]]
        pages += 1
        cursor = body["next_cursor"]
        if not body["has_more"]:
            break
    assert pages == 3 and cursor is None
    assert seen == sorted(zip(days, accessions, strict=True), reverse=True)
    # the old way: before=<the first page's last day> skips that day's third filing
    old = client.get(f"/v1/companies/{A}/insiders", params={"limit": 2, "before": "2026-09-17"},
                     headers=token("t-pro")).json()
    assert [f["filed"] for f in old["filings"]] == ["2026-09-10"]


def test_filing_figures_say_their_currency(client, token):
    """057: gold.filing_figures returns each figure's unit: a euro filing's facts and the net
    debt computed from them are in EUR (the thesis check words them so)."""
    conn = client.app_conn
    for tag, metric, value in (("LongTermDebtNoncurrent", "long_term_debt", 1128010000),
                               ("CashAndCashEquivalentsAtCarryingValue", "cash", 329067000)):
        conn.execute(
            "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
            " value, accession_no, form, filed) VALUES (%s, %s, %s, 'EUR', NULL, '2026-09-30',"
            " %s, %s, '20-F', '2026-12-01')", (A, metric, tag, value, f"{A}-26-000010"))
    r = client.get(f"/v1/companies/{A}/filings/{A}-26-000010/figures", headers=token("t-free"))
    assert r.status_code == 200, r.text
    units = {f["metric"]: f["unit"] for f in r.json()["figures"]}
    assert {"long_term_debt", "cash", "net_debt", "cash_and_investments"} <= set(units)
    assert set(units.values()) == {"EUR"}


def test_text_kpis_respect_rls(client, token):
    """F8b (H11): the backlog read from a 10-K's MD&A is section text, the filings tier; the
    allow-all authorizer lets every call through, RLS alone hides B's and the free plan's."""
    conn = client.app_conn
    for cid in (A, B):
        conn.execute(
            "INSERT INTO silver.filing_sections (accession_no, section_key, parser, parser_version,"
            " item, title, text, char_start, char_end) VALUES (%s, '7', 'python', 't', '7', 'MD&A',"
            " 'Our backlog was $1.2 billion at December 31, 2025.', 100, 150)",
            (f"{cid}-26-000002",))

    def backlog(user, company):
        r = client.get(f"/v1/companies/{company}/text-kpis", headers=token(user))
        assert r.status_code == 200, r.text
        return [(p["period_end"], p["value"]) for p in r.json()["metrics"]["backlog"]["points"]]

    assert backlog("t-pro", A) == [("2025-12-31", 1.2e9)]
    assert backlog("t-pro", B) == [] and backlog("t-free", A) == []


def test_public_catalog_needs_a_user_id(client):
    open_catalog(client.app_conn, A, B)
    for user in (None, ""):
        for table in (*RLS_TABLES, "silver.filing_sections", "authz.public_access"):
            assert visible(client.app_conn, user,
                           f"SELECT count(*) FROM {table}") == [(0,)], (user, table)  # noqa: S608
        assert visible(client.app_conn, user,
                       "SELECT authz.can_read(%s, 'financials')", (B,)) == [(False,)]


def test_public_catalog_stays_within_the_users_tiers(client, token):
    open_catalog(client.app_conn, B)
    assert len(financials(client, token, "t-free", B)) == 1
    assert sections(client, token, "t-free", B) == []  # t-free's plan has no filings tier
    assert financials(client, token, "t-nobody", B) == []  # no subscription, no tiers


def test_public_catalog_by_tier(client, token):
    open_catalog(client.app_conn, B, tiers=("financials",))
    assert len(financials(client, token, "t-reader", B)) == 1
    assert sections(client, token, "t-reader", B) == []


def test_private_grants_still_per_user_with_the_catalog_public(client, token):
    open_catalog(client.app_conn, B)  # A stays outside the catalog: private data
    assert len(financials(client, token, "t-free", A)) == 1
    assert sections(client, token, "t-pro", A)[0]["text"] == "secret risks"
    assert financials(client, token, "t-reader", A) == []
    assert sections(client, token, "t-reader", A) == []
    conn = client.app_conn
    assert visible(conn, "t-pro", "SELECT authz.can_read(%s, 'filings')", (A,)) == [(True,)]
    assert visible(conn, "t-reader", "SELECT authz.can_read(%s, 'filings')", (A,)) == [(False,)]
    assert visible(conn, "t-reader", "SELECT authz.can_read(%s, 'filings')", (B,)) == [(True,)]
    # nobody sees another user's private rows in the authz tables either
    assert visible(conn, "t-reader", "SELECT count(*) FROM authz.user_access") == [(0,)]
    assert visible(conn, "t-reader", "SELECT tier FROM authz.user_tiers ORDER BY tier") == [
        ("filings",), ("financials",)]


def test_closing_the_catalog_hides_it_again(client, token):
    open_catalog(client.app_conn, B)
    assert len(financials(client, token, "t-reader", B)) == 1
    client.app_conn.execute("DELETE FROM authz.public_access")
    assert financials(client, token, "t-reader", B) == []


def test_store_reads_the_catalog_list_under_rls(client):
    store = Store(OneConnection(client.app_conn))
    assert store.catalog_companies("t-reader") == frozenset()  # closed
    open_catalog(client.app_conn, B)
    assert store.catalog_companies("t-reader") == {B}
    assert store.catalog_companies("") == frozenset()  # no user, no catalog


# Open access without an id list (migration 032): RLS alone, as with the catalog public ---------

SEARCH_FN = "gold.search_passages(tsquery, text[], text, text, date, date, int, int)"
SEARCH_PAGE_FN = "gold.search_page(tsquery, text[], text, text, date, date, text, int, int)"


class PublicCatalogSaysOpenFga:
    """Layer 1 says the catalog is public to everyone and lists A for everyone too (the catalog's
    ids are the real ones, under RLS): only layer 2 can hide A from those it wasn't granted to."""

    def can(self, *_):
        return True

    def access(self, _):
        return Access(frozenset({"financials", "filings"}), {A: {"financials", "filings"}})


def public_client(conn):
    c = TestClient(create_app(PublicCatalogSaysOpenFga(), Store(OneConnection(conn)), SECRET))
    c.app_conn = conn
    return c


def passages(conn, user, companies=None, query="secret"):
    return visible(conn, user,
                   "SELECT company_id, section_key, passage, filed, rank > 0 FROM"
                   " gold.search_passages(websearch_to_tsquery('english', %s), %s::text[], NULL,"
                   " NULL, NULL, NULL, 10, 0) ORDER BY company_id",
                   (query, companies))


def add_quarter(conn, company, metric, period_end, value):
    conn.execute(
        "INSERT INTO gold.quarterly_financials (company_id, metric, period_end, value,"
        " unit, value_source, source_tag, accession_no, filed, source_accessions,"
        " ingested_at) VALUES (%s, %s, %s, %s, 'USD', 'reported', 't', 'acc', '2026-08-01',"
        " ARRAY['acc'], now())", (company, metric, period_end, value))


def test_latest_value_is_latest_financials_under_rls(client):
    conn = client.app_conn
    add_quarter(conn, A, "revenue", "2026-03-31", 2)  # older than the fixture's 2026-06-30
    add_quarter(conn, A, "cash", "2026-06-30", 3)  # instant: also the ttm span
    for span, metric, value in (("quarter", "revenue", 1), ("ttm", "net_margin", "0.25"),
                                ("quarter", "cash", 3), ("ttm", "cash", 3)):
        rows = visible(conn, "t-free", "SELECT period_end::text, value, accession_no, filed::text"
                       " FROM gold.latest_value(%s, %s, %s)", (A, span, metric))
        assert rows == visible(conn, "t-free", "SELECT period_end::text, value, accession_no,"
                               " filed::text FROM gold.latest_financials"
                               " WHERE company_id = %s AND span = %s AND metric = %s",
                               (A, span, metric)), (span, metric)
        assert len(rows) == 1 and str(rows[0][1]).rstrip("0").rstrip(".") == str(value)
    # a duration metric has no ttm from the quarterly table, and nothing without a read grant
    assert visible(conn, "t-free", "SELECT * FROM gold.latest_value(%s, 'ttm', 'revenue')",
                   (A,)) == []
    assert visible(conn, "t-free", "SELECT * FROM gold.latest_value(%s, 'quarter', 'revenue')",
                   (B,)) == []
    for user in (None, "", "t-nobody"):
        assert visible(conn, user, "SELECT * FROM gold.latest_value(%s, 'quarter', 'revenue')",
                       (A,)) == []


def test_screen_without_an_id_list_reads_values_only_under_rls(client):
    """Metadata for both; A's value only for who may read it; never a value filter's match."""
    store = Store(OneConnection(client.app_conn))
    both = Screen(columns=["revenue_q"], company_ids=[A, B], coverage=False, sort="company_id")
    compiled = compile_screen(both, None)
    assert "granted" not in compiled.params
    rows = store.screen("t-free", compiled)
    assert [(r["company_id"], r["v0"]) for r in rows] == [(A, 1), (B, None)]
    assert rows[1]["src0"] is None and rows[0]["src0"]["accession_no"] == f"{A}-26-000001"
    assert [r["v0"] for r in store.screen("t-nobody", compiled)] == [None, None]
    assert [r["v0"] for r in store.screen("", compiled)] == [None, None]

    filtered = Screen(columns=["revenue_q"], company_ids=[A, B], coverage=False, sort="company_id",
                      filters=[parse_filter("revenue_q:gte:0")])
    assert [r["company_id"] for r in store.screen("t-free", compile_screen(filtered, None))] == [A]
    assert store.screen("", compile_screen(filtered, None)) == []

    only = Screen(columns=["revenue_q"], company_ids=[A, B], coverage=False, sort="company_id",
                  granted_only=True)
    assert [r["company_id"] for r in store.screen("t-free", compile_screen(only, None))] == [A]
    assert store.screen("", compile_screen(only, None)) == []
    # the sort reads values too: an unreadable one sorts last, whatever it is
    add_quarter(client.app_conn, B, "revenue", "2026-09-30", 999)
    ranked = Screen(columns=["revenue_q"], company_ids=[A, B], coverage=False, sort="-revenue_q")
    assert [r["company_id"] for r in store.screen("t-free", compile_screen(ranked, None))] == [A, B]


def test_screen_with_a_list_gates_on_it_and_rls(client):
    store = Store(OneConnection(client.app_conn))
    both = Screen(columns=["revenue_q"], company_ids=[A, B], coverage=False, sort="company_id")
    assert [r["v0"] for r in store.screen("t-free", compile_screen(both, [A]))] == [1, None]
    assert [r["v0"] for r in store.screen("t-free", compile_screen(both, []))] == [None, None]
    assert [r["v0"] for r in store.screen("t-free", compile_screen(both, [B]))] == [None, None]


def test_screener_route_with_the_catalog_public_is_bounded_by_rls(client, token):
    open_catalog(client.app_conn, B)
    r = public_client(client.app_conn).get(
        "/v1/screener?columns=revenue_q&company_id=" + A + "&company_id=" + B,
        headers=token("t-reader"))
    values = {x["company_id"]: x["values"]["revenue_q"] for x in r.json()["rows"]}
    assert r.status_code == 200 and values == {A: None, B: "1"}  # A: private to t-free, t-pro


def test_latest_filings_without_an_id_list_are_bounded_by_rls(client, token):
    conn = client.app_conn
    open_catalog(conn, B)
    c = public_client(conn)
    latest = lambda user: c.get("/v1/filings/latest", headers=token(user)).json()["filings"]  # noqa: E731
    assert [f["company_id"] for f in latest("t-reader")] == [B]
    assert sorted(f["company_id"] for f in latest("t-pro")) == [A, B]  # private grant + catalog
    assert latest("t-free") == []  # the plan has no filings tier, whatever layer 1 said
    assert Store(OneConnection(conn)).latest_filings("", None, 10) == []
    assert Store(OneConnection(conn)).latest_filings("t-nobody", None, 10) == []


def test_latest_filings_rows_are_the_same_with_and_without_a_list(client):
    conn = client.app_conn
    open_catalog(conn, A, B)
    store = Store(OneConnection(conn))
    key = lambda rows: sorted((r["company_id"], r["accession_no"], r["sections"]) for r in rows)  # noqa: E731
    assert key(store.latest_filings("t-reader", None, 10)) == key(
        store.latest_filings("t-reader", [A, B], 10))


def test_search_passages_applies_the_read_rule(client):
    conn = client.app_conn
    for cid in (A, B):
        conn.execute("SELECT gold.refresh_section_search(ARRAY[%s])", (f"{cid}-26-000002",))
    assert [r[0] for r in passages(conn, "t-pro")] == [A]  # closed: its grant only
    assert passages(conn, "t-pro", [B]) == []  # p_companies can only narrow
    assert [r[0] for r in passages(conn, "t-pro", [A, B])] == [A]
    assert passages(conn, "t-free") == []  # a grant at another tier
    assert passages(conn, "t-reader") == []
    for user in (None, ""):
        assert passages(conn, user) == [] and passages(conn, user, [A, B]) == []
    open_catalog(conn, B)
    assert [r[0] for r in passages(conn, "t-reader")] == [B]
    assert [r[0] for r in passages(conn, "t-reader", [A, B])] == [B]
    assert [r[0] for r in passages(conn, "t-pro")] == [A, B]
    assert passages(conn, "t-free") == []  # public, but no filings tier on the plan
    assert passages(conn, "t-nobody") == []
    for user in (None, ""):
        assert passages(conn, user) == []
    open_catalog(conn, A, tiers=("financials",))  # another tier: doesn't open filings
    assert [r[0] for r in passages(conn, "t-reader")] == [B]


def test_search_passages_is_locked_down(client):
    conn = client.app_conn
    for fn in ((SEARCH_FN,), (SEARCH_PAGE_FN,)):
        assert conn.execute("SELECT has_function_privilege('query_api', %s, 'EXECUTE')",
                            fn).fetchone() == (True,)
        assert conn.execute("SELECT has_function_privilege('ops_reader', %s, 'EXECUTE')",
                            fn).fetchone() == (False,)
        secdef, config = conn.execute(
            "SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure",
            fn).fetchone()
        assert secdef is True and config == ["search_path=pg_catalog, pg_temp"]


def test_search_page_applies_the_read_rule(client):
    """052's gold.search_page is 032's read rule with an order and a total: the same cases."""
    conn = client.app_conn
    for cid in (A, B):
        conn.execute("SELECT gold.refresh_section_search(ARRAY[%s])", (f"{cid}-26-000002",))

    def page(user, companies=None, sort="relevance"):
        return visible(conn, user,
                       "SELECT company_id, total FROM gold.search_page(websearch_to_tsquery("
                       "'english', 'secret'), %s::text[], NULL, NULL, NULL, NULL, %s, 10, 0)"
                       " ORDER BY company_id", (companies, sort))
    assert page("t-pro") == [(A, 1)]
    assert page("t-pro", [B]) == []
    assert page("t-free") == [] and page("t-reader") == []
    for user in (None, ""):
        assert page(user) == [] and page(user, [A, B]) == []
    open_catalog(conn, B)
    assert page("t-reader") == [(B, 1)]
    assert page("t-pro", sort="date") == [(A, 2), (B, 2)]
    with pytest.raises(psycopg.errors.RaiseException):
        page("t-pro", sort="rank DESC; --")


def test_search_with_the_catalog_public_is_bounded_by_rls(client, token):
    conn = client.app_conn
    for cid in (A, B):
        conn.execute("SELECT gold.refresh_section_search(ARRAY[%s])", (f"{cid}-26-000002",))
    open_catalog(conn, B)
    c = public_client(conn)
    found = lambda user, q="q=secret": [  # noqa: E731
        h["company_id"] for h in c.get(f"/v1/search?{q}", headers=token(user)).json()["results"]]
    assert found("t-reader") == [B]  # A is private: never returned, whatever layer 1 said
    assert sorted(found("t-pro")) == [A, B]
    assert found("t-free") == [] and found("t-nobody") == []
    body = c.get("/v1/search?q=secret", headers=token("t-reader")).json()
    assert body["results"][0]["snippet"] == "«secret» risks"
    assert body["searched_from"] is None  # few matches: widened to all time
    # and the store with no user at all
    assert Store(OneConnection(conn)).search_sections("", None, "secret", None, None, None, None,
                                                      10, 0) == []


def add_filing(conn, cik):
    conn.execute(
        "INSERT INTO bronze.filings (accession_no, cik, form_type, filed_at, payload,"
        " kafka_partition, kafka_offset)"
        " VALUES (%s, %s, '10-K', '2026-02-01 12:00+00', '{}', 0, 0)",
        (f"{cik}-26-000002", cik))
    conn.execute(
        "INSERT INTO bronze.submission_filings (accession_no, cik, form, filing_date)"
        " VALUES (%s, %s, '8-K', '2026-01-15')", (f"{cik}-26-000009", cik))


def test_filing_index_without_a_list_covers_readable_companies_under_rls(client, token):
    conn = client.app_conn
    add_filing(conn, A)
    add_filing(conn, B)
    open_catalog(conn, B)
    store = Store(OneConnection(conn))

    def index(user, start=None):
        return [(r["accession_no"], r["sections"]) for r in store.filing_index(
            user, None, [], start, None, 10, 0, None)]
    assert index("t-reader") == [(f"{B}-26-000002", 1), (f"{B}-26-000009", 0)]
    assert index("t-reader", date(2026, 1, 20)) == [(f"{B}-26-000002", 1)]
    # t-free reads financials (A granted, B public): in the index, no section count from RLS
    assert sorted(index("t-free")) == [(f"{c}-26-00000{n}", 0) for c in (A, B) for n in (2, 9)]
    assert sorted(a for a, _ in index("t-pro")) == [
        f"{A}-26-000002", f"{A}-26-000009", f"{B}-26-000002", f"{B}-26-000009"]
    assert index("") == [] and index("t-nobody") == []
    r = public_client(conn).get("/v1/filing-index?limit=1", headers=token("t-reader"))
    assert r.status_code == 200 and [f["company_id"] for f in r.json()["filings"]] == [B]


def test_filed_on_reaches_both_branches_of_the_filing_index(client):
    conn = client.app_conn
    add_filing(conn, A)
    rows = conn.execute("SELECT accession_no, filing_date::text, filed_on::text FROM silver.filings"
                        " WHERE cik = %s ORDER BY 1", (A,)).fetchall()
    assert rows == [(f"{A}-26-000002", "2026-02-01", "2026-02-01"),
                    (f"{A}-26-000009", "2026-01-15", "2026-01-15")]


def test_peers_without_a_list_read_revenue_under_rls(client):
    conn = client.app_conn
    conn.execute("UPDATE silver.companies SET sic = '2080'")
    conn.execute(
        "INSERT INTO gold.derived_financials (company_id, span, metric, period_end, value, unit,"
        " value_source, inputs, accession_no, filed, source_accessions, ingested_at)"
        " VALUES (%s, 'ttm', 'revenue', '2026-06-30', 500e9, 'USD', 'ttm', '{}', 'a',"
        " '2026-08-01', ARRAY['a'], now())", (B,))
    store = Store(OneConnection(conn))
    [row] = store.peers("t-free", A, None, None, 10)
    assert row["company_id"] == B and row["revenue_band"] is None  # t-free can't read B
    open_catalog(conn, B)
    [row] = store.peers("t-free", A, None, None, 10)
    assert row["revenue_band"] == 4
    assert store.peers("t-nobody", A, None, None, 10, granted_only=True) == []
    [row] = store.peers("t-free", A, None, None, 10, granted_only=True)
    assert row["company_id"] == B


def test_expected_filings_recent_covers_only_readable_companies(client):
    conn = client.app_conn
    for company in (A, B):
        for i, (end, filed) in enumerate((("2026-03-31", "2026-05-01"),
                                          ("2026-06-30", "2026-08-01"))):
            conn.execute(
                "INSERT INTO bronze.submission_filings (accession_no, cik, form, filing_date,"
                " report_date) VALUES (%s, %s, '10-Q', %s, %s)",
                (f"{company}-26-10000{i}", company, filed, end))
    open_catalog(conn, B)
    store = Store(OneConnection(conn))
    as_of = date(2026, 8, 15)

    def estimated(user):
        return {r["company_id"] for r in store.expected_filings(user, None, as_of, 2)}
    assert estimated("t-reader") == {B}
    assert estimated("t-pro") == {A, B}
    assert estimated("") == set() and estimated("t-nobody") == set()
    rows = lambda fn: [  # noqa: E731
        {k: r[k] for k in ("form", "period_end", "expected_date", "lag_samples", "status")}
        for r in fn]
    assert rows(store.expected_filings("t-reader", None, as_of, 2)) == rows(
        store.expected_filings("t-reader", [B], as_of, 2))


def business_rows(conn):
    """A's and B's Item 1 compared (migration 047): profiles, a peer row each way, and A's 10-K
    naming B as a competitor."""
    for cid, other in ((A, B), (B, A)):
        conn.execute("INSERT INTO gold.business_profiles (company_id, accession_no, section_key,"
                     " filed, parser, terms, algorithm) VALUES (%s, %s, '1', '2026-02-01',"
                     " 'python', '{secret term}', 'business-1')", (cid, f"{cid}-26-000002"))
        conn.execute("INSERT INTO gold.business_peers (company_id, peer_id, similarity, terms)"
                     " VALUES (%s, %s, 0.4, '{secret term}')", (cid, other))
    conn.execute("INSERT INTO gold.business_mentions (company_id, named_id, accession_no,"
                 " section_key, char_start, char_end) VALUES (%s, %s, %s, '1', 0, 12)",
                 (A, B, f"{A}-26-000002"))


def test_business_peers_need_filings_on_both_companies(client, token):
    """F4: similarities come from Item 1 text. t-pro reads A's filings but not B's: A's profile
    and its own sentence naming B (A's text) show, the A-B similarity and its terms don't."""
    business_rows(client.app_conn)
    body = client.get(f"/v1/companies/{A}/peers", headers=token("t-pro")).json()
    assert body["basis"] == "business" and body["business"]["terms"] == ["secret term"]
    (b,) = body["peers"]
    assert b["company_id"] == B and b["match"]["similarity"] == 0 and b["match"]["terms"] == []
    assert b["match"]["named_by_target"]["char_end"] == 12
    assert b["values"]["revenue_ttm"] is None  # and no financials on B either
    # financials only: no Item 1 for this caller, so SIC and size
    free = client.get(f"/v1/companies/{A}/peers", headers=token("t-free")).json()
    assert free["basis"] == "sic" and free["business"] is None


def test_sectors_co_registrants_and_peer_check_run_as_query_api(client, token):
    """046's view and 047's function are public metadata the query_api role can read."""
    r = client.get("/v1/sectors", headers=token("t-nobody"))
    assert r.status_code == 200, r.text
    staples = next(s for s in r.json()["sectors"] if s["name"] == "Consumer Staples")
    beverages = next(i for i in staples["industries"] if i["name"] == "Beverages")
    assert beverages["companies"] >= 1 and "2080-2089" in beverages["sic"]
    rows = client.get(f"/v1/screener?company_id={A}&company_id={B}",
                      headers=token("t-free")).json()["rows"]
    by_id = {x["company_id"]: x for x in rows}
    assert (by_id[A]["sector"], by_id[A]["sector_industry"]) == ("Consumer Staples", "Beverages")
    assert by_id[B]["sector"] is None and by_id[A]["co_registrants"] == []
    check = client.get(f"/v1/companies/{A}/peers/check/{B}", headers=token("t-free"))
    assert check.status_code == 200, check.text
    assert check.json()["ok"] is True  # B has gold rows; AllowAll reads it


def test_components_and_figure_facts_read_only_what_the_rule_allows(client, token):
    """F5: gold.figure_facts (049) is SECURITY DEFINER over silver.financial_facts' values, so
    its only rule is authz.readable_companies: t-free reads A's facts, not B's, whatever layer 1
    says."""
    conn = client.app_conn
    for cid in (A, B):
        conn.execute(
            "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
            " value, accession_no, form, filed) VALUES (%s, 'revenue', 'Revenues', 'USD',"
            " '2026-04-01', '2026-06-30', 5, %s, '10-Q', '2026-08-01')", (cid, f"{cid}-26-000001"))
    conn.execute("UPDATE gold.quarterly_financials SET period_start = '2026-04-01'"
                 " WHERE company_id IN (%s, %s) AND metric = 'revenue'", (A, B))
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute("SELECT set_config('app.user_id', 't-free', true)")
        seen = {c: cur.execute("SELECT tag, value FROM gold.figure_facts(%s, 'revenue',"
                               " '2026-06-30')", (c,)).fetchall() for c in (A, B)}
        cur.execute("SELECT set_config('app.user_id', '', true)")
        nobody = cur.execute("SELECT count(*) FROM gold.figure_facts(%s, 'revenue', '2026-06-30')",
                             (A,)).fetchone()
        cur.execute("RESET ROLE")
    assert seen == {A: [("Revenues", 5)], B: []} and nobody == (0,)
    url = "/components?metric=revenue&period_end=2026-06-30"
    mine = client.get(f"/v1/companies/{A}{url}", headers=token("t-free"))
    assert mine.status_code == 200, mine.text
    assert [(f["tag"], f["used"]) for f in mine.json()["facts"]] == [("Revenues", True)]
    # layer 1 allows everything here: RLS alone hides B's figure
    assert client.get(f"/v1/companies/{B}{url}", headers=token("t-free")).status_code == 404


def test_filing_figures_compute_from_one_filing_and_read_only_what_the_rule_allows(client, token):
    """H2: gold.filing_figures (051) is SECURITY DEFINER over silver like 049's figure_facts. It
    gives the cited filing's own net debt (NVIDIA's 10-Q: debt less cash less the securities a
    computed figure left out); t-free reads A's, not B's, and no user reads nothing."""
    conn = client.app_conn
    for cid in (A, B):
        for tag, metric, value in (
                ("LongTermDebtNoncurrent", "long_term_debt", 33370),
                ("CashAndCashEquivalentsAtCarryingValue", "cash", 11490),
                ("ShortTermInvestments", "short_term_investments", 99370),
                ("AssetsCurrent", "current_assets", 140000)):
            conn.execute(
                "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start,"
                " period_end, value, accession_no, form, filed) VALUES (%s, %s, %s, 'USD', NULL,"
                " '2026-06-30', %s, %s, '10-Q', '2026-08-01')",
                (cid, metric, tag, value, f"{cid}-26-000009"))
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute("SELECT set_config('app.user_id', 't-free', true)")
        seen = {c: cur.execute(
            "SELECT metric, value FROM gold.filing_figures(%s, %s) WHERE how = 'computed'"
            " ORDER BY metric", (c, f"{c}-26-000009")).fetchall() for c in (A, B)}
        cur.execute("SELECT set_config('app.user_id', '', true)")
        nobody = cur.execute("SELECT count(*) FROM gold.filing_figures(%s, %s)",
                             (A, f"{A}-26-000009")).fetchone()
        cur.execute("RESET ROLE")
    assert seen == {A: [("cash_and_investments", 11490 + 99370),
                        ("net_debt", 33370 - 11490 - 99370)], B: []}
    assert nobody == (0,)
    mine = client.get(f"/v1/companies/{A}/filings/{A}-26-000009/figures", headers=token("t-free"))
    assert mine.status_code == 200, mine.text
    net = next(f for f in mine.json()["figures"] if f["metric"] == "net_debt")
    assert net["value"] == "-77490" and net["inputs"]["short_term_investments"] == "99370"
    # layer 1 allows everything here: the function's rule alone gives B nothing
    theirs = client.get(f"/v1/companies/{B}/filings/{B}-26-000009/figures",
                        headers=token("t-free"))
    assert theirs.status_code == 200 and theirs.json()["figures"] == []
