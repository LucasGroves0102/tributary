"""Headline lines of a 10-K's or 10-Q's own statement tables (data trust, part 2; migration 074).

The statements' tables as parser v7 stores them (rule 11, silver.filing_tables rows: cells in
order, colspans, head rows), read independently of the XBRL gold is built from, so
ops.check_financials can compare gold with what the filing prints. Pure: no I/O (index.py reads
and writes). The rules; bump READER with any change (the catch-up then re-reads every filing):

1. Cells. A cell's columns are the spans of the cells before it summed. A number is "1,234",
   "1,234.5", "(1,234)", "(1,234" (its ")" in the next cell), or "—", "–", "-" (zero), with an
   optional "$"; "%" and anything else is not a number. A data row: not marked head, its first
   non-empty cell not a number, then at least one number, not all of them bare years ("2025").
   Head rows: those before the first data row, and any marked head. A head row narrower than the
   widest head row is aligned to its right edge (a label cell above it spanning rows, a rowspan,
   isn't among the stored cells).
2. Periods. The period cells are the head row with the most cells naming one year (1990-2049) or a
   full date ("September 30, 2026", "Sept. 30 2026", "30 September 2026"), without "vs" or
   "change"; each one's period is its text and that of the head cells of other rows covering its
   columns that name at most one year ("For the years ended November 2, 2025, October 27, 2024
   and October 29, 2023" spans every column and dates none). A data row with as many numbers as
   period cells reads them in order; otherwise each number reads the period cell covering its
   first column; a number with neither has no period.
   Durations: "three months", "13 weeks" (weeks / 4.33 months), "quarter" 3, "year(s)",
   "fiscal" or "annual" 12.
3. Current: a period with a date within 7 days of the filing's period; with no date in any period,
   the period's year; with no period of that year, the latest year when it is the period's year
   less one and the period ends in January to June ("Fiscal 2025" for a year ended February 1,
   2026). Income lines then keep, on a 10-Q, the shortest stated duration (the quarter, not the
   year to date); on a 10-K any duration but under 10 months (a fourth quarter beside the year).
   A period with no duration stays.
4. Scale: in a head cell, else in the nearest line before the table that has one (its first, so
   "in millions, except shares in thousands" is millions): "in thousands/millions/billions",
   "thousands/millions/billions of dollars", "dollars/amounts/$ in ...", "(millions)", "(000)",
   "in 000s", or a cell or line starting "Millions"; else the scale of the table before it in its
   section (a statement continued). None found: no scale (the check then only counts agreement,
   at any scale).
5. Lines, by label (lower case, "’" read "'", footnote marks "(1)", "(a)", "(Note 4)", "*" and a
   final ":" dropped):
     revenue                 (total) (net) (operating) revenue(s) / sales (, net); (total)
                             revenue(s) net of interest expense; total revenues and other
                             income; (total) net sales and revenues; total sales and revenues;
                             sales and other operating revenues
     net_income              net income / earnings / loss / profit, with "(loss)" either side,
                             "attributable to ..." unless to noncontrolling, minority or redeemable
                             interests, and "and comprehensive income / loss"
     total_assets            total assets
     liabilities_and_equity  total liabilities ... equity / deficit / deficiency / capital
   A row labelled "Total" reads as "total" and the label of the row of words above it ("Net sales
   and revenues:"); "Basic" and "Diluted" as that label, " - " and theirs. A "net loss" label
   may print the loss without parentheses (`loss_label`: the check reads it both ways).
6. Titles: the table's head, or one of the 8 lines before it of at most 150 characters, names a
   statement (statement(s) of operations, income, earnings, comprehensive income, financial
   position, condition, assets and liabilities, cash flows; income, earnings or cash flow
   statement(s); balance sheet(s)). A head or such a line naming the parent company, the registrant
   or Schedule I marks a parent-only statement: its table is not read (a 10-Q's "condensed"
   statements are read). The check
   reads a filing's titled tables' lines when it has any for that line, else every table's.

Every current number of every matching row is a candidate; the check (074) agrees when gold matches
any candidate of a line, and disagrees when it matches none.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

READER = "statements-1"

LINES = ("revenue", "net_income", "total_assets", "liabilities_and_equity")
INCOME = frozenset({"revenue", "net_income"})
TITLE_LINES, TITLE_CHARS = 8, 150

_NUMBER = re.compile(r"^\(?\s*\$?\s*\(?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*\)?$")
_DASH = frozenset({"—", "–", "-", "$—", "$ —", "$-", "$ -", "$–", "$ –"})
_YEAR_CELL = re.compile(r"^(?:199|20[0-4])\d$")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_DATE_MDY = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})"
                       r"(?:st|nd|rd|th)?\s*,?\s*((?:19|20)\d{2})\b")
_DATE_DMY = re.compile(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
                       r"\.?\s*,?\s*((?:19|20)\d{2})\b")
_YEAR = re.compile(r"(?<![\d,.$])((?:199|20[0-4])\d)(?![\d,])")
_NOT_PERIOD = re.compile(r"\bvs\.?\b|\bversus\b|change", re.I)
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
                 "fourteen": 14, "fifteen": 15, "sixteen": 16, "twenty-four": 24,
                 "twenty-six": 26, "twenty-eight": 28, "thirty-six": 36, "thirty-nine": 39,
                 "forty": 40, "forty-two": 42, "fifty-two": 52, "fifty-three": 53}
_DURATION = re.compile(r"\b(\d{1,2}|" + "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True))
                       + r")[\s-]+(months?|weeks?)\b")
_QUARTER = re.compile(r"\bquarters?\b")
_YEARLY = re.compile(r"\b(years?|fiscal|annual)\b")
_SCALE_WORDS = {"thousands": 1_000, "millions": 1_000_000, "billions": 1_000_000_000,
                "000": 1_000}
_SCALE = re.compile(
    r"\bin\s+(?:u\.?s\.?\s+)?(?:dollars\s+in\s+)?(thousands|millions|billions)\b"
    r"|\b(thousands|millions|billions)\s+of\s+(?:u\.?s\.?\s+|canadian\s+|[a-z]{2,10}\s+)?dollars\b"
    r"|\b(?:dollars|amounts|\$|[a-z]{3})\s+in\s+(thousands|millions|billions)\b"
    r"|\(\s*(thousands|millions|billions)\s*[,)]"
    r"|\(\s*\$?\s*(000)'?s?\s*(?:omitted)?\s*\)|\bin\s+(000)'?s\b"
    r"|^\W*(?:\$\s*)?(thousands|millions|billions)\b", re.I)
_FOOTNOTE = re.compile(r"(?:\s*\((?:[a-z]|\d{1,2}|[ivx]{1,4}|notes? [\d, and]+[a-z]?)\))+$"
                       r"|\s*\*+$")
_TITLE = re.compile(r"statements?\s+of\s+(?:consolidated\s+)?(?:operations|income|earnings|"
                    r"comprehensive\s+(?:income|loss)|financial\s+(?:position|condition)|"
                    r"condition|assets\s+and\s+liabilities|cash\s+flows?)"
                    r"|\b(?:income|earnings|cash\s+flows?)\s+statements?|balance\s+sheets?", re.I)
_PARENT_ONLY = re.compile(r"\bparent\b|\bregistrant\b|\bschedule\s+i\b", re.I)
_NOT_CONTROLLING = r"(?!.*(?:non-?controlling|minority|redeemable))"
_PATTERNS = {
    "revenue": re.compile(
        r"^(?:total )?(?:net )?(?:operating )?(?:revenues?|sales)(?:,? net)?$"
        r"|^(?:total )?(?:net )?revenues?,? net of interest expense$"
        r"|^total (?:net )?revenues? and other income$"
        r"|^(?:total )?net sales and (?:other )?(?:operating )?revenues?$"
        r"|^total sales and (?:other )?(?:operating )?revenues?$"
        r"|^(?:total )?sales and other operating revenues?$"),
    "net_income": re.compile(
        r"^net (?:income|earnings|loss|profit)(?: \((?:loss|income)\))?"
        rf"(?: attributable to {_NOT_CONTROLLING}.+)?"
        r"(?: and comprehensive (?:income|loss|\(loss\) income|income \(loss\)))?$"
        r"|^net \((?:loss|income)\) (?:income|earnings|loss)"
        rf"(?: attributable to {_NOT_CONTROLLING}.+)?$"),
    "total_assets": re.compile(r"^total assets$"),
    "liabilities_and_equity": re.compile(
        r"^total liabilities\b.*\b(?:equity|deficit|deficiency|capital|investment)\b.*$"),
}


@dataclass(frozen=True)
class Line:
    line: str                # LINES
    label: str               # as printed
    value: Decimal           # the number as printed times the scale (1 without one)
    scale: int | None        # 1,000 / 1,000,000 / 1,000,000,000; None: no scale words
    period_end: date | None  # the period's date when its head has one
    months: int | None       # the period's duration when its head says one
    section_key: str
    table_no: int
    row_no: int
    titled: bool
    loss_label: bool         # "net loss ...": the check reads the value both ways


@dataclass(frozen=True)
class _Cell:
    first: int   # first column
    last: int    # one past the last column
    text: str


@dataclass(frozen=True)
class _Period:
    first: int
    last: int
    dates: tuple[date, ...]
    years: tuple[int, ...]
    months: int | None


def label_key(text: str) -> str:
    s = text.lower().replace("’", "'").replace("‘", "'").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip().rstrip(":").strip()
    return _FOOTNOTE.sub("", s).strip().rstrip(":").strip()


def number(text: str) -> Decimal | None:
    """Rule 1's number, signed; None when the cell is not a number."""
    t = text.strip().replace("\xa0", " ")
    if t in _DASH:
        return Decimal(0)
    if "%" in t:
        return None
    m = _NUMBER.match(t)
    if not m:
        return None
    try:
        v = Decimal(m.group(1).replace(",", "") + (m.group(2) or ""))
    except InvalidOperation:
        return None
    return -v if "(" in t else v


