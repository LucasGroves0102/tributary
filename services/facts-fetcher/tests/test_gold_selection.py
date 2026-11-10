"""Which fact gold believes, and what it computes from them (migrations 038-041), on the shapes real
filings have; the companies in the comments are where each came from. Skipped unless
TEST_DATABASE_URL is set; every test is rolled back."""

import os
from datetime import date
from decimal import Decimal

import pytest

from facts_fetcher.db import company_ids, refresh_gold

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
    "RevenueFromContractWithCustomerIncludingAssessedTax": "revenue",
    "RevenuesNetOfInterestExpense": "revenue",
    "InterestIncomeExpenseNet": "revenue",
    "NoninterestIncome": "revenue",
    "RegulatedAndUnregulatedOperatingRevenue": "revenue",
    "SalesRevenueNet": "revenue",
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
    "InterestExpenseNonoperating": "interest_expense",
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
    "ShareBasedCompensation": "stock_based_compensation",
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


NET = "RevenuesNetOfInterestExpense"
INCLUDING = "RevenueFromContractWithCustomerIncludingAssessedTax"


def test_net_revenue_under_a_fifth_of_total_revenue_is_a_part(conn):
    # Rectitude Holdings' 20-Fs (SGD): S$74,328 under the net tag beside S$43.8M of Revenues, and
    # growth read +68,870% from it (064). The years are stated years only, so TTM reads them.
    years = [
        ("2022-04-01", "2023-03-31", 106263, 37643696, "2023-07-31"),
        ("2023-04-01", "2024-03-31", 83659, 41353555, "2024-07-31"),
        ("2024-04-01", "2025-03-31", 74328, 43796144, "2025-07-31"),
    ]
    put(
        conn,
        [
            row
            for start, end, net, total, filed in years
            for row in (
                (NET, start, end, net, f"f{end}", filed, "20-F"),
                ("Revenues", start, end, total, f"f{end}", filed, "20-F"),
                (INCLUDING, start, end, total, f"f{end}", filed, "20-F"),
            )
        ],
    )
    assert derived(conn, "revenue", "2025-03-31", "ttm") == 43796144
    assert float(derived(conn, "revenue_growth", "2025-03-31", "ttm")) == pytest.approx(
        43796144 / 41353555 - 1, abs=1e-6
    )


def test_a_net_revenue_quarter_under_a_fifth_of_total_revenue_is_a_part(conn):
    put(
        conn,
        [
            (NET, *Q3, 74328, "q", FILED),
            ("Revenues", *Q3, 43796144, "q", FILED),
        ],
    )
    assert gold(conn, "revenue", END) == (43796144, "Revenues")


def test_net_revenue_is_compared_with_contract_revenue_too(conn):
    # Rectitude also tags its total under the contract tag that includes assessed tax: with no
    # Revenues fact, that is the gross line
    put(
        conn,
        [
            (NET, *Q3, 74328, "q", FILED),
            (INCLUDING, *Q3, 43796144, "q", FILED),
        ],
    )
    assert gold(conn, "revenue", END) == (43796144, INCLUDING)


def test_a_zero_and_a_net_revenue_sliver_both_lose_to_contract_revenue(conn):
    # 039's zero rule takes Revenues' zero out; the sliver under the net tag was then believed
    put(
        conn,
        [
            ("Revenues", *Q3, 0, "q", FILED),
            (NET, *Q3, 74328, "q", FILED),
            ("RevenueFromContractWithCustomerExcludingAssessedTax", *Q3, 43796144, "q", FILED),
        ],
    )
    assert gold(conn, "revenue", END) == (
        43796144,
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    )


