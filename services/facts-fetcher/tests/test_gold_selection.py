"""Which fact gold believes, and what it computes from them (migrations 038-041), on the shapes real
filings have; the companies in the comments are where each came from. Skipped unless
TEST_DATABASE_URL is set; every test is rolled back."""

import os
from datetime import date
from decimal import Decimal

import pytest

from facts_fetcher.db import refresh_gold

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set"
)

CO = "0000354950"  # conftest's Home Depot row: any company, these tests set its SIC and facts
Q3, Q2, Q1, FY = (
    ("2026-07-01", "2026-09-30"),
    ("2026-04-01", "2026-06-30"),
    ("2026-01-01", "2026-03-31"),
    ("2025-01-01", "2025-12-31"),
)
METRICS = {
    "Revenues": "revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "RevenuesNetOfInterestExpense": "revenue",
    "InterestIncomeExpenseNet": "revenue",
    "NoninterestIncome": "revenue",
    "RegulatedAndUnregulatedOperatingRevenue": "revenue",
    "CostOfRevenue": "cost_of_revenue",
    "GrossProfit": "gross_profit",
    "OperatingIncomeLoss": "operating_income",
    "SellingGeneralAndAdministrativeExpense": "sga",
    "NetIncomeLoss": "net_income",
    "IncomeTaxExpenseBenefit": "income_tax",
    "NetCashProvidedByUsedInOperatingActivities": "operating_cash_flow",
    "PaymentsToAcquireProductiveAssets": "capex",
    "PaymentsOfDividends": "dividends_paid",
    "InterestExpense": "interest_expense",
    "EarningsPerShareBasic": "eps_basic",
    "EarningsPerShareDiluted": "eps_diluted",
    "CashAndCashEquivalentsAtCarryingValue": "cash",
    "Cash": "cash",
    "Assets": "total_assets",
    "AssetsCurrent": "current_assets",
    "Liabilities": "total_liabilities",
    "LiabilitiesCurrent": "current_liabilities",
    "AccountsReceivableNetCurrent": "receivables",
    "InventoryNet": "inventory",
    "StockholdersEquity": "stockholders_equity",
    "LongTermDebtNoncurrent": "long_term_debt",
    "LongTermDebt": "long_term_debt",
    "LongTermNotesPayable": "long_term_debt",
    "LongTermDebtCurrent": "long_term_debt_current",
    "DebtCurrent": "long_term_debt_current",
    "ShortTermInvestments": "short_term_investments",
    "MarketableSecuritiesCurrent": "short_term_investments",
    "DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent": "short_term_investments",
    "DebtSecuritiesCurrent": "short_term_investments",
    "EquitySecuritiesFvNi": "short_term_investments",
}


def put(conn, facts, sic="3561"):
    """facts: (tag, start, end, value, accession, filed) or (..., form). A None start is an
    instant."""
    conn.execute("UPDATE silver.companies SET sic = %s WHERE company_id = %s", (sic, CO))
    conn.execute("DELETE FROM silver.financial_facts")
    for tag, start, end, value, accession, filed, *form in facts:
        unit = "USD/shares" if tag.startswith("EarningsPerShare") else "USD"
        conn.execute(
            "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
            " value, accession_no, form, filed, ingested_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '2026-11-02')",
            (
                CO,
                METRICS[tag],
                tag,
                unit,
                start,
                end,
                value,
                accession,
                form[0] if form else "10-Q",
                filed,
            ),
        )
    refresh_gold(conn, [CO])


def gold(conn, metric, end):
    row = conn.execute(
        "SELECT value, source_tag FROM gold.quarterly_financials"
        " WHERE company_id = %s AND metric = %s AND period_end = %s",
        (CO, metric, end),
    ).fetchone()
    return (row[0], row[1].split(":")[1]) if row else None


def derived(conn, metric, end, span="quarter"):
    row = conn.execute(
        "SELECT value FROM gold.derived_financials"
        " WHERE company_id = %s AND span = %s AND metric = %s AND period_end = %s",
        (CO, span, metric, end),
    ).fetchone()
    return row[0] if row else None


def findings(conn, check):
    return conn.execute(
        "SELECT metric, period_end, detail FROM ops.quality_findings"
        " WHERE company_id = %s AND check_name = %s ORDER BY metric, period_end",
        (CO, check),
    ).fetchall()


END = "2026-09-30"
FILED = "2026-11-02"


# --- revenue -----------------------------------------------------------------------------------


def test_a_zero_loses_to_another_tags_value_in_the_same_filing(conn):
    # Flowserve's Revenues is 0 every quarter since 2024 beside $1.24B of contract revenue
    put(
        conn,
        [
            ("Revenues", *Q3, 0, "q", FILED),
            ("RevenueFromContractWithCustomerExcludingAssessedTax", *Q3, 1237773000, "q", FILED),
        ],
    )
    assert gold(conn, "revenue", END) == (
        1237773000,
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    )


