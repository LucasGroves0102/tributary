"""Models (F2): the one implementation of a model's maths. `POST /v1/companies/{id}/model`
(model_api.py) runs it for the web app's model page and embeds, agent-api's thesis check and,
later, MCP; nothing else projects a model. A model item stores only what the user typed
(workspaces.ModelState, app DB 009/015); the history is read here, as the viewer, every run.

Inputs (`Store.model_inputs`, under RLS after require(financials)): TTM rows (fiscal basis, 40
quarters) and quarterly rows (12 quarters) of LINE_METRICS, the 10-K's diluted EPS and weighted
diluted shares per fiscal year (gold.annual_per_share, 036), and the latest net debt and shares
outstanding with their readings (gold.latest_reading, 035). Percent inputs are percent; every
ratio in the output is a ratio (0.1 = 10%).

History. A fiscal year is the TTM row at its fiscal Q4 ("FY2025 Q4"); the newest MAX_HISTORY
years with any value, oldest first. Lines: revenue, operating income, pretax income, income tax,
net income, operating cash flow, capex, free cash flow, buybacks (TTM sums), and the 10-K's
diluted EPS and diluted weighted shares for the year ending within 7 days of that Q4. Ratios:
growth (consecutive years, prior revenue > 0), operating margin, interest and other (operating
income less pretax income, over revenue: negative is net income from it), tax rate (tax over
pretax income, pretax > 0), share change, FCF margin. `ttm` is the newest TTM when its quarter
isn't a fiscal Q4 (else the newest year is the TTM): the same lines, the latest quarter's diluted
weighted shares, no EPS (per-share figures don't add across quarters).

One-off adjustments (the user's, `adjustments`): `one_off` is an amount booked in a fiscal
quarter's operating income (positive: it raised it, like a refund; negative: a charge). Adjusted
operating income is reported less the year's one-offs; margins, the statistics, run rate,
defaults and the check all read adjusted figures; adjusted net income and EPS take the one-offs
off after tax (a history year's effective rate, or TAX_FALLBACK outside [0, 50%]; a forecast
year's tax input). `acquired` is revenue from acquisitions in a fiscal year (the user's figure):
organic growth = (revenue - acquired) / revenue a year earlier - 1, shown only with such entries.

Statistics (B2, unchanged): the base year is the latest fiscal year with revenue; CAGR =
(revenue_end / revenue_start)^(1/n) - 1 from the earliest year in [end - 5, end) with positive
revenue; average, lowest and highest operating margin over the 5 years ending at the base year.

Run rate (B7): the newest TTM revenue against the TTM a year earlier, and that TTM's operating
margin (less one-offs in its four quarters). Also from that TTM: interest and other over
revenue, the tax rate (TTM; else the 5-year sums; else TAX_FALLBACK; only within [0, 50%]) and
the FCF margin; the share change is the latest quarter's diluted weighted shares against the same
fiscal quarter a year earlier (else the last two 10-Ks; a change over 50% is read as a split and
skipped).

Current year (`ytd`, every model made since F2): year 1 is the fiscal year after the base year,
built from its reported quarters (Q1..Qk, consecutive, with revenue and operating income) plus
the remainder. Year 1's growth and margin are then the REMAINDER's: its revenue = the same
quarters a year earlier (the base year less its Q1..Qk) x (1 + growth), its operating income =
that revenue x margin; the other rates apply to the remainder; the year's actual net income, FCF,
interest and other come from the reported quarters. With no quarter reported the remainder is
the whole year. A model saved before F2 (no `ytd`) keeps year 1 as a full-year guess, unchanged.

Projection, per case, year t (FY = base + 1 + t): revenue_t = revenue_(t-1) x (1 + growth);
adjusted operating income = revenue x margin; reported = adjusted + the year's one-offs; pretax =
operating income - interest and other (rate x revenue); net income = pretax x (1 - tax rate);
diluted shares_t = shares_(t-1) x (1 + share change), from the base year's 10-K count (else the
latest quarter's); EPS = net income / shares; FCF = FCF margin x revenue. A rate the user left
blank is the default (`defaulted` lists them).

Defaults (B7, per year; Reset sets them): growth and operating margin start at the run rate and
close FADE of the gap each year to the long run (growth: the 5-year CAGR held in [0,
LONG_RUN_GROWTH_CAP]; margin: the 5-year average); FCF margin the same way to its 5-year
average; interest and other, tax rate and share change held at the run rate. With `ytd` and
quarters reported, year 1's remainder: growth = the year-to-date growth over the same quarters
a year earlier; margin = the same remaining quarters a year earlier, moved by this year's change
in margin so far; FCF margin = those quarters' a year earlier. Bear and bull start from the base
case moved by SCENARIO_SPREAD (half the growth, at least 5 points; a fifth of the margin, at least
3), the other rates as the base case's.

Check (B2, the thesis check reads it for the base case): each forecast year's full-year growth
more than GROWTH_FLAG_POINTS above or below the CAGR, or its full-year margin outside [min, max];
`user_set` says per year whether growth and margin differ from both the run-rate default and the
pre-B7 flat one (5-year CAGR and margin), so the check never endorses a default as the user's view.

Valuation (only with the user's own `price`; nothing is fetched or stored beyond what they typed):
market cap = price x the latest shares outstanding (else the latest diluted weighted shares,
said so); EV = market cap + net debt, only when net debt's reading is current or merely behind
(035: an old, not meaningful, not applicable or absent net debt leaves EV blank, with the reason);
per column: P/E = price / adjusted diluted EPS (TTM: market cap / adjusted TTM net income),
EV/Revenue, EV/EBIT (adjusted operating income), FCF yield = FCF / market cap. A multiple over
zero or negative earnings, revenue or EBIT is not meaningful."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import date
from typing import Any

MAX_HISTORY = 10
STATS_WINDOW = 5
HISTORY_QUARTERS = 40
YTD_QUARTERS = 12
# what the model reads from gold, TTM and quarterly
LINE_METRICS = ["revenue", "operating_income", "pretax_income", "income_tax", "net_income",
                "operating_cash_flow", "capex", "free_cash_flow", "share_buybacks",
                "weighted_shares_diluted"]
LATEST_METRICS = ["net_debt", "shares_outstanding"]
GROWTH_FLAG_POINTS = 10.0
MARGIN_EPSILON = 1e-9
LONG_RUN_GROWTH_CAP = 5.0
FADE = 0.5
TAX_FALLBACK = 21.0  # percent: the US federal statutory rate
TAX_RANGE = (0.0, 50.0)  # an effective rate outside this isn't a rate to carry forward
SPLIT_CHANGE = 0.5  # a share count change over this is read as a split, not buybacks
SCENARIO_SPREAD = {"growth": (0.5, 5.0), "margin": (0.2, 3.0)}
GROWTH_RANGE = (-100.0, 1000.0)
MARGIN_RANGE = (-1000.0, 100.0)
NONOP_RANGE = (-100.0, 100.0)
TAX_INPUT_RANGE = (-100.0, 100.0)
SHARE_RANGE = (-50.0, 100.0)
FCF_RANGE = (-1000.0, 100.0)
# per-year inputs beyond growth and margin: state key -> default key
RATES = ("nonOperating", "taxRate", "shareChange", "fcfMargin")
ANNUAL_MATCH_DAYS = 7
_FQ = re.compile(r"^FY(\d{4}) Q([1-4])$")


# --- small helpers ------------------------------------------------------------------------

def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _div(a: float | None, b: float | None, positive: bool = True) -> float | None:
    if a is None or b is None or b == 0 or (positive and b < 0):
        return None
    return a / b


def _sub(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def _round1(v: float) -> float:
    """JavaScript's Math.round(v * 10) / 10 (half up), what B7's defaults were rounded with."""
    return math.floor(v * 10 + 0.5) / 10


def _clamp(v: float, lo_hi: tuple[float, float]) -> float:
    return min(lo_hi[1], max(lo_hi[0], v))


def _first(*values: float | None) -> float | None:
    return next((v for v in values if v is not None), None)


def _iso(d: Any) -> str | None:
    if d is None:
        return None
    return d.isoformat() if isinstance(d, date) else str(d)[:10]


def _day(d: Any) -> date | None:
    if isinstance(d, date):
        return d
    try:
        return date.fromisoformat(str(d)[:10])
    except ValueError:
        return None


def _fq(row: dict) -> tuple[int, int] | None:
    """(fiscal year, quarter) of a Store row (fiscal_year/fiscal_quarter) or an API row
    (fiscal_period "FY2026 Q3")."""
    fy, q = row.get("fiscal_year"), row.get("fiscal_quarter")
    if isinstance(fy, int) and isinstance(q, int) and 1 <= q <= 4:
        return fy, q
    m = _FQ.match(str(row.get("fiscal_period") or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _source(row: dict) -> dict:
    cit = row.get("citation") if isinstance(row.get("citation"), dict) else row
    return {"accession_no": cit.get("accession_no"), "form": cit.get("form"),
            "filed": _iso(cit.get("filed")), "filing_url": cit.get("filing_url"),
            "period_end": _iso(row.get("period_end"))}


def _by_period(rows: Iterable[dict]) -> dict[tuple[int, int], dict[str, tuple[float, dict]]]:
    out: dict[tuple[int, int], dict[str, tuple[float, dict]]] = {}
    for r in rows:
        key = _fq(r)
        value = _f(r.get("value"))
        if key is None or value is None or not isinstance(r.get("metric"), str):
            continue
        out.setdefault(key, {})[r["metric"]] = (value, r)
    return out


def _prev_quarter(fy: int, q: int, back: int) -> tuple[int, int]:
    n = fy * 4 + (q - 1) - back
    return n // 4, n % 4 + 1


# --- state ---------------------------------------------------------------------------------

def case_years(state: dict, case: str) -> list[dict] | None:
    """One case's years from a validated state: base = `assumptions`, bear/bull from
    `scenarios` (same length, workspaces.ModelState checks it); None when absent."""
    if case == "base":
        return list(state.get("assumptions") or [])
    got = (state.get("scenarios") or {}).get(case)
    return list(got) if got else None


# --- history --------------------------------------------------------------------------------

def _one_offs(adjustments: list[dict], kind: str) -> dict[tuple[int, int], float]:
    out: dict[tuple[int, int], float] = {}
    for a in adjustments:
        if a.get("kind") == kind:
            key = (int(a["fy"]), int(a["quarter"]))
            out[key] = out.get(key, 0.0) + float(a["amount"])
    return out


def _year_sum(by: dict[tuple[int, int], float], fy: int, quarters: Iterable[int] = (1, 2, 3, 4)
              ) -> float:
    return sum(by.get((fy, q), 0.0) for q in quarters)


def _tax_rate(tax: float | None, pretax: float | None) -> float | None:
    """An effective rate (percent) worth carrying: pretax > 0 and inside TAX_RANGE."""
    r = _div(tax, pretax)
    return r * 100 if r is not None and TAX_RANGE[0] <= r * 100 <= TAX_RANGE[1] else None


def history(inputs: dict, adjustments: list[dict]) -> list[dict]:
    """The fiscal years (see the module docstring), oldest first."""
    one_offs = _one_offs(adjustments, "one_off")
    acquired = _one_offs(adjustments, "acquired")
    ttm = _by_period(inputs.get("ttm") or [])
    annual: dict[str, list[dict]] = {}
    for r in inputs.get("annual") or []:
        annual.setdefault(str(r.get("metric")), []).append(r)
    years: dict[int, dict] = {}
    for (fy, q), lines in ttm.items():
        if q != 4:
            continue
        vals = {m: v for m, (v, _) in lines.items()}
        if not any(m in vals for m in ("revenue", "operating_income")):
            continue
        end = max((_iso(r.get("period_end")) or "") for _, r in lines.values())
        years[fy] = {"fy": fy, "period_end": end, "_v": vals,
                     "_src": {m: _source(r) for m, (_, r) in lines.items()}}
    out = []
    for fy in sorted(years)[-MAX_HISTORY:]:
        y = years[fy]
        v = y.pop("_v")
        src = y.pop("_src")
        end = _day(y["period_end"])
        for metric in ("eps_diluted", "weighted_shares_diluted"):
            match = next((r for r in annual.get(metric, [])
                          if end and (d := _day(r.get("period_end")))
                          and abs((d - end).days) <= ANNUAL_MATCH_DAYS), None)
            if match is not None and _f(match.get("value")) is not None:
                v[metric] = _f(match.get("value"))  # type: ignore[assignment]
                src[metric] = _source(match)
        out.append(_year(fy, y["period_end"], v, src, _year_sum(one_offs, fy),
                         _year_sum(acquired, fy) if acquired else None))
    for prev, cur in zip([None, *out], out, strict=False):
        _link(prev, cur)
    return out


def _year(fy: int, period_end: str, v: dict, src: dict, one_off: float,
          acquired: float | None) -> dict:
    rev, oi = v.get("revenue"), v.get("operating_income")
    pretax, tax, ni = v.get("pretax_income"), v.get("income_tax"), v.get("net_income")
    shares, eps = v.get("weighted_shares_diluted"), v.get("eps_diluted")
    fcf = v.get("free_cash_flow")
    tax_used = _first(_tax_rate(tax, pretax), TAX_FALLBACK)
    oi_adj = None if oi is None else oi - one_off
    ni_adj = None if ni is None else ni - one_off * (1 - tax_used / 100)
    eps_adj = eps if not one_off or eps is None or not shares else (
        eps - one_off * (1 - tax_used / 100) / shares)
    nonop = _sub(oi, pretax)
    return {
        "fy": fy, "period_end": period_end, "revenue": rev,
        "operating_income": oi, "operating_income_adjusted": oi_adj, "one_offs": one_off,
        "acquired_revenue": acquired,
        "margin": _div(oi_adj, rev), "margin_reported": _div(oi, rev),
        "pretax_income": pretax, "income_tax": tax, "non_operating": nonop,
        "non_operating_rate": _div(nonop, rev), "tax_rate": _div(tax, pretax),
        "net_income": ni, "net_income_adjusted": ni_adj,
        "eps": eps, "eps_adjusted": eps_adj, "shares": shares,
        "operating_cash_flow": v.get("operating_cash_flow"), "capex": v.get("capex"),
        "fcf": fcf, "fcf_margin": _div(fcf, rev), "buybacks": v.get("share_buybacks"),
        "growth": None, "organic_growth": None, "share_change": None, "eps_growth": None,
        "sources": src,
    }


def _link(prev: dict | None, cur: dict) -> None:
    """Year-over-year ratios, only between consecutive years."""
    if prev is None or prev["fy"] != cur["fy"] - 1:
        return
    cur["growth"] = _growth(prev["revenue"], cur["revenue"])
    if cur.get("acquired_revenue") is not None and cur["revenue"] is not None:
        cur["organic_growth"] = _growth(prev["revenue"], cur["revenue"] - cur["acquired_revenue"])
    if prev["shares"] and cur["shares"] is not None:
        cur["share_change"] = cur["shares"] / prev["shares"] - 1
    cur["eps_growth"] = _growth(prev.get("eps_adjusted"), cur.get("eps_adjusted"))


def _growth(prev: float | None, cur: float | None) -> float | None:
    if prev is None or cur is None or prev <= 0:
        return None
    return cur / prev - 1


def base_year(years: list[dict]) -> dict | None:
    return next((y for y in reversed(years) if y["revenue"] is not None), None)


def ttm_column(inputs: dict, adjustments: list[dict]) -> dict | None:
    """The newest TTM when its quarter isn't a fiscal Q4: the same lines as a year."""
    ttm = _by_period(inputs.get("ttm") or [])
    with_rev = [k for k, lines in ttm.items() if "revenue" in lines]
    if not with_rev:
        return None
    key = max(with_rev, key=lambda k: (str(ttm[k]["revenue"][1].get("period_end")), k))
    if key[1] == 4:
        return None
    lines = ttm[key]
    v = {m: val for m, (val, _) in lines.items()}
    window = {_prev_quarter(*key, b) for b in range(4)}
    one_off = sum(a for k, a in _one_offs(adjustments, "one_off").items() if k in window)
    quarters = _by_period(inputs.get("quarters") or [])
    shares = quarters.get(key, {}).get("weighted_shares_diluted")
    v["weighted_shares_diluted"] = shares[0] if shares else None  # type: ignore[assignment]
    out = _year(key[0], _iso(lines["revenue"][1].get("period_end")) or "", v,
                {m: _source(r) for m, (_, r) in lines.items()}, one_off, None)
    out.update(period=f"FY{key[0]} Q{key[1]}", eps=None, eps_adjusted=None)
    ago = ttm.get((key[0] - 1, key[1]), {}).get("revenue")
    out["growth"] = _growth(ago[0] if ago else None, out["revenue"])
    return out


