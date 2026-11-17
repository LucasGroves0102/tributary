"""Fidelity's "Portfolio Positions" CSV (query_api/portfolio_import.py). The fixture is SYNTHETIC:
written from Fidelity's documented export (header names, $ and comma numbers, "--" blanks, the
core money-market rows, "Pending Activity", an option, two accounts, the disclaimer footer and
"Date downloaded"), with made-up account numbers and figures; no real user's file exists here."""

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from query_api.portfolio_import import (
    MAX_BYTES,
    NotAnExport,
    clean_label,
    downloaded_on,
    label_ok,
    number,
    parse,
    ticker_variants,
)

FIXTURE = Path(__file__).parent / "fixtures" / "portfolio" / "fidelity_positions_synthetic.csv"
ACCOUNT_NUMBERS = ("Z09876543", "Y55511122", "X31415926", "31415926")


def text():
    return FIXTURE.read_bytes().decode("utf-8-sig")


def test_the_synthetic_export_reads_its_positions_in_order():
    p = parse(text())
    got = [(x.symbol, x.shares, x.cost_total, x.last_price, x.account_label)
           for x in p.positions]
    assert got == [
        ("NVDA", D("1.25"), D("200.00"), D("186.26"), "Individual"),
        ("AAPL", D("0.75"), D("150.00"), D("228.52"), "Individual"),
        ("BRK.B", D("0.1"), D("1000.00"), D("470.10"), "Individual"),
        ("VOO", D("0.05"), D("25.00"), D("560.00"), "Individual"),
        ("FXAIX", D("0.312"), D("60.00"), D("210.33"), "ROTH IRA"),
        ("NVDA", D("0.5"), D("85.00"), D("186.26"), "ROTH IRA"),
        ("MSFT", D("0.2"), None, D("505.00"), "ROTH IRA"),   # "--": the preview asks for it
        ("TSLA", D("0.1"), D("40.00"), D("350.00"), None),   # its account name held a number
    ]
    assert p.positions[0].description == "NVIDIA CORPORATION COM"
    assert p.downloaded == date(2026, 11, 16)


def test_cash_pending_activity_and_options_are_skipped_with_a_reason():
    assert parse(text()).skipped == [
        {"symbol": "SPAXX", "description": "HELD IN MONEY MARKET", "reason": "cash"},
        {"symbol": None, "description": "Pending activity", "reason": "pending"},
        {"symbol": None, "description": "NVDA DEC 18 2026 $200 CALL", "reason": "option"},
        {"symbol": "FDRXX", "description": "FIDELITY GOVERNMENT CASH RESERVES",
         "reason": "cash"},
    ]


def test_account_numbers_are_dropped_on_parse():
    parsed = parse(text())
    every = repr(parsed.positions) + repr(parsed.skipped)
    for n in ACCOUNT_NUMBERS:
        assert n not in every


def test_columns_are_found_by_name_in_any_order_quoted_with_aliases():
    csv_text = ('﻿"Symbol","Quantity","Account Name","Cost Basis","Description",'
                '"Last Price","Extra"\r\n'
                '"AAPL","2","Brokerage","$300.00","APPLE INC","$200.00","x"\r\n'
                '"GOOG","1","Brokerage","--","ALPHABET","n/a",""\r\n')
    p = parse(csv_text)
    assert [(x.symbol, x.shares, x.cost_total, x.last_price) for x in p.positions] == [
        ("AAPL", D(2), D("300.00"), D("200.00")), ("GOOG", D(1), None, None)]
    assert p.downloaded is None


def test_average_cost_times_quantity_when_there_is_no_total():
    p = parse("Symbol,Quantity,Average Cost Basis\nKO,4,$60.50\n")
    assert p.positions[0].cost_total == D("242.00")


def test_a_row_without_a_positive_quantity_or_a_readable_symbol_is_skipped():
    p = parse("Symbol,Quantity\nKO,0\nPEP,-1\nB@D,1\nXOM,3\n")
    assert [x.symbol for x in p.positions] == ["XOM"]
    assert [s["reason"] for s in p.skipped] == ["no_quantity", "no_quantity", "unreadable"]


@pytest.mark.parametrize("bad", [
    "",
    "Account Number,Account Name,Description\nZ09876543,Individual,Cash\n",
    "hello world",
])
def test_not_an_export_says_why_without_quoting_the_file(bad):
    with pytest.raises(NotAnExport) as e:
        parse(bad)
    assert "Z09876543" not in str(e.value) and "hello" not in str(e.value)


def test_a_header_with_nothing_under_it_is_not_an_export():
    with pytest.raises(NotAnExport, match="no positions"):
        parse("Symbol,Quantity\n\n")


def test_size_and_row_caps():
    with pytest.raises(NotAnExport, match="KB"):
        parse("Symbol,Quantity\n" + "A,1\n" * (MAX_BYTES // 4))
    with pytest.raises(NotAnExport, match="more than 500"):
        parse("Symbol,Quantity\n" + "".join(f"A{i},1\n" for i in range(501)))


@pytest.mark.parametrize("raw, value", [
    ("$1,234.56", D("1234.56")), ("+$1.23", D("1.23")), ("-$5.00", D("-5.00")),
    ("($5.00)", D("-5.00")), ("12.5%", D("12.5")), ("--", None), ("n/a", None), ("", None),
    (None, None), ("abc", None), ("NaN", None),
])
def test_numbers(raw, value):
    assert number(raw) == value


@pytest.mark.parametrize("label, ok", [
    ("Individual", True), ("ROTH IRA 2024", True), ("Joint 401k", True),
    ("X12345678", False), ("Acct 123-456789", False), ("1234 5678", False), ("12 34 56", False),
])
def test_an_account_label_never_holds_an_account_number(label, ok):
    assert label_ok(label) is ok
    assert (clean_label(label) is not None) is ok


def test_labels_are_cleaned():
    assert clean_label("  Brokerage\taccount  ") == "Brokerage account"
    assert clean_label("") is None and clean_label(None) is None
    assert clean_label("x" * 80) == "x" * 60


def test_dates_downloaded():
    assert downloaded_on("Date downloaded Nov-16-2026 10:15 a.m ET") == date(2026, 11, 16)
    assert downloaded_on("Date downloaded 11/16/2026 10:15 AM ET") == date(2026, 11, 16)
    assert downloaded_on("Date downloaded Feb-30-2026") is None
    assert downloaded_on("Date downloaded soon") is None


def test_share_classes_try_the_secs_dash():
    assert ticker_variants("BRK.B") == ["BRK.B", "BRK-B", "BRKB"]
    assert ticker_variants("NVDA") == ["NVDA"]