def test_a_later_filings_value_replaces_an_earlier_zero(conn):
    # Vertiv's FY2019: the SPAC's zeros, then the operating company's comparatives a year later
    put(
        conn,
        [
            ("Revenues", *Q3, 0, "shell", "2026-11-02"),
            (
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                *Q3,
                1054800000,
                "real",
                "2027-11-02",
            ),
        ],
    )
    assert gold(conn, "revenue", END)[0] == 1054800000


def test_a_zero_filed_later_is_a_restatement_and_stays(conn):
    # Pixelworks sold its business: the later filing's comparatives say zero
    put(
        conn,
        [
            (
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                *Q3,
                7094000,
                "old",
                "2026-11-02",
            ),
            ("Revenues", *Q3, 0, "new", "2027-11-02"),
        ],
    )
    assert gold(conn, "revenue", END)[0] == 0


def test_a_sliver_in_the_same_filing_doesnt_replace_a_true_zero(conn):
    # Keros: a pre-revenue biotech with $83K under the contract tag beside Revenues of 0
    put(
        conn,
        [
            ("Revenues", *Q3, 0, "q", FILED),
            ("RevenueFromContractWithCustomerExcludingAssessedTax", *Q3, 83000, "q", FILED),
        ],
    )
    assert gold(conn, "revenue", END)[0] == 0


def test_a_shells_zero_with_no_alternative_stays(conn):
    put(conn, [("Revenues", *Q3, 0, "q", FILED)])
    assert gold(conn, "revenue", END)[0] == 0


def test_a_restatement_within_a_tag_still_wins(conn):
    put(
        conn,
        [
            ("Revenues", *Q3, 100000000, "old", "2026-11-02"),
            ("Revenues", *Q3, 90000000, "new", "2027-11-02"),
        ],
    )
    assert gold(conn, "revenue", END)[0] == 90000000


def test_revenues_under_a_fifth_of_contract_revenue_is_a_part(conn):
    # Verra Mobility: Revenues 28,314,000 (one line) of 258,875,000
    put(
        conn,
        [
            ("Revenues", *Q3, 28314000, "q", FILED),
            ("RevenueFromContractWithCustomerExcludingAssessedTax", *Q3, 258875000, "q", FILED),
        ],
    )
    assert gold(conn, "revenue", END)[0] == 258875000


def test_revenues_below_contract_revenue_after_derivative_losses_stays(conn):
    # EQT: total revenue really is below contract revenue, by the derivative loss (29-90%)
    put(
        conn,
        [
            ("Revenues", *Q3, 290000000, "q", FILED),
            ("RevenueFromContractWithCustomerExcludingAssessedTax", *Q3, 1000000000, "q", FILED),
        ],
    )
    assert gold(conn, "revenue", END)[0] == 290000000


def test_a_banks_revenue_is_net_interest_plus_noninterest_income(conn):
    # M&T's contract revenue is $438M of a $2.54B quarter, which made net margin 176%
    put(
        conn,
        [
            ("RevenueFromContractWithCustomerExcludingAssessedTax", *Q3, 438000000, "q", FILED),
            ("InterestIncomeExpenseNet", *Q3, 1793000000, "q", FILED),
            ("NoninterestIncome", *Q3, 743000000, "q", FILED),
        ],
        sic="6022",
    )
    assert gold(conn, "revenue", END) == (2536000000, "InterestIncomeExpenseNet+NoninterestIncome")


def test_a_net_revenue_tag_outranks_the_sum(conn):
    put(
        conn,
        [
            ("RevenuesNetOfInterestExpense", *Q3, 2500000000, "q", FILED),
            ("InterestIncomeExpenseNet", *Q3, 1793000000, "q", FILED),
            ("NoninterestIncome", *Q3, 743000000, "q", FILED),
        ],
        sic="6021",
    )
    assert gold(conn, "revenue", END)[0] == 2500000000


def test_a_sum_with_a_part_thats_nothing_isnt_a_sum(conn):
    # State Street's 2019 10-Qs tag NoninterestIncome 0 and -1 beside $613M: Revenues stays right
    put(
        conn,
        [
            ("Revenues", *Q3, 2873000000, "q", FILED),
            ("InterestIncomeExpenseNet", *Q3, 613000000, "q", FILED),
            ("NoninterestIncome", *Q3, 0, "q", FILED),
        ],
        sic="6022",
    )
    assert gold(conn, "revenue", END)[0] == 2873000000


def test_a_sums_parts_must_come_from_one_filing(conn):
    put(
        conn,
        [
            ("InterestIncomeExpenseNet", *Q3, 1793000000, "a", FILED),
            ("NoninterestIncome", *Q3, 743000000, "b", "2027-11-02"),
        ],
        sic="6022",
    )
    assert gold(conn, "revenue", END) is None


@pytest.mark.parametrize("tag", ["InterestIncomeExpenseNet", "NoninterestIncome"])
def test_a_part_alone_is_never_revenue(conn, tag):
    # a manufacturer's net interest income isn't revenue
    put(conn, [(tag, *Q3, 5000000, "q", FILED)])
    assert gold(conn, "revenue", END) is None