# --- statistics, run rate and defaults (B2 / B7) --------------------------------------------

def stats(years: list[dict], window: int = STATS_WINDOW) -> dict:
    end = base_year(years)
    empty = {"cagr": None, "cagr_years": 0, "avg_margin": None, "margin_years": 0,
             "last_growth": None, "last_margin": None, "margin_min": None, "margin_max": None,
             "avg_fcf_margin": None, "fcf_years": 0}
    if end is None:
        return empty
    in_window = [y for y in years if end["fy"] - window <= y["fy"] <= end["fy"]]
    start = next((y for y in in_window
                  if y["revenue"] is not None and y["revenue"] > 0 and y["fy"] < end["fy"]), None)
    n = end["fy"] - start["fy"] if start else 0
    cagr = None
    if start is not None and n > 0 and end["revenue"] > 0:
        cagr = (end["revenue"] / start["revenue"]) ** (1 / n) - 1
    recent = [y for y in years if end["fy"] - window < y["fy"] <= end["fy"]]
    margins = [m for y in recent if (m := y["margin"]) is not None and y["revenue"] > 0]
    fcfs = [m for y in recent if (m := y["fcf_margin"]) is not None and y["revenue"] > 0]
    total = 0.0
    for m in margins:  # in order, like B2's reduce
        total += m
    prev = next((y for y in years if y["fy"] == end["fy"] - 1), None)
    return {
        "cagr": cagr, "cagr_years": 0 if cagr is None else n,
        "avg_margin": total / len(margins) if margins else None, "margin_years": len(margins),
        "last_growth": _growth(prev["revenue"], end["revenue"]) if prev else None,
        "last_margin": end["margin"] if end["revenue"] and end["revenue"] > 0 else None,
        "margin_min": min(margins) if margins else None,
        "margin_max": max(margins) if margins else None,
        "avg_fcf_margin": sum(fcfs) / len(fcfs) if fcfs else None, "fcf_years": len(fcfs),
    }


