"""The release reader (filing_parser/releases.py, migration 165) on real 6-K tables: stored rows
exported from silver.filing_tables and trimmed to the tables that matter (fixtures/releases). Where
the 6-K carries XBRL (the fixture's `xbrl`), every figure read equals the filing's own fact."""

import gzip
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from filing_parser import releases

FIXTURES = Path(__file__).parent / "fixtures" / "releases"


def load(name: str) -> dict:
    return json.loads(gzip.decompress((FIXTURES / f"{name}.json.gz").read_bytes()))


def read(name: str) -> tuple[releases.Read, dict]:
    fx = load(name)
    tables = [(t["section_key"], t["table_no"], t["rows"], t["before"]) for t in fx["tables"]]
    return releases.read(tables, date.fromisoformat(fx["period"]),
                         releases.fye_month(fx["fiscal_year_end"])), fx


def figures(r: releases.Read) -> dict[tuple, Decimal]:
    return {(f.metric, f.period_end.isoformat(), f.months): f.value for f in r.figures}


@pytest.mark.parametrize("name", sorted(p.name[:-8] for p in FIXTURES.glob("*.json.gz")))
def test_every_figure_equals_the_filings_own_xbrl_where_it_has_one(name):
    r, fx = read(name)
    for f in r.figures:
        facts = [x for x in fx["xbrl"] if x["m"] == f.metric and x["e"] == f.period_end.isoformat()
                 and (x["s"] is None) == (f.months is None)]
        if facts:
            assert any(abs(f.value - Decimal(str(x["v"]))) <= max(f.precision,
                                                                  Decimal("0.005") * abs(f.value))
                       for x in facts), (f, facts)


def test_tsmc_press_release_quarter_labels_and_a_unit_line():
    """"3Q26 Amounta" columns, "(Unit: NT$ million, except for EPS)" above the table."""
    r, _ = read("tsmc-3q26-release")
    assert figures(r) == {
        ("net_income", "2026-09-30", 3): Decimal("763064000000"),
        ("operating_income", "2026-09-30", 3): Decimal("876445000000"),
        ("revenue", "2026-09-30", 3): Decimal("1494243000000"),
    }
    assert {f.currency for f in r.figures} == {"TWD"}
    rev = next(f for f in r.figures if f.metric == "revenue")
    assert (rev.section_key, rev.table_no, rev.row_no, rev.col_no, rev.label) == (
        "EX-99.1", 0, 1, 1, "Net sales")


def test_tsmc_statements_continued_after_a_page_break_read_the_owners_net_income():
    """The statement of comprehensive income breaks after "(Continued)": its owners' net income
    (the next table, under "NET INCOME ATTRIBUTABLE TO:") is read beside its revenue, not the
    total with minority interests (763,340,827)."""
    r, _ = read("tsmc-3q26-statements")
    got = figures(r)
    assert got[("revenue", "2026-09-30", 3)] == Decimal("1494243284000")
    assert got[("revenue", "2026-09-30", 9)] == Decimal("3898726974000")
    assert got[("net_income", "2026-09-30", 3)] == Decimal("763064422000")
    assert got[("operating_income", "2026-09-30", 3)] == Decimal("876445164000")
    ni = next(f for f in r.figures if f.metric == "net_income" and f.months == 3)
    assert ni.attributable and ni.statement == 3 and ni.table_no == 5


def test_td_highlights_dated_columns_and_a_billions_heading_inside_the_table():
    r, _ = read("td-q2-2022")
    got = figures(r)
    assert got[("revenue", "2022-04-30", 3)] == Decimal("11263000000")  # "Total revenue – reported"
    assert got[("revenue", "2022-04-30", 6)] == Decimal("22544000000")
    assert got[("net_income", "2022-04-30", 3)] == Decimal("3811000000")
    # "Financial position (billions of Canadian dollars)" scales the rows below it
    assert got[("total_assets", "2022-04-30", None)] == Decimal("1825300000000.0")
    assert {f.currency for f in r.figures} == {"CAD"}


