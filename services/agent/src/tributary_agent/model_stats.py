"""A revenue model's history statistics and assumption flags: a line-for-line port of the web
app's `services/web/src/models/model.ts` (`annualHistory`, `growthOf`, `marginOf`, `baseYear`,
`historyStats`, `marginRange`, `checkAssumptions`). The thesis check computes these here,
deterministically, and gives the numbers to the model as data; the model only comments on
them. `services/web/src/models/stats-fixture.json` holds inputs with the expected numbers, and
both this port's test and the web app's vitest check them, so the two can't drift.

- History: the TTM row at each fiscal Q4 (`fiscal_period` "FY2025 Q4") is the fiscal year
  (`/v1/companies/{id}/financials?view=ttm&quarters=40`, revenue and operating income), the
  newest MAX_HISTORY years, oldest first.
- CAGR: (revenue_end / revenue_start)^(1/n) - 1, end = the latest year with revenue (the base
  year), start = the earliest year in [end - 5, end) with positive revenue.
- Average margin: the mean of operating income / revenue over the 5 fiscal years ending at the
  base year (the years that have both); min and max over the same years.
- Flags (GROWTH_FLAG_POINTS): a forecast year's growth more than 10 percentage points above or
  below the CAGR, or its margin outside [min, max] of those margins."""

import re
from dataclasses import asdict, dataclass
from typing import Any

STATS_WINDOW = 5
MAX_HISTORY = 10
HISTORY_METRICS = ["revenue", "operating_income"]
HISTORY_QUARTERS = 40
GROWTH_FLAG_POINTS = 10.0
MARGIN_EPSILON = 1e-9  # margins are typed to 0.01%; this only absorbs float noise
_FY_Q4 = re.compile(r"^FY(\d{4}) Q4$")


@dataclass
class HistoryYear:
    fy: int
    period_end: str
    revenue: float | None = None
    operating_income: float | None = None
    revenue_accession: str | None = None
    operating_income_accession: str | None = None


def _number(value: Any) -> float | None:
    """JavaScript's Number() for the API's decimal strings: finite numbers only."""
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def annual_history(rows: list[dict], years: int = MAX_HISTORY) -> list[HistoryYear]:
    by: dict[int, HistoryYear] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        m = _FY_Q4.match(str(r.get("fiscal_period") or ""))
        metric = r.get("metric")
        if not m or metric not in HISTORY_METRICS:
            continue
        value = _number(r.get("value"))
        if value is None:
            continue
        fy = int(m.group(1))
        end = str(r.get("period_end") or "")
        y = by.get(fy) or HistoryYear(fy=fy, period_end=end)
        acc = (r.get("citation") or {}).get("accession_no") if isinstance(
            r.get("citation"), dict) else None
        if metric == "revenue":
            y.revenue, y.revenue_accession = value, acc
        else:
            y.operating_income, y.operating_income_accession = value, acc
        if end > y.period_end:
            y.period_end = end
        by[fy] = y
    return sorted(by.values(), key=lambda y: y.fy)[-max(1, years):]


def growth_of(prev: HistoryYear | None, cur: HistoryYear) -> float | None:
    if (prev is None or prev.fy != cur.fy - 1 or prev.revenue is None or cur.revenue is None
            or prev.revenue <= 0):
        return None
    return cur.revenue / prev.revenue - 1


def margin_of(y: HistoryYear) -> float | None:
    if y.revenue is not None and y.revenue > 0 and y.operating_income is not None:
        return y.operating_income / y.revenue
    return None


def base_year(history: list[HistoryYear]) -> HistoryYear | None:
    return next((y for y in reversed(history) if y.revenue is not None), None)


@dataclass
class HistoryStats:
    cagr: float | None
    cagr_years: int
    avg_margin: float | None
    margin_years: int
    last_growth: float | None
    last_margin: float | None


def _margins(history: list[HistoryYear], end: HistoryYear, window: int) -> list[float]:
    return [m for y in history if end.fy - window < y.fy <= end.fy
            for m in [margin_of(y)] if m is not None]


def history_stats(history: list[HistoryYear], window: int = STATS_WINDOW) -> HistoryStats:
    end = base_year(history)
    if end is None:
        return HistoryStats(None, 0, None, 0, None, None)
    in_window = [y for y in history if end.fy - window <= y.fy <= end.fy]
    start = next((y for y in in_window
                  if y.revenue is not None and y.revenue > 0 and y.fy < end.fy), None)
    n = end.fy - start.fy if start else 0
    cagr = None
    if start is not None and n > 0 and end.revenue is not None and end.revenue > 0:
        cagr = (end.revenue / start.revenue) ** (1 / n) - 1  # type: ignore[operator]
    margins = _margins(history, end, window)
    total = 0.0
    for m in margins:  # in order, like the web app's reduce
        total += m
    prev = next((y for y in history if y.fy == end.fy - 1), None)
    return HistoryStats(
        cagr=cagr,
        cagr_years=0 if cagr is None else n,
        avg_margin=total / len(margins) if margins else None,
        margin_years=len(margins),
        last_growth=growth_of(prev, end),
        last_margin=margin_of(end),
    )


def margin_range(history: list[HistoryYear],
                 window: int = STATS_WINDOW) -> tuple[float, float] | None:
    """Lowest and highest operating margin over the years history_stats averages."""
    end = base_year(history)
    margins = _margins(history, end, window) if end else []
    return (min(margins), max(margins)) if margins else None


@dataclass
class YearCheck:
    fy: int
    growth: float  # percent, as typed
    margin: float
    growth_vs_cagr: float | None  # percentage points
    growth_flag: str | None  # "above" | "below" | None
    margin_flag: str | None


def check_assumptions(history: list[HistoryYear], assumptions: list[dict]) -> dict:
    """Each forecast year's growth and margin against history (the flag rule above)."""
    stats = history_stats(history)
    rng = margin_range(history)
    base = base_year(history)
    years = []
    for i, a in enumerate(assumptions):
        growth, margin = float(a["growth"]), float(a["margin"])
        diff = growth - stats.cagr * 100 if stats.cagr is not None else None
        g_flag = None if diff is None else (
            "above" if diff > GROWTH_FLAG_POINTS else
            "below" if diff < -GROWTH_FLAG_POINTS else None)
        m_flag = None
        if rng is not None:
            ratio = margin / 100
            m_flag = ("above" if ratio > rng[1] + MARGIN_EPSILON else
                      "below" if ratio < rng[0] - MARGIN_EPSILON else None)
        years.append(YearCheck(fy=(base.fy + i + 1) if base else i + 1, growth=growth,
                               margin=margin, growth_vs_cagr=diff, growth_flag=g_flag,
                               margin_flag=m_flag))
    return {
        "base_fy": base.fy if base else None,
        "stats": asdict(stats),
        "margin_min": rng[0] if rng else None,
        "margin_max": rng[1] if rng else None,
        "years": [asdict(y) for y in years],
    }