def run_rate(inputs: dict, adjustments: list[dict], years: list[dict]) -> dict:
    ttm = _by_period(inputs.get("ttm") or [])
    out: dict[str, Any] = {"growth": None, "margin": None, "period": None, "non_operating": None,
                           "tax_rate": None, "tax_source": None, "share_change": None,
                           "share_source": None, "fcf_margin": None}
    with_rev = [k for k, lines in ttm.items() if "revenue" in lines]
    tax_5y = _five_year_tax(years)
    if not with_rev:
        out["tax_rate"], out["tax_source"] = (tax_5y, "five_year") if tax_5y is not None \
            else (TAX_FALLBACK, "statutory")
        return out
    key = max(with_rev, key=lambda k: (str(ttm[k]["revenue"][1].get("period_end")), k))
    lines = {m: val for m, (val, _) in ttm[key].items()}
    rev = lines["revenue"]
    window = {_prev_quarter(*key, b) for b in range(4)}
    one_off = sum(a for k, a in _one_offs(adjustments, "one_off").items() if k in window)
    ago = ttm.get((key[0] - 1, key[1]), {}).get("revenue")
    oi = lines.get("operating_income")
    out["period"] = f"FY{key[0]} Q{key[1]}"
    out["growth"] = rev / ago[0] - 1 if ago and ago[0] > 0 else None
    out["margin"] = (oi - one_off) / rev if oi is not None and rev > 0 else None
    nonop = _sub(oi, lines.get("pretax_income"))
    out["non_operating"] = nonop / rev if nonop is not None and rev > 0 else None
    t = _tax_rate(lines.get("income_tax"), lines.get("pretax_income"))
    if t is not None:
        out["tax_rate"], out["tax_source"] = t, "ttm"
    elif tax_5y is not None:
        out["tax_rate"], out["tax_source"] = tax_5y, "five_year"
    else:
        out["tax_rate"], out["tax_source"] = TAX_FALLBACK, "statutory"
    fcf = lines.get("free_cash_flow")
    out["fcf_margin"] = fcf / rev if fcf is not None and rev > 0 else None
    out["share_change"], out["share_source"] = _share_change(inputs, years)
    return out


