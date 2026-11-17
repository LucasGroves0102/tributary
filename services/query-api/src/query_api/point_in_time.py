"""Point-in-time reads (B1, migrations 085-087): what Tributary would have shown at the end of a
past day, from the filings filed by then.

1. `as_of` (a date) on /financials, /per-share, /annual-per-share, /formulas/evaluate, /screener,
   /filings, /sections, /search and /restatements. Gold "as of D" is gold's own pipeline over the
   facts of the filings filed by the end of D (gold.usable_facts' `p_as_of`), read from the history
   gold keeps of itself (gold.financials_known: each version of a figure from the day a filing first
   stated it to the day a filing changed it). A figure cites the first filing that stated it; a
   company whose history today's rules haven't built yet is replayed at read time (exact, slower;
   it cites the latest filing to state the figure by D). Filings and sections are those filed by
   the end of D. The response echoes the day as `known_on`; `as_of` stays the response's own time
   (every route had it before). A day before EARLIEST (the first XBRL financial statements) or
   after today is refused (422).
2. Readings as of D: the same rules (gold.reading), "today" being D: a figure is stale against D.
   Splits as of D: only the stock splits a filing had restated by D apply (per_share.py).
3. Restatements (/restatements, `originals=true` on /financials): a figure's value changed between
   two versions. Its kind, one of KINDS:
     split         a share count or EPS that query_api.notes' split rule (`_split_events`, the one
                   rule) recognises: share counts at a clean ratio and EPS at its reciprocal, in
                   the same period and restating filing
     another_line  gold read the period from another XBRL tag than before (a line the company
                   tagged differently, or a better line gold's rules prefer)
     same_line     the company restated the figure on the same line
   `originals=true` adds `original` to each row whose value differs from the first value
   believed for its period (by D): that value, its filing, the day, and the kind of the change from
   it to the row's value. Without the flag the JSON is byte for byte as before (the agent never
   sends it).
4. What stays today's on an as-of page: segments, data notes and the confidence mark (the web
   labels them).
"""

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import HTTPException

from query_api.notes import _split_events
from query_api.shapes import filing_url

EARLIEST = date(2009, 1, 1)
KINDS = ("split", "another_line", "same_line")


def check(as_of: date | None, today: date | None = None) -> date | None:
    """The day to read as of, or a 422 for one outside [EARLIEST, today]."""
    if as_of is None:
        return None
    today = today or date.today()
    if as_of < EARLIEST or as_of > today:
        raise HTTPException(422, f"as_of: a day from {EARLIEST.isoformat()} to today")
    return as_of


def _finding(company_id: str, metric: str, period_end: Any, original: Decimal,
             restated: Decimal, accession: str | None, filed: Any) -> dict | None:
    """A change as the notes' split rule reads a restatement finding (detail.change =
    (new - old) / |old|), or None when the original is zero."""
    if not original:
        return None
    return {"company_id": company_id, "check_name": "restatement", "metric": metric,
            "period_end": period_end,
            "detail": {"change": float((restated - original) / abs(original)),
                       "restated_accession": accession,
                       "restated_filed": filed.isoformat() if isinstance(filed, date) else filed}}


def kinds(company_id: str, changes: list[dict]) -> list[str]:
    """Each change's kind (module docstring, 3), in order. A change: {metric, period_end,
    original, restated, same_line, restated_accession, restated_filed}."""
    findings = [_finding(company_id, c["metric"], c["period_end"], c["original"], c["restated"],
                         c.get("restated_accession"), c.get("restated_filed")) for c in changes]
    split = {id(r) for e in _split_events([f for f in findings if f is not None])
             for r in e["findings"]}
    return ["split" if f is not None and id(f) in split
            else "same_line" if c["same_line"] else "another_line"
            for c, f in zip(changes, findings, strict=True)]


def _version(value: Any, tag: Any, accession: Any, form: Any, filed: Any, known_from: Any,
             cik: str | None) -> dict:
    return {"value": value, "xbrl_tag": tag, "accession_no": accession, "form": form,
            "filed": filed, "known_from": known_from, "filing_url": filing_url(cik, accession)}


def restatement_rows(company_id: str, rows: list[dict]) -> list[dict]:
    """The /restatements route's rows from gold.restatements' (Store.restatements)."""
    found = kinds(company_id, [
        {"metric": r["metric"], "period_end": r["period_end"], "original": r["original"],
         "restated": r["restated"], "same_line": r["same_line"],
         "restated_accession": r["restated_accession"], "restated_filed": r["restated_filed"]}
        for r in rows])
    return [{
        "metric": r["metric"], "span": r["span"], "period_start": r["period_start"],
        "period_end": r["period_end"], "unit": r["unit"], "kind": kind,
        "original": _version(r["original"], r["original_tag"], r["original_accession"],
                             r["original_form"], r["original_filed"], r["original_known_from"],
                             r.get("original_cik")),
        "restated": _version(r["restated"], r["restated_tag"], r["restated_accession"],
                             r["restated_form"], r["restated_filed"], r["restated_known_from"],
                             r.get("restated_cik")),
    } for r, kind in zip(rows, found, strict=True)]


def originals(company_id: str, view: str, rows: list[dict],
              changes: list[dict]) -> dict[tuple, dict]:
    """(metric, period_end) -> `original` for each financials row whose value differs from the
    first value believed for its period (module docstring, 3). rows: the route's rows of `view`
    (value, value_source, source_tag, accession_no, filed); changes: gold.restatements' rows for
    them (Store.originals). A TTM view's balance rows are their quarters (value_source reported or
    derived), the rest its TTM sums."""
    first: dict[tuple, dict] = {}
    for c in changes:  # oldest change first per period: its original is the first version
        first.setdefault((c["span"], c["metric"], c["period_end"]), c)
    pairs = []
    for r in rows:
        span = "quarter" if view == "quarter" or r["value_source"] in ("reported", "derived") \
            else "ttm"
        c = first.get((span, r["metric"], r["period_end"]))
        if c is not None and c["original"] != r["value"]:
            pairs.append((r, c))
    found = kinds(company_id, [
        {"metric": r["metric"], "period_end": r["period_end"], "original": c["original"],
         "restated": r["value"], "same_line": c["original_tag"] == r["source_tag"],
         "restated_accession": r["accession_no"], "restated_filed": r["filed"]}
        for r, c in pairs])
    return {(r["metric"], r["period_end"]): {
        **_version(c["original"], c["original_tag"], c["original_accession"], c["original_form"],
                   c["original_filed"], c["original_known_from"], c.get("filer_cik")),
        "kind": kind} for (r, c), kind in zip(pairs, found, strict=True)}