def test_a_sum_derives_quarters_like_any_other_line(conn):
    put(
        conn,
        [
            ("InterestIncomeExpenseNet", "2026-01-01", "2026-06-30", 200, "h1", "2026-08-01"),
            ("NoninterestIncome", "2026-01-01", "2026-06-30", 100, "h1", "2026-08-01"),
            ("InterestIncomeExpenseNet", "2026-01-01", END, 310, "q3", FILED),
            ("NoninterestIncome", "2026-01-01", END, 160, "q3", FILED),
        ],
        sic="6021",
    )
    assert gold(conn, "revenue", END)[0] == 170


def test_the_utility_tag_only_fills_where_nothing_else_does(conn):
    # DTE stopped tagging Revenues in 2018; where both exist Revenues keeps its place
    put(
        conn,
        [
            ("Revenues", *Q1, 3000000000, "a", "2026-05-02"),
            ("RegulatedAndUnregulatedOperatingRevenue", *Q1, 2900000000, "a", "2026-05-02"),
            ("RegulatedAndUnregulatedOperatingRevenue", *Q3, 5141000000, "q", FILED),
        ],
        sic="4911",
    )
    assert gold(conn, "revenue", "2026-03-31")[0] == 3000000000
    assert gold(conn, "revenue", END)[0] == 5141000000


# --- dates -------------------------------------------------------------------------------------


def test_a_period_after_its_filing_stays_out_and_is_reported(conn):
    # Legacy Housing's income tax at 2033-03-31 in a 10-Q filed 2024-05-09
    rows = [
        ("IncomeTaxExpenseBenefit", s, e, 1000, f"q{i}", "2024-05-09")
        for i, (s, e) in enumerate(
            [
                ("2023-01-01", "2023-03-31"),
                ("2023-04-01", "2023-06-30"),
                ("2023-07-01", "2023-09-30"),
                ("2023-10-01", "2023-12-31"),
            ]
        )
    ]
    put(
        conn,
        [
            *rows,
            ("IncomeTaxExpenseBenefit", "2033-01-01", "2033-03-31", 3373000, "typo", "2024-05-09"),
        ],
    )
    assert gold(conn, "income_tax", "2033-03-31") is None
    assert findings(conn, "series_gap") == []  # no "missing from Oct 2026 to Dec 2032"
    ((metric, end, detail),) = findings(conn, "mistagged_fact")
    assert (metric, end) == ("income_tax", date(2033, 3, 31))
    assert detail["rule"] == "period_after_filing" and detail["accession_no"] == "typo"
    assert "value" not in detail


def test_many_periods_after_a_filing_are_one_finding(conn):
    # PennyMac tags its debt maturities as future balance sheets
    put(
        conn,
        [
            ("Assets", None, END, 100, "q", FILED),
            *[("LongTermDebt", None, f"{y}-12-31", 5, "q", FILED) for y in range(2027, 2031)],
        ],
    )
    ((metric, end, detail),) = findings(conn, "mistagged_fact")
    assert (metric, end, detail["periods"]) == ("long_term_debt", date(2030, 12, 31), 4)
    assert gold(conn, "long_term_debt", "2027-12-31") is None


def test_a_few_days_after_the_filing_is_tolerated(conn):
    put(conn, [("Assets", None, END, 100, "q", "2026-09-25")])
    assert gold(conn, "total_assets", END)[0] == 100
    assert findings(conn, "mistagged_fact") == []


# --- net debt ----------------------------------------------------------------------------------


def balance(
    debt=8000,
    current=0,
    cash=2000,
    assets=30000,
    current_assets=None,
    receivables=None,
    inventory=None,
    end=END,
    filed=FILED,
    accession="q",
):
    rows = [
        ("LongTermDebtNoncurrent", None, end, debt, accession, filed),
        ("CashAndCashEquivalentsAtCarryingValue", None, end, cash, accession, filed),
        ("Assets", None, end, assets, accession, filed),
    ]
    if current:
        rows.append(("LongTermDebtCurrent", None, end, current, accession, filed))
    if current_assets is not None:
        rows.append(("AssetsCurrent", None, end, current_assets, accession, filed))
    if receivables is not None:
        rows.append(("AccountsReceivableNetCurrent", None, end, receivables, accession, filed))
    if inventory is not None:
        rows.append(("InventoryNet", None, end, inventory, accession, filed))
    return rows


def test_nikes_investments_moved_tag(conn):
    put(
        conn,
        [
            ("LongTermDebtNoncurrent", None, "2026-08-31", 5893, "q", FILED),
            ("LongTermDebtCurrent", None, "2026-08-31", 2000, "q", FILED),
            ("CashAndCashEquivalentsAtCarryingValue", None, "2026-08-31", 6903, "q", FILED),
            ("MarketableSecuritiesCurrent", None, "2022-02-28", 4763, "old", "2022-04-05"),
            (
                "DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent",
                None,
                "2026-08-31",
                1465,
                "q",
                FILED,
            ),
        ],
    )
    assert derived(conn, "net_debt", "2026-08-31") == -475


