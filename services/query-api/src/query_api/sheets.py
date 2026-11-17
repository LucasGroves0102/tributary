"""Spreadsheets: many figures in one request, for the Excel add-in and the Google Sheets script
(services/web/public/addins). `POST /v1/sheets/values`.

The body is up to MAX_REQUESTS asks, each one company's metric for one period, or for its newest
`count` periods (a series): {company, metric, period, as_of, shares, currency, count}. Each ask is
answered on its own, in order, so one bad cell never fails the others: `value` only when a sheet
may show the figure as a number, else `status` and `reason` say why, in words a cell can show.

Access, exactly as the single-company routes. The user is the token's or the API key's own row.
Companies resolve by ticker or CIK (silver.resolve_company: identity is public metadata). For each
distinct company, ONE allowed() (OpenFGA, financials tier) before anything of it is read; then
every read is an existing Store method under RLS as that user. A name that matches nothing and a
company the caller can't read get the same answer (`not_permitted`, the single routes' words, no
company id). A request counts as ONE against an API key's limits; it reads at most MAX_COMPANIES
distinct (company, as_of) pairs, counted on the names as sent (no oracle), and MAX_COUNT periods
per series. OpenFGA down: 503 for the whole request (fail closed).

Periods (`period`; case and spaces free; default latest):
  latest        the newest quarter          TTM         the newest trailing twelve months
  FY            the newest fiscal year      FY2024      fiscal year 2024
  FY2024 Q3     that fiscal quarter         CY2024 Q3   that calendar quarter (one per quarter)
  2024-09-30    the quarter ending that day
  TTM FY2024 Q3, TTM CY2024 Q3, TTM 2024-09-30: the twelve months to that quarter.
A fiscal year is the trailing twelve months to its fourth quarter (a balance: its year-end
figure), as gold labels them. EPS and weighted shares go through per_share.py, the one copy of
their rules (split-adjusted unless `shares` is as_filed): a fiscal year is the 10-K's own figure,
found by its end within per_share.MATCH_DAYS of the fourth quarter's; TTM is per_share's. A
`count` needs a newest-period form (latest, TTM or FY) and answers `points`, newest first.

Readings: the database decides (gold.latest_reading, 035-087); this module only applies them.
  latest, TTM       gold.latest_reading's status as is: current and behind are numbers; old,
                    stale, not_meaningful, not_applicable, not_computed and other_currency say so.
  FY, EPS, shares   latest_reading's verdicts on the metric (not_applicable, not_computed, stale,
                    old), the row's own not_meaningful, then gold.period_status of its period.
  a named period    and every point of a series: the row's not_meaningful and the metric's
                    not_applicable; never stale or old, since the period was asked for by name
                    (how to read a stale figure anyway). No row: not_computed where gold
                    computes none for this kind of company, else no_figure.
Currency: an amount is a number only in the currency asked (`currency`, default USD); Tributary
never converts, so another currency reads other_currency. Asked in its own currency, a latest
amount reads its age alone (gold.period_status), as model_inputs does. Ratios and share counts
have no currency.

Every figure cites its filing (`citation`: accession, form, filed, EDGAR link). Nothing new is
computed here: values are Store's and per_share's, exact decimal strings."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from query_api import per_share, point_in_time
from query_api.formula import suggest
from query_api.shapes import currency_of, filing_url
from query_api.store import Store

MAX_REQUESTS = 200
MAX_COMPANIES = 25
MAX_COUNT = 40
ALL_QUARTERS = 120  # a named period can be any quarter gold has (/financials' cap)
NUMBERS = frozenset({"current", "behind", "ok"})  # statuses whose value a sheet shows
NOT_PERMITTED = "not permitted to read financials for this company"  # the single routes' 403
# every answer has every key (a refusal's are null): one shape for the clients
EMPTY = {"company_id": None, "metric": None, "span": None, "status": None, "reason": None,
         "value": None, "period": None, "period_end": None, "unit": None, "currency": None,
         "citation": None}
PERIOD_FORMS = ("latest, TTM, FY, FY2024, FY2024 Q3, CY2024 Q3, 2024-09-30, or TTM before a "
                "quarter")
_NAME = re.compile(r"^[A-Z0-9][A-Z0-9.\-/]{0,19}$")
_PERIOD = re.compile(
    r"^(?:(?P<ttm>TTM)\s*)?(?:"
    r"(?P<latest>LATEST)?"
    r"|(?P<fyword>FY)\s*(?P<fy>\d{4})?(?:\s*Q(?P<fq>[1-4]))?"
    r"|CY\s*(?P<cy>\d{4})\s*Q(?P<cq>[1-4])"
    r"|(?P<day>\d{4}-\d{2}-\d{2}))$")

Span = Literal["quarter", "ttm", "year"]


@dataclass(frozen=True)
class Period:
    span: Span
    kind: Literal["latest", "fiscal", "calendar", "day"]
    year: int | None = None
    quarter: int | None = None
    day: date | None = None

    @property
    def view(self) -> str:
        """gold's span: a fiscal year is a TTM row."""
        return "quarter" if self.span == "quarter" else "ttm"

    @property
    def basis(self) -> str:
        return "calendar" if self.kind == "calendar" else "fiscal"


