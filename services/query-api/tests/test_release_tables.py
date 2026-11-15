"""query_api.release_tables and events.passage_parts on real releases: the text fixtures
(fixtures/releases/*.txt) with their tables as the parser stores them (*.tables.json.gz:
silver.filing_tables rows, parser python-7 over the EX-99.1 HTML stored on hermes, 2026-11-15)."""

import gzip
import json
from pathlib import Path

import pytest

from query_api.events import passage_parts
from query_api.release_tables import grid, is_figure
from query_api.releases import read_release

RELEASES = Path(__file__).parent / "fixtures" / "releases"


def release(name: str) -> tuple[str, list[dict]]:
    text = (RELEASES / f"{name}.txt").read_text(encoding="utf-8")
    tables = json.loads(gzip.decompress((RELEASES / f"{name}.tables.json.gz").read_bytes()))
    return text, tables


def cells(row: list[dict], columns: int) -> list[str]:
    out = [""] * columns
    for c in row:
        out[c["col"]] = c["text"]
    return out


def table_parts(name: str, kind: str, heading: str, n: int = 0) -> list[dict]:
    text, tables = release(name)
    r = read_release(text)
    p = [p for p in r.outlook + r.adjustments if p.kind == kind and p.heading == heading][n]
    parts = passage_parts(text, p.start, p.end, tables)
    assert parts is not None
    return parts


@pytest.mark.parametrize("text,first,want", [
    ("1,234", False, True), ("(139.1)", False, True), ("$4.90", False, True),
    ("22.8%", False, True), ("$4,313M - $4,513M", False, True), ("49% - 51%", False, True),
    ("7% to 8%", False, True), ("—", False, True), ("$—", False, True), ("n/a", False, True),
    ("2026", False, False), ("Three months ended", False, False), ("$2.5 billion", False, True),
    ("$2.5 billion", True, False), ("401(k) plan", True, False), ("Up 65%", False, False),
])
def test_what_a_figure_is(text, first, want):
    assert is_figure(text, first) is want


def test_vertiv_guidance_table_is_a_label_and_a_value_column():
    parts = table_parts("vrt-2026-10-28-ex99-1", "outlook", "Fourth Quarter 2026 Guidance", 1)
    (table,) = [p["table"] for p in parts if "table" in p]
    assert table["columns"] == 2
    assert table["head"] == [[{"text": "Fourth Quarter 2026 Guidance", "col": 1, "span": 1}]]
    body = [cells(r, 2) for r in table["body"]]
    assert body[0] == ["Net sales", "$4,313M - $4,513M"]
    assert body[-1] == ["Adjusted diluted EPS growth(2)", "63% - 67%"]
    assert len(body) == 6


def test_vertiv_reconciliation_columns_with_dollar_signs_and_spacers():
    heading = ("Reconciliation of net cash provided by (used for) operating activities to adjusted"
               " free cash flow")
    parts = table_parts("vrt-2026-10-28-ex99-1", "reconciliation", heading)
    assert "table" in parts[-1]
    table = parts[-1]["table"]
    assert table["columns"] == 5
    assert [c["text"] for c in table["head"][0]] == [
        "Three months ended September 30, 2026", "Three months ended September 30, 2025",
        "Nine months ended September 30, 2026", "Nine months ended September 30, 2025"]
    assert [c["col"] for c in table["head"][0]] == [1, 2, 3, 4]
    body = [cells(r, 5) for r in table["body"]]
    assert body[0] == ["Net cash provided by (used for) operating activities", "$794.0", "$482.7",
                       "$2,660.6", "$1,108.9"]
    assert body[1] == ["Capital expenditures", "(139.1)", "(45.2)", "(425.0)", "(126.7)"]
    assert body[-1][0] == "Adjusted free cash flow"
    # the heading line above the table stays a line
    assert parts[0] == {"lines": [heading]}


def test_vertiv_eps_bridge_title_row_spans_the_table():
    parts = table_parts("vrt-2026-10-28-ex99-1", "reconciliation",
                        "Reconciliation of Diluted EPS to Adjusted Diluted EPS")
    tables = [p["table"] for p in parts if "table" in p]
    assert len(tables) == 4
    first = tables[0]
    assert first["head"][0] == [{"text": "Three months ended September 30, 2026", "col": 0,
                                 "span": first["columns"]}]
    assert cells(first["body"][0], first["columns"])[:3] == ["GAAP", "$983.8", "$6.4"]
    assert cells(first["body"][-1], first["columns"])[-1] == "392.7"