@pytest.mark.parametrize(
    ("net", "believed"),
    [
        (15200000000, 15200000000),  # Goldman-shaped: net revenues 42% of gross, a real line
        (7300000000, 7300000000),  # 20.3%: above the line
        (7100000000, 36000000000),  # 19.7%: a part
    ],
)
def test_a_brokers_net_revenue_stays_above_a_fifth_of_gross(conn, net, believed):
    put(
        conn,
        [
            (NET, *Q3, net, "q", FILED),
            ("Revenues", *Q3, 36000000000, "q", FILED),
        ],
        sic="6211",
    )
    assert gold(conn, "revenue", END)[0] == believed


def test_net_revenue_alone_stays_however_small(conn):
    # nothing to compare it with: no gross line in the period
    put(conn, [(NET, *Q3, 74328, "q", FILED)])
    assert gold(conn, "revenue", END) == (74328, NET)


EXCLUDING = "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_a_contract_tag_filed_a_thousand_times_too_large_is_a_slip(conn):
    # Tigo Energy files contract revenue 1,000x too large in every filing beside the right Revenues
    # (Q1 2023: $50,058,000,000 and $50,058,000; its other figures at most $31.7M), and $100K under
    # the net tag. 039 made Revenues a part of the slip and believed the net sliver; a net revenue
    # part rule alone would believe the slip itself. Its stated year the same ($81.3B and $81.3M).
    put(
        conn,
        [
            (NET, *Q3, 100000, "q", FILED),
            ("Revenues", *Q3, 50058000, "q", FILED),
            (EXCLUDING, *Q3, 50058000000, "q", FILED),
            ("CostOfRevenue", *Q3, 31689000, "q", FILED),
            ("Revenues", *FY, 81323000, "k", "2026-02-25", "10-K"),
            (EXCLUDING, *FY, 81323000000, "k", "2026-02-25", "10-K"),
            ("CostOfRevenue", *FY, 56552000, "k", "2026-02-25", "10-K"),
        ],
    )
    assert gold(conn, "revenue", END) == (50058000, "Revenues")
    assert derived(conn, "revenue", "2025-12-31", "ttm") == 81323000


def test_a_contract_tag_filed_in_thousands_is_the_slip(conn):
    # Plexus files contract revenue excluding tax in thousands (982,607) beside the same figure in
    # dollars including tax ($982,607,000; cost $894M): the smaller one is the slip
    put(
        conn,
        [
            (EXCLUDING, *Q3, 982607, "q", FILED),
            (INCLUDING, *Q3, 982607000, "q", FILED),
            ("CostOfRevenue", *Q3, 894467000, "q", FILED),
        ],
    )
    assert gold(conn, "revenue", END) == (982607000, INCLUDING)


def test_a_thousands_slip_beside_the_right_revenues_changes_nothing(conn):
    # FactSet's contract tag at 329,141 beside Revenues of $329,141,000 (cost $161.5M): the smaller
    # is the slip, as priority already had it
    put(
        conn,
        [
            ("Revenues", *Q3, 329141000, "q", FILED),
            (INCLUDING, *Q3, 329141, "q", FILED),
            ("CostOfRevenue", *Q3, 161524000, "q", FILED),
        ],
    )
    assert gold(conn, "revenue", END) == (329141000, "Revenues")


def test_with_no_other_figure_the_smaller_of_a_pair_is_the_slip(conn):
    # nothing else in the period says which scale is right: the smaller is the slip, the usual case
    # (a figure filed in thousands: Southwest's 2010 Revenues of 3,168 beside $3.168B)
    put(
        conn,
        [
            ("Revenues", *Q3, 3168, "q", FILED),
            ("SalesRevenueNet", *Q3, 3168000000, "q", FILED),
        ],
    )
    assert gold(conn, "revenue", END) == (3168000000, "SalesRevenueNet")


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


