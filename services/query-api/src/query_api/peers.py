"""Peers by business: how /v1/companies/{id}/peers ranks candidates (the spec; README "Peers").

Candidates come from what parser-peers stores (services/parser filing_parser/business.py,
migration 047), read under RLS (`filings` on both companies, since they derive from Item 1 text):

* `similarity`: cosine similarity of the two companies' latest 10-K Item 1 (Business) term vectors,
  0 to 1, with the shared terms that add the most (`terms`);
* `named_by_target`: the company's own Item 1 names this candidate in a sentence about
  competition; `names_target`: the candidate's Item 1 names the company.

Score = similarity x division x size + NAMED (named by the target) + NAMES (names the target).

* division (SIC as a weak signal): the SEC's SIC division (manufacturing, retail, finance,
  services...) is the one level of SIC that separates a maker from its customers reliably (Vertiv's
  data-center customers say "colocation" and "rack" too). Same 4-digit SIC x1.15, same 2-digit
  group x1.10, same division x1.0, another division x0.80, either without a SIC x0.90.
* size: 1 / (1 + 0.3 x |log10(revenue ratio)|) on TTM revenue as the screener reads it (a value
  only when current or behind, never n/m or old), floored at $10M; a 10x gap costs 23%, 100x 38%.
  Only when the caller can read both companies' financials (an unreadable candidate's revenue
  must not move its rank: that would be an oracle). A readable candidate with no current revenue
  counts as the floor: mostly pre-revenue companies, which a $300B company's peers shouldn't open
  on (NVIDIA's had two in its top 10) and a small company's barely notice. A candidate whose
  revenue is in another currency (056) has an unknown size, not a small one: x1, as when the
  target's own revenue isn't in dollars (057; Tributary converts no currency).

No text on file for the company (no 10-K Item 1 in the last three years, or the caller can't read
its filings): SIC and size, as before F4 (`basis` "sic"). Blank-check shells (SIC 6770) are never
suggested for an operating company. The answer says what made each one similar (`why`), in words
the web app shows as they are.

Why SIC and size (`basis_reason`, `fallback_reason`), from the company's latest 10-K stored in the
last TEXT_MAX_AGE_DAYS and its Item 1 as parsed (parser-peers' own test, business.py's spec):
* `filings_access`: the caller's plan doesn't include this company's filings text;
* `no_annual_report`: no 10-K stored from the last three years (a 20-F filer, or none yet);
* `no_business_section`: the 10-K is stored but has no Item 1, or one titled as financial
  statements (a mis-parse): Citigroup's 10-K follows its annual report's own layout, with the
  form's items only in a cross-reference index, so the parser finds no Item heading at all;
* `cross_reference`: Item 1 is shorter than TEXT_MIN_CHARS: a pointer to pages elsewhere in the
  report ("Item 1. Business: Pages 3-5": Intel, General Electric, McDonald's), not text to compare;
* `not_profiled`: a usable Item 1 the weekly parser-peers job hasn't read yet;
* `no_close_peers`: a profile, but no stored neighbour or named competitor is a company here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

NAMED = 0.05
NAMES = 0.03
SIZE_SLOPE = 0.3
SIZE_FLOOR = Decimal(10_000_000)
DIVISION = {4: 1.15, 3: 1.10, 2: 1.10, 1: 1.0, 0: 0.80}
NO_SIC = 0.90
SHELL = "6770"
# parser-peers' document rule (services/parser filing_parser/business.py MAX_AGE_DAYS, MIN_CHARS;
# test_peers keeps them equal)
TEXT_MAX_AGE_DAYS = 1100
TEXT_MIN_CHARS = 3_000
FALLBACK_REASONS = ("filings_access", "no_annual_report", "no_business_section",
                    "cross_reference", "not_profiled", "no_close_peers")
# what `method` says after "By SIC and size: " for each reason
FALLBACK_METHOD = {
    "filings_access": "business descriptions are part of filings text, which your plan doesn't "
                      "include for this company.",
    "no_annual_report": "this company has no 10-K on file from the last three years, so there is "
                        "no Business section to compare.",
    "no_business_section": "this company's latest 10-K has no Business section (Item 1) to "
                           "compare: the report follows its own layout, with the form's items "
                           "only in a cross-reference index.",
    "cross_reference": "this company's latest 10-K gives its Business section (Item 1) as a "
                       "cross-reference to pages elsewhere in the report, so there is no business "
                       "text to compare.",
    "not_profiled": "this company's latest 10-K Business section hasn't been read for peers yet "
                    "(new 10-Ks are read weekly).",
    "no_close_peers": "no other company's Business section reads close to this one's, and none "
                      "is named as a competitor.",
}


def division(sic: str | None) -> str | None:
    """The SEC's SIC division letter (A agriculture ... I services, J public administration)."""
    if not sic or not sic[:2].isdigit():
        return None
    n = int(sic[:2])
    for letter, last in (("A", 9), ("B", 14), ("C", 17), ("D", 39), ("E", 49), ("F", 51),
                         ("G", 59), ("H", 67), ("I", 89), ("J", 99)):
        if n <= last:
            return letter
    return None


