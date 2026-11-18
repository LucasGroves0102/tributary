"""Candidate rows for tests/golden/reference_set.csv, read from stored 10-Ks (README, "Reference
set").

Each document is parsed with the parser's own v7 rules (filing_parser.sections: the visible text
and its tables, never inline XBRL) and read with filing_parser.statements' table reader (rules 1-4:
cells, periods, the current column, the scale) under this file's label patterns, one per
reference metric. The statement's own table comes first: titled tables (rule 6) before the rest,
then document order. Every candidate row is then reviewed against the 10-K's own XBRL facts
(`review`, from the company's companyfacts payload) and by hand where they disagree or nothing
was read; tests never re-run this, the reviewed CSV is the reference.

    python tests/golden/extract_reference.py csv COMPANIES DOCS > candidates.csv
    python tests/golden/extract_reference.py review candidates.csv PAYLOADS > review.txt

COMPANIES: ticker|company_id|name|sic|accession|filed|period_end lines; DOCS: <accession>.htm
files (bronze.filing_documents' body decompressed: content_gzip, or its file in the body store
since migration 150); PAYLOADS: CIK<cik>.json.gz files
(bronze.company_facts.raw_gzip), the company's CIKs in a `ciks` file beside them
(company_id|cik lines).
"""

from __future__ import annotations

import csv
import gzip
import json
import re
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from filing_parser.sections import parse
from filing_parser.statements import _PATTERNS, Line, read

OWNER = r"(?:stockholders|shareholders|shareowners|share owners|owners|members|partners)'?"
PATTERNS: dict[str, re.Pattern] = {
    "revenue": _PATTERNS["revenue"],
    "net_income": _PATTERNS["net_income"],
    "eps_diluted": re.compile(
        r"^.*(?:earnings|income|loss|eps|profit).*per.*share.* - diluted$"
        r"|^diluted (?:net )?(?:earnings|income|loss|\(loss\) income|income \(loss\)|"
        r"\(loss\) earnings|earnings \(loss\))(?: \(loss\))? per (?:common |ordinary )?share.*$"
        r"|^(?:net )?(?:earnings|income|loss|\(loss\) income|income \(loss\)|earnings \(loss\))"
        r"(?: \(loss\))? per (?:common |ordinary )?share,? ?(?:-|—|–)? ?(?:assuming )?dilut.*$"
        r"|^diluted(?: eps| earnings per share)?$"),
    "total_assets": _PATTERNS["total_assets"],
    "total_liabilities": re.compile(r"^total liabilities$"),
    "stockholders_equity": re.compile(
        rf"^total (?!liabilities)(?:.+ )?{OWNER} (?:equity|investment|capital)(?: \(deficit\))?$"
        rf"|^total {OWNER} (?:equity|deficit)(?: \(deficit\))?$|^total equity(?: \(deficit\))?$"
        r"|^.+ share of equity$"),
    "cash": re.compile(r"^cash and (?:cash )?equivalents(?:, at carrying value)?$"
                       r"|^cash and due from banks$"),
    "long_term_debt": re.compile(
        r"^long-term (?:debt|borrowings)(?:,? (?:net|less current (?:portion|maturities)|"
        r"excluding current (?:portion|installments|maturities)|non-?current|"
        r"net of current portion|due after one year|less current portion))*"
        r"(?: \([^)]*\))?$"),
    "long_term_debt_current": re.compile(
        r"^(?:current (?:portion|maturities|installments) of long-term (?:debt|borrowings).*"
        r"|short-term (?:debt|borrowings)(?: and current (?:portion|maturities) of long-term debt)?"
        r"|debt due within one year|current portion of debt|short-term debt and current portion of"
        r" long-term debt|notes payable and current portion of long-term debt)$"),
    "operating_cash_flow": re.compile(
        r"^net cash (?:provided by|from|provided by \(used (?:in|for)\)|\(used in\) provided by|"
        r"used in|generated (?:by|from)|provided from|flows? (?:from|provided by)) "
        r"(?:\(used (?:in|for)\) )?operating activities(?: of continuing operations)?$"
        r"|^cash (?:provided by|from) (?:operating activities|operations)$"),
    "capex": re.compile(
        r"^(?:purchases? of|additions to|payments for|acquisitions? of|expenditures for|"
        r"capital expenditures for|investments? in|purchases related to) (?:property|premises|"
        r"plant|fixed assets|capital assets)(?:.*)?$|^capital expenditures$"),
}
DURATIONS = frozenset({"revenue", "net_income", "eps_diluted", "operating_cash_flow", "capex"})
STATEMENT = {"revenue": "income", "net_income": "income", "eps_diluted": "income",
             "operating_cash_flow": "cash_flow", "capex": "cash_flow"}