def test_a_fallback_tag_below_its_current_portion_is_the_noncurrent_line(conn):
    # the review's case: LongTermDebt 100 beside DebtCurrent 300 was read as a total and gave
    # long-term debt -200 and net debt 50; a total can't be smaller than its own current part
    put(
        conn,
        [
            ("LongTermDebt", None, END, 100, "q", FILED),
            ("DebtCurrent", None, END, 300, "q", FILED),
            ("Assets", None, END, 5000, "q", FILED),
            ("Liabilities", None, END, 2000, "q", FILED),
            ("LiabilitiesCurrent", None, END, 900, "q", FILED),
            ("CashAndCashEquivalentsAtCarryingValue", None, END, 50, "q", FILED),
        ],
    )
    assert gold(conn, "long_term_debt", END) == (100, "LongTermDebt")
    assert derived(conn, "net_debt", END) == 100 + 300 - 50
    assert findings(conn, "impossible_value") == []


def test_negative_debt_reads_as_the_balance(conn):
    # 050: the debt note's "less current portion (x)" sign (Euronet's 2013 10-K tags its current
    # debt -10,903,000 where three 10-Qs said +10,903,000); a balance can't be below zero
    put(conn, [("LongTermDebtNoncurrent", None, END, -5, "q", FILED),
               ("LongTermDebtCurrent", None, END, -1, "q", FILED)])
    assert gold(conn, "long_term_debt", END) == (5, "LongTermDebtNoncurrent")
    assert gold(conn, "long_term_debt_current", END) == (1, "LongTermDebtCurrent")
    assert findings(conn, "impossible_value") == []


def test_a_debt_sign_that_changed_between_filings_isnt_a_restatement(conn):
    put(conn, [("LongTermDebtCurrent", None, END, 10903000, "q", "2026-11-02"),
               ("LongTermDebtCurrent", None, END, -10903000, "k", "2027-02-20", "10-K")])
    assert gold(conn, "long_term_debt_current", END) == (10903000, "LongTermDebtCurrent")
    assert findings(conn, "restatement") == []


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


def test_debt_is_compared_with_liabilities_in_its_own_currency(conn):
    """057: a yen filer's 20-F also tags a convenience dollar translation (filed after it here, so
    the check's latest-first lookup met it): ¥5T of debt against $200B of liabilities and $400B of
    assets read as a scale slip. Compared in yen (¥30T, ¥60T) it is ordinary debt."""
    conn.execute("UPDATE silver.companies SET sic = '3711' WHERE company_id = %s", (CO,))
    conn.execute("DELETE FROM silver.financial_facts")
    for tag, metric, value, unit, filed in (
            ("LongTermDebtNoncurrent", "long_term_debt", 5_000_000_000_000, "JPY", FILED),
            ("Liabilities", "total_liabilities", 30_000_000_000_000, "JPY", FILED),
            ("Assets", "total_assets", 60_000_000_000_000, "JPY", FILED),
            ("Liabilities", "total_liabilities", 200_000_000_000, "USD", "2026-11-03"),
            ("Assets", "total_assets", 400_000_000_000, "USD", "2026-11-03")):
        conn.execute(
            "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
            " value, accession_no, form, filed, ingested_at) VALUES (%s, %s, %s, %s, NULL, %s, %s,"
            " %s, '20-F', %s, '2026-11-03')",
            (CO, metric, tag, unit, END, value, "k" if unit == "JPY" else "k2", filed))
    conn.execute("SELECT ops.check_financials(ARRAY[%s])", (CO,))
    assert [f for f in findings(conn, "mistagged_fact")
            if f[2].get("rule") == "debt_above_liabilities"] == []
    # debt over five times yen liabilities and assets is still a slip
    conn.execute("UPDATE silver.financial_facts SET value = value / 7 WHERE unit = 'JPY'"
                 " AND tag IN ('Liabilities', 'Assets')")
    conn.execute("UPDATE silver.financial_facts SET value = value * 10 WHERE unit = 'JPY'"
                 " AND tag = 'LongTermDebtNoncurrent'")
    conn.execute("SELECT ops.check_financials(ARRAY[%s])", (CO,))
    assert [f[2]["rule"] for f in findings(conn, "mistagged_fact")] == ["debt_above_liabilities"]


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