def _cells(row: dict) -> list[_Cell]:
    spans = row.get("spans") or [1] * len(row["cells"])
    out, at = [], 0
    for text, span in zip(row["cells"], spans, strict=False):
        out.append(_Cell(at, at + span, text))
        at += span
    return out


def _date(y: int, mo: int, d: int) -> list[date]:
    try:
        return [date(y, mo, d)]
    except ValueError:
        return []


def _dates(text: str) -> list[date]:
    out = []
    for m in _DATE_MDY.finditer(text):
        out += _date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
    for m in _DATE_DMY.finditer(text):
        out += _date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    return out


def _years(text: str) -> set[int]:
    return {int(y) for y in _YEAR.findall(text)} | {d.year for d in _dates(text)}


def _months(text: str) -> int | None:
    found = []
    for m in _DURATION.finditer(text):
        n = m.group(1)
        k = int(n) if n.isdigit() else _WORD_NUMBERS[n]
        found.append(k if m.group(2).startswith("month") else round(k / 4.33))
    if found:
        return min(found)
    if _QUARTER.search(text):
        return 3
    if _YEARLY.search(text):
        return 12
    return None


def scale_of(head: Sequence[str], before: str) -> int | None:
    """Rule 4, without the carry from the table before."""
    for text in [*head, *reversed(before.split("\n"))]:
        m = _SCALE.search(text.replace("’", "'"))
        if m:
            return _SCALE_WORDS[next(g for g in m.groups() if g).lower()]
    return None