def _five_year_tax(years: list[dict]) -> float | None:
    recent = [y for y in years[-STATS_WINDOW:]
              if y["income_tax"] is not None and y["pretax_income"] is not None]
    return _tax_rate(sum(y["income_tax"] for y in recent),
                     sum(y["pretax_income"] for y in recent)) if recent else None


def _share_change(inputs: dict, years: list[dict]) -> tuple[float | None, str | None]:
    q = _by_period(inputs.get("quarters") or [])
    with_shares = [k for k, lines in q.items() if "weighted_shares_diluted" in lines]
    if with_shares:
        key = max(with_shares,
                  key=lambda k: (str(q[k]["weighted_shares_diluted"][1].get("period_end")), k))
        now = q[key]["weighted_shares_diluted"][0]
        ago = q.get((key[0] - 1, key[1]), {}).get("weighted_shares_diluted")
        if ago and ago[0] > 0 and abs(now / ago[0] - 1) <= SPLIT_CHANGE:
            return now / ago[0] - 1, f"FY{key[0]} Q{key[1]}"
    with_annual = [y for y in years if y["shares"]]
    if len(with_annual) >= 2 and with_annual[-1]["fy"] == with_annual[-2]["fy"] + 1:
        c = with_annual[-1]["shares"] / with_annual[-2]["shares"] - 1
        if abs(c) <= SPLIT_CHANGE:
            return c, f"FY{with_annual[-1]['fy']}"
    return None, None


