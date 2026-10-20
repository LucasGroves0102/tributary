"""Formulas against real Postgres: alignment, lags and values from real filings (PepsiCo,
Coca-Cola, Apple), and both permission layers. Skipped unless TEST_DATABASE_URL is set; every
test is rolled back."""

import os
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.store import Store

from .conftest import SECRET

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

PEP, KO, AAPL = "9100000001", "9100000002", "9100000003"  # test companies, real tickers
COMPANIES = [(PEP, "PEP", "1226"), (KO, "KO", "1231"), (AAPL, "AAPL", "0927")]
# (company, metric, start, end, value, accession, form, filed): from the companies' filings
FACTS = [
    (PEP, "revenue", "2025-03-23", "2025-06-14", 22726000000, "0000077476-25-000044", "10-Q",
     "2025-07-17"),
    (PEP, "net_income", "2025-03-23", "2025-06-14", 1263000000, "0000077476-25-000044", "10-Q",
     "2025-07-17"),
    (PEP, "revenue", "2025-06-15", "2025-09-06", 23937000000, "0000077476-25-000053", "10-Q",
     "2025-10-09"),
    (PEP, "net_income", "2025-06-15", "2025-09-06", 2603000000, "0000077476-25-000053", "10-Q",
     "2025-10-09"),
    (PEP, "revenue", "2025-12-28", "2026-03-21", 19443000000, "0000077476-26-000017", "10-Q",
     "2026-04-16"),
    (PEP, "net_income", "2025-12-28", "2026-03-21", 2327000000, "0000077476-26-000017", "10-Q",
     "2026-04-16"),
    (KO, "revenue", "2025-03-29", "2025-06-27", 12535000000, "0000021344-25-000061", "10-Q",
     "2025-07-24"),
    (KO, "net_income", "2025-03-29", "2025-06-27", 3810000000, "0000021344-25-000061", "10-Q",
     "2025-07-24"),
    (KO, "revenue", "2025-06-28", "2025-09-26", 12455000000, "0001628280-25-046054", "10-Q",
     "2025-10-23"),
    (KO, "net_income", "2025-06-28", "2025-09-26", 3696000000, "0001628280-25-046054", "10-Q",
     "2025-10-23"),
    (KO, "revenue", "2026-01-01", "2026-04-03", 12472000000, "0001628280-26-028802", "10-Q",
     "2026-04-30"),
    (KO, "net_income", "2026-01-01", "2026-04-03", 3924000000, "0001628280-26-028802", "10-Q",
     "2026-04-30"),
] + [
    (AAPL, "revenue", start, end, value, acc, form, filed)
    for start, end, value, acc, form, filed in [
        ("2024-09-29", "2024-12-28", 124300000000, "0000320193-26-000006", "10-Q", "2026-01-30"),
        ("2024-12-29", "2025-03-29", 95359000000, "0000320193-26-000013", "10-Q", "2026-05-01"),
        ("2025-03-30", "2025-06-28", 94036000000, "0000320193-26-000020", "10-Q", "2026-07-31"),
        ("2025-06-29", "2025-09-27", 102466000000, "0000320193-25-000079", "10-K", "2025-10-31"),
        ("2025-09-28", "2025-12-27", 143756000000, "0000320193-26-000006", "10-Q", "2026-01-30"),
        ("2025-12-28", "2026-03-28", 111184000000, "0000320193-26-000013", "10-Q", "2026-05-01"),
        ("2026-03-29", "2026-06-27", 109417000000, "0000320193-26-000020", "10-Q", "2026-07-31"),
    ]
]
GRANTS = {"f-bob": {PEP, KO}, "f-alice": {PEP, AAPL}}


class Grants:
    """Layer 1 from the same grants as authz.user_access."""

    def __init__(self, grants=GRANTS):
        self.grants = grants

    def can(self, user_id, tier, company_id):
        return tier == "financials" and company_id in self.grants.get(user_id, set())

    def readable(self, user_id):
        return {c: {"financials"} for c in self.grants.get(user_id, set())}


class AllowAll(Grants):
    def __init__(self):
        super().__init__({u: {PEP, KO, AAPL} for u in ("f-bob", "f-alice", "f-nobody")})


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        # the fixture's companies own these tickers for the test
        conn.execute("DELETE FROM silver.company_aliases WHERE alias_type = 'ticker'"
                     " AND upper(alias) IN ('PEP', 'KO', 'AAPL')")
        for cid, ticker, fye in COMPANIES:
            conn.execute("INSERT INTO silver.companies (company_id, name, current_cik,"
                         " fiscal_year_end, tracked) VALUES (%s, %s, %s, %s, true)",
                         (cid, f"Test {ticker}", cid, fye))
            conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                         (cid, cid))
            conn.execute("INSERT INTO silver.company_aliases (company_id, cik, alias_type,"
                         " alias, source) VALUES (%s, %s, 'ticker', %s, 'sec_tickers')",
                         (cid, cid, ticker))
        for cid, metric, start, end, value, acc, form, filed in FACTS:
            conn.execute(
                "INSERT INTO gold.quarterly_financials (company_id, metric, period_start,"
                " period_end, value, unit, value_source, source_tag, accession_no, form, filed,"
                " source_accessions, ingested_at) VALUES (%s, %s, %s, %s, %s, 'USD', 'reported',"
                " 'us-gaap:Revenues', %s, %s, %s, ARRAY[%s], now())",
                (cid, metric, start, end, value, acc, form, filed, acc))
        conn.execute("SELECT gold.refresh_derived_financials(%s)", ([AAPL],))
        conn.execute("DELETE FROM authz.user_access WHERE user_id LIKE 'f-%'")
        for user, companies in GRANTS.items():
            for cid in companies:
                conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier)"
                             " VALUES (%s, %s, 'financials')", (user, cid))
        yield conn


