"""An earnings release's tables as display grids (parser v7, rule 11: silver.filing_tables,
migration 067). Pure; no figure is read or changed: every cell is the filing's own text.

The parser records each HTML table as rows of cells with colspans, the release's own layout: a
"$" in a cell of its own before the figure, a ")" or "%" after it, spacer cells between columns, a
heading spanning the columns it heads. A grid puts that back into columns:

1. Rows. The stored rows of the table whose [start, end) lies within the quoted passage (a block
   of query_api.releases), in order. Each cell sits at its column (the spans of the cells before
   it, summed) and covers its span. Empty cells are dropped.
2. Fragments. In each row, a cell that is only a currency or opening mark ("$", "($", "$(", "(",
   "€", "£") joins the next non-empty cell ("$" + "794.0" = "$794.0"), and a cell that is only a
   closing mark (")", "%", ")%", "%)", "pts", "pts.", "x") joins the previous one ("22.8" + "%"),
   each covering both cells' columns.
3. Figures. A figure is a cell that, whole, is a number ("1,234", "(139.1)", "$4.90", "22.8%",
   "$4,313M - $4,513M", "49% - 51%": digits with "," and ".", an optional sign, currency, "%",
   parentheses, an M/B/K unit, and a range "-", "–", "—" or "to"), or a dash ("—", "-", "–", "$—"),
   "n/a", "nm"; a bare year (1900-2099) is not one. Also a figure: a cell that starts with a
   number ("$2.5 billion") when it is not its row's first cell.
4. Head rows: from the top, rows the parser marked head (a <thead>, <th> cells) and rows without a
   figure whose first cell is right of the table's leftmost first cell (no label) or that have one
   cell (a title); the first other row ends them. A table without a figure in its other rows has no
   grid (the caller keeps the text).
5. Columns. The label area ends where the leftmost figure of the body rows starts. Body cells
   starting before it are the row's label (column 0). The value columns are the figures' column
   ranges over all body rows, merged where they overlap, then the other value cells' ranges that
   meet none of those, merged likewise; left to right after the label column. A body value cell
   spans the value columns it overlaps (a heading inside the table spans several).
6. Head rows (rule 4): a cell spans the grid columns its range overlaps (the label column too when
   it starts in the label area); one that overlaps none goes to the next value column to its right,
   else is dropped. In any row, a cell meeting the one before it in a column joins it (texts joined
   by a space, the span widened).

A grid: {"columns": n, "head": [[{"text", "col", "span"}...]...], "body": [[{"text", "col",
"span"}...]...], "char_start", "char_end"}: each row's non-empty cells at their first column and
width (a body row's label at column 0), offsets of the rows used into the release's text. No "rows"
or "sections" key: the agent's verify.py reads those as financial rows (and the agent never asks
for grids: events.py `tables`).
"""

import re

_OPEN = {"$", "($", "$(", "(", "€", "£", "(€", "(£"}
_CLOSE = {")", "%", ")%", "%)", "pts", "pts.", "x", "bps"}
_NUM = r"[-–—]?[$€£]?\(?[-–—]?\d[\d,]*(?:\.\d+)?\)?%?\)?\s?[MBK]?"
_FIGURE = re.compile(rf"^{_NUM}(?:\s*(?:-|–|—|to)\s*{_NUM})?$|^[$€£]?[-–—]+$|^(?:n/?a|nm)$",
                     re.IGNORECASE)
_STARTS_NUMBER = re.compile(r"^[-–—]?[$€£]?\(?\d")
_YEAR = re.compile(r"^(?:19|20)\d\d$")


def is_figure(text: str, first: bool = False) -> bool:
    """Rule 3: `first` says the cell is its row's first non-empty cell."""
    if _YEAR.match(text):
        return False
    return bool(_FIGURE.match(text)) or (not first and bool(_STARTS_NUMBER.match(text)))


def _cells(row: dict) -> list[list]:
    """Rules 1-2: a row's non-empty cells as [start column, end column, text]."""
    spans = row.get("spans") or [1] * len(row["cells"])
    out: list[list] = []
    col = 0
    for text, span in zip(row["cells"], spans, strict=False):
        if text:
            out.append([col, col + span, text])
        col += span
    merged: list[list] = []
    pending: list | None = None  # an opening mark waiting for its figure
    for cell in out:
        if cell[2] in _OPEN:
            if pending is not None:
                merged.append(pending)
            pending = cell
            continue
        if pending is not None:
            cell = [pending[0], cell[1], pending[2] + cell[2]]
            pending = None
        if cell[2] in _CLOSE and merged:
            merged[-1] = [merged[-1][0], cell[1], merged[-1][2] + cell[2]]
            continue
        merged.append(cell)
    if pending is not None:
        merged.append(pending)
    return merged


def _merge(ranges: list[list[int]]) -> list[list[int]]:
    out: list[list[int]] = []
    for a, b in sorted(ranges):
        if out and a < out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def grid(rows: list[dict], start: int, end: int) -> dict | None:
    """Rules 1-6 over a stored table's rows (silver.filing_tables.rows), keeping the rows within
    [start, end) of the release's text; None when there is no grid (rule 4)."""
    kept = [r for r in rows if start <= r["start"] and r["end"] <= end]
    cells = [_cells(r) for r in kept]
    if not any(cells):
        return None
    left = min(row[0][0] for row in cells if row)
    figure = [[is_figure(c[2], i == 0) for i, c in enumerate(row)] for row in cells]
    head = 0
    while head < len(kept) and (kept[head].get("head") or (
            not any(figure[head]) and (len(cells[head]) == 1 or cells[head][0][0] > left))):
        head += 1
    body = range(head, len(kept))
    figures = [c for r in body for c, f in zip(cells[r], figure[r], strict=True) if f]
    if not figures:
        return None
    label_end = min(c[0] for c in figures)
    bands = _merge([[c[0], c[1]] for c in figures if c[0] >= label_end])
    others = [[c[0], c[1]] for r in body for c in cells[r]
              if c[0] >= label_end and not any(c[0] < y and x < c[1] for x, y in bands)]
    bands = sorted(bands + _merge(others))

    def place(row: list[list], label_one: bool) -> list[dict]:
        placed: list[list] = []  # [first column, last column, text], left to right
        for c in row:
            if c[0] < label_end and label_one:
                cols = [0]
            else:
                cols = ([0] if c[0] < label_end else []) + [
                    i + 1 for i, (x, y) in enumerate(bands) if c[0] < y and x < c[1]]
            if not cols:  # a head cell over a gap between columns: the next column
                cols = [i + 1 for i, (x, _) in enumerate(bands) if x >= c[1]][:1]
            if not cols:
                continue
            if placed and cols[0] <= placed[-1][1]:  # meets the cell before it
                placed[-1][1] = max(placed[-1][1], cols[-1])
                placed[-1][2] = f"{placed[-1][2]} {c[2]}"
            else:
                placed.append([cols[0], cols[-1], c[2]])
        return [{"text": t, "col": a, "span": b - a + 1} for a, b, t in placed]

    return {"columns": len(bands) + 1,
            "head": [place(cells[r], False) for r in range(head)],
            "body": [place(cells[r], True) for r in body],
            "char_start": min(r["start"] for r in kept), "char_end": max(r["end"] for r in kept)}
