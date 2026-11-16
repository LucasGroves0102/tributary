"""Per-share figures on one share basis: EPS and weighted shares by quarter, by year and for
trailing twelve months, split-adjusted by default, with derived fiscal Q4 and TTM figures (Riley,
test 6, 2026-11-15; owner decisions 1 and 2). The one pipeline the Statements tab reads for all
three views; gold keeps every figure as filed, and so does `/financials` (the agent's tool).

Inputs (Store.per_share_inputs, one company, under RLS): gold's quarters of eps_diluted, eps_basic,
weighted_shares_diluted, weighted_shares_basic and net_income (each as gold chose it: the latest
filing to state that quarter), the 10-K's own per-share facts by fiscal year (gold.annual_per_share,
036), and the company's restatement findings on share counts and EPS (ops.quality_findings).

1. Splits. A stock split is what query_api.notes calls one (`_split_events`): restated share counts
   at a clean ratio and EPS at its reciprocal, in the same period and filing. It takes effect for
   the figures filed before its FIRST restating filing; a figure filed on or after it is already on
   the new basis (a company restates every period it presents). NVIDIA: 4-for-1 (first restated in
   the 10-Q filed 2021-08-20) and 10-for-1 (2024-08-28).
2. Split-adjusted ("today's share basis"): a figure as filed, with every split after its own filing
   applied: shares x ratio, EPS / ratio. Where the company itself restated a period in a later
   filing, gold already holds that later figure, so only the splits after it apply ("the company's
   own later restated figures where filed, else the split ratio"). NVIDIA's FY2019 EPS $6.63 (10-K
   filed before both splits) reads $0.17 (/ 40), FY2020's $1.13 (restated once, in a 10-K after the
   4-for-1) reads $0.11 (/ 10). Each figure keeps `as_filed` and the splits it took (`splits`).
3. Fiscal Q4 (derived; a 10-K gives the year, not its fourth quarter). On the adjusted basis:
     shares Q4 = (year's weighted shares x the year's days - sum of Q1-Q3's shares x their days)
                 / Q4's days
   (a weighted average over the year is the day-weighted average of its quarters'), and
     EPS Q4 = Q4 net income x common / shares Q4,
   `common` = the year's EPS x shares / net income when that is more than 1% from 1 beyond the
   filed EPS's own rounding (half a cent: NVIDIA's FY2023 $0.17 is 2.4% off by rounding alone),
   since preferred dividends and participating securities come off net income before EPS
   (JPMorgan's 97%); else 1. Needs the year's Q1-Q3 shares, the 10-K's shares and EPS for the
   year (its end within 7 days of Q4's), and gold's four quarters of net income. Kept only when
   shares Q4 is within 25% of the year's and EPS Q4 within max($0.03, 15%) of the year's EPS less
   Q1-Q3's (else the inputs don't describe one share count).
4. TTM (derived; market convention): EPS = the four quarters' EPS added (derived Q4s included);
   weighted shares = the four quarters' day-weighted average. Four quarters in a row (each starting
   within 7 days of the last one's end), 350-380 days in all.
5. As filed (`basis=as_filed`): every figure as its filing states it; a derived Q4 or TTM only when
   all its inputs are on one share basis (no split between their filings), else none.

A derived figure says what it was made of (`derivation`: method, inputs with their periods); none is
ever stored. Units are the company's (056: "EUR/shares" for a euro filer's EPS)."""

from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from query_api.notes import _split_events

EPS = ("eps_diluted", "eps_basic")
SHARES = {"eps_diluted": "weighted_shares_diluted", "eps_basic": "weighted_shares_basic"}
METRICS = (*EPS, *SHARES.values())
INPUT_METRICS = (*METRICS, "net_income")
FINDING_METRICS = (*METRICS, "shares_outstanding")
MATCH_DAYS = 7          # a 10-K's year end and gold's Q4 end can differ by a few days
COMMON_SLACK = Decimal("0.01")
CENT = Decimal("0.005")  # half a cent: how far a filed EPS can be from its exact figure
SHARE_BAND = Decimal("0.25")
CHECK_FLOOR, CHECK_SHARE = Decimal("0.03"), Decimal("0.15")
TTM_DAYS = (350, 380)
EPS_PLACES = Decimal("0.0001")

