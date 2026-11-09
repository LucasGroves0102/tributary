"""Checks an answer's dollar figures and accession numbers against the tool results behind it.

A figure is, in this order:

1. verified when it equals a returned value at the precision written ($96.2B for
   96,221,000,000);
2. derived when it is the difference or sum of two returned values of one metric that can be
   compared: like with like (one span: quarters with quarters, TTMs with TTMs; a TTM plus one
   of its quarters is reported nowhere), one company's adjacent quarters or the same quarter a
   year apart, or two companies' periods ending within 45 days (`_comparable`; any two of forty
   TTM quarters made coincidences);
3. quoted when it appears in returned filing text: as written ("$50.3 billion"), or as a table
   cell (a number with thousands separators, "82,886") read at the scale that governs it;
4. verified when it equals one of a derived metric's inputs (`inputs` on a /financials row:
   net debt's cash and debt, a growth rate's year-ago value, a TTM's four quarters), or derived
   from them as in 2;
5. unverified otherwise.

Table cells, per section of a sections result (scale notes: "(in thousands" / "(in millions" /
"(in billions", also "($ in millions)", "(Dollars in thousands)"): a section that states no
scale reads its cells in millions (SEC filings mostly are); one that states exactly one scale
reads its cells after that note at that scale, decimals ("1,234.5") too, and cells before it in
millions; one that states more than one reads its cells in millions only. So a section is never
read more loosely than in millions alone: an income statement "(In millions)" beside a share
note "(in thousands)" reads 96,221 as $96.2B only, and a prose "(in billions)" doesn't make it
$96.2T. What this can't do: in a section with several scales, a cell that really is in
thousands still reads in millions, so a figure 1,000 times off that cell can be quoted (as it
always could). A figure written without a unit ("$96,221") is never quoted from a section that
states a scale. /v1/search's echo of the query is never evidence (the model's own words); its
snippets are, without the « » marks.

Rule 4 and the decimal and stated-scale cells came after the recorded suites (tests/adversarial,
tests/citations): they run only for a figure 1-3 left unverified, so a recorded answer verifies
exactly as before. Nothing is loosened: every rule still needs the number itself in what the
tools returned.

Currencies (F8b, after the suites too): since migration 056 a company's figures are in its own
currency, and /financials says so (each row's `unit`, "EUR"; `currency` on the answer when it isn't
USD). A figure written in dollars ("$2,097M", "$45.1B") never stands on an amount in another
currency: when rules 1, 2 or 4 verified or derived it from such a row or input, it is checked again
with only dollar rows, and is unverified unless those verify it. Every row in the recorded suites is
in USD, so this never runs for them. A figure written without "$" ("2,097 million") is matched as
before, whatever the currency. Filing text isn't read for its currency: a "$" figure can still be
quoted from a euro filing's "2,097 million" (rule 3), as before.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
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
# where one section of a sections result begins in its JSON (text is escaped: no bare quote)
SECTION_START = re.compile(r'"section_key"\s*:')
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
    # texts for a figure written without a unit ("$96,221"): the cells of a section that states
    # a scale masked, since such a number is a multiple of dollars, never dollars
    plain_texts: list[str]
    cells: list[float]  # table cells read in millions (rule 3, see _cells)
    scaled: list[float]  # at their section's one stated scale, decimals too (after the suites)
    inputs: list[dict]  # derived metrics' inputs (rule 4)


def verify(answer: str, tool_results: list[str], cited: set[str]) -> Verification:
    """tool_results: the raw content of every successful tool call; cited: accession numbers
    those results contain."""
    sections = [c for c in tool_results if '"sections"' in c]
    by_content = {c: _cells(c) for c in sections}
    cells = [cell for c in sections for cell in by_content[c]]
    texts = [_normalize(_evidence_text(c)) for c in tool_results]
    ev = _Evidence(
        rows=[r for content in tool_results for r in _financial_rows(content)],
        texts=texts,
        plain_texts=[_normalize(_masked(c, by_content[c])) if c in by_content else t
                     for c, t in zip(tool_results, texts, strict=True)],
        cells=sorted({c.value for c in cells if c.scale == 1e6 and not c.decimal}),
        scaled=sorted({c.value for c in cells if c.stated}),
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
    f = _check_rules(written, value, step, ev)
    # added after the recorded suites (F8b): a dollar figure never verifies on another currency
    if f.status in ("verified", "derived") and written.lstrip().startswith("$") and \
            any(_foreign(r) for r in (*ev.rows, *ev.inputs)):
        dollars = _Evidence([r for r in ev.rows if not _foreign(r)], ev.texts, ev.plain_texts,
                            ev.cells, ev.scaled, [r for r in ev.inputs if not _foreign(r)])
        f = _check_rules(written, value, step, dollars)
    return f


def _foreign(r: dict) -> bool:
    """A row or input in a currency other than the dollar (056's units, the answer's
    `currency`)."""
    return r.get("_currency") not in (None, "USD")


def _currency(data: dict, r: dict) -> str | None:
    """The currency of a /financials row: its unit's ("EUR", "EUR/shares"), else the answer's
    (`currency`, present only when not USD), else None (a dollar answer)."""
    code = str(r.get("unit") or "").split("/")[0]
    if re.fullmatch(r"[A-Z]{3}", code):
        return code
    c = data.get("currency")
    return c if isinstance(c, str) and re.fullmatch(r"[A-Z]{3}", c) else None


def _check_rules(written: str, value: float, step: float, ev: _Evidence) -> Figure:
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
    texts = ev.texts if re.search(r"[a-z]", written, re.IGNORECASE) else ev.plain_texts
    if any(form in t for form in _spellings(written) for t in texts) or \
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


def _by_metric(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Like with like: one metric (a year-ago input with its metric) at one span. A TTM and
    one of its own quarters are both "revenue", but their sum or difference is reported
    nowhere."""
    by: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        by.setdefault((r.get("base") or r["metric"], r.get("_span", "quarter")), []).append(r)
    return by


