"""A user's CSV of time series by company and date ("bring your own data", B2c), read by hand.

The file: a header row naming the columns, then one row per company and date. One column holds
the company (a ticker or a CIK), one the date (ISO, YYYY-MM-DD: the end of the period the values
describe, like a filing's period end), optionally one the day each row was published (declared by
the upload, never guessed: without it every row is known from the upload day). Every other column
is a field of numbers. At most MAX_BYTES, MAX_ROWS data rows and MAX_COLUMNS columns.

Reading: UTF-8 (a byte-order mark is dropped), UTF-16 with its mark, else Windows-1252 (Excel's
"CSV (Comma delimited)"). The delimiter is the header's most frequent of comma, semicolon and tab
outside quotes (a tie: comma). Quotes and newlines inside quotes as RFC 4180. Numbers: an optional
sign, digits with thousands separators only in groups of three, a decimal part, an exponent, or
the whole in parentheses (an accountant's negative). The decimal mark is "." except in a
semicolon file, where it is "," (Excel's European export), unless the upload says otherwise; the
other mark is the thousands separator. Blank cells and NA, N/A, NaN, null, none, "-" and "—" are
no value. Anything else in a field (text, a percent sign, a currency symbol, a formula such as
"=cmd") refuses the upload: values are numbers only and nothing a spreadsheet would run is ever
stored. Column names are simplified to formula names (lower case letters, digits and "_", 40
characters, a letter first: "Store count (EoP)" reads `store_count_eop`).

Problems name a row (the spreadsheet's row number: the header is row 1) and a column (1-based),
never a cell's text: an answer quotes nothing the user sent (the same rule as every hand-parsed
body: query-api CLAUDE.md "Secrets"). A file is refused as a whole on any problem (the first
MAX_PROBLEMS are listed with the count); a company that matches no ticker or CIK is not a problem:
its rows are left out and listed by number (datasets.py), never matched to a guess.

Two passes over the decoded text keep memory bounded: `scan` checks everything and keeps one
small key per row (row number, company cell, date, publication date), `values` re-reads the rows
for storing, yielding each field as a normalized decimal string."""

from __future__ import annotations

import codecs
import csv
import io
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 200_000
MAX_COLUMNS = 50
MAX_FIELDS = MAX_COLUMNS - 1  # a company column, a date column, the rest fields
MAX_PROBLEMS = 20
MAX_CELL = 1_000  # characters; a longer cell is no number, date or company
STOP_AFTER = 10_000  # problems: past this the scan stops counting
NAME_MAX = 40
NAME = re.compile(rf"^[a-z][a-z0-9_]{{0,{NAME_MAX - 1}}}$")
UNITS = ("number", "ratio", "money", "shares", "per_share")
EARLIEST, LATEST = date(1900, 1, 1), date(2099, 12, 31)
BLANKS = frozenset({"", "na", "n/a", "nan", "null", "none", "-", "—", "#n/a"})
# header names read as a role when the upload doesn't name the column (simplified, in order)
COMPANY_NAMES = ("ticker", "symbol", "cik", "company", "company_id", "ticker_symbol")
DATE_NAMES = ("date", "day", "period_end", "period", "end_date", "quarter_end", "as_of",
              "observation_date")
# never used unless declared: the preview only suggests one
PUBLISHED_NAMES = ("published", "published_on", "publication_date", "known_at", "known_on",
                   "release_date", "released", "available_on")
_ISO = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_EXP = r"(?:[eE][+-]?[0-9]{1,3})?"
_GROUP = "[. \u00a0\u202f]"  # "," numbers' thousands: ".", a space, a no-break one
# a number with "." as the decimal mark (thousands: ",") and with "," (thousands: ".", a space or
# a no-break space, as French exports write them)
_NUMBER = {
    ".": re.compile(rf"^(?:[0-9]{{1,3}}(?:,[0-9]{{3}})+|[0-9]{{1,30}})(?:\.[0-9]{{1,30}})?{_EXP}$"
                    rf"|^\.[0-9]{{1,30}}{_EXP}$"),
    ",": re.compile(rf"^(?:[0-9]{{1,3}}(?:{_GROUP}[0-9]{{3}})+|[0-9]{{1,30}})"
                    rf"(?:,[0-9]{{1,30}})?{_EXP}$|^,[0-9]{{1,30}}{_EXP}$"),
}