def test_a_stated_year_smaller_than_its_own_quarters_is_a_part(conn):
    # Clover Health's 2022: a 1,097 annual tag beside quarters of about 800 each, one missing
    put(
        conn,
        [
            *[
                ("Revenues", s, e, 800, f"q{e}", "2026-11-02")
                for s, e in [
                    ("2025-01-01", "2025-03-31"),
                    ("2025-04-01", "2025-06-30"),
                    ("2025-10-01", "2025-12-31"),
                ]
            ],
            (
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                *FY,
                1097,
                "k",
                "2026-02-25",
                "10-K",
            ),
        ],
    )
    assert derived(conn, "revenue", "2025-12-31", "ttm") is None


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


def ttm_ends(conn, metric):
    return [str(r[0]) for r in conn.execute(
        "SELECT period_end FROM gold.derived_financials WHERE company_id = %s AND span = 'ttm'"
        " AND metric = %s ORDER BY period_end", (CO, metric)).fetchall()]


def test_a_stated_year_a_few_days_off_its_quarters_ttm_is_the_same_year(conn):
    # ATI: FY2021 summed from quarters ends 2021-12-31; a later 10-K tags the same year ending
    # 2022-01-02 (a 52-53 week calendar), and gold had FY2021 twice. 031's rule: ends within 7
    # days are one period.
    contract = "RevenueFromContractWithCustomerExcludingAssessedTax"
    put(
        conn,
        [
            *[
                (contract, s, e, 700, f"q{e}", "2026-02-25")
                for s, e in [
                    ("2025-01-01", "2025-03-31"),
                    ("2025-04-01", "2025-06-30"),
                    ("2025-07-01", "2025-09-30"),
                    ("2025-10-01", "2025-12-31"),
                ]
            ],
            (contract, *FY, 2800, "k1", "2026-02-25", "10-K"),
            (contract, "2025-01-04", "2026-01-02", 2800, "k2", "2028-02-23", "10-K"),
        ],
    )
    assert ttm_ends(conn, "revenue") == ["2025-12-31"]


def test_two_stated_years_a_few_days_apart_are_one(conn):
    # the same two ends with no quarters to sum: one row, the later filing's
    put(
        conn,
        [
            ("PaymentsToAcquireProductiveAssets", *FY, 45, "k1", "2026-02-25", "10-K"),
            ("PaymentsToAcquireProductiveAssets", "2025-01-04", "2026-01-02", 45, "k2",
             "2028-02-23", "10-K"),
        ],
    )
    assert ttm_ends(conn, "capex") == ["2026-01-02"]


def test_stated_years_in_a_chain_are_compared_with_their_own_neighbours(conn):
    # 063 made 040's NOT EXISTS a window over ends within 7 days. Three ends, each within 7 days of
    # the next but the outer two 12 days apart: the middle one loses to the later-filed first, the
    # last loses only to the middle (the first is out of its reach), so the first stays alone
    capex = "PaymentsToAcquireProductiveAssets"
    put(
        conn,
        [
            (capex, "2024-12-27", "2025-12-25", 45, "k3", "2028-02-23", "10-K"),
            (capex, *FY, 45, "k2", "2027-02-23", "10-K"),
            (capex, "2025-01-07", "2026-01-06", 45, "k1", "2026-02-25", "10-K"),
        ],
    )
    assert ttm_ends(conn, "capex") == ["2025-12-25"]


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


# --- interest expense's sign (043) ---------------------------------------------------------------


