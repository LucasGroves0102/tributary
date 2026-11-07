"""UI data over real Postgres (migration 017): expected filing dates, filing recaps, flags and
peers, through the API with both layers. Skipped unless TEST_DATABASE_URL is set; every test is
rolled back."""

import os
from contextlib import contextmanager
from datetime import date

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.authorizer import Access
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

    def access(self, user_id):
        return Access(frozenset(), self.readable(user_id))

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
    assert rev["current"]["not_meaningful"] is None  # a reported figure: nothing to say


def test_recap_says_when_a_margin_isnt_meaningful(recap_conn, token):
    recap_conn.execute(
        "INSERT INTO gold.derived_financials (company_id, span, metric, period_start, period_end,"
        " value, unit, value_source, inputs, accession_no, form, filed, source_accessions,"
        " ingested_at) VALUES (%s, 'quarter', 'gross_margin', '2026-04-01', '2026-06-30', 20.9,"
        " 'ratio', 'computed', %s, %s, '10-Q', '2026-07-30', ARRAY[%s], now())",
        (R, '{"revenue": 420, "gross_profit": 8778}', ACC, ACC))
    r = client(recap_conn).get(f"/v1/filings/{ACC}/recap?metrics=gross_margin",
                               headers=token("t-free"))
    assert r.status_code == 200, r.text
    [margin] = r.json()["metrics"]
    assert float(margin["current"]["value"]) == 20.9
    assert "under $100K" in margin["current"]["not_meaningful"]


def test_a_filter_on_accession_reaches_the_sections_table(recap_conn):
    """033: outputting s.accession_no lets the filter into the DISTINCT ON, as query_api too; on
    d.accession_no it stayed on a Subquery Scan above a sort of every section (recap 503)."""
    with recap_conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute("SELECT set_config('app.user_id', 't-pro', true)")
        cur.execute("EXPLAIN SELECT * FROM gold.filing_sections WHERE accession_no = %s", (ACC,))
        plan = "\n".join(r[0] for r in cur.fetchall())
        cur.execute("RESET ROLE")
    assert ACC not in plan.split("Unique")[0], plan  # no filter left above the DISTINCT ON
    assert ACC in plan


def test_recap_and_flags_respect_rls(recap_conn, token):
    assert recap(recap_conn, token, "t-nobody")["metrics"] == []
    flags = recap(recap_conn, token, "t-pro")["flags"]
    assert [(f["flag"], f["matches"], f["citation"]["char_start"]) for f in flags] == [
        ("impairment", 2, 1023), ("restructuring", 1, 1052)]
    assert "goodwill impairment" in flags[0]["snippet"]


P1, P2, P3, P4 = "9100000011", "9100000012", "9100000013", "9100000014"


class AllowAllCompanies(AllowAll):
    def access(self, user_id):
        return Access(frozenset(), self.readable(user_id))

    def readable(self, _):
        return {c: {"financials"} for c in (P1, P2, P3, P4)}


@pytest.fixture
def peer_conn(conn):
    # P4 is not granted to t-free and sorts first by name: only RLS keeps its revenue out
    for cid, name, sic, revenue in ((P1, "UI Peer target", "3571", 200),
                                    (P2, "UI Peer B", "3571", 150),
                                    (P3, "UI Peer C", "3572", 210),
                                    (P4, "UI Peer A", "3571", 190)):
        conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, sic)"
                     " VALUES (%s, %s, %s, %s)", (cid, name, cid, sic))
        conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                     (cid, cid))
        acc = f"{cid}-26-000001"
        conn.execute(
            "INSERT INTO gold.quarterly_financials (company_id, metric, period_start, period_end,"
            " value, unit, value_source, source_tag, accession_no, filed, source_accessions,"
            " ingested_at) VALUES (%s, 'revenue', '2026-04-01', '2026-06-30', %s, 'USD',"
            " 'reported', 'us-gaap:Revenues', %s, '2026-08-01', ARRAY[%s], now())",
            (cid, revenue // 4, acc, acc))
        conn.execute(
            "INSERT INTO gold.derived_financials (company_id, span, metric, period_start,"
            " period_end, value, unit, value_source, inputs, accession_no, filed,"
            " source_accessions, ingested_at) VALUES (%s, 'ttm', 'revenue', '2025-07-01',"
            " '2026-06-30', %s, 'USD', 'ttm', '{}', %s, '2026-08-01', ARRAY[%s], now())",
            (cid, revenue, acc, acc))
    conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier) VALUES"
                 " ('t-free', %s, 'financials'), ('t-free', %s, 'financials'),"
                 " ('t-free', %s, 'financials')", (P1, P2, P3))
    return conn