def parse_period(text: str | None) -> Period:
    """The module docstring's period forms; ValueError with the words a cell shows."""
    m = _PERIOD.fullmatch(" ".join((text or "").upper().split()))
    if m is None:
        raise ValueError(f"period: one of {PERIOD_FORMS}")
    ttm = m["ttm"] is not None
    if m["day"]:
        try:
            day = date.fromisoformat(m["day"])
        except ValueError:
            raise ValueError("period: a day as YYYY-MM-DD") from None
        return Period("ttm" if ttm else "quarter", "day", day=day)
    if m["cy"]:
        return Period("ttm" if ttm else "quarter", "calendar", int(m["cy"]), int(m["cq"]))
    if m["fq"]:
        if not m["fy"]:
            raise ValueError("period: a fiscal quarter names its year (FY2024 Q3)")
        return Period("ttm" if ttm else "quarter", "fiscal", int(m["fy"]), int(m["fq"]))
    if m["fyword"]:  # FY or FY2024 (a TTM before it changes nothing: the same year)
        return Period("year", "fiscal", int(m["fy"])) if m["fy"] else Period("year", "latest")
    return Period("ttm" if ttm else "quarter", "latest")


def label(period: Period | str, row: dict) -> str | None:
    """A row's period in the forms parse_period reads back: FY2027 Q2, TTM CY2026 Q2, FY2026."""
    span = period.span if isinstance(period, Period) else period
    calendar = isinstance(period, Period) and period.kind == "calendar"
    if span == "year":
        return f"FY{row['fiscal_year']}" if row.get("fiscal_year") else None
    prefix, year, quarter = (("CY", row.get("calendar_year"), row.get("calendar_quarter"))
                             if calendar else
                             ("FY", row.get("fiscal_year"), row.get("fiscal_quarter")))
    if not year or not quarter:
        return None
    return f"{'TTM ' if span == 'ttm' else ''}{prefix}{year} Q{quarter}"


class Ask(BaseModel):
    """One figure, or a series. Loosely typed on purpose: a bad cell is that ask's `invalid`
    answer, never a 422 for the whole sheet."""
    model_config = ConfigDict(extra="forbid")
    company: str = Field(max_length=200)
    metric: str = Field(max_length=200)
    period: str | None = Field(None, max_length=200)
    as_of: str | None = Field(None, max_length=200)
    shares: str | None = Field(None, max_length=200)
    currency: str | None = Field(None, max_length=200)
    count: int | None = None


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requests: list[Ask] = Field(min_length=1, max_length=MAX_REQUESTS)


@dataclass(frozen=True)
class Checked:
    """An ask that passed every check that needs no database."""
    name: str          # the company as sent, upper case
    metric: str
    period: Period
    known_on: date | None
    shares: str
    currency: str
    count: int | None

    @property
    def per_share(self) -> bool:
        return self.metric in per_share.METRICS

    @property
    def reading_span(self) -> str:
        """The latest reading whose verdicts apply: per-share figures' quarters (gold has no TTM
        EPS), else the ask's own span."""
        return "quarter" if self.per_share else self.period.view

    @property
    def depth(self) -> int:
        """How many rows per metric to read for it (newest first)."""
        if self.period.kind != "latest":
            return ALL_QUARTERS
        if self.count is not None:
            return min(ALL_QUARTERS, 4 * self.count + 3) if self.period.span == "year" \
                else self.count
        return 8 if self.period.span == "year" else 1