def test_a_year_tagged_negative_still_derives_its_fourth_quarter(conn):
    # Disney's FY2025 10-K tags InterestExpense -1,812M; its 10-Qs +487M, +958M and +1,396M year
    # to date. The fourth quarter was -3,208M; it's 1,812 - 1,396 = 416M.
    put(
        conn,
        [
            ("InterestExpense", "2024-09-29", "2024-12-28", 487000000, "q1", "2025-02-05"),
            ("InterestExpense", "2024-09-29", "2025-03-29", 958000000, "q2", "2025-05-07"),
            ("InterestExpense", "2024-09-29", "2025-06-28", 1396000000, "q3", "2025-08-06"),
            ("InterestExpense", "2024-09-29", "2025-09-27", -1812000000, "k", "2025-11-13", "10-K"),
        ],
    )
    assert gold(conn, "interest_expense", "2025-09-27") == (416000000, "InterestExpense")
    assert gold(conn, "interest_expense", "2025-06-28") == (438000000, "InterestExpense")


def test_a_quarter_tagged_negative_reads_as_the_cost(conn):
    # UDR tags some first quarters -32,884K among +31,000K quarters
    put(
        conn,
        [
            ("InterestExpense", *Q1, -32884000, "q1", "2026-05-01"),
            ("InterestExpense", *Q2, 31691000, "q2", "2026-08-01"),
        ],
    )
    assert gold(conn, "interest_expense", "2026-03-31") == (32884000, "InterestExpense")
    assert gold(conn, "interest_expense", "2026-06-30") == (31691000, "InterestExpense")


def test_a_derived_quarter_still_negative_is_left_out(conn):
    # Sphere's FY2022: a 10-KT filed in 2025 restates the year's interest to 0 after a spin-off,
    # against nine months of 23.2M in the older 10-Qs. 0 - 23.2M isn't a quarter.
    put(
        conn,
        [
            ("InterestExpense", "2021-07-01", "2021-09-30", 9248000, "q1", "2021-11-09"),
            ("InterestExpense", "2021-07-01", "2021-12-31", 17415000, "q2", "2022-02-09"),
            ("InterestExpense", "2021-07-01", "2022-03-31", 23246000, "q3", "2022-05-09"),
            ("InterestExpense", "2021-07-01", "2022-06-30", 27155000, "k", "2022-08-19", "10-K"),
            (
                "InterestExpenseNonoperating", "2021-07-01", "2022-06-30", 0, "kt", "2025-03-03",
                "10-KT",
            ),
        ],
    )
    assert gold(conn, "interest_expense", "2022-06-30") is None
    assert gold(conn, "interest_expense", "2022-03-31") == (5831000, "InterestExpense")


def test_no_interest_expense_reaches_gold_negative(conn):
    put(
        conn,
        [
            ("InterestExpense", *Q1, -5000000, "q1", "2026-05-01"),
            ("InterestExpense", "2026-01-01", "2026-06-30", 9000000, "q2", "2026-08-01"),
            ("InterestExpense", "2026-01-01", "2026-09-30", -14000000, "q3", "2026-11-02"),
        ],
    )
    values = [
        r[0]
        for r in conn.execute(
            "SELECT value FROM gold.quarterly_financials"
            " WHERE company_id = %s AND metric = 'interest_expense'",
            (CO,),
        ).fetchall()
    ]
    assert sorted(values) == [4000000, 5000000, 5000000]


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


# --- filers the XBRL names, debt parts' scale, stock-based compensation benefits (050) --------

SUB = "0000028385"  # DTE Electric: the SEC filed DTE Energy's 2026 10-Qs under this CIK


def filing(conn, accession, entity, filers):
    """A stored 10-Q whose XBRL names `entity`, filed by `filers` (the feed's rows)."""
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name, url,"
        " content_gzip, content_sha256, size_bytes)"
        " VALUES (%s, %s, '10-Q', %s, 'q.htm', 'https://www.sec.gov/q.htm', '\\x00', 'x', 1)",
        (accession, entity, FILED),
    )
    conn.execute(
        "INSERT INTO bronze.filing_xbrl (accession_no, cik, entity_cik, document_sha256,"
        " extractor_version, status) VALUES (%s, %s, %s, 'x', 1, 'ok')",
        (accession, entity, entity),
    )
    for cik in filers:
        conn.execute(
            "INSERT INTO bronze.filings (accession_no, cik, form_type, filed_at, payload,"
            " kafka_partition, kafka_offset) VALUES (%s, %s, '10-Q', %s, '{}', 0, 0)",
            (accession, cik, FILED),
        )