def client_for(conn, authorizer):
    return TestClient(create_app(authorizer, Store(OneConnection(conn)), SECRET))


def evaluate(client, token, user, formula, **body):
    return client.post("/v1/formulas/evaluate", json={"formula": formula, **body},
                       headers=token(user))


def values(r):
    assert r.status_code == 200, r.text
    return {p["period"]: p["value"] for p in r.json()["points"]}


def test_pep_vs_ko_net_margin_aligned_by_calendar_quarter(conn, token):
    c = client_for(conn, Grants())
    r = evaluate(c, token, "f-bob",
                 "PEP.net_income / PEP.revenue - KO.net_income / KO.revenue")
    assert r.json()["alignment"] == "calendar"
    # PEP's quarter ends Jun 14 and KO's Jun 27: both CY2025 Q2
    assert values(r) == pytest.approx({
        "CY2025 Q2": 1263 / 22726 - 3810 / 12535,     # 5.6% vs 30.4%
        "CY2025 Q3": 2603 / 23937 - 3696 / 12455,
        "CY2026 Q1": 2327 / 19443 - 3924 / 12472,
    })
    point = r.json()["points"][0]
    assert point["period_end"] == "2025-06-27"  # the latest of its inputs
    assert point["source_accessions"] == ["0000021344-25-000061", "0000077476-25-000044"]
    assert {i["input"] for i in point["inputs"]} == {0, 1, 2, 3}


def test_ko_over_pep_revenue(conn, token):
    r = evaluate(client_for(conn, Grants()), token, "f-bob", "KO.revenue / PEP.revenue")
    assert values(r)["CY2025 Q2"] == pytest.approx(12535 / 22726)


def test_apple_yoy_growth_on_fiscal_quarters(conn, token):
    r = evaluate(client_for(conn, Grants()), token, "f-alice", "revenue / revenue[-4q] - 1",
                 default_company=AAPL)
    assert r.json()["alignment"] == "fiscal"
    assert values(r) == pytest.approx({
        "FY2026 Q1": 143756 / 124300 - 1,  # 15.7%
        "FY2026 Q2": 111184 / 95359 - 1,
        "FY2026 Q3": 109417 / 94036 - 1,
    })
    q3 = r.json()["points"][-1]
    assert q3["period_end"] == "2026-06-27"
    assert [(i["period_end"], i["citation"]["accession_no"]) for i in q3["inputs"]] == [
        ("2026-06-27", "0000320193-26-000020"), ("2025-06-28", "0000320193-26-000020")]


def test_apple_ttm_revenue_and_range(conn, token):
    c = client_for(conn, Grants())
    r = evaluate(c, token, "f-alice", "ttm(revenue)", default_company=AAPL)
    assert values(r)["FY2025 Q4"] == 416161000000  # Apple's fiscal 2025 revenue
    r = evaluate(c, token, "f-alice", "revenue", default_company=AAPL, view="ttm",
                 **{"from": "2026-01-01", "to": "2026-03-31"})
    assert list(values(r)) == ["FY2026 Q2"]


def test_division_by_zero_and_missing_inputs_are_gaps(conn, token):
    c = client_for(conn, Grants())
    assert values(evaluate(c, token, "f-alice", "revenue / (revenue - revenue)",
                           default_company=AAPL)) == {}
    assert values(evaluate(c, token, "f-alice", "net_income", default_company=AAPL)) == {}


def test_layer_one_refuses_a_formula_with_an_ungranted_company(conn, token):
    r = evaluate(client_for(conn, Grants()), token, "f-alice", "KO.revenue / PEP.revenue")
    assert r.status_code == 403
    assert r.json()["detail"]["errors"][0]["message"] == "KO isn't one of your companies"


def test_rls_alone_hides_an_ungranted_company_in_a_formula(conn, token):
    c = client_for(conn, AllowAll())
    assert values(evaluate(c, token, "f-alice", "KO.revenue / PEP.revenue")) == {}
    assert values(evaluate(c, token, "f-alice", "KO.revenue")) == {}
    assert values(evaluate(c, token, "f-alice", "abs(KO.revenue) * 0 + PEP.revenue")) == {}
    assert len(values(evaluate(c, token, "f-bob", "KO.revenue / PEP.revenue"))) == 3
    assert values(evaluate(c, token, "f-nobody", "PEP.revenue")) == {}


def test_ticker_resolution_is_parameterized(conn):
    store = Store(OneConnection(conn))
    assert store.resolve_tickers("f-bob", ["KO", "KO' OR '1'='1", "PEP;--"]) == {"KO": KO}
    assert store.tickers("f-bob", [PEP, KO]) == {PEP: ["PEP"], KO: ["KO"]}


def test_crafted_formulas_never_reach_sql(conn, token):
    c = client_for(conn, AllowAll())
    for formula in ("revenue'; DROP TABLE gold.quarterly_financials; --",
                    "revenue) FROM gold.financials --", "KO.revenue/**/union"):
        r = evaluate(c, token, "f-alice", formula, default_company=PEP)
        assert r.status_code == 422
    assert conn.execute("SELECT count(*) FROM gold.quarterly_financials"
                        " WHERE company_id = %s", (PEP,)).fetchone()[0] == 6
