"""A bank's own lines (migration 048) through bronze -> silver -> gold on real Postgres: net interest
income, noninterest income and expense, the provision, loans, deposits, CET1 and the efficiency
ratio. Skipped unless TEST_DATABASE_URL is set; rolled back."""

import json
import os
from datetime import date
from decimal import Decimal

import pytest

from facts_fetcher.db import refresh_gold, refresh_silver, store_payload

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

BANK = "0009000048"  # a made-up bank, its facts in the shape companyfacts uses
QUARTERS = [("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
            ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31")]


def facts(tag, unit, rows):
    return {tag: {"units": {unit: rows}}}


def duration(tag, values):
    """Three 10-Qs (each quarter, and year to date in the second and third) and the 10-K's year."""
    rows = [{"start": s, "end": e, "val": v, "accn": f"0009000048-25-00000{i}", "fy": 2025,
             "fp": f"Q{i + 1}", "form": "10-Q", "filed": "2025-11-01"}
            for i, ((s, e), v) in enumerate(zip(QUARTERS[:3], values[:3], strict=True))]
    for i in (1, 2):
        rows.append({"start": "2025-01-01", "end": QUARTERS[i][1], "val": sum(values[:i + 1]),
                     "accn": f"0009000048-25-00000{i}", "fy": 2025, "fp": f"Q{i + 1}",
                     "form": "10-Q", "filed": "2025-11-01"})
    rows.append({"start": "2025-01-01", "end": "2025-12-31", "val": sum(values), "fy": 2025,
                 "fp": "FY", "accn": "0009000048-26-000001", "form": "10-K",
                 "filed": "2026-02-20"})
    return facts(tag, "USD", rows)


def instant(tag, value, unit="USD"):
    return facts(tag, unit, [{"end": "2025-12-31", "val": value, "accn": "0009000048-26-000001",
                              "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-02-20"}])


def payload():
    g = {}
    g |= duration("InterestIncomeExpenseNet", [100, 110, 120, 130])
    g |= duration("NoninterestIncome", [50, 50, 60, 60])
    g |= duration("NoninterestExpense", [90, 95, 100, 105])
    g |= duration("ProvisionForLoanLeaseAndOtherLosses", [10, -5, 12, 8])
    g |= instant("FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss", 9000)
    g |= instant("Deposits", 12000)
    g |= instant("CommonEquityTierOneCapitalRatio", 0.118, unit="pure")
    return json.dumps({"cik": int(BANK), "entityName": "Test Bank", "facts": {"us-gaap": g}}).encode()


@pytest.fixture
def bank(conn):
    conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, tracked, sic)"
                 " VALUES (%s, 'Test Bank', %s, true, '6022')", (BANK, BANK))
    conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)", (BANK, BANK))
    store_payload(conn, BANK, payload())
    refresh_silver(conn, [BANK])
    refresh_gold(conn, [BANK])
    return conn


def gold(conn, metric, span="quarter"):
    derived = span == "ttm" or metric == "efficiency_ratio"
    table = "gold.derived_financials" if derived else "gold.quarterly_financials"
    where = f" AND span = '{span}'" if derived else ""
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT period_end, value FROM {table} WHERE company_id = %s AND metric = %s{where}",
        (BANK, metric)).fetchall()}


def test_bank_lines_reach_gold_with_a_derived_fourth_quarter(bank):
    assert gold(bank, "net_interest_income") == {
        date(2025, 3, 31): 100, date(2025, 6, 30): 110, date(2025, 9, 30): 120,
        date(2025, 12, 31): 130}  # Q4 = the year less nine months
    assert gold(bank, "provision_for_credit_losses")[date(2025, 6, 30)] == -5  # a release
    assert gold(bank, "noninterest_expense", "ttm") == {date(2025, 12, 31): 390}
    assert gold(bank, "loans") == {date(2025, 12, 31): 9000}
    assert gold(bank, "deposits") == {date(2025, 12, 31): 12000}
    (unit,), = bank.execute("SELECT unit FROM gold.quarterly_financials WHERE company_id = %s"
                            " AND metric = 'cet1_ratio'", (BANK,)).fetchall()
    assert unit == "pure" and gold(bank, "cet1_ratio") == {date(2025, 12, 31): Decimal("0.118")}


def test_efficiency_ratio_is_expense_over_net_revenue(bank):
    q = gold(bank, "efficiency_ratio")
    assert q[date(2025, 3, 31)] == round(Decimal(90) / 150, 6)
    assert q[date(2025, 12, 31)] == round(Decimal(105) / 190, 6)
    assert gold(bank, "efficiency_ratio", "ttm") == {date(2025, 12, 31): round(Decimal(390) / 680, 6)}
    (inputs,), = bank.execute(
        "SELECT inputs FROM gold.derived_financials WHERE company_id = %s AND span = 'ttm'"
        " AND metric = 'efficiency_ratio'", (BANK,)).fetchall()
    assert inputs == {"noninterest_expense": 390, "net_interest_income": 460,
                      "noninterest_income": 220}


def test_one_tag_feeds_revenue_and_net_interest_income(bank):
    """038 maps InterestIncomeExpenseNet into a bank's revenue sum (with NoninterestIncome); 048
    maps it to its own metric too. Silver stores the fact once; gold reads it for both (by tag)."""
    assert gold(bank, "revenue")[date(2025, 3, 31)] == 150
    assert gold(bank, "net_interest_income")[date(2025, 3, 31)] == 100
    assert gold(bank, "noninterest_income")[date(2025, 3, 31)] == 50
    (stored,), = bank.execute("SELECT count(*) FROM silver.financial_facts WHERE cik = %s"
                              " AND tag = 'InterestIncomeExpenseNet'", (BANK,)).fetchall()
    assert stored == 6  # 3 quarters, 2 year-to-date, 1 year: once each
