"""Valuation from the caller's own price and gold financials ("Connect your data").

Inputs: the latest close in the caller's own prices (market_prices, app migration 011) and the
latest gold row of each metric below (Store.financials, view ttm: TTM sums for flows, the
quarter's balance for instants, gold's derived net_debt). Each figure keeps its own as-of
date and citation; nothing is aligned or restated.

  market cap = close × shares_outstanding (latest reported)
  P/E        = market cap / TTM net_income            (net income > 0, else not meaningful)
  P/S        = market cap / TTM revenue               (revenue > 0)
  EV         = market cap + net_debt                  (net_debt = long-term debt incl. current
                                                       - cash - short-term investments)
  EV/EBIT    = EV / TTM operating_income              (operating income > 0, net_debt known)

P/E is market cap over TTM net income rather than price over TTM diluted EPS: gold has no TTM
EPS (fiscal Q4 EPS is never reported or derived, migration 014). The two differ by the
diluted-versus-outstanding share count and by preferred dividends. EBIT is operating income.
Companies with several share classes (several current tickers) price one class against the
total share count: the result says so (`share_classes`).

Values are exact decimal strings (multiples rounded to 2 places, market cap/EV to whole
dollars), so JSON never turns them into floats."""

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from query_api.shapes import filing_url

METRICS = ["shares_outstanding", "net_income", "revenue", "operating_income", "net_debt"]
FORMULAS = {
    "market_cap": "close × shares_outstanding",
    "pe": "market cap / TTM net income",
    "ps": "market cap / TTM revenue",
    "ev": "market cap + net debt",
    "ev_ebit": "(market cap + net debt) / TTM operating income",
}
TWO = Decimal("0.01")
ONE = Decimal("1")


def _figure(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {
        "value": str(row["value"]), "unit": row["unit"], "period_end": row["period_end"],
        "citation": {"accession_no": row["accession_no"], "form": row["form"],
                     "filed": row["filed"], "xbrl_tag": row.get("source_tag"),
                     "filing_url": filing_url(row.get("filer_cik"), row["accession_no"])},
    }


def _ratio(num: Decimal, den: Decimal | None, why: str) -> dict:
    if den is None:
        return {"value": None, "note": f"no {why} in the filings"}
    if den <= 0:
        return {"value": None, "note": f"not meaningful: {why} is zero or negative"}
    return {"value": str((num / den).quantize(TWO, ROUND_HALF_EVEN)), "note": None}


def valuation(price: dict | None, gold: list[dict], tickers: list[str]) -> dict[str, Any]:
    """price: {day, close, ticker} or None; gold: Store.financials rows (latest per metric)."""
    rows = {r["metric"]: r for r in gold if r["metric"] in METRICS}
    val = {m: Decimal(str(rows[m]["value"])) if m in rows else None for m in METRICS}
    out: dict[str, Any] = {
        "price": ({"close": str(price["close"]), "day": price["day"], "ticker": price["ticker"]}
                  if price else None),
        "inputs": {m: _figure(rows.get(m)) for m in METRICS},
        "formulas": FORMULAS,
        "share_classes": tickers if len(tickers) > 1 else None,
        "market_cap": None, "enterprise_value": None,
        "multiples": {},
    }
    shares = val["shares_outstanding"]
    if price is None or shares is None or shares <= 0:
        out["note"] = ("no close in the last 14 days" if price is None
                       else "no shares outstanding in the filings: market cap unknown")
        return out
    cap = Decimal(str(price["close"])) * shares
    out["market_cap"] = {"value": str(cap.quantize(ONE, ROUND_HALF_EVEN)),
                         "price_day": price["day"],
                         "shares_period_end": rows["shares_outstanding"]["period_end"]}
    multiples = {"pe": _ratio(cap, val["net_income"], "TTM net income"),
                 "ps": _ratio(cap, val["revenue"], "TTM revenue")}
    if val["net_debt"] is not None:
        ev = cap + val["net_debt"]
        out["enterprise_value"] = {"value": str(ev.quantize(ONE, ROUND_HALF_EVEN)),
                                   "net_debt_period_end": rows["net_debt"]["period_end"]}
        multiples["ev_ebit"] = _ratio(ev, val["operating_income"], "TTM operating income")
    else:
        multiples["ev_ebit"] = {"value": None, "note": "no net debt in the filings"}
    out["multiples"] = multiples
    out["note"] = None
    return out