View = Literal["quarter", "ttm", "annual"]
Basis = Literal["adjusted", "as_filed"]


def _day(v) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _days(start: date | None, end: date | None) -> int | None:
    return None if start is None or end is None else (end - start).days + 1


def splits(findings: list[dict]) -> list[dict]:
    """The company's stock splits, oldest first: {label, ratio, reverse, factor (shares x),
    first_filed,
    first_accession}. `factor` is what a share count filed before `first_filed` is multiplied by."""
    out = []
    for e in _split_events(findings):
        first = min(e["findings"], key=lambda r: str(r["detail"].get("restated_filed") or "9999"))
        filed = _day(first["detail"].get("restated_filed"))
        if filed is None:
            continue
        ratio = Decimal(str(e["ratio"]))
        out.append({"label": e["label"], "ratio": float(ratio), "reverse": e["reverse"],
                    "factor": (1 / ratio) if e["reverse"] else ratio,
                    "first_filed": filed,
                    "first_accession": first["detail"].get("restated_accession")})
    # one event per split: the same split seen in two clusters (rare) keeps its first
    seen: dict[tuple, dict] = {}
    for s in sorted(out, key=lambda s: s["first_filed"]):
        seen.setdefault((s["label"], s["first_filed"].year), s)
    return sorted(seen.values(), key=lambda s: s["first_filed"])


def _after(events: list[dict], filed: date | None) -> list[dict]:
    """The splits a figure filed then doesn't reflect yet."""
    if filed is None:
        return []
    return [s for s in events if s["first_filed"] > filed]


def _factor(applied: Iterable[dict]) -> Decimal:
    f = Decimal(1)
    for s in applied:
        f *= s["factor"]
    return f


def _scaled(metric: str, value: Decimal, factor: Decimal) -> Decimal:
    if factor == 1:
        return value
    if metric in EPS:
        return (value / factor).quantize(EPS_PLACES, rounding=ROUND_HALF_EVEN).normalize()
    return (value * factor).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)


def _split_words(s: dict) -> dict:
    return {"label": s["label"], "reverse": s["reverse"],
            "first_filed": s["first_filed"].isoformat(), "first_accession": s["first_accession"]}


def _period(prefix: str, year, quarter) -> str | None:
    return f"{prefix}{year} Q{quarter}" if year and quarter else None


class _Figure:
    """One figure as filed and on the requested basis."""

    __slots__ = ("row", "metric", "start", "end", "filed", "as_filed", "applied", "value")

    def __init__(self, row: dict, events: list[dict], basis: Basis):
        self.row = row
        self.metric = row["metric"]
        self.start, self.end = _day(row.get("period_start")), _day(row["period_end"])
        self.filed = _day(row.get("filed"))
        self.as_filed = _dec(row["value"])
        self.applied = _after(events, self.filed) if self.metric in METRICS else []
        f = _factor(self.applied) if basis == "adjusted" else Decimal(1)
        self.value = None if self.as_filed is None else _scaled(self.metric, self.as_filed, f)

    @property
    def key(self) -> Decimal:
        """Its share basis: the product of the splits it doesn't reflect yet."""
        return _factor(self.applied)