def check(ask: Ask, metrics: list[str], today: date) -> Checked | dict:
    """The ask, checked; or its `invalid` answer."""
    name = " ".join(ask.company.upper().split())
    if not _NAME.fullmatch(name):
        return invalid("company: a ticker (NVDA) or a CIK (1045810)")
    metric = ask.metric.strip().lower()
    if metric not in metrics:
        s = suggest(metric, metrics) if len(metric) <= 40 else None  # bounded work per cell
        return invalid(f"unknown metric {metric[:60]!r}" + (f": did you mean {s}?" if s else
                                                              ""))
    try:
        period = parse_period(ask.period)
    except ValueError as e:
        return invalid(str(e))
    known_on = None
    if ask.as_of and ask.as_of.strip():
        try:
            known_on = point_in_time.check(date.fromisoformat(ask.as_of.strip()), today)
        except ValueError:
            return invalid("as_of: a day as YYYY-MM-DD")
        except HTTPException as e:
            return invalid(str(e.detail))
    shares = (ask.shares or "adjusted").strip().lower()
    if shares not in ("adjusted", "as_filed"):
        return invalid("shares: adjusted or as_filed")
    currency = (ask.currency or "USD").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        return invalid("currency: a three-letter code such as USD or EUR")
    if ask.count is not None:
        if not 1 <= ask.count <= MAX_COUNT:
            return invalid(f"count: 1 to {MAX_COUNT} periods")
        if period.kind != "latest":
            return invalid("count: a series runs back from latest, TTM or FY")
    return Checked(name, metric, period, known_on, shares, currency, ask.count)


def invalid(reason: str) -> dict:
    return {"status": "invalid", "reason": reason, "value": None}


def refused(status: str, reason: str | None) -> dict:
    return {"status": status, "reason": reason, "value": None}


def _day(v: Any) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _when(d: date) -> str:
    """gold's own date words (FMMon FMDD, YYYY): Sep 30, 2024."""
    return f"{d:%b} {d.day}, {d.year}"


def _currency_gate(unit: str | None, wanted: str) -> dict | None:
    cur = currency_of(unit)
    if cur is None or cur == wanted:
        return None
    return refused("other_currency",
                   f"reported in {cur}, and Tributary doesn't convert currencies: ask for {cur} "
                   "(the currency argument) to read it")


def _old(period_end: date | None) -> dict:
    since = f" since the period ended {_when(period_end)}" if period_end else ""
    return refused("old", f"not reported{since}: older than the company's latest figures, so "
                          "not its current figure (ask for that period by name to read it)")


# --- one company's figures --------------------------------------------------------------------

@dataclass
class _Fig:
    """A row from Store or per_share, as the answer needs it."""
    period: str | None
    period_end: date | None
    value: Any
    unit: str | None
    accession_no: str | None
    form: str | None
    filed: Any
    filer_cik: str | None
    not_meaningful: str | None = None


def _from_row(span: Period | str, row: dict) -> _Fig:
    return _Fig(label(span, row), _day(row["period_end"]), row["value"], row.get("unit"),
                row.get("accession_no"), row.get("form"), row.get("filed"),
                row.get("filer_cik"), row.get("not_meaningful"))


def _from_per_share(row: dict, period: str | None) -> _Fig:
    c = row["citation"]
    return _Fig(period, _day(row["period_end"]), row["value"], row.get("unit"),
                c.get("accession_no"), c.get("form"), c.get("filed"), c.get("filer_cik"))