def fact(conn, cik, tag, start, end, value, accession):
    conn.execute(
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed) VALUES (%s, %s, %s, 'USD', %s, %s, %s, %s, '10-Q', %s)",
        (cik, METRICS[tag], tag, start, end, value, accession, FILED),
    )


def dte(conn):
    """CO's Q2 under its own CIK; its Q3 10-Q (filed with SUB) only under SUB, as the SEC has it."""
    put(conn, [("Revenues", *Q2, 3369, "q2", "2026-07-28")])
    fact(conn, SUB, "Revenues", *Q3, 3727, "q3")
    fact(conn, SUB, "NetIncomeLoss", *Q3, 470, "q3")


def test_a_filing_the_sec_put_under_a_co_registrant_is_the_named_filers(conn):
    dte(conn)
    filing(conn, "q3", CO, [CO, SUB])
    assert company_ids(conn, [SUB]) == [CO]  # the loaders refresh CO when SUB's payload changes
    refresh_gold(conn, company_ids(conn, [SUB]))
    assert gold(conn, "revenue", END) == (3727, "Revenues")
    assert gold(conn, "net_income", END) == (470, "NetIncomeLoss")
    assert gold(conn, "revenue", "2026-06-30") == (3369, "Revenues")


def test_an_entity_that_isnt_a_filer_moves_nothing(conn):
    # a mistyped identifier in the XBRL: CO didn't file it, so the facts stay where the SEC put them
    dte(conn)
    filing(conn, "q3", CO, [SUB])
    assert company_ids(conn, [SUB]) == []
    refresh_gold(conn, [CO])
    assert gold(conn, "revenue", END) is None


def test_a_co_registrant_with_gold_of_its_own_gives_the_parents_figures_back(conn):
    # Southern California Edison's Q2 2026 revenue was Edison International's 4,357M
    dte(conn)
    filing(conn, "q3", CO, [CO, SUB])
    conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, tracked, sic)"
                 " VALUES (%s, 'DTE ELECTRIC', %s, false, '4911')", (SUB, SUB))
    conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)", (SUB, SUB))
    fact(conn, SUB, "Revenues", *Q2, 1900, "sub-q2")
    refresh_gold(conn, company_ids(conn, [SUB]))
    assert sorted(company_ids(conn, [SUB])) == sorted([CO, SUB])
    assert gold(conn, "revenue", END) == (3727, "Revenues")
    sub = conn.execute(
        "SELECT period_end, value FROM gold.quarterly_financials"
        " WHERE company_id = %s AND metric = 'revenue' ORDER BY period_end", (SUB,)).fetchall()
    assert sub == [(date(2026, 6, 30), 1900)]


def test_the_named_filers_own_facts_win(conn):
    # the SEC filed the facts under both: the company's own are read, nothing twice
    dte(conn)
    filing(conn, "q3", CO, [CO, SUB])
    fact(conn, CO, "Revenues", *Q3, 3727, "q3")
    fact(conn, CO, "NetIncomeLoss", *Q3, 470, "q3")
    refresh_gold(conn, [CO])
    rows = conn.execute(
        "SELECT count(*) FROM gold.quarterly_financials"
        " WHERE company_id = %s AND period_end = %s", (CO, END)).fetchone()[0]
    assert rows == 2
    assert gold(conn, "revenue", END) == (3727, "Revenues")