def _titles(head: Sequence[str], before: str) -> list[str]:
    """Rule 6: the statement titles in the table's head and the short lines just before it."""
    lines = [x for x in before.split("\n") if x.strip()][-TITLE_LINES:]
    return [x for x in [*head, *(x for x in lines if len(x) <= TITLE_CHARS)] if _TITLE.search(x)]


def _head(grid: list[list[_Cell]], rows: Sequence[dict], first_data: int) -> list[list[_Cell]]:
    """Rule 1's head rows, a narrow one aligned right."""
    head = [g for i, g in enumerate(grid) if g and (i < first_data or rows[i].get("head"))]
    width = max((g[-1].last for g in head), default=0)
    return [[_Cell(c.first + width - g[-1].last, c.last + width - g[-1].last, c.text) for c in g]
            for g in head]


def _periods(head: list[list[_Cell]]) -> list[_Period]:
    """Rule 2's period cells, left to right, each with the head text above it."""
    def is_period(c: _Cell) -> bool:
        t = c.text.lower()
        return bool(t.strip()) and not _NOT_PERIOD.search(t) and (
            bool(_dates(t)) or len(set(_YEAR.findall(t))) == 1)

    best = max(head, key=lambda g: sum(is_period(c) for c in g), default=[])
    out = []
    for c in best:
        if not is_period(c):
            continue
        text = " ".join(h.text for g in head for h in g
                        if h.text.strip() and h.first < c.last and c.first < h.last
                        and (h is c or g is not best and len(_years(h.text.lower())) <= 1)
                        ).lower()
        out.append(_Period(c.first, c.last, tuple(_dates(text)), tuple(sorted(_years(text))),
                           _months(text)))
    return out


def _current(periods: list[_Period], period: date) -> set[int]:
    """Rule 3 (dates and years): indexes of the current periods."""
    if any(p.dates for p in periods):
        return {i for i, p in enumerate(periods)
                if any(abs((d - period).days) <= 7 for d in p.dates)}
    hit = {i for i, p in enumerate(periods) if period.year in p.years}
    if hit:
        return hit
    latest = max((max(p.years) for p in periods if p.years), default=None)
    if latest == period.year - 1 and period.month <= 6:
        return {i for i, p in enumerate(periods) if latest in p.years}
    return set()