def _shown(fig: _Fig, base: dict) -> dict:
    """`base` (a status) with the figure's period, unit and citation; the value only when the
    status is a number's."""
    return {**base,
            "value": None if base["status"] not in NUMBERS or fig.value is None
            else str(fig.value),
            "period": fig.period, "period_end": fig.period_end, "unit": fig.unit,
            "currency": currency_of(fig.unit),
            "citation": None if not fig.accession_no else {
                "accession_no": fig.accession_no, "form": fig.form, "filed": fig.filed,
                "filing_url": filing_url(fig.filer_cik, fig.accession_no)}}


class _Company:
    """Every read for one (company, as_of), each through an existing Store method, under RLS."""

    def __init__(self, store: Store, user_id: str, company_id: str, known_on: date | None,
                 asks: list[Checked]):
        self.store, self.user_id, self.company_id, self.known_on = (store, user_id, company_id,
                                                                    known_on)
        need: dict[tuple[str, str], tuple[set[str], int]] = {}
        for a in asks:
            if a.per_share:
                continue
            key = (a.period.view, a.period.basis)
            metrics, depth = need.get(key, (set(), 0))
            need[key] = (metrics | {a.metric}, max(depth, a.depth))
        self.rows: dict[tuple[str, str], dict[str, list[dict]]] = {}
        for (view, basis), (metrics, depth) in sorted(need.items()):
            by_metric: dict[str, list[dict]] = {}
            for r in store.financials(user_id, company_id, sorted(metrics), depth, view, basis,
                                      known_on):
                by_metric.setdefault(r["metric"], []).append(r)  # newest first per metric
            self.rows[(view, basis)] = by_metric
        self.share_rows: dict[tuple[str, str], dict[str, list[dict]]] = {}
        wanted = {(_PER_SHARE_VIEW[a.period.span], a.shares) for a in asks if a.per_share}
        wanted |= {("quarter", a.shares) for a in asks if a.per_share and a.period.span == "year"}
        if wanted:
            inputs = store.per_share_inputs(user_id, company_id, known_on)
            for view, shares in sorted(wanted):
                by_metric = {}
                for r in per_share.compute(inputs, view, shares)["rows"]:  # newest first
                    by_metric.setdefault(r["metric"], []).append(r)
                self.share_rows[(view, shares)] = by_metric
        self.figs = [self._figures(a) for a in asks]
        # one readings query: the metrics' latest readings and the ages of the latest-type rows
        dates = sorted({f.period_end for a, figs in zip(asks, self.figs, strict=True)
                        if a.count is None and a.period.kind == "latest"
                        and (a.per_share or a.period.span == "year")
                        for f in figs[:1] if f.period_end is not None})
        pairs = sorted({(a.reading_span, a.metric) for a in asks})
        self.readings = store.sheet_readings(user_id, company_id, pairs, dates, known_on)
        missing = sorted({a.metric for a, figs in zip(asks, self.figs, strict=True)
                          if not figs and (a.period.kind != "latest" or a.count is not None)})
        self.not_computed = store.not_computed(user_id, company_id, missing) if missing else {}

    def _figures(self, a: Checked) -> list[_Fig]:
        """The ask's figures, newest first: one for a single figure (none when there is
        nothing), up to `count` for a series."""
        p = a.period
        if a.per_share:
            return self._per_share_figures(a)
        rows = self.rows[(p.view, p.basis)].get(a.metric, [])
        if p.span == "year":  # a fiscal year: the TTM to its fourth quarter
            rows = [r for r in rows if r.get("fiscal_quarter") == 4]
        if p.kind == "latest":
            if a.count is not None:
                return [_from_row(p, r) for r in rows[:a.count]]
            return [_from_row(p, r) for r in rows[:1]]
        return [_from_row(p, r) for r in rows if _matches(p, r)][:1]

    def _per_share_figures(self, a: Checked) -> list[_Fig]:
        p = a.period
        rows = self.share_rows[(_PER_SHARE_VIEW[p.span], a.shares)].get(a.metric, [])
        if p.span == "year":  # the 10-K's own figures, named by the fourth quarter they end at
            quarters = self.share_rows[("quarter", a.shares)].get(a.metric, [])
            figs = []
            for r in rows:
                fy = _fiscal_year_of(_day(r["period_end"]), quarters)
                figs.append(_from_per_share(r, f"FY{fy}" if fy else None))
            if p.kind == "latest":
                return figs[:a.count or 1]
            return [f for f in figs if f.period == f"FY{p.year}"][:1]
        if p.basis == "calendar":
            rows = [r for r in rows if r.get("calendar_primary", True)]
        prefix = "TTM " if p.span == "ttm" else ""

        def named(r: dict) -> str | None:
            base = r.get("calendar_period") if p.basis == "calendar" else r.get("fiscal_period")
            return f"{prefix}{base}" if base else None

        if p.kind == "latest":
            return [_from_per_share(r, named(r)) for r in rows[:a.count or 1]]
        want = (f"{prefix}{'CY' if p.kind == 'calendar' else 'FY'}{p.year} Q{p.quarter}"
                if p.kind != "day" else None)
        for r in rows:
            if (p.kind == "day" and _day(r["period_end"]) == p.day) or \
                    (want is not None and named(r) == want):
                return [_from_per_share(r, named(r))]
        return []

    def answer(self, i: int, a: Checked) -> dict:
        figs = self.figs[i]
        latest = self.readings["latest"].get((a.reading_span, a.metric))
        out = {"company_id": self.company_id, "metric": a.metric, "span": a.period.span,
               **({"known_on": self.known_on} if self.known_on else {})}
        if a.count is not None:
            if latest and latest["status"] == "not_applicable":
                return {**out, **refused("not_applicable", latest["reason"])}
            points = [self._named(a, f) for f in figs]
            if not points:
                return {**out, **self._nothing(a)}
            return {**out, "status": "ok", "reason": None, "value": None, "points": points}
        if a.period.kind != "latest":
            if latest and latest["status"] == "not_applicable":
                return {**out, **refused("not_applicable", latest["reason"])}
            if not figs:
                return {**out, **self._nothing(a)}
            return {**out, **self._named(a, figs[0])}
        if not a.per_share and a.period.span != "year":
            return {**out, **self._latest(a, latest, figs)}
        return {**out, **self._newest(a, latest, figs)}

    def _nothing(self, a: Checked) -> dict:
        reason = self.not_computed.get(a.metric)
        if reason:
            return refused("not_computed", reason)
        return refused("no_figure", f"no {a.metric} figure for that period"
                       if a.period.kind != "latest" else f"no {a.metric} figure on file")

    def _named(self, a: Checked, fig: _Fig) -> dict:
        """A figure asked for by its period (or a series' point)."""
        if fig.not_meaningful:
            return _shown(fig, refused("not_meaningful", fig.not_meaningful))
        gate = _currency_gate(fig.unit, a.currency)
        return _shown(fig, gate or {"status": "ok", "reason": None})

    def _latest(self, a: Checked, x: dict | None, figs: list[_Fig]) -> dict:
        """latest and TTM: gold.latest_reading's own status."""
        if x is None or (x["value"] is None and x["status"] is None):
            return self._nothing(a)
        found = next((f for f in figs if f.period_end == x["period_end"]), None)
        fig = _Fig(found.period if found else None, x["period_end"], x["value"], x["unit"],
                   x["accession_no"], x["form"], x["filed"], x["filer_cik"])
        status = x["status"]
        if status == "other_currency":
            gate = _currency_gate(x["unit"], a.currency)
            if gate:
                return _shown(fig, gate)
            status = x["period_status"]  # in its own currency: its age alone (model_inputs)
        elif status not in ("not_applicable", "not_computed"):
            gate = _currency_gate(x["unit"], a.currency)  # e.g. EUR asked of a dollar figure
            if gate:
                return _shown(fig, gate)
        status = status or "current"
        if x["value"] is None and status in NUMBERS:
            return self._nothing(a)
        if status == "old":
            return _shown(fig, _old(fig.period_end))
        return _shown(fig, {"status": status,
                            "reason": None if status in NUMBERS else x["reason"]})

    def _newest(self, a: Checked, x: dict | None, figs: list[_Fig]) -> dict:
        """FY, EPS and shares: the metric's verdicts, the row's n/m, its own age."""
        if x is not None and x["status"] in ("not_applicable", "not_computed", "stale"):
            base = refused(x["status"], x["reason"])
            return _shown(figs[0], base) if figs else base
        if not figs:
            return self._nothing(a)
        fig = figs[0]
        if x is not None and x["status"] == "old":
            return _shown(fig, _old(fig.period_end))
        if fig.not_meaningful:
            return _shown(fig, refused("not_meaningful", fig.not_meaningful))
        gate = _currency_gate(fig.unit, a.currency)
        if gate:
            return _shown(fig, gate)
        age = self.readings["ages"].get(fig.period_end)
        if age == "old":
            return _shown(fig, _old(fig.period_end))
        return _shown(fig, {"status": age or "current", "reason": None})


