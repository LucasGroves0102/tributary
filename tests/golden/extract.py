"""Extract the printed financial statements from a stored 10-K, for tests/golden.

Reads the visible text of the HTML (what a reader sees), never the inline XBRL tag names or
`ix:nonFraction` attributes, so the figures are independent of the XBRL the pipeline loads.
For each primary statement (income, balance sheet, cash flow) it picks the table the filing
prints, reads the scale from the table or the text just above it ("in millions"), and lists
every row as label + the numbers printed in it.

    python -I tests/golden/extract.py dump DIR/<accession>.htm ...   # every row, for review
    python -I tests/golden/extract.py csv DIR > candidates.csv          # FILINGS x MAPPING

`csv` reads DIR/<accession>.htm for each filing in FILINGS and writes candidate rows for
`10k_figures.csv`. The committed CSV is that output after review against the filing; tests
never re-run extraction. Documents come from bronze.filing_documents (content_gzip is gzip of
the HTML); see README.md.
"""

from __future__ import annotations

import csv
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from lxml import html

STATEMENTS = {
    # statement → (title pattern, row labels that must all appear)
    "income": (r"statements? of (consolidated )?(income|earnings|operations)",
               [r"per (common )?share|earnings per", r"net income|net earnings"]),
    "balance": (r"balance sheets?|statements? of financial (position|condition)",
                [r"^total assets", r"total liabilities"]),
    "cash_flow": (r"statements? of (consolidated )?cash flows?|cash flows? statements?",
                  [r"operating activities", r"investing activities", r"financing activities"]),
}
COLUMNS = {"income": 3, "balance": 2, "cash_flow": 3}  # fiscal years printed, latest first

NUMBER = re.compile(r"\(?\s*\$?\s*\d[\d,]*(?:\.\d+)?\s*\)?|—|–(?=\s|$)|-(?=\s|$)")
SCALE = re.compile(r"\bin (millions|thousands|billions)\b|\b(millions|thousands|billions) of "
                   r"dollars\b", re.I)
SCALES = {"thousands": 1_000, "millions": 1_000_000, "billions": 1_000_000_000}


@dataclass
class Row:
    label: str
    values: list[Decimal]


@dataclass
class Statement:
    kind: str
    scale: int
    rows: list[Row]

    def row(self, pattern: str, nth: int = 0) -> Row:
        hits = [r for r in self.rows if re.search(pattern, r.label, re.I) and r.values]
        if len(hits) <= nth:
            raise LookupError(f"{self.kind}: no row matching {pattern!r}")
        return hits[nth]

    def value(self, row: Row, column: int) -> Decimal:
        """Printed value of a fiscal-year column (0 = latest). Note-reference columns
        (Exxon prints one before the years) come first, so count from the right."""
        n = COLUMNS[self.kind]
        if len(row.values) < n:
            raise LookupError(f"{self.kind}: {row.label!r} has {row.values}, want {n} columns")
        return row.values[len(row.values) - n + column]


def text(el) -> str:
    return re.sub(r"\s+", " ", el.text_content().replace("\xa0", " ")).strip()


def cells_text(table) -> str:
    return " ".join(text(td) for td in table.iter("td", "th"))


def parse_number(s: str) -> Decimal | None:
    s = s.strip()
    if s in {"—", "–", "-"}:
        return Decimal(0)
    digits = re.sub(r"[^\d.]", "", s)
    if not digits:
        return None
    return -Decimal(digits) if s.startswith("(") else Decimal(digits)


def table_rows(table) -> list[Row]:
    rows = []
    for tr in table.iter("tr"):
        cells = [c for c in (text(td) for td in tr if td.tag in ("td", "th")) if c]
        if not cells:
            continue
        label = cells[0]
        if NUMBER.fullmatch(label) or label == "$":
            label = ""
        # "$ 1,234" or "(56" ")" split across cells: join, then read the numbers in order
        rest = " ".join(cells[1:] if label else cells)
        rest = re.sub(r"\(\s+", "(", rest).replace(" )", ")")
        values = [parse_number(m.group()) for m in NUMBER.finditer(rest)]
        rows.append(Row(label, [v for v in values if v is not None]))
    return rows