def peers(conn, token, user):
    c = TestClient(create_app(AllowAllCompanies(), Store(OneConnection(conn)), SECRET))
    r = c.get(f"/v1/companies/{P1}/peers?columns=revenue_ttm", headers=token(user))
    assert r.status_code == 200, r.text
    return r.json()


def test_peers_rank_by_sic_then_size_band(peer_conn, token):
    body = peers(peer_conn, token, "t-free")
    assert body["company"]["revenue_band"] == "<$100M"
    assert [(p["company_id"], p["match"]["sic_level"], p["match"]["same_revenue_band"],
             p["values"]["revenue_ttm"]) for p in body["peers"]] == [
        (P2, 4, True, "150"), (P4, 4, False, None), (P3, 3, True, "210")]


def test_peers_without_access_rank_by_public_metadata_only(peer_conn, token):
    body = peers(peer_conn, token, "t-nobody")
    assert body["company"]["revenue_band"] is None
    assert [p["company_id"] for p in body["peers"]] == [P4, P2, P3]  # SIC level, then name
    assert all(p["values"]["revenue_ttm"] is None for p in body["peers"])


def test_peers_and_search_can_be_only_granted_companies(peer_conn):
    """A demo sandbox lists only its companies: the SQL filter, not just the client."""
    store = Store(OneConnection(peer_conn))
    ranked = store.peers("t-free", P1, [P1, P3], None, 10, granted_only=True)
    assert [p["company_id"] for p in ranked] == [P3]
    assert {p["company_id"] for p in store.peers("t-free", P1, [P1, P3], None, 10)} \
        == {P2, P3, P4}
    assert [r["company_id"] for r in store.search("t-free", P2)] == [P2]
    assert store.search("t-free", P2, only=[P1, P3]) == []
    assert [r["company_id"] for r in store.search("t-free", P3, only=[P3])] == [P3]


def test_screener_lists_a_data_check_only_for_companies_the_caller_can_read(recap_conn, token):
    """The status page's drill-through: same RLS as everything, so an unreadable company with a
    finding simply isn't there (no oracle), and the check's name goes in as a bind parameter."""
    run = recap_conn.execute("INSERT INTO ops.quality_runs (companies, findings) VALUES (1, 1)"
                             " RETURNING run_id").fetchone()[0]
    recap_conn.execute("INSERT INTO ops.quality_findings (company_id, check_name, metric,"
                       " period_end, severity, run_id) VALUES (%s, 'impossible_value', 'revenue',"
                       " '2026-06-30', 'error', %s)", (R, run))
    api = client(recap_conn)

    def listed(user, issue):
        r = api.get(f"/v1/screener?issue={issue}", headers=token(user))
        assert r.status_code == 200, r.text
        return [row["company_id"] for row in r.json()["rows"]]

    assert listed("t-free", "impossible_value") == [R]
    assert listed("t-free", "outlier") == []
    assert listed("t-nobody", "impossible_value") == []
    assert api.get("/v1/screener?issue=DROP%20TABLE", headers=token("t-free")).status_code == 422


S_APPLE, S_APPLEBY, S_ACME, S_GS, S_SPROUT, S_SPROUTS = (
    "9100000021", "9100000022", "9100000023", "9100000024", "9100000025", "9100000026")


@pytest.fixture
def search_conn(conn):
    """Fictional names (no SEC data in fixtures): rows the search must rank, in every shape."""
    for cid, name, filer, tickers, former in (
        (S_APPLE, "ZQAPPLE Inc.", "Large accelerated filer", ["ZQAP"], "Zqfruit Computer Corp"),
        (S_APPLEBY, "ZQAPPLE Hospitality REIT", "Accelerated filer", ["ZQAH"], None),
        (S_ACME, "Holding of ZQAPPLE Trust", None, None, None),
        (S_GS, "ZQGOLD SACHSEN GROUP INC", "Large accelerated filer", ["ZQG"], None),
        (S_SPROUT, "ZQSprout Social, Inc.", "Large accelerated filer", ["ZQS"], None),
        (S_SPROUTS, "ZQSprouts Farmers Market, Inc.", "Accelerated filer", ["ZQSF"], None),
    ):
        conn.execute("INSERT INTO silver.companies (company_id, name, current_cik,"
                     " filer_categories, tickers) VALUES (%s, %s, %s, %s, %s)",
                     (cid, name, cid, [filer] if filer else None, tickers))
        conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                     (cid, cid))
        conn.execute("INSERT INTO silver.company_aliases (company_id, cik, alias_type, alias,"
                     " source) VALUES (%s, %s, 'name', %s, 'sec_submissions')", (cid, cid, name))
        for t in tickers or []:
            conn.execute("INSERT INTO silver.company_aliases (company_id, cik, alias_type, alias,"
                         " source) VALUES (%s, %s, 'ticker', %s, 'sec_tickers')", (cid, cid, t))
        if former:
            conn.execute("INSERT INTO silver.company_aliases (company_id, cik, alias_type, alias,"
                         " valid_to, source) VALUES (%s, %s, 'name', %s, '2020-01-01',"
                         " 'sec_submissions')", (cid, cid, former))
    # a retired ticker on one company, the same letters starting another's current ticker
    conn.execute("INSERT INTO silver.company_aliases (company_id, cik, alias_type, alias,"
                 " valid_to, source) VALUES (%s, %s, 'ticker', 'ZQO', '2020-01-01', 'curated')",
                 (S_ACME, S_ACME))
    return conn