def test_nvidias_investments_are_the_sum_of_its_two_notes(conn):
    put(
        conn,
        [
            *balance(
                debt=32366,
                current=1000,
                cash=22443,
                assets=320272,
                current_assets=197412,
                receivables=63059,
                inventory=31575,
            ),
            ("MarketableSecuritiesCurrent", None, "2025-10-26", 49122, "old", "2025-11-19"),
            ("DebtSecuritiesCurrent", None, END, 34143, "q", FILED),
            ("EquitySecuritiesFvNi", None, END, 42783, "q", FILED),
        ],
    )
    assert gold(conn, "short_term_investments", END) == (
        76926,
        "DebtSecuritiesCurrent+EquitySecuritiesFvNi",
    )
    assert derived(conn, "net_debt", END) == -66003
    assert findings(conn, "net_debt_gap") == []


def test_equity_securities_alone_arent_short_term_investments(conn):
    put(conn, [("EquitySecuritiesFvNi", None, END, 22251, "q", FILED)])
    assert gold(conn, "short_term_investments", END) is None


def test_a_recent_material_series_with_room_leaves_net_debt_absent(conn):
    # NVIDIA's shape if it tagged nothing: a third of assets in securities, last seen three
    # quarters ago
    put(
        conn,
        [
            ("MarketableSecuritiesCurrent", None, "2026-03-31", 5000, "old", "2026-05-02"),
            *balance(current_assets=12000, receivables=1000, inventory=500),
        ],
    )
    assert derived(conn, "net_debt", END) is None
    assert derived(conn, "net_debt", END, "ttm") is None
    ((metric, end, detail),) = findings(conn, "net_debt_gap")
    assert (metric, end) == ("net_debt", date(2026, 9, 30))
    assert detail["investments_end"] == "2026-03-31"


def test_no_room_on_the_balance_sheet_is_the_filing_supporting_zero(conn):
    # VF, Neumora: current assets can't hold the old amount, so it's gone
    put(
        conn,
        [
            ("MarketableSecuritiesCurrent", None, "2026-03-31", 5000, "old", "2026-05-02"),
            *balance(current_assets=6000, receivables=1000, inventory=500),
        ],
    )
    assert derived(conn, "net_debt", END) == 6000
    assert findings(conn, "net_debt_gap") == []


def test_a_filing_that_tags_zero_is_a_value(conn):
    put(
        conn,
        [
            ("MarketableSecuritiesCurrent", None, "2026-03-31", 5000, "old", "2026-05-02"),
            ("MarketableSecuritiesCurrent", None, END, 0, "q", FILED),
            *balance(current_assets=12000),
        ],
    )
    assert derived(conn, "net_debt", END) == 6000


@pytest.mark.parametrize(
    "last,why",
    [
        (100, "under 1% of assets"),
        (400, "under 10% of debt less cash"),
    ],
)
def test_an_immaterial_series_counts_as_zero(conn, last, why):
    put(
        conn,
        [
            ("MarketableSecuritiesCurrent", None, "2026-03-31", last, "old", "2026-05-02"),
            *balance(debt=30000, cash=2000, assets=30000, current_assets=12000),
        ],
    )
    assert derived(conn, "net_debt", END) == 28000, why


def test_a_series_that_ended_over_two_years_ago_isnt_evidence(conn):
    put(
        conn,
        [
            ("MarketableSecuritiesCurrent", None, "2024-03-31", 5000, "old", "2024-05-02"),
            *balance(current_assets=12000),
        ],
    )
    assert derived(conn, "net_debt", END) == 6000


def test_a_company_that_never_tagged_investments_is_as_before(conn):
    put(conn, balance())
    assert derived(conn, "net_debt", END) == 6000


def test_investments_that_are_part_of_cash_arent_subtracted_twice(conn):
    # Target's 10-K: short-term investments of 4,611 inside cash of 5,488
    put(
        conn,
        [
            *balance(
                debt=15000,
                current=1000,
                cash=5488,
                assets=60000,
                current_assets=20005,
                receivables=800,
                inventory=12000,
            ),
            ("ShortTermInvestments", None, END, 4611, "q", FILED),
        ],
    )
    assert derived(conn, "net_debt", END) == 16000 - 5488
    assert gold(conn, "short_term_investments", END)[0] == 4611  # the note's figure is still there


def test_investments_beside_cash_are_subtracted(conn):
    put(
        conn,
        [
            *balance(current_assets=12000, receivables=1000, inventory=500),
            ("ShortTermInvestments", None, END, 3000, "q", FILED),
        ],
    )
    assert derived(conn, "net_debt", END) == 3000


def quarters(tag, value, last_end=END):
    return [
        (tag, s, e, value, f"i{e}", last_end) for s, e in [("2025-10-01", "2025-12-31"), Q1, Q2, Q3]
    ]


