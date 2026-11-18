"""Migration 155 (Morgan, test 11, part 1) on real payloads: the companies' companyfacts as stored
in bronze on hermes (2026-11-18), trimmed by trim_facts.py's rule with 155's tags mapped
(fixtures/analyst/), through bronze -> silver -> gold with their real SICs, once for the module.

- Debt due within a year: Apple's commercial paper and Walmart's short-term borrowings added to
  the current portion of long-term debt; convertibles (Alnylam, Rapid7) read as debt.
- No debt: Palantir's balance sheet tags no debt and it pays no interest: long-term debt zero, net
  debt its cash; Teradyne's only debt is a revolver due within a year.
- Gross profit on net sales: Walmart's FY2026 $706,413M - $535,395M.
- A periodic report's citation: Broadcom's FY2018 quarters, recast in an 8-K of 2020 at the same
  values, cite their 10-Qs.

Skipped unless TEST_DATABASE_URL is set; rolled back."""

import gzip
import os
from decimal import Decimal
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="TEST_DATABASE_URL not set")

HERE = Path(__file__).parent / "fixtures" / "analyst"
COMPANIES = [  # company_id, name, SIC, CIKs
    ("0000320193", "Apple Inc.", "3571", ["0000320193"]),
    ("0000104169", "Walmart Inc.", "5331", ["0000104169"]),
    ("0001321655", "Palantir Technologies Inc.", "7372", ["0001321655"]),
    ("0001560327", "Rapid7, Inc.", "7372", ["0001560327"]),
    ("0000097210", "TERADYNE, INC", "3825", ["0000097210"]),
    ("0001178670", "ALNYLAM PHARMACEUTICALS, INC.", "2834", ["0001178670"]),
    ("0001730168", "Broadcom Inc.", "3674", ["0001649338", "0001730168"]),
]
M = Decimal(1_000_000)


@pytest.fixture(scope="module")
def gold():
    from facts_fetcher.db import refresh_gold, refresh_silver, store_payload

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn, \
            conn.transaction(force_rollback=True):
        conn.execute("TRUNCATE gold.quarterly_financials, gold.derived_financials,"
                     " gold.segment_financials, ops.quality_findings, ops.quality_companies,"
                     " silver.financial_facts, bronze.company_facts, silver.company_aliases,"
                     " silver.company_ciks, silver.companies")
        for company, name, sic, ciks in COMPANIES:
            conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, tracked,"
                         " sic) VALUES (%s, %s, %s, true, %s)", (company, name, ciks[-1], sic))
            for cik in ciks:
                conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                             (cik, company))
                store_payload(conn, cik, gzip.decompress((HERE / f"CIK{cik}.json.gz").read_bytes()))
            refresh_silver(conn, ciks)
        refresh_gold(conn, [c[0] for c in COMPANIES])
        yield conn


def row(conn, company: str, metric: str, end: str, span: str = "quarter") -> dict | None:
    table = "gold.quarterly_financials" if span == "quarter" else "gold.derived_financials"
    extra = "" if span == "quarter" else " AND span = 'ttm'"
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute(f"SELECT * FROM {table} WHERE company_id = %s AND metric = %s"
                f" AND period_end = %s{extra}", (company, metric, end))
    return cur.fetchone()


def test_commercial_paper_is_debt_due_within_a_year(gold):
    """Finding 3: Apple's FY2026 10-K: commercial paper $1,997M, term debt $9,906M current and
    $68,208M noncurrent: $80,111M of debt, not $78,114M."""
    cur = row(gold, "0000320193", "long_term_debt_current", "2026-09-26")
    assert (cur["value"], cur["source_tag"]) == (
        11_903 * M, "us-gaap:LongTermDebtCurrent+CommercialPaper")
    net = row(gold, "0000320193", "net_debt", "2026-09-26", "ttm")
    assert Decimal(str(net["inputs"]["long_term_debt_current"])) == 11_903 * M
    assert Decimal(str(net["inputs"]["long_term_debt"])) == 68_208 * M


def test_short_term_borrowings_are_debt_due_within_a_year(gold):
    """Walmart's balance sheet: short-term borrowings beside long-term debt due within one year,
    both debt (its FY2026 10-K)."""
    cur = row(gold, "0000104169", "long_term_debt_current", "2026-01-31")
    assert cur["source_tag"] == "us-gaap:LongTermDebtCurrent+ShortTermBorrowings"
    parts = gold.execute(
        "SELECT tag, value FROM silver.financial_facts WHERE cik = '0000104169'"
        " AND period_end = '2026-01-31' AND accession_no = %s"
        " AND tag IN ('LongTermDebtCurrent', 'ShortTermBorrowings')",
        (cur["accession_no"],)).fetchall()
    assert len(parts) == 2 and cur["value"] == sum(v for _, v in parts)


