"""The dataset CSV reader (query_api.datasets_csv): what a file may hold, every refusal by row and
column number only (never a cell's text), and bounded work on the largest file allowed."""

import codecs
import time
from datetime import date

import pytest

from query_api import datasets_csv as c

TODAY = date(2026, 11, 16)
SECRETISH = "sk_live_DO_NOT_ECHO"


def scan(data: bytes, **options) -> c.Scan:
    return c.scan(data, c.Options(**options), TODAY)


def refused(data: bytes, **options) -> dict:
    with pytest.raises(c.Refused) as e:
        scan(data, **options)
    return e.value.to_dict()


def values(s: c.Scan) -> list[list[str | None]]:
    return [v for _, v in c.values(s)]


def test_a_plain_file():
    s = scan(b"Ticker,Date,Stores (EoP),Sales\nAAPL,2026-06-30,10,1000.5\nMSFT,2026-06-30,,7\n")
    assert s.header == ["ticker", "date", "stores_eop", "sales"]
    assert [n for n, _ in s.fields] == ["stores_eop", "sales"]
    assert s.units == ["number", "number"]
    assert s.keys == [(2, "AAPL", date(2026, 6, 30), None), (3, "MSFT", date(2026, 6, 30), None)]
    assert values(s) == [["10", "1000.5"], [None, "7"]]


@pytest.mark.parametrize("raw", [
    codecs.BOM_UTF8 + b"ticker,date,x\nAAPL,2026-06-30,1\n",
    "ticker,date,x\nAAPL,2026-06-30,1\n".encode("utf-16"),
    "ticker,date,Umsatz €\nAAPL,2026-06-30,1\n".encode("cp1252"),
    b"ticker,date,x\r\nAAPL,2026-06-30,1\r\n",
])
def test_encodings_and_line_ends(raw):
    s = scan(raw)
    assert s.header[0] == "ticker" and s.keys[0][1] == "AAPL" and values(s) == [["1"]]


def test_a_semicolon_file_reads_decimal_commas():
    s = scan("ticker;date;sales;margin\nAAPL;2026-06-30;1.234,5;0,25\nMSFT;2026-06-30;"
             "1 234,5;(3)\n".encode())
    assert s.delimiter == ";" and s.decimal == ","
    assert values(s) == [["1234.5", "0.25"], ["1234.5", "-3"]]
    # unless the upload says otherwise
    s = scan(b"ticker;date;x\nAAPL;2026-06-30;1,234.5\n", decimal=".")
    assert values(s) == [["1234.5"]]


def test_tabs_and_quotes():
    s = scan(b'ticker\tdate\t"a\tb"\nAAPL\t2026-06-30\t"1,000"\n')
    assert s.delimiter == "\t" and s.header[2] == "a_b"
    s = scan(b'"ticker","date","say ""hi""\nthere"\n"AAPL","2026-06-30","1,000.25"\n')
    assert s.header == ["ticker", "date", "say_hi_there"] and values(s) == [["1000.25"]]


@pytest.mark.parametrize("cell, want", [
    ("1", "1"), ("-1.5", "-1.5"), ("+2", "2"), (".5", "0.5"), ("1e6", "1e6"),
    ("1,234,567", "1234567"), ("(12.5)", "-12.5"), (" 7 ", "7"), ("NA", None), ("n/a", None),
    ("-", None), ("", None), ("NaN", None), ("#N/A", None),
])
def test_numbers(cell, want):
    assert c.number(cell, ".") == want


@pytest.mark.parametrize("cell", [
    "=cmd|' /C calc'!A0", "=1+1", "+cmd", "@SUM(A1)", "-2+3", "12%", "$5", "1,23", "1.2.3",
    "inf", "1e9999", "0x10", "1_000", "(-1)", "--1", "１２", "1 000",
])
def test_not_numbers(cell):
    with pytest.raises(ValueError):
        c.number(cell, ".")


def test_formula_injection_is_refused_and_never_echoed():
    cell = f'"=HYPERLINK(""http://evil"",""{SECRETISH}"")"'
    body = refused(f"ticker,date,x\nAAPL,2026-06-30,{cell}\n".encode())
    assert body["errors"] == [{"code": "number", "message": "Not a number", "row": 2,
                               "column": 3}]
    assert "evil" not in str(body) and SECRETISH not in str(body)
    # unquoted, its quotes are malformed CSV: refused at that row, still never echoed
    body = refused(f'ticker,date,x\nAAPL,2026-06-30,=HYPERLINK("http://evil","{SECRETISH}")\n'
                   .encode())
    assert [(e["code"], e["row"]) for e in body["errors"]] == [("quoting", 2)]
    assert "evil" not in str(body) and SECRETISH not in str(body)


def test_every_problem_by_row_and_column_and_none_quotes_a_cell():
    data = (f"ticker,date,x,y\nAAPL,{SECRETISH},1,2\n,2026-06-30,1,2\n"
            f"AAPL,2026-06-30,{SECRETISH},2\nAAPL,2026-06-30,1,2,{SECRETISH}\n"
            f"{'A' * 50},2026-06-30,1,2\n").encode()
    body = refused(data)
    assert [(e["code"], e["row"], e["column"]) for e in body["errors"]] == [
        ("date", 2, 2), ("no_company", 3, 1), ("number", 4, 3), ("extra_cells", 5, 5),
        ("company", 6, 1)]
    assert SECRETISH not in str(body) and "AAAA" not in str(body)


