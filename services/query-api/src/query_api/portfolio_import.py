"""Reads Fidelity's "Portfolio Positions" CSV export (Accounts & Trade → Portfolio → Positions →
Download) into positions a user can preview before anything is saved (query_api/portfolio.py
matches them to companies). Pure: no database, no network, nothing logged.

The format, as Fidelity documents and ships it (the test fixture is synthetic, written from it):
a header row, one row per position per account, then a blank line and quoted disclaimer lines,
the last "Date downloaded <Mon-DD-YYYY> <time> ET". Columns are read by their header names, in any
order, with extras ignored: Account Number, Account Name, Symbol, Description, Quantity, Last
Price, Last Price Change, Current Value, Today's Gain/Loss..., Total Gain/Loss..., Percent Of
Account, Cost Basis Total, Average Cost Basis, Type. Numbers may be quoted, with $, commas, a sign
or parentheses; "--" and "n/a" are blanks. Rows end with a trailing comma; a BOM may lead.

Rules:
- Account Number is dropped on parse: it is never read into a row, returned or stored. Account
  Name (the user's own nickname for the account, "Individual", "ROTH IRA") becomes the suggested
  account label, unless it carries what looks like an account number (`label_ok`), then none.
- Skipped, with a reason and never saved: the core cash and money-market positions (a symbol
  ending "**", or a money-market description), "Pending Activity", options (a symbol starting
  "-" or with spaces), rows without a symbol or a positive quantity. A row whose symbol isn't a
  plain ticker-like code (letters, digits, . / -) is skipped too.
- Cost: Cost Basis Total, else Average Cost Basis x Quantity, else unknown (the preview asks for
  it). Last Price is the user's own price "from your Fidelity export of <date>".
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

MAX_BYTES = 512 * 1024
MAX_ROWS = 500
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,11}$")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

# header name (lower case, spaces collapsed) → field
HEADERS = {
    "symbol": "symbol",
    "description": "description", "security description": "description",
    "quantity": "quantity", "shares": "quantity",
    "last price": "last_price",
    "cost basis total": "cost_total", "cost basis": "cost_total",
    "total cost basis": "cost_total",
    "average cost basis": "average_cost", "cost basis per share": "average_cost",
    "avg cost basis": "average_cost",
    "account name": "account_name", "account name/number": "account_name",
    "type": "type",
}


class NotAnExport(ValueError):
    """The text isn't a positions export this reader understands (the message says why, never
    quoting the file)."""


@dataclass
class Position:
    symbol: str
    description: str | None
    shares: Decimal
    cost_total: Decimal | None
    last_price: Decimal | None
    account_label: str | None


@dataclass
class Parsed:
    positions: list[Position] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)   # {symbol, description, reason}
    downloaded: date | None = None


def label_ok(label: str) -> bool:
    """An account label never holds an account number: no five digits in a row, at most five in
    all (app migration 021 checks the same)."""
    return not re.search(r"\d{5}", label) and sum(c.isdigit() for c in label) < 6


def clean_label(raw: str | None) -> str | None:
    """A free-text account label as stored: printable, spaces collapsed, at most 60 characters;
    None when empty or when it looks like it carries an account number."""
    if raw is None:
        return None
    text = " ".join("".join(c if c.isprintable() else " " for c in raw).split())[:60].strip()
    return text if text and label_ok(text) else None


def number(raw: str | None) -> Decimal | None:
    """'$1,234.56', '+$1.23', '-5', '(5.00)', '12.5%' → Decimal; '', '--', 'n/a' → None."""
    if raw is None:
        return None
    s = raw.strip().replace("$", "").replace(",", "").replace("%", "").replace(" ", "")
    if s in ("", "--", "-", "n/a", "N/A", "NA"):
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").lstrip("+")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return -d if negative else d


def downloaded_on(line: str) -> date | None:
    """'Date downloaded Nov-16-2026 10:15 a.m ET' or '... 11/16/2026 ...' → the date."""
    m = re.search(r"([A-Za-z]{3})[a-z]*[- ](\d{1,2}),?[- ](\d{4})", line)
    try:
        if m and m.group(1).lower() in MONTHS:
            return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", line)
        if m:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    return None


def _header_key(cell: str) -> str:
    return " ".join(cell.replace("﻿", "").strip().strip('"').lower().split())


def _cash(symbol: str, description: str) -> bool:
    d = description.lower()
    return symbol.endswith("**") or "money market" in d or d in ("cash", "core position")


def parse(text: str) -> Parsed:
    """The export → positions and skipped rows (module docstring). Raises NotAnExport."""
    if len(text.encode("utf-8", "replace")) > MAX_BYTES:
        raise NotAnExport(f"the file is over {MAX_BYTES // 1024} KB: is it a positions export?")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    header_at, columns = None, {}
    for i, row in enumerate(rows[:20]):
        keys = [_header_key(c) for c in row]
        if "symbol" in keys and ("quantity" in keys or "shares" in keys):
            header_at = i
            for j, k in enumerate(keys):
                if k in HEADERS and HEADERS[k] not in columns:
                    columns[HEADERS[k]] = j
            break
    if header_at is None:
        raise NotAnExport("no header row with Symbol and Quantity: download Positions from "
                          "Fidelity's Portfolio page as a CSV")
    out = Parsed()

    def cell(row: list[str], name: str) -> str:
        j = columns.get(name)
        return row[j].strip() if j is not None and j < len(row) else ""

    for row in rows[header_at + 1:]:
        joined = " ".join(row)
        if "date downloaded" in joined.lower():
            out.downloaded = downloaded_on(joined) or out.downloaded
            continue
        if len(row) < 2:  # a blank line or a quoted disclaimer: the footer
            continue
        raw_symbol = cell(row, "symbol")
        description = " ".join(cell(row, "description").split())[:120] or None
        first = row[0].strip().lower()
        if first.startswith("pending activity") or raw_symbol.lower().startswith("pending"):
            out.skipped.append({"symbol": None, "description": "Pending activity",
                                "reason": "pending"})
            continue
        if not raw_symbol:
            continue
        symbol = raw_symbol.upper().replace(" ", "") if not raw_symbol.startswith("-") \
            else raw_symbol
        if _cash(symbol, description or ""):
            out.skipped.append({"symbol": symbol.rstrip("*"), "description": description,
                                "reason": "cash"})
            continue
        if raw_symbol.startswith("-") or " " in raw_symbol.strip():
            out.skipped.append({"symbol": None, "description": description, "reason": "option"})
            continue
        symbol = symbol.rstrip("*")
        if not SYMBOL.fullmatch(symbol):
            out.skipped.append({"symbol": None, "description": description,
                                "reason": "unreadable"})
            continue
        shares = number(cell(row, "quantity"))
        if shares is None or shares <= 0 or shares >= Decimal(10) ** 12:
            out.skipped.append({"symbol": symbol, "description": description,
                                "reason": "no_quantity"})
            continue
        cost = number(cell(row, "cost_total"))
        if cost is None:
            avg = number(cell(row, "average_cost"))
            cost = avg * shares if avg is not None else None
        if cost is not None and (cost < 0 or cost >= Decimal(10) ** 15):
            cost = None
        price = number(cell(row, "last_price"))
        if price is not None and (price <= 0 or price >= Decimal(10) ** 9):
            price = None
        if len(out.positions) >= MAX_ROWS:
            raise NotAnExport(f"more than {MAX_ROWS} positions: import a smaller export")
        out.positions.append(Position(symbol, description, shares, cost, price,
                                      clean_label(cell(row, "account_name"))))
    if not out.positions and not out.skipped:
        raise NotAnExport("no positions found under the header")
    return out


def ticker_variants(symbol: str) -> list[str]:
    """Fidelity writes share classes with a dot (BRK.B); the SEC's tickers use a dash (BRK-B)."""
    out = [symbol]
    for alt in (symbol.replace(".", "-"), symbol.replace("/", "-"), symbol.replace(".", "")):
        if alt not in out:
            out.append(alt)
    return out
