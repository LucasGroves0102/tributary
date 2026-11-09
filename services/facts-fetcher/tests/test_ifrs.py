"""Foreign filers (migration 056): IFRS tags, the company's own currency, no quarters made of
years or halves. Each case is a companyfacts payload shaped like the filer it came from, loaded the
way the jobs load one. Skipped unless TEST_DATABASE_URL is set; every test is rolled back."""

import json
import os
from decimal import Decimal

import pytest

from facts_fetcher.db import refresh_gold, refresh_silver, store_payload

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set"
)

CO = "0000354950"  # conftest's Home Depot row, re-shaped per test
FY25, FY24 = ("2024-10-01", "2025-09-30"), ("2023-10-01", "2024-09-30")
H1 = ("2024-10-01", "2025-03-31")


def fact(start, end, val, accn="a20f", form="20-F", filed="2025-12-18"):
    d = {"end": end, "val": val, "accn": accn, "form": form, "filed": filed}
    if start:
        d["start"] = start
    return d


def load(conn, ifrs: dict, us_gaap: dict | None = None, sic="3021"):
    """ifrs: tag -> {unit: [facts]}"""
    conn.execute("UPDATE silver.companies SET sic = %s WHERE company_id = %s", (sic, CO))
    doc = {"cik": int(CO), "entityName": "Birkenstock-like", "facts": {
        "ifrs-full": {t: {"units": u} for t, u in ifrs.items()},
        **({"us-gaap": {t: {"units": u} for t, u in us_gaap.items()}} if us_gaap else {})}}
    store_payload(conn, CO, json.dumps(doc).encode())
    refresh_silver(conn, [CO])
    refresh_gold(conn, [CO])


def gold(conn, metric, end, span="ttm"):
    return conn.execute(
        "SELECT value, unit, source_tag FROM gold.financials WHERE company_id = %s AND span = %s"
        " AND metric = %s AND period_end = %s", (CO, span, metric, end)).fetchone()


def test_an_ifrs_filer_in_euros_has_its_fiscal_years_in_euros(conn):
    load(conn, {
        "RevenueFromContractsWithCustomers": {"EUR": [fact(*FY25, 2097429000),
                                                      fact(*FY24, 1804690000)]},
        "ProfitLossAttributableToOwnersOfParent": {"EUR": [fact(*FY25, 348327000)]},
        "ProfitLossFromOperatingActivities": {"EUR": [fact(*FY25, 549544000)]},
        "Assets": {"EUR": [fact(None, "2025-09-30", 4942120000)]},
        "LongtermBorrowings": {"EUR": [fact(None, "2025-09-30", 1128010000)]},
        "ShorttermBorrowings": {"EUR": [fact(None, "2025-09-30", 17133000)]},
        "CashAndCashEquivalents": {"EUR": [fact(None, "2025-09-30", 329067000)]},
        "DilutedEarningsLossPerShare": {"EUR/shares": [fact(*FY25, 1.87)]},
    })
    assert gold(conn, "revenue", "2025-09-30") == (
        Decimal("2097429000"), "EUR", "ifrs-full:RevenueFromContractsWithCustomers")
    assert gold(conn, "net_income", "2025-09-30")[:2] == (Decimal("348327000"), "EUR")
    assert gold(conn, "total_assets", "2025-09-30", "quarter")[:2] == (Decimal("4942120000"), "EUR")
    # computed amounts are in the company's currency, not the definition's USD
    net_debt = gold(conn, "net_debt", "2025-09-30", "quarter")
    assert net_debt[:2] == (Decimal("816076000"), "EUR")
    assert gold(conn, "operating_margin", "2025-09-30")[1] == "ratio"
    # a year is never split into quarters
    assert conn.execute("SELECT count(*) FROM gold.quarterly_financials WHERE company_id = %s"
                        " AND period_start IS NOT NULL", (CO,)).fetchone() == (0,)


def test_a_convenience_translation_into_dollars_never_mixes_with_the_reporting_currency(conn):
    # a 20-F in renminbi that also states the latest year in dollars (Alibaba's shape)
    load(conn, {}, {
        "Revenues": {"CNY": [fact(*FY25, 941168000000), fact(*FY24, 868687000000)],
                     "USD": [fact(*FY25, 130350000000)]},
        "NetIncomeLoss": {"CNY": [fact(*FY25, 129470000000)], "USD": [fact(*FY25, 17930000000)]},
        "Assets": {"CNY": [fact(None, "2025-09-30", 1800000000000)]},
    }, sic="5961")
    assert gold(conn, "revenue", "2025-09-30")[:2] == (Decimal("941168000000"), "CNY")
    assert gold(conn, "revenue", "2024-09-30")[:2] == (Decimal("868687000000"), "CNY")
    units = conn.execute("SELECT DISTINCT unit FROM gold.financials WHERE company_id = %s"
                         " AND unit ~ '^[A-Z]{3}$'", (CO,)).fetchall()
    assert units == [("CNY",)]


def test_half_years_make_no_quarters_but_real_quarters_stay(conn):
    load(conn, {
        "Revenue": {"EUR": [fact(*H1, 900, accn="h1", form="6-K", filed="2025-05-15"),
                            fact(*FY25, 2000),
                            fact("2025-04-01", "2025-06-30", 600, accn="q3", form="6-K",
                                 filed="2025-08-14")]},
        "Assets": {"EUR": [fact(None, "2025-09-30", 5000)]},
    })
    quarters = conn.execute(
        "SELECT period_start, period_end FROM gold.quarterly_financials WHERE company_id = %s"
        " AND metric = 'revenue' ORDER BY period_end", (CO,)).fetchall()
    # the 6-K's own quarter stays; FY - H1 (183 days) is not a quarter
    assert [(str(s), str(e)) for s, e in quarters] == [("2025-04-01", "2025-06-30")]


def test_current_borrowings_tagged_apart_are_summed(conn):
    # Eni's shape: short-term borrowings and the current portion of long-term ones
    load(conn, {
        "Assets": {"EUR": [fact(None, "2025-09-30", 140000000000)]},
        "Liabilities": {"EUR": [fact(None, "2025-09-30", 90000000000)]},
        "LongtermBorrowings": {"EUR": [fact(None, "2025-09-30", 20139000000)]},
        "ShorttermBorrowings": {"EUR": [fact(None, "2025-09-30", 4929000000)]},
        "CurrentPortionOfLongtermBorrowings": {"EUR": [fact(None, "2025-09-30", 3434000000)]},
    }, sic="2911")
    current = gold(conn, "long_term_debt_current", "2025-09-30", "quarter")
    assert current == (Decimal("8363000000"), "EUR",
                       "ifrs-full:ShorttermBorrowings+CurrentPortionOfLongtermBorrowings")


def test_us_gaap_and_dollars_are_read_as_before(conn):
    # the 056 rules change nothing for a dollar filer: one us-gaap fact, one in another currency
    load(conn, {}, {
        "Revenues": {"USD": [fact("2025-07-01", "2025-09-30", 100, form="10-Q")],
                     "EUR": [fact("2025-07-01", "2025-09-30", 90, form="10-Q")]},
        "Assets": {"USD": [fact(None, "2025-09-30", 1000, form="10-Q")]},
    }, sic="5211")
    assert gold(conn, "revenue", "2025-09-30", "quarter")[:2] == (Decimal("100"), "USD")