def test_icecure_a_net_loss_printed_without_parentheses_is_a_loss():
    r, _ = read("icecure-h1-2024")
    got = figures(r)
    assert got[("net_income", "2024-06-30", 6)] == Decimal("-6690000")
    assert got[("operating_income", "2024-06-30", 6)] == Decimal("-6878000")
    assert got[("revenue", "2024-06-30", 6)] == Decimal("1754000")


def test_astrazeneca_figures_under_the_value_column_not_the_change_columns():
    """Its head puts "H1 2021" over the "CER % change" column; the guarantors' summary (titled,
    revenue "-") is a part of the company and never read."""
    r, _ = read("astrazeneca-h1-2021")
    got = figures(r)
    assert got[("revenue", "2021-06-30", 6)] == Decimal("15540000000")
    assert got[("revenue", "2021-06-30", 3)] == Decimal("8220000000")
    assert ("net_income", "2021-06-30", 6) not in got  # the obligor group's "Loss for the period"


def test_barclays_a_units_row_switches_the_scale():
    r, _ = read("barclays-q2-2025")
    assert figures(r) == {("total_assets", "2025-06-30", None): Decimal("1598700000000.0")}


def test_bp_owners_under_comprehensive_income_are_not_net_income():
    """"Attributable to: bp shareholders" follows total comprehensive income (2,883), not net
    income; the profit lines here have no revenue beside them in these tables."""
    r, _ = read("bp-q2-2025")
    assert all(f.value != Decimal("2883000000") for f in r.figures)
    assert r.left.get("unanchored")


def test_santander_parent_only_and_movement_tables_are_not_read():
    r, _ = read("santander-h1-2018")
    # the consolidated 1,433,833 million, not the parent's 510,823 nor foreclosed assets' "Sales"
    assert figures(r) == {("total_assets", "2018-06-30", None): Decimal("1433833000000")}


def test_aercap_a_guarantors_table_is_not_read():
    r, _ = read("aercap-guarantors-h1-2024")
    assert r.figures == []


def test_descartes_a_share_count_heading_does_not_scale_money():
    r, _ = read("descartes-fy2025")
    assert figures(r)[("total_assets", "2025-01-31", None)] == Decimal("1647400000.0")


def test_a_6k_dated_by_its_filing_reads_the_quarter_it_reports():
    r, fx = read("filed-dated-q4-2025")
    assert fx["period"] == "2026-03-03" and r.period == date(2025, 12, 31)
    got = figures(r)
    assert got[("revenue", "2025-12-31", 3)] == Decimal("38155000")
    assert got[("revenue", "2025-12-31", 12)] == Decimal("141057000")


def test_numeric_dates_and_a_notes_column():
    r, _ = read("numeric-dates-q3-2026")
    got = figures(r)
    assert r.period == date(2026, 9, 30)
    assert got[("total_assets", "2026-09-30", None)] == Decimal("455570000")


# rules on their own ---------------------------------------------------------------------------

def table(head: list[list[str]], rows: list[list[str]]) -> list[dict]:
    return [{"cells": c} for c in head] + [{"cells": c} for c in rows]


def test_fiscal_and_calendar_readings_of_a_quarter_label():
    assert releases._label_ends("q3 fy26", 3)[0][0] == date(2025, 12, 31)  # March year
    assert date(2025, 9, 30) in releases._label_ends("q3 2025", 3)[0]      # its calendar Q3
    assert releases._label_ends("3q26", 12) == ([date(2026, 9, 30)], 3)
    assert releases._label_ends("1h26", 12) == ([date(2026, 6, 30)], 6)
    assert releases._label_ends("9m 2026", 12)[0][0] == date(2026, 9, 30)
    assert releases._label_ends("q1-q3 2026", 12) == ([], None)  # two labels: not read


def test_numeric_dates_read_one_way_only():
    assert releases._all_dates("9/30/2026") == [date(2026, 9, 30)]
    assert releases._all_dates("30.09.2026") == [date(2026, 9, 30)]
    assert releases._all_dates("6/7/2026") == []
    assert releases._all_dates("sep 26") == [date(2026, 9, 30)]


