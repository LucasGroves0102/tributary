"""Checks an answer's dollar figures and accession numbers against the tool results behind it.

A figure is verified when it equals a returned value at the precision written ($96.2B for
96,221,000,000), derived when it is the difference or sum of two values of one metric (across
quarters or companies), quoted
when it appears in returned filing text (including table cells, which SEC filings state in
millions), and unverified otherwise.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from itertools import combinations
from typing import Any

ACCESSION = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
UNITS = {"t": 1e12, "tn": 1e12, "trillion": 1e12, "b": 1e9, "bn": 1e9, "billion": 1e9,
         "m": 1e6, "mn": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3}
NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?"
FIGURE = re.compile(
    rf"\$\s?{NUMBER}(?:\s?(trillion|billion|million|thousand|tn|bn|mn|[tbmk])\b)?"
    rf"|(?<![\w.$,]){NUMBER}\s(trillion|billion|million)\b",
    re.IGNORECASE,
)
TABLE_CELL = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?![\d,]|\.\d)")
FRESHNESS = re.compile(r"fresh|as of|as_of", re.IGNORECASE)


@dataclass
class Figure:
    text: str
    value: float
    status: str  # verified | derived | quoted | unverified
    source: dict | None = None


@dataclass
class Verification:
    figures: list[Figure] = field(default_factory=list)
    unverified_accessions: list[str] = field(default_factory=list)
    used_data: bool = False
    freshness: bool = False

    @property
    def ok(self) -> bool:
        return (not self.unverified_accessions
                and all(f.status != "unverified" for f in self.figures)
                and (self.freshness or not self.used_data))

    def to_dict(self) -> dict:
        return {**asdict(self), "ok": self.ok}


def figures_in(text: str) -> list[tuple[str, float, float]]:
    """(as written, value, rounding step) for each dollar amount in text."""
    out = []
    for m in FIGURE.finditer(text):
        whole, frac, unit = (m.group(1), m.group(2), m.group(3)) if m.group(1) else \
            (m.group(4), m.group(5), m.group(6))
        scale = UNITS[unit.lower()] if unit else 1.0
        decimals = len(frac) if frac else 0
        value = float(whole.replace(",", "") + (f".{frac}" if frac else "")) * scale
        out.append((m.group(0).strip(), value, scale * 10 ** -decimals))
    return out


def verify(answer: str, tool_results: list[str], cited: set[str]) -> Verification:
    """tool_results: the raw content of every successful tool call; cited: accession numbers
    those results contain."""
    rows = [r for content in tool_results for r in _financial_rows(content)]
    texts = [_normalize(c) for c in tool_results]
    cells = sorted({float(n.replace(",", "")) * 1e6 for c in tool_results if '"sections"' in c
                    for n in TABLE_CELL.findall(c)})
    v = Verification(
        unverified_accessions=sorted({a for a in ACCESSION.findall(answer) if a not in cited}),
        used_data=bool(rows) or any('"sections"' in c for c in tool_results),
        freshness=bool(FRESHNESS.search(answer)),
    )
    for written, value, step in figures_in(answer):
        v.figures.append(_check(written, value, step, rows, texts, cells))
    return v


def _check(written: str, value: float, step: float, rows: list[dict], texts: list[str],
           cells: list[float]) -> Figure:
    tol = step / 2 * 1.0001
    target = abs(value)
    for r in rows:
        if abs(abs(r["value"]) - target) <= tol:
            return Figure(written, value, "verified", _source(r))
    by_metric: dict[str, list[dict]] = {}
    for r in rows:
        by_metric.setdefault(r["metric"], []).append(r)
    for metric, rs in by_metric.items():
        for a, b in combinations(rs, 2):
            for combined in (a["value"] - b["value"], a["value"] + b["value"]):
                if abs(abs(combined) - target) <= tol:
                    return Figure(written, value, "derived",
                                  {"metric": metric, "from": [_source(a), _source(b)]})
    if any(form in t for form in _spellings(written) for t in texts) or \
            any(abs(c - target) <= tol for c in cells):
        return Figure(written, value, "quoted")
    return Figure(written, value, "unverified")


def _financial_rows(content: str) -> list[dict]:
    try:
        data = json.loads(content)
    except ValueError:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return []
    company = data.get("company_id")
    return [{**r, "company_id": company, "value": float(r["value"])}
            for r in data["rows"] if isinstance(r, dict) and "value" in r and "metric" in r]


def _source(r: dict) -> dict:
    c: Any = r.get("citation") or {}
    return {"company_id": r.get("company_id"), "metric": r["metric"],
            "period_end": r.get("period_end"), "accession_no": c.get("accession_no")}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("$", ""))


def _spellings(written: str) -> set[str]:
    """The figure as filing text would write it: "$50.3B" -> {"50.3b", "50.3 billion"}."""
    plain = _normalize(written).strip()
    m = re.fullmatch(r"([\d,.]+)\s?([a-z]+)?", plain)
    if not m or not m.group(2):
        return {plain}
    word = {1e12: "trillion", 1e9: "billion", 1e6: "million", 1e3: "thousand"}[
        UNITS[m.group(2)]]
    return {plain, f"{m.group(1)} {word}"}
