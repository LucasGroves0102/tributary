"""Layer 2 on its own: the API wired to an authorizer that allows EVERYTHING, against real
Postgres. Row-level security must still hide what authz.user_access doesn't grant.
Skipped unless TEST_DATABASE_URL is set; every test is rolled back."""

import os
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.formula import CATALOG
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


def test_every_session_has_a_statement_timeout(client):
    """SET LOCAL ROLE skips role settings, so Store must set the timeout itself."""
    import psycopg

    store = Store(OneConnection(client.app_conn))
    with store._session("t-free") as cur:
        cur.execute("SHOW statement_timeout")
        assert cur.fetchone()["statement_timeout"] == "5s"
    with pytest.raises(psycopg.errors.QueryCanceled), store._session("t-free") as cur:
        cur.execute("SELECT pg_sleep(6)")