_PER_SHARE_VIEW = {"quarter": "quarter", "ttm": "ttm", "year": "annual"}


def _matches(p: Period, r: dict) -> bool:
    if p.kind == "day":
        return _day(r["period_end"]) == p.day
    if p.kind == "calendar":
        return r.get("calendar_year") == p.year and r.get("calendar_quarter") == p.quarter
    if p.span == "year":
        return r.get("fiscal_year") == p.year
    return r.get("fiscal_year") == p.year and r.get("fiscal_quarter") == p.quarter


def _fiscal_year_of(end: date | None, quarters: list[dict]) -> int | None:
    """The fiscal year a 10-K's figure belongs to: the fourth quarter (per_share's quarter view)
    ending within per_share.MATCH_DAYS of it."""
    if end is None:
        return None
    for q in quarters:
        label_ = q.get("fiscal_period") or ""
        q_end = _day(q["period_end"])
        if label_.endswith(" Q4") and q_end is not None \
                and abs((q_end - end).days) <= per_share.MATCH_DAYS:
            return int(label_[2:6])
    return None


# --- the route --------------------------------------------------------------------------------

def add_sheet_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                     allowed: Callable[[str, str, str], bool],
                     metric_names: Callable[[], dict[str, list[str]]],
                     today: Callable[[], date]) -> None:
    """POST /v1/sheets/values (module docstring)."""
    User = Annotated[str, Depends(current_user)]

    @app.post("/v1/sheets/values", response_model=dict)
    def sheet_values(user_id: User, body: Batch) -> dict:
        """Up to 200 figures in one request, each answered on its own (module docstring):
        one allowed() per distinct company before any read, then Store under RLS."""
        names = metric_names()
        metrics = sorted(set(names.get("metrics", [])) | set(names.get("derived_metrics", [])))
        day = today()
        checked = [check(a, metrics, day) for a in body.requests]
        asks = {i: c for i, c in enumerate(checked) if isinstance(c, Checked)}
        if len({(c.name, c.known_on) for c in asks.values()}) > MAX_COMPANIES:
            raise HTTPException(422, f"at most {MAX_COMPANIES} companies (and as-of days) in one "
                                     "request: split it")
        resolved = store.resolve_companies(user_id, sorted({c.name for c in asks.values()})) \
            if asks else {}
        # layer 1, once per distinct company, before anything of it is read
        permitted = {cid: allowed(user_id, "financials", cid)
                     for cid in sorted(set(resolved.values()))}
        answers: list[dict | None] = [c if isinstance(c, dict) else None for c in checked]
        groups: dict[tuple[str, date | None], list[int]] = {}
        for i, c in asks.items():
            cid = resolved.get(c.name)
            if cid is None or not permitted.get(cid):
                answers[i] = refused("not_permitted", NOT_PERMITTED)
                continue
            groups.setdefault((cid, c.known_on), []).append(i)
        for (cid, known_on), idx in groups.items():
            company = _Company(store, user_id, cid, known_on, [asks[i] for i in idx])
            for n, i in enumerate(idx):
                answers[i] = company.answer(n, asks[i])
        return {"as_of": datetime.now(UTC).isoformat(),
                "limits": {"requests": MAX_REQUESTS, "companies": MAX_COMPANIES,
                           "count": MAX_COUNT},
                "values": [{"company": body.requests[i].company, **EMPTY, **(a or {})}
                           for i, a in enumerate(answers)]}