def preceding_text(el, limit: int = 1500) -> str:
    """Text just before the element in document order (headings, 'in millions')."""
    parts: list[str] = []
    size = 0
    node = el
    while node is not None and size < limit:
        for sib in node.itersiblings(preceding=True):
            t = text(sib)
            parts.append(t)
            size += len(t)
            if size >= limit:
                break
        node = node.getparent()
    return " ".join(reversed(parts))[-limit:]


@dataclass
class Document:
    statements: dict[str, Statement]
    text: str  # whole visible text, for figures printed in notes


def read(path: Path) -> Document:
    doc = html.fromstring(path.read_bytes())
    found: dict[str, tuple[int, Statement]] = {}
    for table in doc.iter("table"):
        rows = table_rows(table)
        labels = [r.label.lower() for r in rows]
        numeric = sum(1 for r in rows if r.values)
        if numeric < 8:
            continue
        before = preceding_text(table)
        for kind, (title, required) in STATEMENTS.items():
            if not all(any(re.search(p, label) for label in labels) for p in required):
                continue
            # the statement itself is titled just above; MD&A summaries are not
            titled = re.search(title, before[-600:].lower()) is not None
            score = numeric + (1000 if titled else 0)
            if kind in found and found[kind][0] >= score:
                continue
            hits = SCALE.findall(cells_text(table)[:600]) or SCALE.findall(before)
            scale = SCALES[next(h for h in hits[-1] if h).lower()] if hits else 1
            found[kind] = (score, Statement(kind, scale, rows))
    body = " ".join(t.strip() for t in doc.itertext() if t.strip())
    return Document({k: v[1] for k, v in found.items()}, re.sub(r"\s+", " ", body))


@dataclass
class Filing:
    company: str
    ticker: str
    cik: str
    fiscal_year: int
    period_end: str
    prior_end: str  # the comparative year printed next to it


FILINGS = {
    "0001045810-26-000021": Filing("NVIDIA", "NVDA", "0001045810", 2026, "2026-01-25",
                                   "2025-01-26"),
    "0000909832-26-000093": Filing("Costco", "COST", "0000909832", 2026, "2026-08-30",
                                   "2025-08-31"),
    "0001628280-26-008131": Filing("JPMorgan Chase", "JPM", "0000019617", 2025, "2025-12-31",
                                   "2024-12-31"),
    "0000034088-26-000045": Filing("Exxon Mobil", "XOM", "0000034088", 2025, "2025-12-31",
                                   "2024-12-31"),
    "0001628280-26-019436": Filing("Home Depot", "HD", "0000354950", 2025, "2026-02-01",
                                   "2025-02-02"),
}


@dataclass
class Line:
    """One printed figure: a statement row (column 0 = latest year, 1 = prior year), or a
    custom reader for figures printed in captions or notes.

    sign -1 turns a printed outflow "(1,234)" into the positive amount gold stores (capex,
    dividends, buybacks, Nvidia's interest expense). check: annual (gold equals it),
    annual_silver (EPS and weighted shares: gold keeps quarters only, so silver's fiscal-year
    fact from this 10-K must equal it), absent (printed, but gold has no value: a known gap,
    asserted so a fix shows up)."""
    metric: str
    statement: str
    pattern: str = ""
    nth: int = 0
    column: int = 0
    sign: int = 1
    unit: str = "USD"
    printed_unit: str = ""  # size of the last printed digit; default: the statement scale
    scale: int = 0  # overrides the statement scale (share counts printed in thousands)
    check: str = "annual"
    note: str = ""
    custom: Callable[[Document], tuple[Decimal, str]] | None = None


def caption(statement: str, row: str, number: str, scale: int):
    """A number printed inside a row label, e.g. '24,304 shares issued and outstanding'."""
    def read_caption(doc: Document) -> tuple[Decimal, str]:
        label = doc.statements[statement].row(row).label
        m = re.search(number, label)
        if not m:
            raise LookupError(f"{number!r} not in {label!r}")
        return Decimal(m.group(1).replace(",", "")) * scale, label
    return read_caption


def in_text(pattern: str, scale: int):
    """A number printed in running text or a note table."""
    def read_text(doc: Document) -> tuple[Decimal, str]:
        m = re.search(pattern, doc.text)
        if not m:
            raise LookupError(f"{pattern!r} not in the document text")
        return Decimal(m.group(1).replace(",", "")) * scale, m.group(0)
    return read_text