def test_debt_that_cant_carry_its_interest_has_no_net_debt(conn):
    # Ford: $291M of mapped debt beside $5.05B of interest gave -$49.67B and 0.01x
    put(
        conn,
        [
            *balance(debt=291, cash=25243, assets=267000),
            ("LongTermDebtNoncurrent", None, "2025-09-30", 291, "a", "2025-11-02"),
            ("StockholdersEquity", None, END, 30000, "q", FILED),
            *quarters("InterestExpense", 1262),
        ],
    )
    assert derived(conn, "net_debt", END) is None
    assert derived(conn, "debt_to_equity", END) is None
    assert findings(conn, "net_debt_gap")[0][2]["investments_end"] is None


def test_debt_paid_down_in_the_year_keeps_its_net_debt(conn):
    # Sonos, Teradata: the interest was on debt that's gone now
    put(
        conn,
        [
            *balance(debt=10, cash=500, assets=2000),
            ("LongTermDebtNoncurrent", None, "2025-09-30", 468, "a", "2025-11-02"),
            *quarters("InterestExpense", 10),
        ],
    )
    assert derived(conn, "net_debt", END) == -490


def test_no_debt_at_all_keeps_its_net_debt(conn):
    put(conn, [*balance(debt=0, cash=500, assets=2000), *quarters("InterestExpense", 10)])
    assert derived(conn, "net_debt", END) == -500


# --- debt tags ---------------------------------------------------------------------------------


def test_longtermdebt_fills_in_for_the_noncurrent_line(conn):
    # Adobe: LongTermDebt 4,766 and DebtCurrent 1,597 are the balance sheet's two lines
    put(
        conn,
        [
            ("LongTermDebt", None, END, 4766, "q", FILED),
            ("DebtCurrent", None, END, 1597, "q", FILED),
            ("Liabilities", None, END, 18217, "q", FILED),
            ("LiabilitiesCurrent", None, END, 11888, "q", FILED),
            ("CashAndCashEquivalentsAtCarryingValue", None, END, 4359, "q", FILED),
        ],
    )
    assert gold(conn, "long_term_debt", END) == (4766, "LongTermDebt")
    assert gold(conn, "long_term_debt_current", END) == (1597, "DebtCurrent")
    assert derived(conn, "net_debt", END) == 4766 + 1597 - 4359


def test_the_noncurrent_tag_outranks_a_total(conn):
    # NVIDIA: LongTermDebt 33,366 = LongTermDebtNoncurrent 32,366 + current 1,000
    put(
        conn,
        [
            ("LongTermDebt", None, END, 33366, "q", FILED),
            ("LongTermDebtNoncurrent", None, END, 32366, "q", FILED),
            ("LongTermDebtCurrent", None, END, 1000, "q", FILED),
            ("CashAndCashEquivalentsAtCarryingValue", None, END, 100, "q", FILED),
        ],
    )
    assert gold(conn, "long_term_debt", END) == (32366, "LongTermDebtNoncurrent")
    assert derived(conn, "net_debt", END) == 33266


def test_a_total_larger_than_noncurrent_liabilities_isnt_counted_twice(conn):
    # Molina: LongTermDebt 1,420 with 455 current and only 1,216 of noncurrent liabilities
    put(
        conn,
        [
            ("LongTermDebt", None, END, 1420, "q", FILED),
            ("LongTermDebtCurrent", None, END, 455, "q", FILED),
            ("Liabilities", None, END, 5000, "q", FILED),
            ("LiabilitiesCurrent", None, END, 3784, "q", FILED),
            ("CashAndCashEquivalentsAtCarryingValue", None, END, 100, "q", FILED),
        ],
    )
    assert gold(conn, "long_term_debt", END) == (965, "LongTermDebt-CurrentPortion")
    assert derived(conn, "net_debt", END) == 1320


def test_a_total_no_larger_than_its_current_portion_isnt_counted_twice(conn):
    # Advanced Energy: LongTermDebt 568.2 and LongTermDebtCurrent 568.2
    put(
        conn,
        [
            ("LongTermDebt", None, END, 568, "q", FILED),
            ("LongTermDebtCurrent", None, END, 568, "q", FILED),
            ("CashAndCashEquivalentsAtCarryingValue", None, END, 100, "q", FILED),
        ],
    )
    assert derived(conn, "net_debt", END) == 468


@pytest.mark.parametrize("sic", ["6021", None])
def test_no_fallback_debt_for_a_bank_or_an_unknown_industry(conn, sic):
    # JPMorgan's unclassified balance sheet has no noncurrent line: the golden `absent` row
    put(conn, [("LongTermDebt", None, END, 400000, "q", FILED)], sic=sic)
    assert gold(conn, "long_term_debt", END) is None


def test_a_debt_figure_many_times_total_liabilities_is_a_scale_slip(conn):
    # Bank First's 2019 10-K: 38,400,000,000 for $38.4M, against liabilities of $1.98B and assets
    # of $2.21B
    put(
        conn,
        [
            ("LongTermDebtNoncurrent", None, END, 38400000000, "k", FILED, "10-K"),
            ("Liabilities", None, END, 1979957000, "k", FILED, "10-K"),
            ("Assets", None, END, 2210168000, "k", FILED, "10-K"),
            ("CashAndCashEquivalentsAtCarryingValue", None, END, 100, "k", FILED, "10-K"),
        ],
        sic="6021",
    )
    assert gold(conn, "long_term_debt", END) is None
    assert derived(conn, "net_debt", END) is None
    ((metric, end, detail),) = findings(conn, "mistagged_fact")
    assert metric == "long_term_debt" and detail["rule"] == "debt_above_liabilities"