def test_period_start_and_fiscal_year_end():
    assert releases.period_start(date(2026, 3, 31), 3) == date(2026, 1, 1)
    assert releases.period_start(date(2026, 9, 30), 9) == date(2026, 1, 1)
    assert releases.period_start(date(2026, 12, 31), None) is None
    assert releases.fye_month("0331") == 3
    assert releases.fye_month("0102") == 12  # a 52-53 week year ending in January's first week
    assert releases.fye_month(None) == 12


@pytest.mark.parametrize("text, scale, currency", [
    ("(Unit: NT$ million, except for EPS)", 1_000_000, "TWD"),
    ("(In Thousands of New Taiwan Dollars, Except Earnings Per Share)", 1_000, "TWD"),
    ("(millions of Canadian dollars, except as noted)", 1_000_000, "CAD"),
    ("U.S. dollars in thousands", 1_000, "USD"),
    ("£m", 1_000_000, "GBP"),
    ("RMB'000", 1_000, "CNY"),
    ("Revenue – R million", 1_000_000, "ZAR"),
    ("(in $ millions)", 1_000_000, "$"),
    ("Shares (thousands)", None, None),
    ("in thousands, except share and per share data", 1_000, None),
])
def test_scale_and_currency(text, scale, currency):
    assert releases.scale_and_currency([text]) == (scale, currency)


@pytest.mark.parametrize("label, metric", [
    ("Net sales", "revenue"),
    ("Total revenue – reported", "revenue"),
    ("Total revenue – adjusted1", None),
    ("Revenue ($ millions)", "revenue"),
    ("Net income attributable to equity holders of the Bank", "net_income"),
    ("Net income attributable to non-controlling interests", None),
    ("Net income available to common shareholders", None),
    ("Profit for the period", "net_income"),
    ("Income", None),
    ("Loss from operations", "operating_income"),
    ("Total operating loss", "operating_income"),
    ("Underlying operating profit", None),
    ("Total assets", "total_assets"),
])
def test_labels(label, metric):
    assert releases.line_of(releases.key_of(label), "", None)[0] == metric


def test_a_loss_line_printed_both_ways_is_left_out():
    rows = table([["", "Three months ended March 31, 2026", "Three months ended March 31, 2025"],
                  ["(in thousands of U.S. dollars)", "", ""]],
                 [["Revenue", "1,000", "900"], ["Net loss", "(50)", "40"]])
    r = releases.Read()
    got = releases.read_table(rows, "", date(2026, 3, 31), 12, read=r)
    assert [(f.metric, f.value) for f in got] == [("revenue", Decimal("1000000"))]
    assert r.left == {"sign": 1}


def test_a_table_without_a_scale_is_read_as_printed_and_flagged():
    rows = table([["", "Three months ended March 31, 2026"]], [["Revenue", "1,234,567"]])
    got = releases.read_table(rows, "", date(2026, 3, 31), 12)
    assert [(f.value, f.scale, f.scale_named) for f in got] == [(Decimal("1234567"), 1, False)]


def test_disagreeing_candidates_are_left_out():
    one = table([["", "Three months ended March 31, 2026"], ["(in millions)", ""]],
                [["Revenue", "1,000"]])
    two = table([["", "Three months ended March 31, 2026"], ["(in millions)", ""]],
                [["Revenue", "800"]])
    r = releases.read([("EX-99.1", 0, one, ""), ("EX-99.1", 1, two, "")], date(2026, 3, 31))
    assert r.figures == [] and r.left == {"ambiguous": 2}


def test_one_figure_in_two_scales_reads_the_more_precise():
    one = table([["", "Three months ended March 31, 2026"], ["(in millions)", ""]],
                [["Revenue", "1,235"]])
    two = table([["", "Three months ended March 31, 2026"], ["(in thousands)", ""]],
                [["Revenue", "1,234,567"]])
    r = releases.read([("EX-99.1", 0, one, ""), ("EX-99.2", 0, two, "")], date(2026, 3, 31))
    assert [f.value for f in r.figures] == [Decimal("1234567000")]