def found(conn, q, **kw):
    return [r["company_id"] for r in Store(OneConnection(conn)).search("t-free", q, 25, **kw)]


def test_search_exact_ticker_first_then_prefix(search_conn):
    assert found(search_conn, "zqs")[:2] == [S_SPROUT, S_SPROUTS]
    assert found(search_conn, "ZQSF") == [S_SPROUTS]
    assert found(search_conn, "ZQA")[:2] == [S_APPLE, S_APPLEBY]  # prefix: bigger filer first
    row = Store(OneConnection(search_conn)).search("t-free", "ZQAP", 25)[0]
    assert (row["matched_on"], row["matched_alias"], row["tickers"]) == ("ticker", "ZQAP", ["ZQAP"])


def test_search_name_prefix_word_and_contains(search_conn):
    # a whole-word prefix beats the same letters run together: Sprouts isn't Sprout Social
    assert found(search_conn, "ZQSprouts")[:2] == [S_SPROUTS, S_SPROUT]
    assert found(search_conn, "zqapple")[:2] == [S_APPLE, S_APPLEBY]  # exact name, then prefix
    assert found(search_conn, "zqapple")[2] == S_ACME  # a later word
    assert found(search_conn, "sachsen") == [S_GS]  # a word that isn't the first
    assert found(search_conn, "achsen") == [S_GS]  # contained
    assert found(search_conn, "zq gold") == [S_GS]  # spaces ignored


def test_search_former_names_and_retired_tickers_rank_below_current(search_conn):
    rows = Store(OneConnection(search_conn)).search("t-free", "Zqfruit Computer", 25)
    assert [(r["company_id"], r["is_current"]) for r in rows] == [(S_APPLE, False)]
    assert found(search_conn, "ZQO") == [S_ACME]


def test_search_takes_no_pattern_from_what_is_typed(search_conn):
    assert found(search_conn, "zq%") == found(search_conn, "zq")
    assert found(search_conn, "z_apple") == []
    assert found(search_conn, "%") == found(search_conn, "_") == []


def test_search_finds_a_cik_and_narrows_before_the_limit(search_conn):
    assert found(search_conn, S_GS.lstrip("0")) == [S_GS]
    assert found(search_conn, "ZQ", only=[S_ACME]) == [S_ACME]
    # identity is public: t-free reads nothing here and still finds it, unless asked to narrow
    assert S_GS in found(search_conn, "ZQ")
    assert found(search_conn, "ZQ", readable_only=True) == []
    search_conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier)"
                        " VALUES ('t-free', %s, 'financials')", (S_ACME,))
    assert found(search_conn, "ZQ", readable_only=True) == [S_ACME]


def test_filings_meta_reads_the_public_index_as_query_api(conn):
    """Recent filings on Home: form and dates from silver.filings for any accession."""
    meta = Store(OneConnection(conn)).filings_meta("t-nobody", [f"{C}-00-000001", "nope"])
    assert meta == {f"{C}-00-000001": {"form": "10-Q", "filed": date(2025, 4, 30),
                                       "period_end": date(2025, 3, 31)}}


def test_feed_status_runs_as_query_api_without_bronze_access(conn):
    conn.execute("INSERT INTO bronze.filings (accession_no, cik, form_type, filed_at, payload,"
                 " kafka_partition, kafka_offset) VALUES ('9100000001-26-000001', %s, '8-K',"
                 " '2999-01-01T15:00:00Z', '{}', 0, 0)", (C,))
    rows = as_query_api(conn, "SELECT last_accepted_at::date::text FROM ops.feed_status()")
    assert rows == [("2999-01-01",)]
    r = client(conn).get("/v1/status")
    assert r.status_code == 200 and r.json()["last_accepted_at"].startswith("2999-01-01")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            as_query_api(conn, "SELECT 1 FROM bronze.filings LIMIT 1")