def _reported(fig: _Figure, basis: Basis, labels: dict | None = None) -> dict:
    r = fig.row
    labels = labels or r
    out = {
        "metric": fig.metric, "period_start": r.get("period_start"), "period_end": r["period_end"],
        "fiscal_period": _period("FY", labels.get("fiscal_year"), labels.get("fiscal_quarter")),
        "calendar_period": _period("CY", labels.get("calendar_year"),
                                   labels.get("calendar_quarter")),
        "calendar_primary": labels.get("calendar_primary", True),
        "value": None if fig.value is None else str(fig.value), "unit": r["unit"],
        "value_source": r.get("value_source") or "reported",
        "as_filed": None if fig.as_filed is None else str(fig.as_filed),
        "splits": [_split_words(s) for s in fig.applied] if basis == "adjusted" else [],
        "citation": {"accession_no": r["accession_no"], "form": r.get("form"),
                     "filed": r.get("filed"), "xbrl_tag": r.get("source_tag"),
                     "source_accessions": r.get("source_accessions") or [r["accession_no"]],
                     "filer_cik": r.get("filer_cik")},
        "freshness": {"filed": r.get("filed"), "ingested_at": r.get("ingested_at")},
    }
    return out


def _derived(metric: str, value: Decimal, unit: str, at: dict, method: str,
             inputs: list[dict], note: str | None = None) -> dict:
    """A derived figure, labelled like the quarter row `at` (its period, its citation)."""
    out = _reported_like(at)
    out.update({"metric": metric, "value": str(value), "unit": unit, "value_source": "derived",
                "as_filed": None, "splits": [],
                "derivation": {"method": method, "inputs": inputs,
                               **({"note": note} if note else {})}})
    return out


def _reported_like(at: dict) -> dict:
    return {
        "period_start": at.get("period_start"), "period_end": at["period_end"],
        "fiscal_period": _period("FY", at.get("fiscal_year"), at.get("fiscal_quarter")),
        "calendar_period": _period("CY", at.get("calendar_year"), at.get("calendar_quarter")),
        "calendar_primary": at.get("calendar_primary", True),
        "citation": {"accession_no": at["accession_no"], "form": at.get("form"),
                     "filed": at.get("filed"), "xbrl_tag": None,
                     "source_accessions": at.get("source_accessions") or [at["accession_no"]],
                     "filer_cik": at.get("filer_cik")},
        "freshness": {"filed": at.get("filed"), "ingested_at": at.get("ingested_at")},
    }


def _input(fig: _Figure, role: str) -> dict:
    return {"role": role, "metric": fig.metric, "period_end": fig.row["period_end"],
            "value": str(fig.value), "accession_no": fig.row["accession_no"],
            "form": fig.row.get("form")}


def _by_quarter(quarters: list[dict], events: list[dict],
                basis: Basis) -> tuple[list[_Figure], dict[tuple[str, int, int], _Figure]]:
    """Every quarter's figure, and (metric, fiscal_year, fiscal_quarter) → figure for the Q4 rule
    (a quarter without fiscal labels is shown, never used to derive one)."""
    figures: list[_Figure] = []
    by: dict[tuple[str, int, int], _Figure] = {}
    for r in quarters:
        if r["metric"] not in INPUT_METRICS:
            continue
        fig = _Figure(r, events, basis)
        if fig.value is None:
            continue
        figures.append(fig)
        if r.get("fiscal_year") is not None and r.get("fiscal_quarter") is not None:
            by.setdefault((r["metric"], r["fiscal_year"], r["fiscal_quarter"]), fig)
    return figures, by


def _annual(annual: list[dict], events: list[dict], basis: Basis) -> dict[str, list[_Figure]]:
    out: dict[str, list[_Figure]] = {}
    for r in annual:
        fig = _Figure(r, events, basis)
        if fig.value is not None:
            out.setdefault(r["metric"], []).append(fig)
    return out


def _year_of(annual: list[_Figure], end: date) -> _Figure | None:
    return next((a for a in annual if a.end and abs((a.end - end).days) <= MATCH_DAYS), None)