@dataclass(frozen=True)
class Problem:
    """What's wrong, where: row and column numbers only, never a cell's text."""
    code: str
    message: str
    row: int | None = None
    column: int | None = None

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "row": self.row,
                "column": self.column}


class Refused(Exception):
    """The file can't be read as a dataset: `problems` (the first MAX_PROBLEMS), `count` (all
    found, at most STOP_AFTER)."""

    def __init__(self, problems: list[Problem], count: int | None = None,
                 hint: str | None = None) -> None:
        super().__init__(problems[0].message if problems else "refused")
        self.problems = problems[:MAX_PROBLEMS]
        self.count = count if count is not None else len(problems)
        self.hint = hint

    def to_dict(self) -> dict:
        return {"errors": [p.to_dict() for p in self.problems], "count": self.count,
                **({"hint": self.hint} if self.hint else {})}


@dataclass(frozen=True)
class Options:
    """What the upload says about the file. Column names as typed (matched once simplified)."""
    company_column: str | None = None
    date_column: str | None = None
    published_column: str | None = None
    decimal: str | None = None              # "." or ","; None: by the delimiter
    units: dict[str, str] = field(default_factory=dict)  # field name → one of UNITS


@dataclass
class Scan:
    """A checked file: the columns and one key per data row (row number, company cell as
    written, date, publication date or None)."""
    text: str
    delimiter: str
    decimal: str
    header: list[str]                      # simplified names, "" where a header cell is blank
    company: int                           # column indexes (0-based)
    day: int
    published: int | None
    fields: list[tuple[str, int]]          # (name, column index) in file order
    units: list[str]
    keys: list[tuple[int, str, date, date | None]]
    blank_rows: int = 0                    # rows with a company and date but no value
    suggested: dict[str, str | None] = field(default_factory=dict)

    @property
    def companies(self) -> set[str]:
        return {k[1] for k in self.keys}


def simplify(text: str) -> str:
    """A column name as a formula reads it: "Store count (EoP)" → "store_count_eop"; "" when
    nothing is left."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")
    if not s:
        return ""
    if not s[0].isalpha():
        s = "f_" + s
    return s[:NAME_MAX].rstrip("_")


def decode(data: bytes) -> str:
    """UTF-8 (BOM dropped), UTF-16 with a BOM, else Windows-1252, else Latin-1 (never fails)."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def sniff_delimiter(text: str) -> str:
    """The header line's most frequent of , ; and tab outside quotes; a tie or none: comma."""
    counts = {",": 0, ";": 0, "\t": 0}
    quoted = False
    for c in text[:65_536]:
        if c == '"':
            quoted = not quoted
        elif not quoted and c in "\r\n":
            if any(counts.values()):
                break
        elif not quoted and c in counts:
            counts[c] += 1
    best = max(counts.values())
    return "," if best == 0 or counts[","] == best else max(counts, key=lambda d: counts[d])


_PLAIN = {".": re.compile(r"-?[0-9]{1,30}(?:\.[0-9]{1,30})?"), ",": re.compile(r"-?[0-9]{1,30}")}


def number(cell: str, decimal: str) -> str | None:
    """A cell as a normalized decimal string ("-1234.5"); None when it is no value; ValueError
    when it isn't a number."""
    if _PLAIN[decimal].fullmatch(cell):  # the common case, already normal (half the time)
        return cell
    s = cell.strip()
    if s.lower() in BLANKS:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        s, negative = s[1:-1].strip(), True
    if s[:1] in "+-":
        if negative:
            raise ValueError("number")
        negative, s = s[0] == "-", s[1:]
    if len(s) > 80 or not _NUMBER[decimal].match(s):
        raise ValueError("number")
    if decimal == ",":
        s = re.sub(_GROUP, "", s).replace(",", ".")
    else:
        s = s.replace(",", "")
    if s.startswith("."):
        s = "0" + s
    return ("-" if negative else "") + s


def day_of(cell: str) -> date:
    """An ISO date (YYYY-MM-DD) between EARLIEST and LATEST; ValueError otherwise."""
    s = cell.strip()
    if not _ISO.match(s):
        raise ValueError("date")
    d = date.fromisoformat(s)
    if not EARLIEST <= d <= LATEST:
        raise ValueError("date")
    return d