def test_debt_parts_at_a_thousand_times_their_total_are_left_out(conn):
    # Bank First's 2019 10-K: current 1,400,000,000 and noncurrent 38,400,000,000 beside its own
    # LongTermDebt of 39,800,000; 041 caught the noncurrent (above liabilities), not the current
    put(
        conn,
        [
            ("LongTermDebtCurrent", None, END, 1400000000, "k", FILED, "10-K"),
            ("LongTermDebtNoncurrent", None, END, 38400000000, "k", FILED, "10-K"),
            ("LongTermDebt", None, END, 39800000, "k", FILED, "10-K"),
            ("Liabilities", None, END, 1979957000, "k", FILED, "10-K"),
            ("Assets", None, END, 2210168000, "k", FILED, "10-K"),
        ],
        sic="6021",
    )
    assert gold(conn, "long_term_debt_current", END) is None
    assert gold(conn, "long_term_debt", END) is None
    rules = sorted((m, d["rule"], d.get("factor")) for m, _, d in findings(conn, "mistagged_fact"))
    assert rules == [("long_term_debt", "debt_above_liabilities", None),
                     ("long_term_debt_current", "debt_parts_scale", 1000)]


def test_a_non_bank_with_slipped_parts_keeps_the_filings_total(conn):
    put(
        conn,
        [
            ("LongTermDebtCurrent", None, END, 1400000000, "k", FILED, "10-K"),
            ("LongTermDebtNoncurrent", None, END, 38400000000, "k", FILED, "10-K"),
            ("LongTermDebt", None, END, 39800000, "k", FILED, "10-K"),
            ("Liabilities", None, END, 900000000, "k", FILED, "10-K"),
            ("Assets", None, END, 2000000000, "k", FILED, "10-K"),
        ],
    )
    assert gold(conn, "long_term_debt_current", END) is None
    assert gold(conn, "long_term_debt", END) == (39800000, "LongTermDebt")


def test_parts_that_add_up_to_their_total_stay(conn):
    put(
        conn,
        [
            ("LongTermDebtCurrent", None, END, 1400000, "k", FILED, "10-K"),
            ("LongTermDebtNoncurrent", None, END, 38400000, "k", FILED, "10-K"),
            ("LongTermDebt", None, END, 39800000, "k", FILED, "10-K"),
        ],
    )
    assert gold(conn, "long_term_debt_current", END) == (1400000, "LongTermDebtCurrent")
    assert gold(conn, "long_term_debt", END) == (38400000, "LongTermDebtNoncurrent")
    assert findings(conn, "mistagged_fact") == []


def sign_flips(conn):
    return conn.execute(
        "SELECT period_end, severity, detail->>'rule' FROM ops.quality_findings"
        " WHERE company_id = %s AND check_name = 'sign_flip'", (CO,)).fetchall()


SBC = [("ShareBasedCompensation", *Q1, 6728000, "q1", "2026-05-01"),
       ("ShareBasedCompensation", *Q2, -866000, "q2", "2026-08-01"),  # Aurinia: a reversal
       ("ShareBasedCompensation", *Q3, 2839000, "q3", "2026-11-02")]


def test_a_negative_stock_compensation_quarter_is_a_benefit_as_filed(conn):
    put(conn, SBC)
    assert gold(conn, "stock_based_compensation", "2026-06-30") == (-866000,
                                                                     "ShareBasedCompensation")
    assert sign_flips(conn) == [(date(2026, 6, 30), "info", "benefit")]


def test_a_benefit_another_filing_reports_positive_is_still_a_warning(conn):
    put(conn, [*SBC, ("ShareBasedCompensation", *Q2, 866000, "q2-orig", "2026-07-30")])
    assert sign_flips(conn) == [(date(2026, 6, 30), "warning", None)]


def test_a_cost_that_flips_sign_is_still_a_warning(conn):
    put(conn, [("PaymentsToAcquireProductiveAssets", *Q1, 100, "q1", "2026-05-01"),
               ("PaymentsToAcquireProductiveAssets", *Q2, -90, "q2", "2026-08-01"),
               ("PaymentsToAcquireProductiveAssets", *Q3, 110, "q3", "2026-11-02")])
    assert sign_flips(conn) == [(date(2026, 6, 30), "warning", None)]