OUTFLOWS = frozenset({"capex"})  # printed "(6,042)": gold stores the amount paid, positive
# gold's tags per metric, in priority order (silver.metric_tags), for the review
GOLD_TAGS = {
    "revenue": ["RevenuesNetOfInterestExpense", "Revenues",
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                "RegulatedAndUnregulatedOperatingRevenue"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "stockholders_equity": ["StockholdersEquity",
                            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
             "CashAndDueFromBanks", "Cash"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations",
                       "LongTermDebt", "LongTermNotesPayable", "LongTermNotesAndLoans"],
    "long_term_debt_current": ["LongTermDebtCurrent",
                               "LongTermDebtAndCapitalLeaseObligationsCurrent", "DebtCurrent",
                               "NotesPayableCurrent"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsToAcquireOtherPropertyPlantAndEquipment", "PaymentsForCapitalImprovements"],
}
HEADER = ["company", "ticker", "company_id", "fiscal_year", "period_end", "metric", "value",
          "unit", "printed_unit", "statement", "line_label", "check", "accession_no", "notes"]


def candidates(raw: bytes, period: date) -> dict[str, list[Line]]:
    tables = [(s.key, n, _rows(t), s.text[max(0, t.start - 600):t.start])
              for s in parse(raw, "10-K") for n, t in enumerate(s.tables)]
    by: dict[str, list[Line]] = defaultdict(list)
    for x in read(tables, "10-K", period, patterns=PATTERNS, durations=DURATIONS):
        by[x.line].append(x)
    return {k: sorted(v, key=rank) for k, v in by.items()}


def rank(x: Line) -> tuple[bool, bool]:
    """Titled tables first, then Item 8's (the statements) before other sections'."""
    return (not x.titled, x.section_key not in ("8", "15", "16"))


def _rows(t) -> list[dict]:
    from filing_parser.db import table_rows
    return table_rows(t)


def printed(x: Line, metric: str) -> tuple[Decimal, int]:
    """(the figure gold should hold, the size of its last printed digit)."""
    if metric == "eps_diluted":
        v = x.value / (x.scale or 1)
        return v, 0
    v = -x.value if metric in OUTFLOWS else x.value
    return v, x.scale or 1


def csv_rows(companies: Path, docs: Path):
    for line in companies.read_text().splitlines():
        ticker, cid, name, _sic, acc, _filed, period = line.split("|")
        end = date.fromisoformat(period)
        found = candidates((docs / f"{acc}.htm").read_bytes(), end)
        for metric in PATTERNS:
            for k, x in enumerate(found.get(metric, [])[:3]):
                value, unit = printed(x, metric)
                yield [name, ticker, cid, end.year, period, metric, format(value, "f"),
                       "USD/shares" if metric == "eps_diluted" else "USD",
                       "0.01" if metric == "eps_diluted" else str(unit),
                       STATEMENT.get(metric, "balance"), x.label, "annual" if k == 0 else "alt",
                       acc, f"table {x.section_key}#{x.table_no} row {x.row_no}"
                            f"{'' if x.titled else ' (untitled)'}"]


def review(candidates_csv: Path, payloads: Path):
    ciks: dict[str, list[str]] = defaultdict(list)
    for line in (payloads / "ciks").read_text().splitlines():
        cid, cik = line.split("|")[:2]
        ciks[cid].append(cik)
    cache: dict[str, dict] = {}
    for r in csv.DictReader(candidates_csv.open()):
        cid, acc, end = r["company_id"], r["accession_no"], date.fromisoformat(r["period_end"])
        facts = []
        for cik in ciks[cid]:
            if cik not in cache:
                cache[cik] = json.loads(gzip.decompress((payloads / f"CIK{cik}.json.gz")
                                                        .read_bytes()))
            for tag in GOLD_TAGS[r["metric"]]:
                c = cache[cik].get("facts", {}).get("us-gaap", {}).get(tag)
                for unit, rows in (c or {}).get("units", {}).items():
                    for f in rows:
                        e = date.fromisoformat(f["end"])
                        if f.get("accn") == acc and abs((e - end).days) <= 7 and (
                                "start" not in f
                                or (e - date.fromisoformat(f["start"])).days > 300):
                            facts.append((tag, unit, Decimal(str(f["val"]))))
        v = Decimal(r["value"])
        half = Decimal(r["printed_unit"]) / 2 or Decimal("0.005")
        hit = [f for f in facts if abs(f[2] - v) <= half]
        status = "xbrl" if hit else ("no-xbrl" if not facts else "DIFF")
        yield status, r, hit, facts


def main(argv: list[str]) -> int:
    if argv[:1] == ["csv"]:
        out = csv.writer(sys.stdout, lineterminator="\n")
        out.writerow(HEADER)
        out.writerows(csv_rows(Path(argv[1]), Path(argv[2])))
        return 0
    if argv[:1] == ["review"]:
        for status, r, hit, facts in review(Path(argv[1]), Path(argv[2])):
            print(status, r["ticker"], r["metric"], r["check"], r["value"], repr(r["line_label"]),
                  r["notes"], [(t, str(v)) for t, _, v in (hit or facts)[:3]])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