def test_debt_a_little_above_a_partial_liabilities_tag_stays(conn):
    # FirstEnergy 2008: LongTermDebt 9,100 against a Liabilities tag of 7,098
    put(
        conn,
        [
            ("LongTermDebtNoncurrent", None, END, 9100, "k", FILED),
            ("Liabilities", None, END, 7098, "k", FILED),
        ],
    )
    assert gold(conn, "long_term_debt", END)[0] == 9100


def test_debt_far_above_a_wrong_liabilities_tag_stays_when_assets_hold_it(conn):
    # USA Compression's 2017: 783M of debt against a 54M Liabilities tag and $1.72B of assets
    put(
        conn,
        [
            ("LongTermDebtNoncurrent", None, END, 782902000, "k", FILED),
            ("Liabilities", None, END, 54083000, "k", FILED),
            ("Assets", None, END, 1718953000, "k", FILED),
        ],
    )
    assert gold(conn, "long_term_debt", END)[0] == 782902000
    assert findings(conn, "mistagged_fact") == []


# --- cash --------------------------------------------------------------------------------------


def test_cash_fills_in_only_where_the_usual_tags_stop_and_on_a_balance_sheet(conn):
    # Guerrilla RF; Boston Scientific's "Cash" on 2023-02-20 is the price of a deal
    put(
        conn,
        [
            ("CashAndCashEquivalentsAtCarryingValue", None, "2026-06-30", 90, "a", "2026-08-01"),
            ("Cash", None, "2026-06-30", 30, "a", "2026-08-01"),
            ("Assets", None, "2026-06-30", 500, "a", "2026-08-01"),
            ("Cash", None, END, 40, "q", FILED),
            ("Assets", None, END, 500, "q", FILED),
            ("Cash", None, "2026-07-15", 140, "q", FILED),
        ],
    )
    assert gold(conn, "cash", "2026-06-30")[0] == 90
    assert gold(conn, "cash", END) == (40, "Cash")
    assert gold(conn, "cash", "2026-07-15") is None


# --- gross profit ------------------------------------------------------------------------------


def income(revenue=4312, cost=2669, op=984, sga=None, gross=None):
    rows = [("Revenues", *Q3, revenue, "q", FILED), ("CostOfRevenue", *Q3, cost, "q", FILED)]
    if op is not None:
        rows.append(("OperatingIncomeLoss", *Q3, op, "q", FILED))
    if sga is not None:
        rows.append(("SellingGeneralAndAdministrativeExpense", *Q3, sga, "q", FILED))
    if gross is not None:
        rows.append(("GrossProfit", *Q3, gross, "q", FILED))
    return rows


def test_vertiv_gets_a_gross_profit_marked_as_computed(conn):
    put(conn, income())
    row = conn.execute(
        "SELECT value, value_source, source_tag, accession_no, source_accessions"
        " FROM gold.quarterly_financials WHERE company_id = %s AND metric = 'gross_profit'",
        (CO,),
    ).fetchone()
    assert row == (1643, "derived", "derived:revenue-cost_of_revenue", "q", ["q"])
    assert derived(conn, "gross_margin", END) == Decimal("0.381030")


def test_a_reported_gross_profit_wins(conn):
    put(conn, income(gross=1500))
    assert gold(conn, "gross_profit", END) == (1500, "GrossProfit")


@pytest.mark.parametrize(
    "sic,who",
    [
        ("6324", "UnitedHealth"),
        ("6021", "a bank"),
        ("4911", "CenterPoint"),
        ("4931", "Xcel"),
        ("5812", "McDonald's"),
        ("5331", "Costco"),
        ("1311", "an oil producer"),
        ("2911", "Chevron"),
        ("4841", "a cable company"),
        ("5912", "CVS"),
        (None, "no SIC"),
    ],
)
def test_no_gross_profit_where_cost_isnt_the_cost_of_revenue(conn, sic, who):
    put(conn, income(), sic=sic)
    assert gold(conn, "gross_profit", END) is None, who


def test_no_gross_profit_when_cost_is_under_15_percent_of_revenue(conn):
    put(conn, income(revenue=7099, cost=680, op=None))
    assert gold(conn, "gross_profit", END) is None


def test_no_gross_profit_below_operating_income(conn):
    put(conn, income(revenue=100, cost=90, op=20))
    assert gold(conn, "gross_profit", END) is None


def test_no_gross_profit_with_a_large_unexplained_cost(conn):
    # CVS: 43.6% "gross margin", 3.3% operating margin, 6% SG&A: benefit costs are outside the
    # cost tag
    put(conn, income(revenue=108399, cost=61185, op=3577, sga=6500))
    assert gold(conn, "gross_profit", END) is None