def current_year(inputs: dict, adjustments: list[dict], years: list[dict]) -> dict | None:
    """The fiscal year after the base year: its reported quarters (Q1..Qk) and the same
    quarters a year earlier. None without a base year."""
    base = base_year(years)
    if base is None:
        return None
    fy = base["fy"] + 1
    q = _by_period(inputs.get("quarters") or [])
    one_offs = _one_offs(adjustments, "one_off")
    k = 0
    while k < 3 and all(m in q.get((fy, k + 1), {}) for m in ("revenue", "operating_income")) \
            and all(m in q.get((base["fy"], k + 1), {}) for m in ("revenue", "operating_income")):
        k += 1

    def total(year: int, metric: str) -> float | None:
        vals = [q.get((year, i), {}).get(metric) for i in range(1, k + 1)]
        return sum(v[0] for v in vals) if all(vals) else None  # type: ignore[index]

    out: dict[str, Any] = {"fy": fy, "quarters": k, "through": None, "actual": None,
                           "prior": None, "remainder_prior": None, "sources": []}
    if k == 0:
        out["remainder_prior"] = {"revenue": base["revenue"],
                                  "operating_income": base["operating_income_adjusted"],
                                  "margin": base["margin"], "fcf": base["fcf"],
                                  "fcf_margin": base["fcf_margin"]}
        return out
    last = q[(fy, k)]["revenue"][1]
    out["through"] = _iso(last.get("period_end"))
    out["sources"] = [_source(q[(fy, i)]["revenue"][1]) for i in range(1, k + 1)]
    actual_off = _year_sum(one_offs, fy, range(1, k + 1))
    prior_off = _year_sum(one_offs, base["fy"], range(1, k + 1))
    rev, oi = total(fy, "revenue"), total(fy, "operating_income")
    p_rev, p_oi = total(base["fy"], "revenue"), total(base["fy"], "operating_income")
    out["actual"] = {
        "revenue": rev, "operating_income": oi,
        "operating_income_adjusted": None if oi is None else oi - actual_off,
        "one_offs": actual_off, "pretax_income": total(fy, "pretax_income"),
        "income_tax": total(fy, "income_tax"), "net_income": total(fy, "net_income"),
        "fcf": total(fy, "free_cash_flow"),
    }
    a = out["actual"]
    a["margin"] = _div(a["operating_income_adjusted"], rev)
    a["fcf_margin"] = _div(a["fcf"], rev)
    a["growth"] = _growth(p_rev, rev)
    p_oi_adj = None if p_oi is None else p_oi - prior_off
    p_fcf = total(base["fy"], "free_cash_flow")
    out["prior"] = {"revenue": p_rev, "operating_income": p_oi_adj, "margin": _div(p_oi_adj, p_rev),
                    "fcf": p_fcf, "fcf_margin": _div(p_fcf, p_rev)}
    r_rev = _sub(base["revenue"], p_rev)
    r_oi = _sub(base["operating_income_adjusted"], p_oi_adj)
    r_fcf = _sub(base["fcf"], p_fcf)
    out["remainder_prior"] = {"revenue": r_rev, "operating_income": r_oi,
                              "margin": _div(r_oi, r_rev), "fcf": r_fcf,
                              "fcf_margin": _div(r_fcf, r_rev)}
    return out


def _fade(start: float, long: float, k: int) -> float:
    return long + (start - long) * FADE ** k


def defaults(st: dict, run: dict, n: int, current: dict | None = None) -> list[dict]:
    """The base case's defaults for n forecast years (percent, rounded to 0.1). `current` (a
    ytd model's current year) gives year 1 the remainder's defaults."""
    g0 = _first(run.get("growth"), st["last_growth"], st["cagr"])
    m0 = _first(run.get("margin"), st["last_margin"], st["avg_margin"])
    g0 = 0.0 if g0 is None else g0 * 100
    m0 = 0.0 if m0 is None else m0 * 100
    g_long = _clamp(st["cagr"] * 100 if st["cagr"] is not None else g0, (0.0, LONG_RUN_GROWTH_CAP))
    m_long = st["avg_margin"] * 100 if st["avg_margin"] is not None else m0
    f0 = _first(run.get("fcf_margin"), st["avg_fcf_margin"])
    f0 = 0.0 if f0 is None else f0 * 100
    f_long = st["avg_fcf_margin"] * 100 if st["avg_fcf_margin"] is not None else f0
    nonop = (run.get("non_operating") or 0.0) * 100
    tax = run.get("tax_rate") if run.get("tax_rate") is not None else TAX_FALLBACK
    shares = (run.get("share_change") or 0.0) * 100
    out = []
    for k in range(max(1, n)):
        out.append({"growth": _round1(_clamp(_fade(g0, g_long, k), GROWTH_RANGE)),
                    "margin": _round1(_clamp(_fade(m0, m_long, k), MARGIN_RANGE)),
                    "nonOperating": _round1(_clamp(nonop, NONOP_RANGE)),
                    "taxRate": _round1(_clamp(tax, TAX_INPUT_RANGE)),
                    "shareChange": _round1(_clamp(shares, SHARE_RANGE)),
                    "fcfMargin": _round1(_clamp(_fade(f0, f_long, k), FCF_RANGE))})
    ytd = remainder_defaults(current)
    if ytd is not None:
        out[0].update(ytd)
    return out