def sic_match(a: str | None, b: str | None) -> int | None:
    """4 same SIC, 2 same 2-digit group, 1 same division, 0 another division, None unknown."""
    if not a or not b or a == "0000" or b == "0000":
        return None
    if a == b:
        return 4
    if a[:3] == b[:3]:
        return 3
    if a[:2] == b[:2]:
        return 2
    return 1 if division(a) == division(b) else 0


def size_factor(target: Decimal | None, peer: Decimal | None, readable: bool) -> float:
    if target is None or not readable:
        return 1.0
    ratio = max(peer or SIZE_FLOOR, SIZE_FLOOR) / max(target, SIZE_FLOOR)
    return 1 / (1 + SIZE_SLOPE * abs(math.log10(ratio)))


@dataclass
class Candidate:
    company_id: str
    sic: str | None
    similarity: float = 0.0
    terms: list[str] = field(default_factory=list)
    named_by_target: bool = False
    names_target: bool = False
    revenue: Decimal | None = None
    readable: bool = False  # the caller reads its financials (its revenue may count)
    # its revenue is in another currency (057): its size is unknown, not the $10M floor
    size_unknown: bool = False
    score: float = 0.0


def rank(target_sic: str | None, target_revenue: Decimal | None,
         candidates: list[Candidate]) -> list[Candidate]:
    """Scored, best first (ties by company id). Shells are dropped for a company that isn't one."""
    out = []
    for c in candidates:
        if c.sic == SHELL and target_sic != SHELL:
            continue
        match = sic_match(target_sic, c.sic)
        div = NO_SIC if match is None else DIVISION[match]
        sized = c.readable and not c.size_unknown
        c.score = (c.similarity * div * size_factor(target_revenue, c.revenue, sized)
                   + (NAMED if c.named_by_target else 0.0) + (NAMES if c.names_target else 0.0))
        out.append(c)
    return sorted(out, key=lambda c: (-c.score, c.company_id))


def fallback_reason(profile: bool, can_read_filings: bool, text: dict | None) -> str:
    """Why peers are by SIC and size (module docstring). `profile`: the company has a stored
    business profile; `text`: its latest stored 10-K in the window and Item 1's length and title
    (Store.business_text; None or empty: no 10-K stored)."""
    if profile:
        return "no_close_peers"
    if not can_read_filings:
        return "filings_access"
    if not text:
        return "no_annual_report"
    chars, title = text.get("chars"), text.get("title") or ""
    if chars is None or "financial statements" in title.lower():
        return "no_business_section"
    if chars < TEXT_MIN_CHARS:
        return "cross_reference"
    return "not_profiled"
