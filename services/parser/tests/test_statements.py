"""statements.py (migration 074): headline lines from a filing's own statement tables. Fixtures are
real tables as parser v7 stores them (fixtures/statements.json: Apple's FY2026 income statement,
Zions' balance sheet with a rowspan head, Ford's oldest-first columns, Occidental's "millions"
head cell, Deere's consolidating columns and a 10-Q with three and nine months)."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from filing_parser import statements as st

FIXTURES = {x["name"]: x for x in json.loads(
    (Path(__file__).parent / "fixtures" / "statements.json").read_text())}


def read(name: str) -> list[st.Line]:
    x = FIXTURES[name]
    return st.read([(x["key"], x["n"], x["rows"], x["before"])], x["form"],
                   date.fromisoformat(x["period"]))


def values(lines: list[st.Line], line: str) -> set[Decimal]:
    return {x.value for x in lines if x.line == line}


def test_the_current_years_lines_with_the_tables_scale():
    lines = read("AAPL")
    assert values(lines, "revenue") == {Decimal("478834000000")}  # Total net sales, 2026
    assert values(lines, "net_income") == {Decimal("132175000000")}
    assert all(x.scale == 1_000_000 and x.titled and x.months == 12 for x in lines)


def test_a_narrow_head_row_sits_right_of_a_rowspan_label():
    # "(In millions, shares in thousands)" spans two head rows; "2025 2024" sits right of it
    assert values(read("ZION"), "total_assets") == {Decimal("88990000000")}


def test_columns_oldest_first():
    assert values(read("F"), "revenue") == {Decimal("187267000000")}  # 2023, 2024, 2025


def test_a_millions_head_cell_is_the_scale():
    assert values(read("OXY"), "total_assets") == {Decimal("84186000000")}


def test_a_head_naming_every_year_dates_no_column():
    # Deere's "For the Years Ended November 2, 2025, October 27, 2024, and October 29, 2023" spans
    # every column: the years row alone dates them, so only fiscal 2025's columns are current
    lines = read("DE")
    assert Decimal("44759") not in values(lines, "revenue")  # 2024
    assert Decimal("5027") in values(lines, "net_income")    # consolidated, attributable


def test_a_10q_reads_the_quarter_not_the_year_to_date():
    lines = read("10-Q")
    assert {x.months for x in lines if x.line in st.INCOME} == {3}


def test_parent_company_statements_are_not_read():
    x = FIXTURES["AAPL"]
    lines, _ = st.read_table(x["rows"], "SCHEDULE I\nCONDENSED FINANCIAL INFORMATION OF REGISTRANT"
                             "\nCondensed Statements of Operations\n", "10-K",
                             date.fromisoformat(x["period"]))
    assert lines == []
    # a 10-Q's "condensed" statements are the group's
    lines, _ = st.read_table(x["rows"],
                             "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS\n(In millions)\n",
                             "10-K", date.fromisoformat(x["period"]))
    assert values(lines, "revenue") == {Decimal("478834000000")}


def test_a_continued_table_takes_the_scale_of_the_one_before():
    x = FIXTURES["OXY"]
    rows = [dict(r, cells=["" if c == "millions" else c for c in r["cells"]]) for r in x["rows"]]
    assert st.read_table(rows, "", "10-K", date(2025, 12, 31))[0][0].scale is None
    lines = st.read([("8", 0, x["rows"], x["before"]), ("8", 1, rows, "")], "10-K",
                    date(2025, 12, 31))
    assert {x.scale for x in lines if x.table_no == 1} == {1_000_000}


@pytest.mark.parametrize("cell,value", [
    ("1,234", Decimal("1234")), ("(1,234)", Decimal("-1234")), ("(56", Decimal("-56")),
    ("$ 9.01", Decimal("9.01")), ("—", Decimal(0)), ("$—", Decimal(0)), ("12.5%", None),
    ("Net sales", None), ("$", None), (")", None)])
def test_numbers(cell, value):
    assert st.number(cell) == value


@pytest.mark.parametrize("label,line", [
    ("Total net sales", "revenue"), ("Revenues", "revenue"), ("Total revenues (Note 4)", "revenue"),
    ("Total revenue, net of interest expense", "revenue"),
    ("Sales and other operating revenues", "revenue"),
    ("Net income attributable to Apple", "net_income"), ("Net loss", "net_income"),
    ("Net income (loss)(1)", "net_income"), ("Net loss and comprehensive loss", "net_income"),
    ("Net income attributable to noncontrolling interests", None), ("Cost of revenue", None),
    ("TOTAL ASSETS", "total_assets"), ("Total assets*", "total_assets"),
    ("Total liabilities and stockholders’ equity (deficit)", "liabilities_and_equity"),
    ("Total liabilities, redeemable noncontrolling interests and equity",
     "liabilities_and_equity"),
    ("Total liabilities", None)])
def test_labels(label, line):
    key = st.label_key(label)
    assert next((n for n, p in st._PATTERNS.items() if p.match(key)), None) == line


@pytest.mark.parametrize("head,before,scale", [
    (["(In millions, except number of shares, which are reflected in thousands)"], "", 10**6),
    ([], "CONSOLIDATED BALANCE SHEETS\n(in thousands, except par value)\n", 1000),
    (["Millions, except share and per share amounts"], "", 10**6),
    (["(Amounts in 000’s of US$ except for share data)"], "", 1000),
    (["(dollars in millions, except per share amounts)"], "", 10**6),
    ([], "Results of Operations\n", None)])
def test_scale(head, before, scale):
    assert st.scale_of(head, before) == scale
