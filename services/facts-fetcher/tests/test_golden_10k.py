"""Gold against the figures printed in five 10-Ks (tests/golden/10k_figures.csv).

The CSV was read from the statements' visible text, not from XBRL (tests/golden/README.md).
Here the same companies' real companyfacts payloads (fixtures/golden/, trimmed to the mapped
tags and two fiscal years, later filings included) go through bronze → silver → gold, and:

- check `annual`: the fiscal-year value in gold equals the printed figure within half its last
  printed digit (±0.5 million when printed in millions). Flows: the TTM ending at fiscal
  year-end (the sum of gold's four fiscal quarters); balance-sheet items: the year-end value.
- the same flows again from gold.quarterly_financials directly: four quarters, the last one
  ending at fiscal year-end (usually Q4 = year - nine months), sum to the printed figure.
- check `annual_silver`: EPS and weighted shares are never summed or derived (gold keeps
  reported quarters only), so silver must hold the 10-K's fiscal-year fact with the printed
  value.
- check `absent`: printed, but gold has no value (an unmapped extension tag, a bank's
  unclassified balance sheet). Asserted so the gap stays documented and a fix shows up here.
- every TTM derived metric (margins, FCF, growth, payout, leverage, ROE) equals its 014
  formula recomputed from the CSV's validated (`annual`) figures, or is absent when an input
  is. eps_growth is quarterly only and has no fiscal-year value to compare.

Skipped unless TEST_DATABASE_URL is set; every test is rolled back.
"""

import csv
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="TEST_DATABASE_URL not set")

GOLDEN = Path(__file__).resolve().parents[3] / "tests" / "golden" / "10k_figures.csv"
ROWS = list(csv.DictReader(GOLDEN.open(newline="")))
FLOW_CHECKS = [r for r in ROWS if r["check"] == "annual" and r["statement"] in
               ("income", "cash_flow") and r["metric"] != "cash"]


@pytest.fixture
def fixtures_dir():
    return Path(__file__).parent / "fixtures" / "golden"


def row_id(r: dict) -> str:
    return f"{r['ticker']}-FY{r['fiscal_year']}-{r['metric']}"


def tolerance(r: dict) -> Decimal:
    return Decimal(r["printed_unit"]) / 2


def kind(conn, metric: str) -> str:
    return conn.execute("SELECT kind FROM silver.metrics WHERE metric = %s",
                        (metric,)).fetchone()[0]


def gold_value(conn, company_id: str, metric: str, period_end: str) -> Decimal | None:
    if kind(conn, metric) == "instant":
        row = conn.execute(
            "SELECT value FROM gold.quarterly_financials"
            " WHERE company_id = %s AND metric = %s AND period_end = %s",
            (company_id, metric, period_end)).fetchone()
    else:
        row = conn.execute(
            "SELECT value FROM gold.derived_financials WHERE company_id = %s AND span = 'ttm'"
            " AND metric = %s AND period_end = %s", (company_id, metric, period_end)).fetchone()
    return None if row is None else row[0]


@pytest.mark.parametrize("r", ROWS, ids=row_id)
def test_printed_figure(loaded, r):
    company_id, metric, end = r["cik"], r["metric"], r["period_end"]
    printed = Decimal(r["value"])
    if r["check"] == "annual":
        value = gold_value(loaded, company_id, metric, end)
        assert value is not None, f"no gold {metric} at {end}"
        assert abs(value - printed) <= tolerance(r), (
            f"gold {value} vs printed {printed} ({r['line_label']})")
    elif r["check"] == "annual_silver":
        assert kind(loaded, metric) == "duration"
        facts = loaded.execute(
            "SELECT f.value FROM silver.financial_facts f"
            " JOIN silver.company_ciks k ON k.cik = f.cik"
            " JOIN silver.metric_tags t ON t.metric = f.metric AND t.tag = f.tag"
            " WHERE k.company_id = %s AND f.metric = %s AND f.period_end = %s"
            "   AND f.period_type = 'annual' AND f.accession_no = %s"
            " ORDER BY t.priority LIMIT 1",
            (company_id, metric, end, r["accession_no"])).fetchone()
        assert facts is not None, f"no fiscal-year {metric} from {r['accession_no']}"
        assert abs(facts[0] - printed) <= tolerance(r)
    elif r["check"] == "absent":
        assert gold_value(loaded, company_id, metric, end) is None, (
            f"gold now has {metric} at {end}: compare it and make the row an annual check")
    else:
        pytest.fail(f"unknown check {r['check']!r}")


@pytest.mark.parametrize("r", FLOW_CHECKS, ids=row_id)
def test_fiscal_quarters_sum_to_the_printed_year(loaded, r):
    end = r["period_end"]
    quarters = loaded.execute(
        "SELECT period_start, period_end, value, value_source FROM gold.quarterly_financials"
        " WHERE company_id = %s AND metric = %s AND period_end <= %s"
        "   AND period_start >= %s::date - 375 ORDER BY period_end",
        (r["cik"], r["metric"], end, end)).fetchall()
    assert len(quarters) == 4, quarters
    assert str(quarters[-1][1]) == end
    for (_, prev_end, _, _), (start, _, _, _) in zip(quarters, quarters[1:], strict=False):
        assert timedelta(0) <= start - prev_end <= timedelta(days=7)
    total = sum(q[2] for q in quarters)
    q4 = quarters[-1]
    assert abs(total - Decimal(r["value"])) <= tolerance(r), (
        f"quarters {[q[2] for q in quarters]} (Q4 {q4[3]}) sum to {total}, printed "
        f"{r['value']}")