def remainder_defaults(current: dict | None) -> dict | None:
    """Year 1's remainder (a ytd model with quarters reported): growth at the year to date's,
    margin as the same quarters a year earlier moved by this year's change so far, FCF margin
    as those quarters' a year earlier. None when a figure is missing."""
    if not current or not current["quarters"] or not current["actual"]:
        return None
    a, p, r = current["actual"], current["prior"], current["remainder_prior"]
    if a["growth"] is None or None in (a["margin"], p["margin"], r["margin"]):
        return None
    out = {"growth": _round1(_clamp(a["growth"] * 100, GROWTH_RANGE)),
           "margin": _round1(_clamp((r["margin"] + a["margin"] - p["margin"]) * 100,
                                    MARGIN_RANGE))}
    if r["fcf_margin"] is not None:
        out["fcfMargin"] = _round1(_clamp(r["fcf_margin"] * 100, FCF_RANGE))
    return out


def old_default(st: dict) -> dict:
    """What models made before B7 started from: the 5-year CAGR and margin, every year."""
    return {"growth": 0.0 if st["cagr"] is None else _round1(_clamp(st["cagr"] * 100,
                                                                    GROWTH_RANGE)),
            "margin": 0.0 if st["avg_margin"] is None else _round1(
                _clamp(st["avg_margin"] * 100, MARGIN_RANGE))}


def shifted(base: list[dict], case: str) -> list[dict]:
    """A new bear (bull) case: each base year's growth and margin moved down (up) by
    SCENARIO_SPREAD, without notes, rounded to 0.1; other rates as the base's."""
    sign = -1 if case == "bear" else 1

    def step(v: float, spread: tuple[float, float]) -> float:
        return max(spread[1], abs(v) * spread[0])

    out = []
    for a in base:
        y = {"growth": _round1(_clamp(a["growth"] + sign * step(a["growth"],
                                                                SCENARIO_SPREAD["growth"]),
                                      GROWTH_RANGE)),
             "margin": _round1(_clamp(a["margin"] + sign * step(a["margin"],
                                                                SCENARIO_SPREAD["margin"]),
                                      MARGIN_RANGE))}
        y.update({k: a[k] for k in RATES if a.get(k) is not None})
        out.append(y)
    return out


# --- projection -----------------------------------------------------------------------------

def project(years: list[dict], assumptions: list[dict], defaults_: list[dict],
            adjustments: list[dict], current: dict | None, run: dict) -> list[dict]:
    """One case's forecast years (see the module docstring); empty without a base year."""
    base = base_year(years)
    if base is None:
        return []
    one_offs = _one_offs(adjustments, "one_off")
    acquired = _one_offs(adjustments, "acquired")
    rev_prev = base["revenue"]
    eps_prev = base["eps_adjusted"]
    shares = base["shares"]
    if shares is None:
        shares = _latest_shares(run)
    out = []
    for i, a in enumerate(assumptions):
        fy = base["fy"] + 1 + i
        d = defaults_[i] if i < len(defaults_) else defaults_[-1]
        used = {k: (float(a[k]) if a.get(k) is not None else d[k]) for k in RATES}
        defaulted = [k for k in RATES if a.get(k) is None]
        g, m = float(a["growth"]) / 100, float(a["margin"]) / 100
        nonop_r, tax_r = used["nonOperating"] / 100, used["taxRate"] / 100
        fcf_r = used["fcfMargin"] / 100
        off = _year_sum(one_offs, fy)
        ytd = current if (i == 0 and current is not None and current["fy"] == fy
                          and current["quarters"]) else None
        row: dict[str, Any] = {"fy": fy, "inputs": {"growth": float(a["growth"]),
                                                     "margin": float(a["margin"]), **used},
                               "defaulted": defaulted}
        if a.get("note"):
            row["note"] = a["note"]
        if ytd is not None:
            act, prior = ytd["actual"], ytd["remainder_prior"]
            r_rev = (prior["revenue"] or 0.0) * (1 + g)
            r_oi = r_rev * m
            r_nonop = r_rev * nonop_r
            fut_off = off - act["one_offs"]
            rev = (act["revenue"] or 0.0) + r_rev
            oi_adj = (act["operating_income_adjusted"] or 0.0) + r_oi
            a_nonop = _sub(act["operating_income"], act["pretax_income"]) or 0.0
            nonop = a_nonop + r_nonop
            r_pretax = r_oi + fut_off - r_nonop
            ni = (act["net_income"] or 0.0) + r_pretax * (1 - tax_r)
            pretax = (act["pretax_income"] or 0.0) + r_pretax
            fcf = (act["fcf"] or 0.0) + r_rev * fcf_r
            row["remainder"] = {"quarters": 4 - ytd["quarters"], "revenue": r_rev,
                                "operating_income": r_oi, "growth": g, "margin": m,
                                "fcf": r_rev * fcf_r}
            row["actual"] = {"quarters": ytd["quarters"], "through": ytd["through"],
                             **{k: act[k] for k in ("revenue", "operating_income",
                                                    "operating_income_adjusted", "net_income",
                                                    "fcf")}}
        else:
            rev = (rev_prev or 0.0) * (1 + g)
            oi_adj = rev * m
            nonop = rev * nonop_r
            pretax = oi_adj + off - nonop
            ni = pretax * (1 - tax_r)
            fcf = rev * fcf_r
        ni_adj = ni - off * (1 - tax_r)
        shares = None if shares is None else shares * (1 + used["shareChange"] / 100)
        eps = _div(ni, shares)
        eps_adj = _div(ni_adj, shares)
        acq = _year_sum(acquired, fy) if acquired else None
        row.update({
            "revenue": rev, "growth": _growth(rev_prev, rev),
            "organic_growth": None if acq is None else _growth(rev_prev, rev - acq),
            "acquired_revenue": acq,
            "operating_income": oi_adj + off, "operating_income_adjusted": oi_adj, "one_offs": off,
            "margin": _div(oi_adj, rev), "margin_reported": _div(oi_adj + off, rev),
            "non_operating": nonop, "non_operating_rate": _div(nonop, rev),
            "pretax_income": pretax, "tax_rate": tax_r,
            "net_income": ni, "net_income_adjusted": ni_adj,
            "shares": shares, "share_change": used["shareChange"] / 100,
            "eps": eps, "eps_adjusted": eps_adj, "eps_growth": _growth(eps_prev, eps_adj),
            "fcf": fcf, "fcf_margin": _div(fcf, rev),
        })
        # a full-year guess's growth and margin are exactly what was typed (B2's check)
        if ytd is None:
            row["growth"], row["margin"] = g, m
        out.append(row)
        rev_prev, eps_prev = rev, eps_adj
    return out


