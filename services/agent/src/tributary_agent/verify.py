"""Checks an answer's dollar figures and accession numbers against the tool results behind it.

A figure is, in this order:

1. verified when it equals a returned value at the precision written ($96.2B for
   96,221,000,000);
2. derived when it is the difference or sum of two returned values of one metric (across
   quarters or companies);
3. quoted when it appears in returned filing text: as written ("$50.3 billion"), or as a table
   cell (a number with thousands separators, "82,886") read at the scale the text states;
4. verified when it equals one of a derived metric's inputs (`inputs` on a /financials row:
   net debt's cash and debt, a growth rate's year-ago value, a TTM's four quarters), or derived
   from them as in 2;
5. unverified otherwise.

Table cells: a result whose text says "(in thousands" / "(in millions" / "(in billions" (also
"($ in millions)", "(Dollars in thousands)") is read at each scale it states; text that states
none is read in millions (SEC filings mostly are). A cell may carry decimals ("1,234.5") at a
stated scale. A filing in thousands doesn't read as millions: its "46,743" is $46.7M, never
$46.7B.

Rule 4 and the decimal and stated-scale cells came after the recorded suites (tests/adversarial,
tests/citations): they run only for a figure 1-3 left unverified, so a recorded answer verifies
exactly as before. Nothing is loosened: every rule still needs the number itself in what the
tools returned.
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
# a cell with decimals ("1,234.5"): read only at a scale the text states
DECIMAL_CELL = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+\.\d+(?![\d,]|\.\d)")
# "(In millions, except per share data)", "($ in thousands)", "(Dollars in millions)"
SCALE_NOTE = re.compile(r"\([^()]{0,30}?\bin\s+(thousands|millions|billions)\b", re.IGNORECASE)
SCALES = {"thousands": 1e3, "millions": 1e6, "billions": 1e9}
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


@dataclass
class _Evidence:
    rows: list[dict]
    texts: list[str]
    cells: list[float]  # table cells in millions where no other scale is stated (rule 3)
    scaled: list[float]  # cells at a stated scale, decimals too (rule 3, after the suites)
    inputs: list[dict]  # derived metrics' inputs (rule 4)


def verify(answer: str, tool_results: list[str], cited: set[str]) -> Verification:
    """tool_results: the raw content of every successful tool call; cited: accession numbers
    those results contain."""
    sections = [c for c in tool_results if '"sections"' in c]
    ev = _Evidence(
        rows=[r for content in tool_results for r in _financial_rows(content)],
        texts=[_normalize(c) for c in tool_results],
        cells=sorted({float(n.replace(",", "")) * 1e6 for c in sections if _in_millions(c)
                      for n in TABLE_CELL.findall(c)}),
        scaled=sorted({float(n.replace(",", "")) * scale for c in sections
                       for scale in _stated_scales(c)
                       for n in [*TABLE_CELL.findall(c), *DECIMAL_CELL.findall(c)]}),
        inputs=_dedupe([r for content in tool_results for r in _input_rows(content)]),
    )
    v = Verification(
        unverified_accessions=sorted({a for a in ACCESSION.findall(answer) if a not in cited}),
        used_data=bool(ev.rows) or bool(sections),
        freshness=bool(FRESHNESS.search(answer)),
    )
    for written, value, step in figures_in(answer):
        v.figures.append(_check(written, value, step, ev))
    return v


def _check(written: str, value: float, step: float, ev: _Evidence) -> Figure:
    tol = step / 2 * 1.0001
    target = abs(value)

    def close(v: float) -> bool:
        return abs(abs(v) - target) <= tol

    for r in ev.rows:
        if close(r["value"]):
            return Figure(written, value, "verified", _source(r))
    pair = _pair(_by_metric(ev.rows), close)
    if pair:
        return Figure(written, value, "derived", pair)
    if any(form in t for form in _spellings(written) for t in ev.texts) or \
            any(close(c) for c in ev.cells):
        return Figure(written, value, "quoted")
    # added after the recorded suites: only what 1-3 left unverified gets here
    for r in ev.inputs:
        if close(r["value"]):
            return Figure(written, value, "verified", _source(r))
    if any(close(c) for c in ev.scaled):
        return Figure(written, value, "quoted")
    if ev.inputs:
        pair = _pair(_by_metric([*ev.rows, *ev.inputs]), close)
        if pair:
            return Figure(written, value, "derived", pair)
    return Figure(written, value, "unverified")


def _by_metric(rows: list[dict]) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r.get("base") or r["metric"], []).append(r)
    return by


def _pair(by_metric: dict[str, list[dict]], close: Any) -> dict | None:
    for metric, rs in by_metric.items():
        for a, b in combinations(rs, 2):
            for combined in (a["value"] - b["value"], a["value"] + b["value"]):
                if close(combined):
                    return {"metric": metric, "from": [_source(a), _source(b)]}
    return None


def _stated_scales(content: str) -> set[float]:
    return {SCALES[m.lower()] for m in SCALE_NOTE.findall(content)}


def _in_millions(content: str) -> bool:
    """Table cells read in millions unless the text states only other scales."""
    stated = _stated_scales(content)
    return not stated or 1e6 in stated


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


def _number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", v):
        return float(v)
    return None


def _input_rows(content: str) -> list[dict]:
    """The values /financials rows were computed from (`inputs`): a named value at the row's
    period (net debt's cash and debts), `<metric>_year_ago` (a growth rate's base; its period
    isn't given) or a TTM's `quarters`, each with its own period and filing."""
    try:
        data = json.loads(content)
    except ValueError:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return []
    company = data.get("company_id")
    out = []
    for r in data["rows"]:
        if not isinstance(r, dict) or not isinstance(r.get("inputs"), dict) or "metric" not in r:
            continue
        cite = r.get("citation") if isinstance(r.get("citation"), dict) else {}
        for name, v in r["inputs"].items():
            if name == "quarters" and isinstance(v, list):
                for q in v:
                    value = _number(q.get("value")) if isinstance(q, dict) else None
                    if value is not None:
                        out.append({"metric": str(r["metric"]), "value": value,
                                    "company_id": company, "period_end": q.get("period_end"),
                                    "citation": {"accession_no": q.get("accession_no")}})
                continue
            value = _number(v)
            if value is None:
                continue
            year_ago = str(name).endswith("_year_ago")
            out.append({"metric": str(name), "base": str(name).removesuffix("_year_ago"),
                        "value": value, "company_id": company,
                        "period_end": None if year_ago else r.get("period_end"),
                        "citation": {} if year_ago else cite})
    return out


def _dedupe(rows: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out = []
    for r in rows:
        key = (r["company_id"], r.get("base") or r["metric"], r["period_end"], r["value"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


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