def issued_less_treasury(issued: str, treasury: str, scale: int):
    def read_shares(doc: Document) -> tuple[Decimal, str]:
        b = doc.statements["balance"]
        i_label, t_label = b.row(issued[0]).label, b.row(treasury[0]).label
        i = re.search(issued[1], i_label)
        t = re.search(treasury[1], t_label)
        if not (i and t):
            raise LookupError("share counts not in captions")
        value = (Decimal(i.group(1).replace(",", "")) - Decimal(t.group(1).replace(",", "")))
        return value * scale, f"{i_label} | {t_label}"
    return read_shares


M = 1_000_000
NI_GROWTH_INPUTS = [  # prior-year comparatives: inputs of revenue/net income growth and ROE
    ("revenue", "income"), ("net_income", "income"), ("stockholders_equity", "balance")]

MAPPING: dict[str, list[Line]] = {
    "NVDA": [
        Line("revenue", "income", r"^revenue$"),
        Line("cost_of_revenue", "income", r"^cost of revenue$"),
        Line("gross_profit", "income", r"^gross profit$"),
        Line("research_and_development", "income", r"^research and development$"),
        Line("sga", "income", r"^sales, general and administrative$"),
        Line("operating_income", "income", r"^operating income$"),
        Line("interest_expense", "income", r"^interest expense$", sign=-1,
             note="printed as a negative in other income; gold stores the expense as positive"),
        Line("pretax_income", "income", r"^income before income tax$"),
        Line("income_tax", "income", r"^income tax expense$"),
        Line("net_income", "income", r"^net income$"),
        Line("eps_basic", "income", r"^basic$", 0, unit="USD/shares", printed_unit="0.01",
             check="annual_silver"),
        Line("eps_diluted", "income", r"^diluted$", 0, unit="USD/shares", printed_unit="0.01",
             check="annual_silver"),
        Line("weighted_shares_basic", "income", r"^basic$", 1, unit="shares",
             check="annual_silver"),
        Line("weighted_shares_diluted", "income", r"^diluted$", 1, unit="shares",
             check="annual_silver"),
        Line("cash", "balance", r"^cash and cash equivalents$"),
        Line("short_term_investments", "balance", r"^marketable securities$", check="absent",
             note="FY2026 10-K tags this line with Nvidia's own extension "
                  "(nvda:MarketableSecuritiesAndEquitySecuritiesFVNI); gold maps us-gaap tags "
                  "only, so there is no value and net_debt treats it as 0"),
        Line("receivables", "balance", r"^accounts receivable, net$"),
        Line("inventory", "balance", r"^inventories$"),
        Line("current_assets", "balance", r"^total current assets$"),
        Line("total_assets", "balance", r"^total assets$"),
        Line("current_liabilities", "balance", r"^total current liabilities$"),
        Line("long_term_debt", "balance", r"^long-term debt$"),
        Line("long_term_debt_current", "balance", r"^short-term debt$",
             note="printed as Short-term debt; it is the current portion of long-term notes "
                  "(LongTermDebtCurrent)"),
        Line("total_liabilities", "balance", r"^total liabilities$"),
        Line("stockholders_equity", "balance", r"^total shareholders.? equity$"),
        Line("shares_outstanding", "balance", unit="shares", check="annual",
             custom=caption("balance", r"^common stock",
                            r"([\d,]+) shares issued and outstanding", M),
             note="balance-sheet caption, in millions of shares"),
        Line("operating_cash_flow", "cash_flow", r"^net cash provided by operating"),
        Line("capex", "cash_flow", r"^purchases related to property and equipment", sign=-1,
             note="includes intangible assets (PaymentsToAcquireProductiveAssets)"),
        Line("depreciation_amortization", "cash_flow", r"^depreciation and amortization$"),
        Line("stock_based_compensation", "cash_flow", r"^stock-based compensation expense$"),
        Line("dividends_paid", "cash_flow", r"^dividends paid$", sign=-1),
        Line("share_buybacks", "cash_flow", r"^payments related to repurchases of common stock",
             sign=-1),
    ],
    "COST": [
        Line("revenue", "income", r"^total revenue$",
             note="net sales plus membership fees"),
        Line("cost_of_revenue", "income", r"^merchandise costs$"),
        Line("sga", "income", r"^selling, general and administrative$"),
        Line("operating_income", "income", r"^operating income$"),
        Line("interest_expense", "income", r"^interest expense$", sign=-1,
             note="printed as a negative under other income (expense)"),
        Line("pretax_income", "income", r"^income before income taxes$"),
        Line("income_tax", "income", r"^provision for income taxes$"),
        Line("net_income", "income", r"^net income$"),
        Line("eps_basic", "income", r"^basic$", 0, unit="USD/shares", printed_unit="0.01",
             check="annual_silver"),
        Line("eps_diluted", "income", r"^diluted$", 0, unit="USD/shares", printed_unit="0.01",
             check="annual_silver"),
        Line("weighted_shares_basic", "income", r"^basic$", 1, unit="shares", scale=1000,
             check="annual_silver", note="printed in thousands (000's)"),
        Line("weighted_shares_diluted", "income", r"^diluted$", 1, unit="shares", scale=1000,
             check="annual_silver", note="printed in thousands (000's)"),
        Line("cash", "balance", r"^cash and cash equivalents$"),
        Line("short_term_investments", "balance", r"^short-term investments$"),
        Line("receivables", "balance", r"^receivables, net$"),
        Line("inventory", "balance", r"^merchandise inventories$"),
        Line("current_assets", "balance", r"^total current assets$"),
        Line("total_assets", "balance", r"^total assets$"),
        Line("current_liabilities", "balance", r"^total current liabilities$"),
        Line("long_term_debt", "balance", r"^long-term debt, excluding current portion$"),
        Line("long_term_debt_current", "balance", r"^current portion of long-term debt$"),
        Line("total_liabilities", "balance", r"^total liabilities$"),
        Line("stockholders_equity", "balance", r"^total equity$",
             note="no noncontrolling interests at this date, so total equity = Costco "
                  "stockholders' equity"),
        Line("shares_outstanding", "balance", unit="shares", printed_unit="1",
             custom=caption("balance", r"^common stock",
                            r"([\d,]+) and [\d,]+ shares issued and outstanding", 1),
             note="balance-sheet caption"),
        Line("operating_cash_flow", "cash_flow", r"^net cash provided by operating"),
        Line("capex", "cash_flow", r"^additions to property and equipment$", sign=-1),
        Line("depreciation_amortization", "cash_flow", r"^depreciation and amortization$"),
        Line("stock_based_compensation", "cash_flow", r"^stock-based compensation$"),
        Line("dividends_paid", "cash_flow", r"^cash dividend payments$", sign=-1),
        Line("share_buybacks", "cash_flow", r"^repurchases of common stock$", sign=-1),
    ],
    "JPM": [
        Line("revenue", "income", r"^total net revenue$",
             note="bank revenue net of interest expense (RevenuesNetOfInterestExpense)"),
        Line("interest_expense", "income", r"^interest expense$",
             note="bank interest expense, mostly on deposits (InterestExpenseOperating)"),
        Line("pretax_income", "income", r"^income before income tax expense$"),
        Line("income_tax", "income", r"^income tax expense$"),
        Line("net_income", "income", r"^net income$"),
        Line("eps_basic", "income", r"^basic earnings per share$", unit="USD/shares",
             printed_unit="0.01", check="annual_silver"),
        Line("eps_diluted", "income", r"^diluted earnings per share$", unit="USD/shares",
             printed_unit="0.01", check="annual_silver"),
        Line("weighted_shares_basic", "income", r"^weighted-average basic shares$",
             unit="shares", printed_unit="100000", check="annual_silver",
             note="printed in millions with one decimal"),
        Line("weighted_shares_diluted", "income", r"^weighted-average diluted shares$",
             unit="shares", printed_unit="100000", check="annual_silver",
             note="printed in millions with one decimal"),
        Line("total_assets", "balance", r"^total assets"),
        Line("long_term_debt", "balance", r"^long-term debt", check="absent",
             note="unclassified bank balance sheet: no noncurrent debt tag, so gold has no "
                  "long_term_debt for JPM by design (012); debt ratios are not computed"),
        Line("total_liabilities", "balance", r"^total liabilities"),
        Line("stockholders_equity", "balance", r"^total stockholders. equity$"),
        Line("shares_outstanding", "note", unit="shares", printed_unit="100000",
             custom=in_text(r"Outstanding at December 31 ([\d,]+\.\d)", M),
             note="common stock note, millions with one decimal (issued 4,104.9 less treasury "
                  "1,408.7)"),
        Line("cash", "cash_flow", r"^cash and due from banks and deposits with banks at the end",
             note="gold's cash for JPM is cash and due from banks plus deposits with banks "
                  "(the restricted-cash total); the balance sheet prints them as two lines, "
                  "Cash and due from banks 21,742 and Deposits with banks 321,596"),
        Line("operating_cash_flow", "cash_flow", r"^net cash .*operating activities$"),
        Line("depreciation_amortization", "cash_flow", r"^depreciation and amortization$"),
        Line("dividends_paid", "cash_flow", r"^dividends paid$", sign=-1,
             note="common and preferred dividends (PaymentsOfDividends)"),
        Line("share_buybacks", "cash_flow", r"^treasury stock repurchased$", sign=-1),
    ],
    "XOM": [
        Line("revenue", "income", r"^total revenues and other income$",
             note="the Revenues tag is total revenues and other income (includes income from "
                  "equity affiliates and other income); sales and other operating revenue is "
                  "323,905"),
        Line("sga", "income", r"^selling, general and administrative expenses$"),
        Line("interest_expense", "income", r"^interest expense$"),
        Line("pretax_income", "income", r"^income \(loss\) before income taxes$"),
        Line("income_tax", "income", r"^income tax expense \(benefit\)$"),
        Line("net_income", "income", r"^net income \(loss\) attributable to exxonmobil$"),
        Line("eps_basic", "income", r"^earnings \(loss\) per common share \(dollars\)$",
             unit="USD/shares", printed_unit="0.01", check="annual_silver"),
        Line("eps_diluted", "income", r"assuming dilution", unit="USD/shares",
             printed_unit="0.01", check="annual_silver"),
        Line("cash", "balance", r"^cash and cash equivalents$"),
        Line("receivables", "balance", r"^notes and accounts receivable"),
        Line("current_assets", "balance", r"^total current assets$"),
        Line("total_assets", "balance", r"^total assets$"),
        Line("current_liabilities", "balance", r"^total current liabilities$"),
        Line("long_term_debt", "balance", r"^long-term debt$"),
        Line("long_term_debt_current", "note", printed_unit="100000000",
             custom=in_text(r"portion of long-term debt, totaling \$ ?([\d.]+) billion, which "
                            r"matures within one year", 1_000_000_000),
             note="not a balance-sheet line (inside notes and loans payable); printed in the "
                  "long-term debt note as $6.2 billion"),
        Line("total_liabilities", "balance", r"^total liabilities$"),
        Line("stockholders_equity", "balance", r"^exxonmobil share of equity$",
             note="excludes noncontrolling interests (total equity 266,626)"),
        Line("shares_outstanding", "balance", unit="shares",
             custom=issued_less_treasury(
                 (r"^common stock without par value", r"([\d,]+) million shares issued"),
                 (r"^common stock held in treasury", r"([\d,]+) million shares in 2025"), M),
             note="balance-sheet captions: 8,019 million issued less 3,840 million in treasury"),
        Line("operating_cash_flow", "cash_flow", r"^net cash provided by operating"),
        Line("capex", "cash_flow", r"^additions to property, plant, and equipment$", sign=-1),
        Line("depreciation_amortization", "cash_flow", r"^depreciation and depletion",
             note="includes impairments"),
        Line("dividends_paid", "cash_flow", r"^cash dividends to exxonmobil shareholders$",
             sign=-1),
        Line("share_buybacks", "cash_flow", r"^common stock acquired$", sign=-1),
    ],
    "HD": [
        Line("revenue", "income", r"^net sales$"),
        Line("cost_of_revenue", "income", r"^cost of sales$"),
        Line("gross_profit", "income", r"^gross profit$"),
        Line("sga", "income", r"^selling, general and administrative$"),
        Line("operating_income", "income", r"^operating income$"),
        Line("interest_expense", "income", r"^interest expense$"),
        Line("pretax_income", "income", r"^earnings before provision for income taxes$"),
        Line("income_tax", "income", r"^provision for income taxes$"),
        Line("net_income", "income", r"^net earnings$"),
        Line("eps_basic", "income", r"^basic earnings per share$", unit="USD/shares",
             printed_unit="0.01", check="annual_silver"),
        Line("eps_diluted", "income", r"^diluted earnings per share$", unit="USD/shares",
             printed_unit="0.01", check="annual_silver"),
        Line("weighted_shares_basic", "income", r"^basic weighted average common shares$",
             unit="shares", check="annual_silver"),
        Line("weighted_shares_diluted", "income", r"^diluted weighted average common shares$",
             unit="shares", check="annual_silver"),
        Line("cash", "balance", r"^cash and cash equivalents$"),
        Line("receivables", "balance", r"^receivables, net$"),
        Line("inventory", "balance", r"^merchandise inventories$"),
        Line("current_assets", "balance", r"^total current assets$"),
        Line("total_assets", "balance", r"^total assets$"),
        Line("current_liabilities", "balance", r"^total current liabilities$"),
        Line("long_term_debt", "balance", r"^long-term debt, excluding current installments$"),
        Line("long_term_debt_current", "balance", r"^current installments of long-term debt$"),
        Line("total_liabilities", "balance", r"^total liabilities$"),
        Line("stockholders_equity", "balance", r"^total stockholders. equity$"),
        Line("shares_outstanding", "balance", unit="shares",
             custom=caption("balance", r"^common stock", r"outstanding: ([\d,]+) shares", M),
             note="balance-sheet caption, in millions of shares"),
        Line("operating_cash_flow", "cash_flow", r"^net cash provided by operating"),
        Line("capex", "cash_flow", r"^capital expenditures$", sign=-1),
        Line("depreciation_amortization", "cash_flow",
             r"^depreciation and amortization, excluding amortization of intangible",
             note="the cash-flow line (DepreciationDepletionAndAmortization); the income "
                  "statement's Depreciation and amortization (3,273) excludes depreciation in "
                  "cost of sales, and intangible amortization (607) is a separate line"),
        Line("stock_based_compensation", "cash_flow", r"^stock-based compensation expense$"),
        Line("dividends_paid", "cash_flow", r"^cash dividends$", sign=-1),
        Line("share_buybacks", "cash_flow", r"^repurchases of common stock$", sign=-1,
             note="printed as a dash: no repurchases in fiscal 2025"),
    ],
}