def _latest_shares(run: dict) -> float | None:
    return run.get("_latest_shares")


# --- the check (B2) -------------------------------------------------------------------------

def check(st: dict, base_fy: int | None, projected: list[dict], assumptions: list[dict]
          ) -> dict:
    years = []
    # without a base year nothing is projected: B2 still lists the years as typed, 1, 2, ...
    rows = projected or [{"fy": i + 1, "inputs": a} for i, a in enumerate(assumptions)]
    for i, p in enumerate(rows):
        # a full-year guess is checked as typed; a ytd year 1 on its full year
        ytd = "remainder" in p
        growth = p["growth"] * 100 if ytd and p["growth"] is not None else float(
            p["inputs"]["growth"])
        margin = p["margin"] * 100 if ytd and p["margin"] is not None else float(
            p["inputs"]["margin"])
        diff = growth - st["cagr"] * 100 if st["cagr"] is not None else None
        g_flag = None if diff is None else ("above" if diff > GROWTH_FLAG_POINTS else
                                            "below" if diff < -GROWTH_FLAG_POINTS else None)
        m_flag = None
        if st["margin_min"] is not None:
            ratio = margin / 100
            m_flag = ("above" if ratio > st["margin_max"] + MARGIN_EPSILON else
                      "below" if ratio < st["margin_min"] - MARGIN_EPSILON else None)
        years.append({"fy": p["fy"] if base_fy is not None else i + 1, "growth": growth,
                      "margin": margin, "growth_vs_cagr": diff, "growth_flag": g_flag,
                      "margin_flag": m_flag})
    return {"base_fy": base_fy,
            "stats": {k: st[k] for k in ("cagr", "cagr_years", "avg_margin", "margin_years",
                                         "last_growth", "last_margin")},
            "margin_min": st["margin_min"], "margin_max": st["margin_max"], "years": years}


def user_set(st: dict, assumptions: list[dict], defaults_: list[dict]) -> list[bool]:
    """Per year: growth and margin differ from today's default and from the pre-B7 flat one."""
    old = old_default(st)

    def same(a: dict, d: dict) -> bool:
        return abs(float(a["growth"]) - d["growth"]) < 0.05 and \
            abs(float(a["margin"]) - d["margin"]) < 0.05

    return [not (same(a, defaults_[i]) or same(a, old)) for i, a in enumerate(assumptions)]


# --- valuation ------------------------------------------------------------------------------

USABLE = (None, "behind")


def _reading(r: dict | None) -> dict | None:
    if not r or _f(r.get("value")) is None and r.get("status") is None:
        return None
    return {"value": _f(r.get("value")), "period_end": _iso(r.get("period_end")),
            "status": r.get("status"), "reason": r.get("reason"),
            "citation": None if not r.get("accession_no") else {
                "accession_no": r.get("accession_no"), "form": r.get("form"),
                "filed": _iso(r.get("filed")), "filing_url": r.get("filing_url")}}


def _multiples(col: dict, price: float, cap: float, ev: float | None, ttm: bool = False) -> dict:
    def nm(num: float | None, den: float | None) -> float | str | None:
        if num is None or den is None:
            return None
        return "n/m" if den <= 0 else num / den

    if ttm:
        pe = nm(cap, col.get("net_income_adjusted"))
    else:
        pe = nm(price, col.get("eps_adjusted"))
    return {"pe": pe, "ev_revenue": nm(ev, col.get("revenue")) if ev is not None else None,
            "ev_ebit": nm(ev, col.get("operating_income_adjusted")) if ev is not None else None,
            "fcf_yield": None if col.get("fcf") is None or cap <= 0 else col["fcf"] / cap}