def _column(header: list[str], wanted: str | None, defaults: tuple[str, ...], role: str,
            problems: list[Problem]) -> int | None:
    if wanted is not None:
        name = simplify(wanted)
        if name and name in header:
            return header.index(name)
        problems.append(Problem(f"no_{role}_column",
                                f"The file has no column with the {role} column's name"))
        return None
    for name in defaults:
        if name in header:
            return header.index(name)
    return None


def _reader(text: str, delimiter: str) -> Iterator[list[str]]:
    return csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quotechar='"',
                      doublequote=True, skipinitialspace=False, strict=True)


def scan(data: bytes, options: Options, today: date) -> Scan:
    """Checks the whole file; Refused with the problems. `today`: the upload day (a publication
    date after it is a problem: nothing is known before it is published)."""
    if len(data) > MAX_BYTES:
        raise Refused([Problem("too_large", f"The file is larger than {MAX_BYTES // 2**20} MB")])
    text = decode(data)
    delimiter = sniff_delimiter(text)
    decimal = options.decimal or ("," if delimiter == ";" else ".")
    if decimal not in _NUMBER:
        raise Refused([Problem("decimal", 'The decimal mark is "." or ","')])
    problems: list[Problem] = []
    count = 0

    def problem(p: Problem) -> None:
        nonlocal count
        count += 1
        if len(problems) < MAX_PROBLEMS:
            problems.append(p)

    rows = _reader(text, delimiter)
    record = 0
    raw_header: list[str] | None = None
    try:
        for cells in rows:
            record += 1
            if any(c.strip() for c in cells):
                raw_header = cells
                break
    except csv.Error:
        raise Refused([Problem("quoting", "The header row's quotes don't close", record)]) \
            from None
    if raw_header is None:
        raise Refused([Problem("empty", "The file has no rows")])
    if len(raw_header) > MAX_COLUMNS:
        raise Refused([Problem("too_many_columns",
                               f"The file has more than {MAX_COLUMNS} columns", record)])
    header = [simplify(h) for h in raw_header]
    header_row = record
    head: list[Problem] = []
    company = _column(header, options.company_column, COMPANY_NAMES, "company", head)
    day = _column(header, options.date_column, DATE_NAMES, "date", head)
    published = (_column(header, options.published_column, (), "publication date", head)
                 if options.published_column is not None else None)
    if company is None and not any(p.code == "no_company_column" for p in head):
        head.append(Problem("no_company_column", "Name the column that holds the tickers or "
                            "CIKs (company_column): no column is called ticker, symbol or cik"))
    if day is None and not any(p.code == "no_date_column" for p in head):
        head.append(Problem("no_date_column", "Name the column that holds the dates "
                            "(date_column): no column is called date or period_end"))
    roles = [i for i in (company, day, published) if i is not None]
    if len(set(roles)) != len(roles):
        head.append(Problem("same_column", "The company, date and publication date must be "
                            "three different columns"))
    for i, name in enumerate(header):
        if i in roles:
            continue
        if not name:
            head.append(Problem("unnamed_column", "A column has no name (or only symbols)",
                                header_row, i + 1))
        elif header.index(name) != i:
            head.append(Problem("same_name", f"Columns {header.index(name) + 1} and {i + 1} "
                                "have the same name once simplified", header_row, i + 1))
    fields = [(name, i) for i, name in enumerate(header) if i not in roles and name]
    if not fields and not head:
        head.append(Problem("no_fields", "The file has no column of values beside the company "
                            "and date"))
    unknown_units = set(options.units) - {name for name, _ in fields}
    if unknown_units:
        head.append(Problem("units", "The units name a column the file doesn't have"))
    if any(u not in UNITS for u in options.units.values()):
        head.append(Problem("units", f"A unit is one of {', '.join(UNITS)}"))
    if head:
        raise Refused(head)
    assert company is not None and day is not None

    suggested = {
        "company_column": raw_header[company].strip(),
        "date_column": raw_header[day].strip(),
        "published_column": next((raw_header[header.index(n)].strip() for n in PUBLISHED_NAMES
                                  if n in header and header.index(n) not in roles), None),
    }
    keys: list[tuple[int, str, date, date | None]] = []
    days: dict[str, date] = {}
    cells_seen: dict[str, str] = {}
    blank_rows = 0
    width = len(raw_header)
    try:
        for cells in rows:
            record += 1
            if count >= STOP_AFTER:
                break
            if not any(c.strip() for c in cells):
                continue
            if len(keys) + blank_rows >= MAX_ROWS:
                raise Refused([Problem("too_many_rows",
                                       f"The file has more than {MAX_ROWS:,} rows", record)])
            if len(cells) > width and any(c.strip() for c in cells[width:]):
                problem(Problem("extra_cells", "The row has more cells than the header has "
                                "columns", record, width + 1))
                continue
            cell = cells[company].strip() if company < len(cells) else ""
            if not cell:
                problem(Problem("no_company", "The row has no company", record, company + 1))
                continue
            if len(cell) > 40:
                problem(Problem("company", "The company is longer than a ticker or CIK",
                                record, company + 1))
                continue
            raw_day = cells[day].strip() if day < len(cells) else ""
            when = days.get(raw_day)
            if when is None:
                try:
                    when = days[raw_day] = day_of(raw_day)
                except ValueError:
                    problem(Problem("date", "Not a date as YYYY-MM-DD between 1900 and 2099",
                                    record, day + 1))
                    continue
            known: date | None = None
            if published is not None:
                raw = cells[published].strip() if published < len(cells) else ""
                try:
                    known = day_of(raw)
                except ValueError:
                    problem(Problem("published", "Not a publication date as YYYY-MM-DD",
                                    record, published + 1))
                    continue
                if known > today:
                    problem(Problem("published_later", "The publication date is after today",
                                    record, published + 1))
                    continue
            any_value = False
            bad = False
            for _, i in fields:
                c = cells[i] if i < len(cells) else ""
                if len(c) > MAX_CELL:
                    problem(Problem("number", "Not a number", record, i + 1))
                    bad = True
                    continue
                try:
                    any_value |= number(c, decimal) is not None
                except ValueError:
                    problem(Problem("number", "Not a number", record, i + 1))
                    bad = True
            if bad:
                continue
            if not any_value:
                blank_rows += 1
                continue
            keys.append((record, cells_seen.setdefault(cell, cell), when, known))
    except csv.Error:  # raised while reading the next record, before it was counted
        problem(Problem("quoting", "A quoted cell doesn't close, or a quote stands inside an "
                        "unquoted cell", record + 1))
    if problems:
        dated = next((i for i, n in enumerate(header) if n in PUBLISHED_NAMES), None)
        hint = (f"Column {dated + 1} looks like a publication date: name it as the publication "
                "date column (published_column), or leave it out of the file"
                if published is None and dated is not None and dated not in roles
                and any(p.column == dated + 1 for p in problems) else None)
        raise Refused(problems, count, hint)
    if not keys:
        raise Refused([Problem("no_rows", "The file has no row with a company, a date and a "
                               "value")])
    names = [name for name, _ in fields]
    return Scan(text=text, delimiter=delimiter, decimal=decimal, header=header, company=company,
                day=day, published=published, fields=fields,
                units=[options.units.get(n, "number") for n in names], keys=keys,
                blank_rows=blank_rows, suggested=suggested)


def values(s: Scan) -> Iterator[tuple[int, list[str | None]]]:
    """(row number, each field's normalized decimal string or None) for every row `scan` kept,
    in file order. Re-reads the text the scan checked, so it never raises for a kept row."""
    kept = {k[0] for k in s.keys}
    record = 0
    for cells in _reader(s.text, s.delimiter):
        record += 1
        if record in kept:
            yield record, [number(cells[i], s.decimal) if i < len(cells) else None
                           for _, i in s.fields]


def parse_units(text: str | None) -> dict[str, str]:
    """"stores:number, revenue_est:money" → {stores: number, revenue_est: money}; names are
    simplified. ValueError (a fixed message) when malformed."""
    out: dict[str, str] = {}
    if not text or not text.strip():
        return out
    if len(text) > 4_000:
        raise ValueError("units: too long")
    for part in text.split(","):
        name, sep, unit = part.partition(":")
        if not sep or not simplify(name) or unit.strip() not in UNITS:
            raise ValueError(f"units: name:unit pairs, a unit one of {', '.join(UNITS)}")
        out[simplify(name)] = unit.strip()
    return out