def _pair(by_metric: dict[tuple[str, str], list[dict]], close: Any) -> dict | None:
    for (metric, _span), rs in by_metric.items():
        for a, b in combinations(rs, 2):
            if not _comparable(a, b):
                continue
            for combined in (a["value"] - b["value"], a["value"] + b["value"]):
                if close(combined):
                    return {"metric": metric, "from": [_source(a), _source(b)]}
    return None


def _end(r: dict) -> date | None:
    try:
        return date.fromisoformat(str(r.get("_end") or r.get("period_end")))
    except ValueError:
        return None


def _comparable(a: dict, b: dict) -> bool:
    """Two values a sum or difference can honestly come from: one company's adjacent quarters
    (80-100 days apart: a change on the quarter, a half year) or the same period a year apart
    (350-380 days), or two companies' periods ending within 45 days of each other. Any two of
    forty TTM quarters made coincidences: NVIDIA's TTM operating income of July 2025 less April
    2018's "verified" a $99.9B nobody reported."""
    ea, eb = _end(a), _end(b)
    if ea is None or eb is None:
        return False
    days = abs((ea - eb).days)
    if a.get("company_id") != b.get("company_id"):
        return days <= 45
    return 80 <= days <= 100 or 350 <= days <= 380


@dataclass
class _Cell:
    start: int  # where it is in the tool result's content
    end: int
    value: float  # at its scale
    scale: float
    stated: bool  # read at its section's one stated scale (else millions, as the suites were)
    decimal: bool
    noted: bool  # its section states a scale: the number is a multiple of dollars