def valuation(price: dict | None, latest: dict, years: list[dict], ttm: dict | None,
              cases: dict[str, list[dict]], latest_shares: float | None) -> dict | None:
    if not price:
        return None
    p = float(price["value"])
    shares = _reading(latest.get("shares_outstanding"))
    debt = _reading(latest.get("net_debt"))
    out: dict[str, Any] = {"price": p, "date": price["date"], "shares_outstanding": shares,
                           "shares_basis": "outstanding", "net_debt": debt,
                           "market_cap": None, "enterprise_value": None, "ev_note": None,
                           "history": [], "ttm": None, "cases": {}}
    count = shares["value"] if shares and shares["value"] and shares["status"] in USABLE \
        else None
    if count is None and latest_shares:
        count, out["shares_basis"] = latest_shares, "diluted"
    if not count:
        out["note"] = "no share count in the filings: market cap unknown"
        return out
    cap = p * count
    out["market_cap"] = cap
    if debt is None or debt["value"] is None and debt["status"] is None:
        out["ev_note"] = "absent"
    elif debt["status"] not in USABLE:
        out["ev_note"] = debt["status"]
    else:
        out["enterprise_value"] = cap + debt["value"]
    ev = out["enterprise_value"]
    out["history"] = [{"fy": y["fy"], **_multiples(y, p, cap, ev)} for y in years]
    out["ttm"] = None if ttm is None else _multiples(ttm, p, cap, ev, ttm=True)
    out["cases"] = {c: [{"fy": y["fy"], **_multiples(y, p, cap, ev)} for y in ys]
                    for c, ys in cases.items()}
    return out


# --- the run --------------------------------------------------------------------------------

def run(inputs: dict, state: dict) -> dict:
    """A model state (validated, workspaces.ModelState) against its company's inputs."""
    adjustments = list(state.get("adjustments") or [])
    years = history(inputs, adjustments)
    st = stats(years)
    rr = run_rate(inputs, adjustments, years)
    q = _by_period(inputs.get("quarters") or [])
    with_shares = sorted((k for k, lines in q.items() if "weighted_shares_diluted" in lines),
                         key=lambda k: str(q[k]["weighted_shares_diluted"][1].get("period_end")))
    latest_shares = q[with_shares[-1]]["weighted_shares_diluted"][0] if with_shares else None
    rr["_latest_shares"] = latest_shares
    base = base_year(years)
    ytd = bool(state.get("ytd"))
    cur = current_year(inputs, adjustments, years)
    n = len(state.get("assumptions") or []) or 1
    defs = defaults(st, rr, n, cur if ytd else None)
    cases: dict[str, Any] = {}
    projected: dict[str, list[dict]] = {}
    base_years = case_years(state, "base") or []
    for case in ("bear", "base", "bull"):
        ys = case_years(state, case)
        preview = False
        if ys is None:
            if not base_years:
                continue
            ys, preview = shifted(base_years, case), True
        case_defs = defs if case == "base" else shifted(defs, case)
        proj = project(years, ys, case_defs, adjustments, cur if ytd else None, rr)
        projected[case] = proj
        entry: dict[str, Any] = {"preview": preview, "years": proj, "defaults": case_defs}
        if case == "base":
            entry["check"] = check(st, base["fy"] if base else None, proj, ys)
            entry["user_set"] = user_set(st, ys, defs)
        cases[case] = entry
    ttm = ttm_column(inputs, adjustments)
    rr_out = {k: v for k, v in rr.items() if not k.startswith("_")}
    implied = None
    if not ytd and cur and cur["quarters"] and base_years:
        implied = _implied_remainder(cur, base, base_years[0])
    return {
        "company_id": state.get("companyId"),
        "base_fy": base["fy"] if base else None,
        "ytd": ytd,
        "history": years,
        "ttm": ttm,
        "current": cur,
        "implied_remainder": implied,
        "stats": st,
        "run_rate": rr_out,
        "long_run": {
            "growth": _clamp(st["cagr"] * 100 if st["cagr"] is not None else
                             (rr["growth"] or 0.0) * 100, (0.0, LONG_RUN_GROWTH_CAP)),
            "margin": None if st["avg_margin"] is None else st["avg_margin"] * 100,
            "fcf_margin": None if st["avg_fcf_margin"] is None else st["avg_fcf_margin"] * 100},
        "cases": cases,
        "adjustments": adjustments,
        "valuation": valuation(state.get("price"), inputs.get("latest") or {}, years, ttm,
                               projected, latest_shares),
    }


def _implied_remainder(cur: dict, base: dict | None, year1: dict) -> dict | None:
    """For a model saved before F2 (year 1 a full-year guess): the remainder its year 1 implies
    given the reported quarters, so it can be rebuilt from actuals without changing the year."""
    if base is None or base["revenue"] is None:
        return None
    act, prior = cur["actual"], cur["remainder_prior"]
    rev = base["revenue"] * (1 + float(year1["growth"]) / 100)
    oi = rev * float(year1["margin"]) / 100
    r_rev = rev - (act["revenue"] or 0.0)
    r_oi = oi - (act["operating_income_adjusted"] or 0.0)
    g = _growth(prior["revenue"], r_rev)
    m = _div(r_oi, r_rev)
    if g is None or m is None:
        return None
    return {"growth": g * 100, "margin": m * 100}