def test_no_gross_profit_before_2019(conn):
    put(
        conn,
        [
            ("Revenues", "2018-07-01", "2018-09-30", 100, "q", "2018-11-02"),
            ("CostOfRevenue", "2018-07-01", "2018-09-30", 60, "q", "2018-11-02"),
        ],
    )
    assert gold(conn, "gross_profit", "2018-09-30") is None


def test_a_quarter_far_from_the_companys_usual_margin_is_left_out(conn):
    # a Q4 whose cost mixes two tags: 84% against a usual 40% (Caleres)
    rows = []
    for i, (s, e) in enumerate(
        [
            ("2025-01-01", "2025-03-31"),
            ("2025-04-01", "2025-06-30"),
            ("2025-07-01", "2025-09-30"),
            ("2025-10-01", "2025-12-31"),
            Q1,
        ]
    ):
        rows += [
            ("Revenues", s, e, 100, f"q{i}", "2026-11-02"),
            ("CostOfRevenue", s, e, 16 if i == 3 else 60, f"q{i}", "2026-11-02"),
        ]
    put(conn, rows)
    assert gold(conn, "gross_profit", "2025-12-31") is None
    assert gold(conn, "gross_profit", "2025-09-30")[0] == 40


# --- a year without its quarters -----------------------------------------------------------------


def test_a_stated_year_fills_ttm_when_quarters_dont_exist(conn):
    # NVIDIA's FY2023 capex: 1,833 in the 10-K, quarters tagged with its own extension
    put(
        conn,
        [
            ("PaymentsToAcquireProductiveAssets", *FY, 1833, "k", "2026-02-25", "10-K"),
            ("NetCashProvidedByUsedInOperatingActivities", *FY, 5641, "k", "2026-02-25", "10-K"),
        ],
    )
    assert derived(conn, "capex", "2025-12-31", "ttm") == 1833
    assert derived(conn, "free_cash_flow", "2025-12-31", "ttm") == 3808
    inputs = conn.execute(
        "SELECT inputs FROM gold.derived_financials WHERE company_id = %s AND metric = 'capex'",
        (CO,),
    ).fetchone()[0]
    assert inputs["quarters"][0]["value"] == 1833


def test_quarters_win_over_the_stated_year(conn):
    put(
        conn,
        [
            *[
                ("PaymentsToAcquireProductiveAssets", s, e, 10, f"q{e}", "2026-11-02")
                for s, e in [
                    ("2025-01-01", "2025-03-31"),
                    ("2025-04-01", "2025-06-30"),
                    ("2025-07-01", "2025-09-30"),
                    ("2025-10-01", "2025-12-31"),
                ]
            ],
            ("PaymentsToAcquireProductiveAssets", *FY, 45, "k", "2026-02-25", "10-K"),
        ],
    )
    assert derived(conn, "capex", "2025-12-31", "ttm") == 40
    assert len(findings(conn, "annual_sum")) == 1  # the disagreement is still a finding


def test_an_annual_share_count_isnt_a_ttm(conn):
    put(conn, [("EarningsPerShareBasic", *FY, 3, "k", "2026-02-25", "10-K")])
    assert derived(conn, "eps_basic", "2025-12-31", "ttm") is None


# --- rounded sentences --------------------------------------------------------------------------


def test_a_figure_rounded_for_a_sentence_loses_to_the_exact_difference(conn):
    # NVIDIA Q2 FY2027: PaymentsOfDividends 6,000 for the quarter; 6,290 year to date less 243
    m = 1000000
    put(
        conn,
        [
            ("PaymentsOfDividends", "2026-01-26", "2026-04-26", 243 * m, "q1", "2026-05-20"),
            ("PaymentsOfDividends", "2026-01-26", "2026-07-26", 6290 * m, "q2", "2026-08-26"),
            ("PaymentsOfDividends", "2026-04-27", "2026-07-26", 6000 * m, "q2", "2026-08-26"),
        ],
    )
    assert gold(conn, "dividends_paid", "2026-07-26")[0] == 6047 * m


@pytest.mark.parametrize(
    "reported,why",
    [
        (1230000000, "a filer in millions rounding one unit: under 0.2% from the difference"),
        (5000000000, "far from the difference: a restatement, not rounding"),
        (1237000000, "four significant digits isn't a rounded sentence"),
        (1250000000, "three significant digits can be an exact figure in millions (IBM's 5,260)"),
    ],
)
def test_other_reported_quarters_stay(conn, reported, why):
    put(
        conn,
        [
            ("PaymentsOfDividends", "2026-01-01", "2026-03-31", 1000000000, "q1", "2026-05-20"),
            ("PaymentsOfDividends", "2026-01-01", "2026-06-30", 2231000000, "q2", "2026-08-26"),
            ("PaymentsOfDividends", *Q2, reported, "q2", "2026-08-26"),
        ],
    )
    assert gold(conn, "dividends_paid", "2026-06-30")[0] == reported, why