def figures(ticker: str) -> dict[tuple[str, int], tuple[Decimal, Decimal]]:
    """(metric, fiscal year) → (printed value, half its last digit), validated rows only."""
    return {(r["metric"], int(r["fiscal_year"])): (Decimal(r["value"]), tolerance(r))
            for r in ROWS if r["ticker"] == ticker and r["check"] == "annual"}


def ratio(a, b, positive=None):
    if a is None or b is None or (positive if positive is not None else b) <= 0:
        return None
    return a / b


def plus(*xs):
    return None if any(x is None for x in xs) else sum(xs)


# gold.refresh_derived_financials (014): coalesce() there is `or 0` here
FORMULAS = {
    "gross_margin": lambda v: ratio(v["gross_profit"], v["revenue"]),
    "operating_margin": lambda v: ratio(v["operating_income"], v["revenue"]),
    "net_margin": lambda v: ratio(v["net_income"], v["revenue"]),
    "free_cash_flow": lambda v: plus(v["operating_cash_flow"], neg(v["capex"])),
    "fcf_margin": lambda v: ratio(plus(v["operating_cash_flow"], neg(v["capex"])),
                                  v["revenue"]),
    "revenue_growth": lambda v: plus(ratio(v["revenue"], v["revenue_year_ago"]), -1),
    "net_income_growth": lambda v: plus(ratio(v["net_income"], v["net_income_year_ago"]), -1),
    "payout_ratio": lambda v: ratio(v["dividends_paid"], v["net_income"], v["net_income"]),
    "debt_to_equity": lambda v: ratio(plus(v["long_term_debt"],
                                           v["long_term_debt_current"] or 0),
                                      v["stockholders_equity"]),
    "net_debt": lambda v: plus(v["long_term_debt"], v["long_term_debt_current"] or 0,
                               neg(v["cash"]), -(v["short_term_investments"] or 0)),
    "current_ratio": lambda v: ratio(v["current_assets"], v["current_liabilities"]),
    "roe": lambda v: ratio(v["net_income"],
                           half(plus(v["stockholders_equity"],
                                     v["stockholders_equity_year_ago"]))),
}
INPUTS = ["revenue", "gross_profit", "operating_income", "net_income", "operating_cash_flow",
          "capex", "dividends_paid", "long_term_debt", "long_term_debt_current", "cash",
          "short_term_investments", "current_assets", "current_liabilities",
          "stockholders_equity"]
YEAR_AGO = ["revenue", "net_income", "stockholders_equity"]


def neg(x):
    return None if x is None else -x


def half(x):
    return None if x is None else x / 2


def expected(ticker: str, fiscal_year: int, metric: str) -> tuple[Decimal | None, Decimal]:
    """The formula over the printed inputs, and how far half a printed digit on each input
    can move it (plus gold's rounding of ratios to six decimals)."""
    figs = figures(ticker)
    names = {m: (m, fiscal_year) for m in INPUTS}
    names |= {f"{m}_year_ago": (m, fiscal_year - 1) for m in YEAR_AGO}
    values = {k: figs.get(key, (None, Decimal(0)))[0] for k, key in names.items()}
    value = FORMULAS[metric](values)
    if value is None:
        return None, Decimal(0)
    slack = Decimal("0.000001")
    for k, key in names.items():
        if values[k] is None:
            continue
        nudged = FORMULAS[metric](values | {k: values[k] + figs[key][1]})
        slack += abs((nudged if nudged is not None else value) - value)
    return value, slack


CURRENT = {r["ticker"]: (int(r["fiscal_year"]), r["cik"], r["period_end"])
           for r in ROWS if r["fiscal_year"] == max(x["fiscal_year"] for x in ROWS
                                                     if x["ticker"] == r["ticker"])}


@pytest.mark.parametrize("ticker,metric", [(t, m) for t in sorted(CURRENT) for m in FORMULAS])
def test_derived_metric_matches_its_formula_on_printed_inputs(loaded, ticker, metric):
    fiscal_year, company_id, end = CURRENT[ticker]
    want, slack = expected(ticker, fiscal_year, metric)
    row = loaded.execute(
        "SELECT value FROM gold.derived_financials WHERE company_id = %s AND span = 'ttm'"
        " AND metric = %s AND period_end = %s", (company_id, metric, end)).fetchone()
    if want is None:
        assert row is None, f"gold has {metric} = {row[0]} but a printed input is missing"
        return
    assert row is not None, f"no gold {metric} at {end}"
    assert abs(row[0] - want) <= slack, f"gold {row[0]} vs {want} (±{slack})"