HEADER = ["company", "ticker", "cik", "fiscal_year", "period_end", "metric", "value", "unit",
          "printed_unit", "statement", "line_label", "check", "accession_no", "verified_by",
          "notes"]


def rows_for(accession: str, filing: Filing, doc: Document):
    lines = list(MAPPING[filing.ticker])
    prior = []
    for metric, statement in NI_GROWTH_INPUTS:
        line = next((x for x in lines if x.metric == metric), None)
        if line and not line.custom:
            prior.append(Line(metric, statement, line.pattern, line.nth, 1, line.sign,
                              note="prior-year comparative in this 10-K: year-ago input of "
                                   "growth and ROE"))
    for line in lines + prior:
        if line.custom:
            value, label = line.custom(doc)
            printed_unit = line.printed_unit or "1000000"
        else:
            st = doc.statements[line.statement]
            row = st.row(line.pattern, line.nth)
            scale = line.scale or (1 if line.unit == "USD/shares" else st.scale)
            value = st.value(row, line.column) * scale * line.sign
            label = row.label
            printed_unit = line.printed_unit or str(scale)
        fiscal_year = filing.fiscal_year - line.column
        period_end = filing.period_end if line.column == 0 else filing.prior_end
        shown = format(value, "f") if line.unit == "USD/shares" else str(int(value))
        yield [filing.company, filing.ticker, filing.cik, fiscal_year, period_end, line.metric,
               shown, line.unit, printed_unit, line.statement, label, line.check, accession,
               "", line.note]


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "dump":
        for path in map(Path, argv[1:]):
            for kind, st in read(path).statements.items():
                print(f"## {path.stem} {kind} scale={st.scale}")
                for row in st.rows:
                    print(f"   {row.label[:90]!r:95} {[str(v) for v in row.values]}")
        return 0
    if cmd == "csv":
        out = csv.writer(sys.stdout, lineterminator="\n")
        out.writerow(HEADER)
        for accession, filing in FILINGS.items():
            doc = read(Path(argv[1]) / f"{accession}.htm")
            out.writerows(rows_for(accession, filing, doc))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