def _q4(eps_metric: str, fy: int, q: dict, annual: dict[str, list[_Figure]],
        basis: Basis) -> list[dict]:
    """A fiscal year's derived Q4 EPS and shares (module docstring, 3), or nothing."""
    shares_metric = SHARES[eps_metric]
    ni4 = q.get(("net_income", fy, 4))
    if ni4 is None or (eps_metric, fy, 4) in q or ni4.end is None:
        return []
    firsts = [q.get((shares_metric, fy, i)) for i in (1, 2, 3)]
    eps3 = [q.get((eps_metric, fy, i)) for i in (1, 2, 3)]
    nis = [q.get(("net_income", fy, i)) for i in (1, 2, 3)]
    year_shares = _year_of(annual.get(shares_metric, []), ni4.end)
    year_eps = _year_of(annual.get(eps_metric, []), ni4.end)
    if None in firsts or None in eps3 or None in nis or year_shares is None or year_eps is None:
        return []
    if basis == "as_filed" and len({f.key for f in (*firsts, *eps3, year_shares, year_eps)}) > 1:
        return []  # a split between their filings: one basis only when adjusted
    q1 = firsts[0]
    days_year = _days(year_shares.start, year_shares.end)
    days = [_days(f.start, f.end) for f in firsts]
    days4 = _days(ni4.start, ni4.end)
    if None in (days_year, days4, *days) or q1.start is None or year_shares.start is None \
            or abs((q1.start - year_shares.start).days) > MATCH_DAYS:
        return []
    shares4 = (year_shares.value * days_year  # type: ignore[operator]
               - sum(f.value * d for f, d in zip(firsts, days, strict=True))) / days4
    if shares4 <= 0 or abs(shares4 / year_shares.value - 1) > SHARE_BAND:
        return []
    ni_year = ni4.value + sum(n.value for n in nis)
    common = Decimal(1)
    if ni_year and year_eps.value and year_eps.as_filed:
        k = year_eps.value * year_shares.value / ni_year
        # the year's EPS is rounded to the cent as filed: NVIDIA's FY2023 $0.17 is 2.4% under
        # net income / shares by rounding alone, which is no preferred dividend
        if k > 0 and abs(k - 1) > COMMON_SLACK + CENT / abs(year_eps.as_filed):
            common = k
    eps4 = ni4.value * common / shares4
    rest = year_eps.value - sum(e.value for e in eps3)
    if abs(eps4 - rest) > max(CHECK_FLOOR, CHECK_SHARE * abs(eps4)):
        return []
    shares4 = shares4.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    eps4 = eps4.quantize(EPS_PLACES, rounding=ROUND_HALF_EVEN).normalize()
    at = {**ni4.row, "fiscal_quarter": 4}
    share_inputs = [_input(year_shares, "year"), *(_input(f, f"Q{i}") for i, f in
                                                  enumerate(firsts, start=1))]
    note = None if common == 1 else (
        f"net income available to common shareholders: the year's EPS x shares is "
        f"{(common * 100).quantize(Decimal('0.1'))}% of its net income")
    # its share basis: as filed, the one all its inputs share; adjusted, today's
    key = year_shares.key if basis == "as_filed" else Decimal(1)
    return [
        {**_derived(shares_metric, shares4, year_shares.row["unit"], at, "q4_shares", share_inputs),
         "_key": key},
        {**_derived(eps_metric, eps4, year_eps.row["unit"], at, "q4_eps",
                    [_input(ni4, "Q4 net income"),
                     {"role": "Q4 shares", "metric": shares_metric,
                      "period_end": ni4.row["period_end"], "value": str(shares4),
                      "accession_no": None, "form": None},
                     _input(year_eps, "year")], note), "_key": key},
    ]


def _consecutive(quarters: list[dict]) -> bool:
    ends = [_day(q["period_end"]) for q in quarters]
    starts = [_day(q.get("period_start")) for q in quarters]
    if None in ends or None in starts:
        return False
    for prev_end, start in zip(ends, starts[1:], strict=False):
        if not 0 <= (start - prev_end).days <= MATCH_DAYS:  # type: ignore[operator]
            return False
    span = (ends[-1] - starts[0]).days + 1  # type: ignore[operator]
    return TTM_DAYS[0] <= span <= TTM_DAYS[1]


