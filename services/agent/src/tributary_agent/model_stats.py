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
  below the CAGR, or its margin outside [min, max] of those margins.
- Scenarios (`scenario_assumptions`): a model's `assumptions` are its BASE case (models saved
  before scenarios are base only); `scenarios.bear` / `scenarios.bull` hold the other cases for
  the same years. The thesis check checks the base case only.
- Defaults (`run_rate`, `default_assumptions`): what a new model starts from. Year 1 at the
  trailing-twelve-month run rate (the newest TTM revenue against the TTM a year earlier, and
  its operating margin; the latest fiscal year's when there's no TTM), then each year closes
  half the gap (FADE) to the long run: growth to the 5-year CAGR held between 0% and
  LONG_RUN_GROWTH_CAP, margin to the 5-year average. `OLD_DEFAULT` years (the 5-year CAGR and
  average margin, flat) are what models made before this rule start from. The thesis check
  tells the model which years are still a default (`user_set` false), so it doesn't endorse
  them as the user's view."""

import math
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


SCENARIOS = ("bear", "base", "bull")
MAX_YEARS = 10
MAX_NOTE = 500


def _finite(value: Any) -> bool:
    """JavaScript's `typeof v === "number" && Number.isFinite(v)`: no booleans, no strings."""
    return (isinstance(value, int | float) and not isinstance(value, bool)
            and value == value and value not in (float("inf"), float("-inf")))


def _years(raw: Any) -> list[dict]:
    out = []
    for a in (raw if isinstance(raw, list) else [])[:MAX_YEARS]:
        if not isinstance(a, dict) or not _finite(a.get("growth")) or not _finite(a.get("margin")):
            continue
        note = a.get("note").strip()[:MAX_NOTE] if isinstance(a.get("note"), str) else ""
        out.append({"growth": float(a["growth"]), "margin": float(a["margin"]),
                    **({"note": note} if note else {})})
    return out


def scenario_assumptions(state: Any, scenario: str = "base") -> list[dict] | None:
    """One case's assumptions from a saved model state (untrusted): `base` is `assumptions`;
    `bear` / `bull` come from `scenarios` and count only with as many years as the base case.
    None when the state has no such case. web models/model.ts `scenarioAssumptions`."""
    if not isinstance(state, dict) or scenario not in SCENARIOS:
        return None
    base = _years(state.get("assumptions"))
    if not base:
        return None
    if scenario == "base":
        return base
    cases = state.get("scenarios") if isinstance(state.get("scenarios"), dict) else {}
    raw = cases.get(scenario)
    case = _years(raw)
    return case if case and isinstance(raw, list) and len(raw) == len(case) == len(base) \
        else None


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


# --- defaults: what a new model starts from (web models/model.ts defaultAssumptions) --------

LONG_RUN_GROWTH_CAP = 5.0  # percent: the most a default fades toward
FADE = 0.5  # each later year closes this share of the gap to the long run
GROWTH_RANGE = (-100.0, 1000.0)
MARGIN_RANGE = (-1000.0, 100.0)
_FY_Q = re.compile(r"^FY(\d{4}) Q([1-4])$")


@dataclass
class RunRate:
    growth: float | None  # ratio: the newest TTM revenue against the TTM a year earlier
    margin: float | None  # ratio: that TTM's operating income / revenue
    period: str | None  # "FY2027 Q2"


def run_rate(rows: list[dict]) -> RunRate:
    """The trailing-twelve-month run rate from the same TTM rows the history comes from: the
    newest revenue row, the row for the same fiscal quarter a year earlier, and operating
    income at the newest row's quarter."""
    rev: dict[tuple[int, int], tuple[str, float]] = {}
    oi: dict[tuple[int, int], float] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        m = _FY_Q.match(str(r.get("fiscal_period") or ""))
        value = _number(r.get("value"))
        if not m or value is None:
            continue
        key = (int(m.group(1)), int(m.group(2)))
        if r.get("metric") == "revenue":
            rev[key] = (str(r.get("period_end") or ""), value)
        elif r.get("metric") == "operating_income":
            oi[key] = value
    if not rev:
        return RunRate(None, None, None)
    key = max(rev, key=lambda k: (rev[k][0], k))
    now = rev[key][1]
    ago = rev.get((key[0] - 1, key[1]))
    growth = now / ago[1] - 1 if ago is not None and ago[1] > 0 else None
    margin = oi[key] / now if key in oi and now > 0 else None
    return RunRate(growth, margin, f"FY{key[0]} Q{key[1]}")


def _round1(v: float) -> float:
    """JavaScript's Math.round(v * 10) / 10 (half up, not Python's half to even)."""
    return math.floor(v * 10 + 0.5) / 10


def _clamp(v: float, lo_hi: tuple[float, float]) -> float:
    return min(lo_hi[1], max(lo_hi[0], v))


def _first(*values: float | None) -> float | None:
    return next((v for v in values if v is not None), None)


def default_assumptions(stats: HistoryStats, years: int, run: RunRate | None = None,
                        ) -> list[dict]:
    """Year 1 at the run rate (else the latest fiscal year's growth and margin, else the
    5-year figures), then each year closes FADE of the gap to the long run: growth to the
    5-year CAGR held in [0, LONG_RUN_GROWTH_CAP] percent, margin to the 5-year average.
    Percent, rounded to 0.1."""
    run = run or RunRate(None, None, None)
    g0 = _first(run.growth, stats.last_growth, stats.cagr)
    m0 = _first(run.margin, stats.last_margin, stats.avg_margin)
    g0 = 0.0 if g0 is None else g0 * 100
    m0 = 0.0 if m0 is None else m0 * 100
    g_long = _clamp(stats.cagr * 100 if stats.cagr is not None else g0,
                    (0.0, LONG_RUN_GROWTH_CAP))
    m_long = stats.avg_margin * 100 if stats.avg_margin is not None else m0
    out = []
    for k in range(min(MAX_YEARS, max(1, years))):
        keep = FADE ** k
        out.append({"growth": _round1(_clamp(g_long + (g0 - g_long) * keep, GROWTH_RANGE)),
                    "margin": _round1(_clamp(m_long + (m0 - m_long) * keep, MARGIN_RANGE))})
    return out


def old_default(stats: HistoryStats) -> dict:
    """What models made before the run-rate rule started from: the 5-year CAGR and average
    margin, every year."""
    return {"growth": 0.0 if stats.cagr is None else _round1(_clamp(stats.cagr * 100,
                                                                    GROWTH_RANGE)),
            "margin": 0.0 if stats.avg_margin is None else _round1(
                _clamp(stats.avg_margin * 100, MARGIN_RANGE))}


def user_set(stats: HistoryStats, run: RunRate | None, assumptions: list[dict]) -> list[bool]:
    """Per forecast year, whether the user changed it from a default (the run-rate rule or
    the old flat one, both as of today's history): False means it's still Tributary's
    starting point, not the user's view."""
    new = default_assumptions(stats, len(assumptions), run)
    old = old_default(stats)

    def same(a: dict, d: dict) -> bool:
        return abs(float(a["growth"]) - d["growth"]) < 0.05 and \
            abs(float(a["margin"]) - d["margin"]) < 0.05

    return [not (same(a, new[i]) or same(a, old)) for i, a in enumerate(assumptions)]