def test_problems_are_listed_up_to_twenty_and_all_counted():
    data = b"ticker,date,x\n" + b"AAPL,2026-06-30,x\n" * 50
    body = refused(data)
    assert len(body["errors"]) == c.MAX_PROBLEMS and body["count"] == 50


@pytest.mark.parametrize("data, code", [
    (b"", "empty"),
    (b"\n\n", "empty"),
    (b"name,when,x\nAAPL,2026-06-30,1\n", "no_company_column"),
    (b"ticker,when,x\nAAPL,2026-06-30,1\n", "no_date_column"),
    (b"ticker,date\nAAPL,2026-06-30\n", "no_fields"),
    (b"ticker,date,x,X\nAAPL,2026-06-30,1,2\n", "same_name"),
    (b"ticker,date,%%%\nAAPL,2026-06-30,1\n", "unnamed_column"),
    (b"ticker,date,x\nAAPL,2026-06-30,\n", "no_rows"),
    (b'ticker,date,x\nAAPL,2026-06-30,"1\n', "quoting"),
    (b'ticker,date,x\nAAPL,2026-06-30,1"2\n', "number"),
    (b"ticker,date,x\nAAPL,30/06/2026,1\n", "date"),
    (b"ticker,date,x\nAAPL,2026-02-30,1\n", "date"),
    (b"ticker,date,x\nAAPL,1899-12-31,1\n", "date"),
])
def test_refusals(data, code):
    assert refused(data)["errors"][0]["code"] == code


def test_declared_columns_and_publication_dates():
    data = b"Code,Period end,Published,EPS\nAAPL,2026-06-30,2026-07-30,1.5\n"
    assert refused(data)["errors"][0]["code"] == "no_company_column"
    # never used undeclared: a date isn't a number, and the refusal says which column it may be
    undeclared = refused(data, company_column="Code", date_column="period end")
    assert undeclared["errors"][0]["column"] == 3
    assert undeclared["hint"].startswith("Column 3 looks like a publication date")
    s = scan(b"Code,Period end,Published,EPS\nAAPL,2026-06-30,,1.5\n",
             company_column="Code", date_column="period end")
    assert s.published is None and s.suggested["published_column"] == "Published"
    s = scan(data, company_column="code", date_column="Period end",
             published_column="published")
    assert s.keys == [(2, "AAPL", date(2026, 6, 30), date(2026, 7, 30))]
    assert [n for n, _ in s.fields] == ["eps"]
    later = refused(b"ticker,date,published,x\nAAPL,2026-06-30,2026-11-17,1\n",
                    published_column="published")
    assert later["errors"][0]["code"] == "published_later"
    missing = refused(data, company_column="Ticker")
    assert missing["errors"][0]["code"] == "no_company_column"
    same = refused(data, company_column="Code", date_column="Code")
    assert same["errors"][0]["code"] == "same_column"


def test_units():
    assert c.parse_units("Stores:number, sales :money") == {"stores": "number",
                                                            "sales": "money"}
    for bad in ("stores", "stores:dollars", ":money", "x" * 5000):
        with pytest.raises(ValueError):
            c.parse_units(bad)
    s = scan(b"ticker,date,stores,sales\nAAPL,2026-06-30,1,2\n", units={"sales": "money"})
    assert s.units == ["number", "money"]
    assert refused(b"ticker,date,x\nAAPL,2026-06-30,1\n",
                   units={"y": "money"})["errors"][0]["code"] == "units"


def test_blank_rows_and_rows_without_values():
    s = scan(b"ticker,date,x\n\nAAPL,2026-06-30,1\n,,\nMSFT,2026-06-30,\n")
    assert [k[0] for k in s.keys] == [3] and s.blank_rows == 1


def test_too_many_columns_and_rows():
    wide = ",".join(["ticker", "date"] + [f"f{i}" for i in range(c.MAX_COLUMNS - 1)])
    assert refused(wide.encode() + b"\n")["errors"][0]["code"] == "too_many_columns"
    tall = b"ticker,date,x\n" + b"AAPL,2026-06-30,1\n" * (c.MAX_ROWS + 1)
    assert refused(tall)["errors"][0]["code"] == "too_many_rows"
    assert refused(b"x" * (c.MAX_BYTES + 1))["errors"][0]["code"] == "too_large"


def test_the_largest_file_allowed_reads_in_bounded_time():
    columns = c.MAX_COLUMNS - 2
    header = ",".join(["ticker", "date"] + [f"f{i}" for i in range(columns)]) + "\n"
    row = "AAPL,2026-06-30," + ",".join(["12345.678"] * columns) + "\n"
    rows = min(c.MAX_ROWS, (c.MAX_BYTES - len(header)) // len(row))
    data = (header + row * rows).encode()
    assert len(data) <= c.MAX_BYTES
    started = time.monotonic()
    s = scan(data)
    count = sum(1 for _ in c.values(s))
    assert count == rows == len(s.keys)
    assert time.monotonic() - started < 60