def _ttm(series: dict[str, list[dict]], basis: Basis) -> list[dict]:
    """Twelve months to each quarter end (module docstring, 4). `series`: each metric's quarters
    (reported and derived Q4s), oldest first, each with its share basis `_key`."""
    out = []
    for eps_metric, shares_metric in SHARES.items():
        for metric, how in ((eps_metric, "ttm_eps"), (shares_metric, "ttm_shares")):
            rows = series.get(metric, [])
            for i in range(3, len(rows)):
                four = rows[i - 3:i + 1]
                if not _consecutive(four):
                    continue
                if basis == "as_filed" and len({r["_key"] for r in four}) > 1:
                    continue
                values = [Decimal(r["value"]) for r in four]
                if how == "ttm_eps":
                    value = sum(values, Decimal(0)).normalize()
                else:
                    days = [_days(_day(r["period_start"]), _day(r["period_end"])) for r in four]
                    total = sum(v * d for v, d in zip(values, days, strict=True))  # type: ignore
                    value = (total / sum(days)).quantize(  # type: ignore[arg-type]
                        Decimal(1), rounding=ROUND_HALF_EVEN)
                last = four[-1]
                at = {"period_start": four[0]["period_start"], "period_end": last["period_end"],
                      "fiscal_year": _fy(last), "fiscal_quarter": _fq(last),
                      "calendar_year": _cy(last), "calendar_quarter": _cq(last),
                      "calendar_primary": last.get("calendar_primary", True),
                      "accession_no": last["citation"]["accession_no"],
                      "form": last["citation"]["form"], "filed": last["citation"]["filed"],
                      "source_accessions": sorted({a for r in four
                                                   for a in r["citation"]["source_accessions"]}),
                      "ingested_at": last["freshness"]["ingested_at"],
                      "filer_cik": last["citation"]["filer_cik"]}
                out.append(_derived(metric, value, last["unit"], at, how, [
                    {"role": r["fiscal_period"] or r["period_end"], "metric": metric,
                     "period_end": r["period_end"], "value": r["value"],
                     "accession_no": r["citation"]["accession_no"], "form": r["citation"]["form"],
                     **({"derived": True} if r["value_source"] == "derived" else {})}
                    for r in four]))
    return out


def _fy(r):
    p = r.get("fiscal_period")
    return int(p[2:6]) if p else None


def _fq(r):
    p = r.get("fiscal_period")
    return int(p[-1]) if p else None


def _cy(r):
    p = r.get("calendar_period")
    return int(p[2:6]) if p else None


def _cq(r):
    p = r.get("calendar_period")
    return int(p[-1]) if p else None


def compute(inputs: dict, view: View, basis: Basis = "adjusted") -> dict:
    """{splits, rows} for one company (module docstring). Rows are newest first per metric."""
    events = splits(inputs.get("findings") or [])
    figures, q = _by_quarter(inputs.get("quarters") or [], events, basis)
    annual = _annual(inputs.get("annual") or [], events, basis)
    if view == "annual":
        rows = [_reported(f, basis) for m in METRICS for f in annual.get(m, [])]
    else:
        quarter_rows = [{**_reported(f, basis), "_key": f.key} for f in figures
                        if f.metric in METRICS]
        years = sorted({fy for (_, fy, _) in q})
        for eps_metric in EPS:
            for fy in years:
                quarter_rows += _q4(eps_metric, fy, q, annual, basis)
        if view == "quarter":
            rows = quarter_rows
        else:
            series: dict[str, list[dict]] = {}
            for r in sorted(quarter_rows, key=lambda r: str(r["period_end"])):
                series.setdefault(r["metric"], []).append(r)
            rows = _ttm(series, basis)
        for r in quarter_rows:
            r.pop("_key", None)
    rows.sort(key=lambda r: (r["metric"], str(r["period_end"])), reverse=True)
    return {"basis": basis, "splits": [_split_words(s) | {"ratio": s["ratio"]} for s in events],
            "rows": rows}