def test_coca_cola_reconciliation_keeps_columns_past_a_mid_table_heading():
    """A heading row inside the body ("Three Months Ended September 26, 2025" over every
    column) spans the columns instead of merging them into one."""
    text, tables = release("ko-2026-10-27-ex99-1")
    r = read_release(text)
    found = []
    for p in r.adjustments:
        for part in passage_parts(text, p.start, p.end, tables) or []:
            if "table" in part:
                found.append(part["table"])
    table = next(t for t in found if any(c["text"] == "Net operating revenues"
                                         for row in t["head"] for c in row))
    assert table["columns"] == 9
    rows: dict[str, list[str]] = {}  # the first block's (this quarter's) rows
    for r in table["body"]:
        rows.setdefault(cells(r, 9)[0], cells(r, 9))
    assert rows["Reported (GAAP)"][1:4] == ["$13,380", "$4,930", "$8,450"]
    assert rows["Other Items"][1:4] == ["(26)", "52", "(78)"]
    wide = [c for r in table["body"] for c in r if c["span"] > 1]
    assert any(c["text"].startswith("Three Months Ended September") for c in wide)


def test_coca_cola_guidance_rows_in_words_are_body_rows():
    text, tables = release("ko-2026-10-27-ex99-1")
    r = read_release(text)
    p = next(p for p in r.outlook if p.heading == "Full Year 2026 Guidance")
    parts = passage_parts(text, p.start, p.end, tables)
    (table,) = [x["table"] for x in parts or [] if "table" in x]
    assert [[c["text"] for c in row] for row in table["head"]] == [["Current", "Prior"]]
    assert cells(table["body"][0], 3) == ["Organic revenues (non-GAAP)", "At least 5% growth",
                                          "Approx. 5% growth"]


def test_nvidia_head_rows_title_units_periods_and_years():
    text, tables = release("nvda-2026-08-26-ex99-1")
    r = read_release(text)
    p = next(p for p in r.adjustments if p.kind == "reconciliation")
    first = next(x["table"] for x in passage_parts(text, p.start, p.end, tables) or []
                 if "table" in x)
    heads = [[c["text"] for c in row] for row in first["head"]]
    assert heads[-2:] == [["July 26,", "April 26,", "July 27,", "July 26,", "July 27,"],
                          ["2026", "2026", "2025", "2026", "2025"]]
    assert cells(first["body"][0], 6) == ["GAAP cost of revenue", "$24,079", "$20,458",
                                          "$12,890", "$44,538", "$30,284"]


def test_no_parts_without_a_table_and_no_grid_without_figures():
    text, tables = release("vrt-2026-10-28-ex99-1")
    r = read_release(text)
    bullets = r.outlook[0]  # the summary's bullets: no table
    assert passage_parts(text, bullets.start, bullets.end, tables) is None
    assert passage_parts(text, bullets.start, bullets.end, []) is None
    layout = [{"start": 0, "end": 5, "cells": ["•", "Text"]},
              {"start": 6, "end": 11, "cells": ["•", "More"]}]
    assert grid(layout, 0, 11) is None


def test_fragments_join_their_figure():
    rows = [{"start": 0, "end": 9, "cells": ["", "2026", "", "2025"], "head": True},
            {"start": 10, "end": 30, "cells": ["Margin", "45.1", "%", "", "(", "3.2", ")"]},
            {"start": 31, "end": 50, "cells": ["Sales", "$", "1,234", "", "$", "1,100", ""]}]
    g = grid(rows, 0, 50)
    assert g is not None and g["columns"] == 3
    assert [cells(r, 3) for r in g["body"]] == [["Margin", "45.1%", "(3.2)"],
                                                ["Sales", "$1,234", "$1,100"]]
    assert (g["char_start"], g["char_end"]) == (0, 50)
    # rows outside the passage are left out
    assert grid(rows, 10, 50)["head"] == []