def test_a_filers_own_rounding_of_two_year_to_date_figures_stays(conn):
    # Lowe's income tax: 450 for the quarter, 449 as the difference of two figures in millions
    m = 1000000
    put(
        conn,
        [
            ("IncomeTaxExpenseBenefit", "2018-02-03", "2018-05-04", 637 * m, "q1", "2018-05-30"),
            ("IncomeTaxExpenseBenefit", "2018-02-03", "2018-08-03", 1086 * m, "q2", "2018-08-31"),
            ("IncomeTaxExpenseBenefit", "2018-05-05", "2018-08-03", 450 * m, "q2", "2018-08-31"),
        ],
    )
    assert gold(conn, "income_tax", "2018-08-03")[0] == 450 * m


# --- EPS in a loss ------------------------------------------------------------------------------


def test_a_loss_quarter_with_only_basic_eps_has_that_as_diluted(conn):
    # Guerrilla RF: +0.06 diluted in Q1, then only basic: -0.21, -0.35
    put(
        conn,
        [
            ("EarningsPerShareDiluted", *Q1, Decimal("0.06"), "a", "2026-05-02"),
            ("EarningsPerShareBasic", *Q1, Decimal("0.06"), "a", "2026-05-02"),
            ("EarningsPerShareBasic", *Q2, Decimal("-0.21"), "b", "2026-08-01"),
            ("EarningsPerShareBasic", *Q3, Decimal("-0.35"), "c", FILED),
        ],
    )
    assert gold(conn, "eps_diluted", "2026-03-31") == (Decimal("0.06"), "EarningsPerShareDiluted")
    assert gold(conn, "eps_diluted", END) == (Decimal("-0.35"), "EarningsPerShareBasic")


def test_a_profit_quarter_never_borrows_basic_eps(conn):
    put(conn, [("EarningsPerShareBasic", *Q3, Decimal("0.50"), "c", FILED)])
    assert gold(conn, "eps_diluted", END) is None


# --- gaps ---------------------------------------------------------------------------------------


def test_the_same_quarter_missing_for_years_is_one_finding(conn):
    # NVIDIA's capex, FY2017-21: three quarters every year, the 10-K under its own tag
    rows = []
    for y in range(2021, 2026):
        rows += [
            (
                "PaymentsToAcquireProductiveAssets",
                f"{y}-01-01",
                f"{y}-03-31",
                1,
                f"q{y}a",
                f"{y}-05-01",
            ),
            (
                "PaymentsToAcquireProductiveAssets",
                f"{y}-04-01",
                f"{y}-06-30",
                1,
                f"q{y}b",
                f"{y}-08-01",
            ),
            (
                "PaymentsToAcquireProductiveAssets",
                f"{y}-07-01",
                f"{y}-09-30",
                1,
                f"q{y}c",
                f"{y}-11-01",
            ),
        ]
    put(conn, rows)
    ((metric, end, detail),) = findings(conn, "series_gap")
    assert (metric, end, detail["repeated"]) == ("capex", date(2025, 3, 31), 4)
    assert detail["missing_from"] == "2024-10-01"


def test_a_one_off_gap_is_as_before(conn):
    rows = [
        ("PaymentsToAcquireProductiveAssets", s, e, 1, f"q{e}", "2026-11-02")
        for s, e in [
            ("2025-01-01", "2025-03-31"),
            ("2025-04-01", "2025-06-30"),
            ("2025-07-01", "2025-09-30"),
            ("2025-10-01", "2025-12-31"),
            Q2,
        ]
    ]
    put(conn, rows)
    ((_, end, detail),) = findings(conn, "series_gap")
    assert end == date(2026, 6, 30) and "repeated" not in detail


# --- the fiscal month ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mmdd,month",
    [
        ("0103", 12),
        ("0102", 12),
        ("0101", 12),
        ("0201", 1),
        ("0202", 1),
        ("0301", 2),
        ("0403", 3),
        ("0503", 4),
        ("0703", 6),
        ("0902", 8),
        ("1003", 9),
        ("1101", 10),
        ("0131", 1),
        ("0126", 1),
        ("0108", 1),
        ("1231", 12),
        ("0926", 9),
        ("0630", 6),
        (None, None),
    ],
)
def test_fiscal_year_end_month(conn, mmdd, month):
    assert conn.execute("SELECT silver.fiscal_year_end_month(%s)", (mmdd,)).fetchone()[0] == month


def test_the_month_changes_no_fiscal_label(conn):
    # J&J's "0103": its year ending 2025-12-28 is still fiscal 2025 Q4
    conn.execute(
        "UPDATE silver.companies SET fiscal_year_end = '0103' WHERE company_id = %s", (CO,)
    )
    put(conn, [("Revenues", "2025-09-29", "2025-12-28", 100, "k", "2026-02-11")])
    row = conn.execute(
        "SELECT fiscal_year, fiscal_quarter FROM gold.financials"
        " WHERE company_id = %s AND span = 'quarter' AND metric = 'revenue'",
        (CO,),
    ).fetchone()
    assert row == (2025, 4)