def test_status_data_runs_as_query_api_without_bronze_or_ops_access(conn):
    """The public status page (migration 028): aggregates through SECURITY DEFINER functions,
    while query_api still can't read the tables behind them."""
    before = as_query_api(conn, "SELECT * FROM ops.public_coverage()")[0]
    # the window queries are exact on emptied tables (the test's transaction rolls back)
    conn.execute("DELETE FROM bronze.filings")
    conn.execute("DELETE FROM ops.filing_completeness")
    conn.execute(
        "INSERT INTO bronze.filings (accession_no, cik, form_type, filed_at, payload,"
        " kafka_partition, kafka_offset, ingested_at) VALUES"
        # caught live, 30 s from acceptance to bronze; the daily-index row of the same
        # accession (midnight ET) must not count as its acceptance time
        " ('9100000001-26-000001', %(c)s, '8-K', now() - interval '1 hour',"
        "  '{\"source\": \"feed\"}', 0, 0, now() - interval '1 hour' + interval '30 seconds'),"
        " ('9100000001-26-000001', %(w)s, '8-K', now() - interval '1 day',"
        "  '{\"source\": \"daily-index\"}', 0, 1, now()),"
        # only the backfill had this one; the last is outside the 7-day window
        " ('9100000002-26-000001', %(w)s, '10-Q', now() - interval '2 days',"
        "  '{\"source\": \"daily-index\"}', 0, 2, now()),"
        " ('9100000003-26-000001', %(s)s, '8-K', now() - interval '8 days',"
        "  '{\"source\": \"feed\"}', 0, 3, now() - interval '8 days')",
        {"c": C, "w": W, "s": S})
    conn.execute(
        "INSERT INTO ops.filing_completeness (day, indexed, missing, republished) VALUES"
        " ((now() AT TIME ZONE 'America/New_York')::date - 1, 100, 1, 2),"
        " ((now() AT TIME ZONE 'America/New_York')::date - 2, 50, 0, 0),"
        " ((now() AT TIME ZONE 'America/New_York')::date - 40, 9, 9, 9)")  # outside the window
    conn.execute("INSERT INTO gold.quarterly_financials (company_id, metric, period_end, value,"
                 " unit, value_source, source_tag, accession_no, filed, source_accessions,"
                 " ingested_at) VALUES (%s, 'revenue', '2026-06-30', 1, 'USD', 'reported',"
                 " 'us-gaap:Revenues', '9100000001-26-000002', '2026-07-30', '{}', now())", (C,))
    conn.execute("INSERT INTO bronze.filing_documents (accession_no, cik, form, filed,"
                 " document_name, url, content_gzip, content_sha256, size_bytes) VALUES"
                 " ('9100000001-26-000002', %s, '10-Q', '2026-07-30', 'q.htm', 'https://x', '',"
                 " 'h', 0)", (C,))
    conn.execute("INSERT INTO ops.section_index (accession_no, company_id, form, filed, period,"
                 " sections_parsed_at, sections, passages) VALUES ('9100000001-26-000002', %s,"
                 " '10-Q', '2026-07-30', '2026-06-30', now(), 7, 30)", (C,))

    assert as_query_api(conn, "SELECT filings, via_feed, p50_seconds, p95_seconds"
                              " FROM ops.public_ingest()") == [(2, 1, 30.0, 30.0)]
    assert as_query_api(conn, "SELECT days, indexed, missing, republished, days_with_missing"
                              " FROM ops.public_completeness()") == [(2, 150, 1, 2, 1)]
    coverage = as_query_api(conn, "SELECT * FROM ops.public_coverage()")[0]
    assert coverage[:4] == (before[0] + 1, before[1] + 1, before[2] + 1, before[3] + 7)
    assert len(as_query_api(conn, "SELECT * FROM ops.public_quality()")) == 11

    body = client(conn).get("/v1/status/data").json()  # no token
    assert body["ingest"]["filings"] == 2 and body["ingest"]["live_pct"] == 50.0
    assert body["completeness"]["days_checked"] == 2
    for table in ("bronze.filings", "ops.filing_completeness", "ops.section_index"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                as_query_api(conn, f"SELECT 1 FROM {table} LIMIT 1")
