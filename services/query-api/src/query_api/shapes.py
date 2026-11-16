"""Small response helpers shared by app.py and ui.py."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from urllib.parse import quote

EDGAR = "https://www.sec.gov/Archives/edgar/data"


def filing_url(cik: str | None, accession_no: str) -> str | None:
    """EDGAR filing index, e.g. https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/"""
    if not cik:
        return None
    return f"{EDGAR}/{int(cik)}/{accession_no.replace('-', '')}/"


def document_url(cik: str | None, accession_no: str, document: str | None) -> str | None:
    """A filing's primary document on EDGAR (public), from the submissions index."""
    base = filing_url(cik, accession_no)
    if base is None or not document:
        return None
    return base + quote(document)


# Why a percentage change says nothing (percent_change); the words reach the user as they are.
PCT_FROM_ZERO = "the earlier figure is zero"
PCT_FROM_NEGATIVE = "the earlier figure is negative, so a percentage change from it says nothing"
PCT_TO_NEGATIVE = "the figure went from positive to negative"
PCT_SMALL_BASE = "growth of over 1,000% from under $10M earlier"
# 035's revenue growth bound, the same numbers: over 1,000% from a dollar base under $10M
SMALL_BASE, SMALL_BASE_GROWTH = Decimal("10000000"), Decimal("10")


def percent_change(current: Decimal, earlier: Decimal, unit: str,
                   places: Decimal = Decimal("0.000001")) -> tuple[Decimal | None, str | None]:
    """The one rule for a period-on-period percentage change (the recap's QoQ and YoY, alerts'
    "on a year earlier"): (current - earlier) / earlier, rounded to `places`, or None with the
    reason it would mislead, worded like 035's gold.not_meaningful:

      ratio (and pure) units  None, no reason: a margin changes in points, not percent
      earlier = 0             PCT_FROM_ZERO
      earlier < 0             PCT_FROM_NEGATIVE: JPMorgan's operating cash flow from -$25.28B to
                              $33.16B is "+231%" by |earlier|, which says nothing (analyst test 4)
      earlier > 0 > current   PCT_TO_NEGATIVE: -150% for a profit turned loss, as 035 reads net
                              income growth below -100% ("went from a profit to a loss")
      over 1,000% from under $10M (dollar amounts only, 057)  PCT_SMALL_BASE: 035's revenue growth
                              bound for any amount (Riley 45: Pro-Dex's cash +35,533.3%, $33K to
                              $11.76M)

    A zero now from a positive base stays -100%: a real fall to nothing."""
    if unit in ("ratio", "pure"):
        return None, None
    if earlier == 0:
        return None, PCT_FROM_ZERO
    if earlier < 0:
        return None, PCT_FROM_NEGATIVE
    if current < 0:
        return None, PCT_TO_NEGATIVE
    if unit == "USD" and earlier < SMALL_BASE and current - earlier > SMALL_BASE_GROWTH * earlier:
        return None, PCT_SMALL_BASE
    with localcontext() as ctx:
        ctx.prec = 60
        try:
            return ((current - earlier) / earlier).quantize(places), None
        except InvalidOperation:
            return None, None


def period(prefix: str, year: int | None, quarter: int | None) -> str | None:
    """e.g. FY2026 Q1, CY2025 Q4; None when the fiscal year end is unknown."""
    return f"{prefix}{year} Q{quarter}" if year and quarter else None


def newest_windows(days: tuple[int, ...], start: date | None, end: date | None,
                   today: date) -> Iterator[date | None]:
    """Lower bounds on a filing date to try in turn, newest first: `days` back from `end` (or
    today), then `start` itself (None: all time). A query sorted newest first can stop at the
    first window that fills its page: anything outside the window is older."""
    anchor = end or today
    for n in days:
        lower = date.fromordinal(anchor.toordinal() - n)
        if start is not None and lower <= start:
            break
        yield lower
    yield start