def _cells(content: str) -> list[_Cell]:
    """Every table cell of a sections result, read per section (the text between one
    `"section_key":` and the next; escaped JSON text holds no bare quote):
    - no scale note: in millions, as before any of this (SEC filings mostly are);
    - one scale, stated once or more: a cell after its first note at that scale, decimals
      too; a cell before it in millions, as a note-less section (a statement printed before a
      "(in thousands)" share note is not in thousands);
    - more than one scale: in millions only, as before, never looser than that (which note
      governs a cell can't be told: a share note, a prose "(in billions)", a later table with
      no note of its own all broke "the nearest note before it").
    Decimal cells only at a stated scale."""
    starts = [0, *(m.start() for m in SECTION_START.finditer(content)), len(content)]
    out = []
    for a, b in zip(starts, starts[1:], strict=False):
        notes = [(m.start(), SCALES[m.group(1).lower()])
                 for m in SCALE_NOTE.finditer(content, a, b)]
        one = notes[0] if notes and len({s for _, s in notes}) == 1 else None
        for pattern, decimal in ((TABLE_CELL, False), (DECIMAL_CELL, True)):
            for m in pattern.finditer(content, a, b):
                stated = one is not None and m.start() > one[0]
                if decimal and not stated:
                    continue
                scale = one[1] if stated and one is not None else 1e6
                number = float(m.group(0).replace(",", ""))
                out.append(_Cell(m.start(), m.end(), number * scale, scale, stated, decimal,
                                 bool(notes)))
    return out


def _masked(content: str, cells: list[_Cell]) -> str:
    """The content with every cell of a section that states a scale blanked (see
    _Evidence.plain_texts)."""
    out, at = [], 0
    for c in sorted((c for c in cells if c.noted), key=lambda c: c.start):
        out.append(content[at:c.start] + "#")
        at = c.end
    return "".join(out) + content[at:]


def _evidence_text(content: str) -> str:
    """A tool result as evidence for quoted figures: /v1/search's echo of the query is the
    model's own words, not the data, so it never counts (a searched "$46.7B" would quote
    itself); its snippets are filing text, read without their « » match marks."""
    try:
        data = json.loads(content)
    except ValueError:
        return content
    if not isinstance(data, dict) or "query" not in data or not isinstance(
            data.get("results"), list):
        return content
    rest = {k: v for k, v in data.items() if k != "query"}
    return json.dumps(rest, separators=(",", ":"), default=str).replace("«", "").replace("»", "")


def _span(data: dict, r: dict) -> str:
    """A /financials row's span: "ttm" from a view=ttm answer, or a row summed from quarters."""
    inputs = r.get("inputs")
    if data.get("view") == "ttm" or (isinstance(inputs, dict)
                                     and isinstance(inputs.get("quarters"), list)):
        return "ttm"
    return "quarter"


def _financial_rows(content: str) -> list[dict]:
    try:
        data = json.loads(content)
    except ValueError:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return []
    company = data.get("company_id")
    return [{**r, "company_id": company, "value": float(r["value"]), "_span": _span(data, r),
             "_currency": _currency(data, r)}
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
        span = _span(data, r)
        currency = _currency(data, r) if data.get("currency") else None  # the company's
        for name, v in r["inputs"].items():
            if name == "quarters" and isinstance(v, list):
                for q in v:  # a TTM's quarters: quarters, never paired with the TTM itself
                    value = _number(q.get("value")) if isinstance(q, dict) else None
                    if value is not None:
                        out.append({"metric": str(r["metric"]), "value": value,
                                    "company_id": company, "period_end": q.get("period_end"),
                                    "citation": {"accession_no": q.get("accession_no")},
                                    "_span": "quarter", "_currency": currency})
                continue
            value = _number(v)
            if value is None:
                continue
            year_ago = str(name).endswith("_year_ago")
            row = {"metric": str(name), "base": str(name).removesuffix("_year_ago"),
                   "value": value, "company_id": company,
                   "period_end": None if year_ago else r.get("period_end"),
                   "citation": {} if year_ago else cite, "_span": span,
                   "_currency": data.get("currency") if data.get("currency") else None}
            end = _end(r)
            if year_ago and end is not None:  # a year before the row's: for pairing only
                row["_end"] = (end - timedelta(days=364)).isoformat()
            out.append(row)
    return out


def _dedupe(rows: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out = []
    for r in rows:
        key = (r["company_id"], r.get("base") or r["metric"], r["period_end"], r["value"],
               r.get("_span"))
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