def test_convertible_notes_are_debt(gold):
    """Alnylam tags its convertible notes ConvertibleDebtNoncurrent / ConvertibleDebtCurrent:
    gold read no debt at all, and its net debt was 'none'."""
    ltd = row(gold, "0001178670", "long_term_debt", "2025-12-31")
    assert ltd is not None and ltd["source_tag"] == "us-gaap:ConvertibleDebtNoncurrent"
    assert ltd["value"] > 0
    assert row(gold, "0001178670", "net_debt", "2025-12-31", "ttm") is not None


def test_a_company_with_no_debt_has_net_cash(gold):
    """Finding 7: Palantir tags no debt and pays no interest: long-term debt zero (a reading,
    `derived:no-debt`), net debt minus its cash and investments, debt/equity zero."""
    ltd = row(gold, "0001321655", "long_term_debt", "2025-12-31")
    assert (ltd["value"], ltd["source_tag"], ltd["value_source"]) == (
        0, "derived:no-debt", "reported")
    net = row(gold, "0001321655", "net_debt", "2025-12-31", "ttm")
    cash = row(gold, "0001321655", "cash", "2025-12-31")
    assert net["value"] < 0 and net["value"] <= -cash["value"]
    assert row(gold, "0001321655", "debt_to_equity", "2025-12-31", "ttm")["value"] == 0


def test_a_revolver_due_within_a_year_is_the_debt(gold):
    """Teradyne at Dec 31, 2025: $200M under its revolver (ShortTermBorrowings, alone), no
    long-term debt: net debt is that less its cash and investments."""
    cur = row(gold, "0000097210", "long_term_debt_current", "2025-12-31")
    assert (cur["value"], cur["source_tag"]) == (200 * M, "us-gaap:ShortTermBorrowings")
    assert row(gold, "0000097210", "long_term_debt", "2025-12-31")["value"] == 0
    net = row(gold, "0000097210", "net_debt", "2025-12-31", "ttm")
    assert Decimal(str(net["inputs"]["long_term_debt_current"])) == 200 * M


def test_convertibles_the_tester_expected_are_read(gold):
    """Rapid7 (the tester's suspected false positive) tags its convertible senior notes
    ConvertibleDebtNoncurrent: $892.3M at Dec 31, 2025, its net debt that less its cash and
    investments, never a zero (its interest, 0.6% of its assets, would say debt too)."""
    ltd = row(gold, "0001560327", "long_term_debt", "2025-12-31")
    assert (ltd["value"], ltd["source_tag"]) == (Decimal("892284000"),
                                                 "us-gaap:ConvertibleDebtNoncurrent")
    net = row(gold, "0001560327", "net_debt", "2025-12-31", "ttm")
    assert Decimal(str(net["inputs"]["long_term_debt"])) == Decimal("892284000")


def test_gross_profit_is_on_net_sales_where_the_company_reports_them(gold):
    """Finding 9: Walmart's FY2026: net sales $706,413M less cost of sales $535,395M (the
    auditor's figures), as its gross profit rate is stated; the gross margin over net sales."""
    gp = row(gold, "0000104169", "gross_profit", "2026-01-31", "ttm")
    assert gp["value"] == 171_018 * M
    q = row(gold, "0000104169", "gross_profit", "2026-01-31")
    assert (q["value_source"], q["source_tag"]) == ("derived", "derived:net_sales-cost_of_revenue")
    gm = row(gold, "0000104169", "gross_margin", "2026-01-31", "ttm")
    assert round(gm["value"], 3) == Decimal("0.242")


def test_a_periodic_report_is_cited_over_an_8k_stating_the_same_value(gold):
    """Finding 27: Broadcom's 8-K of 2020-06-26 recast FY2017-2019; where it states a quarter's
    figure at the 10-Q's value, the 10-Q is cited. Every figure keeps the latest filing's value."""
    cur = gold.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT metric, period_end, form, value FROM gold.quarterly_financials"
                " WHERE company_id = '0001730168' AND value_source = 'reported'"
                " AND period_end BETWEEN '2017-01-01' AND '2018-11-04' AND form ~ '^8-K'")
    eight_k = cur.fetchall()
    cur.execute(
        "SELECT count(*) AS n FROM gold.quarterly_financials q"
        " WHERE q.company_id = '0001730168' AND q.value_source = 'reported' AND q.form ~ '^8-K'"
        " AND EXISTS (SELECT 1 FROM silver.financial_facts f"
        "             WHERE f.cik IN ('0001649338', '0001730168')"
        "               AND f.tag = split_part(q.source_tag, ':', 2)"
        "               AND f.period_end = q.period_end"
        "               AND f.period_start IS NOT DISTINCT FROM q.period_start"
        "               AND abs(f.value) = abs(q.value) AND f.form ~ '^(10-K|10-Q)')")
    assert cur.fetchone()["n"] == 0, eight_k
    gp = row(gold, "0001730168", "gross_profit", "2018-02-04")
    assert gp["form"] in ("10-Q", "10-K"), gp