def read_table(rows: Sequence[dict], before: str, form: str, period: date, *,
               section_key: str = "", table_no: int = 0, carry: int | None = None,
               patterns: dict[str, re.Pattern] | None = None,
               durations: frozenset[str] = INCOME) -> tuple[list[Line], int | None]:
    """Rules 1-6 over one stored table (silver.filing_tables.rows) and the text before it, with
    the scale of the table before it in its section (`carry`): its lines and its scale.
    `patterns` (line -> label pattern) and `durations` (the lines read for a period of time)
    default to rule 5's; tests/golden's reference extractor passes its own."""
    patterns = _PATTERNS if patterns is None else patterns
    grid = [_cells(r) for r in rows]
    first_data = None
    labels: list[tuple[int, str] | None] = []
    for i, cells in enumerate(grid):
        texts = [c for c in cells if c.text.strip()]
        lab = None
        if texts and number(texts[0].text) is None and not rows[i].get("head"):
            nums = [c for c in cells if c.first >= texts[0].last and number(c.text) is not None]
            if nums and not all(_YEAR_CELL.match(c.text.strip()) for c in nums):
                lab = (texts[0].last, texts[0].text)
        labels.append(lab)
        if lab is not None and first_data is None:
            first_data = i
    if first_data is None:
        return [], carry
    head = _head(grid, rows, first_data)
    head_texts = [c.text for g in head for c in g if c.text.strip()]
    scale = scale_of(head_texts, before)
    if scale is None:
        scale = carry
    titles = _titles(head_texts, before)
    near = [x for x in before.split("\n") if x.strip()][-TITLE_LINES:]
    if any(_PARENT_ONLY.search(t)
           for t in [*head_texts, *(x for x in near if len(x) <= TITLE_CHARS)]):
        return [], scale  # rule 6: a parent company's own statements (Schedule I)
    periods = _periods(head)
    if not periods:
        return [], scale
    current = _current(periods, period)
    out: list[Line] = []
    heading = ""
    for i, (cells, lab) in enumerate(zip(grid, labels, strict=True)):
        if lab is None:
            texts = [c.text for c in cells if c.text.strip()]
            if len(texts) == 1 and i >= first_data:
                heading = label_key(texts[0])
            continue
        key = label_key(lab[1])
        if key == "total" and heading:  # rule 5: "Total" under a "Net sales and revenues:" row
            key = heading if heading.startswith("total ") else f"total {heading}"
        elif key in ("basic", "diluted") and heading:  # "Diluted" under "Earnings per share:"
            key = f"{heading} - {key}"
        line = next((name for name, p in patterns.items() if p.match(key)), None)
        if line is None:
            continue
        nums = [(c, v) for c in cells if c.first >= lab[0] and (v := number(c.text)) is not None]
        if len(nums) == len(periods):
            read = [(periods.index(p), v) for p, (_, v) in zip(periods, nums, strict=True)]
        else:
            read = [(k, v) for c, v in nums for k, p in enumerate(periods)
                    if p.first <= c.first < p.last]
        keep = [(k, v) for k, v in read if k in current]
        if line in durations:
            stated = {periods[k].months for k, _ in keep if periods[k].months is not None}
            if form.startswith("10-Q") and stated:
                keep = [(k, v) for k, v in keep if periods[k].months in (min(stated), None)]
            elif form.startswith("10-K"):
                keep = [(k, v) for k, v in keep if (periods[k].months or 12) >= 10]
        for k, v in keep:
            p = periods[k]
            dates = [d for d in p.dates if abs((d - period).days) <= 7]
            out.append(Line(line, lab[1], v * (scale or 1), scale, dates[0] if dates else None,
                            p.months, section_key, table_no, i, bool(titles),
                            key.startswith("net loss")))
    return out, scale


def read(tables: Iterable[tuple[str, int, Sequence[dict], str]], form: str, period: date, *,
         patterns: dict[str, re.Pattern] | None = None,
         durations: frozenset[str] = INCOME) -> list[Line]:
    """Every table of one filing, in order: (section key, table number, rows, the text before
    the table)."""
    out: list[Line] = []
    carry: tuple[str, int | None] = ("", None)
    for key, n, rows, before in tables:
        lines, scale = read_table(rows, before, form, period, section_key=key, table_no=n,
                                  carry=carry[1] if carry[0] == key else None,
                                  patterns=patterns, durations=durations)
        out += lines
        carry = (key, scale)
    return out
