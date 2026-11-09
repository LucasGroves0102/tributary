"""Data notes (Phase 6): what a reader should know about a company's figures before trusting
them. Sources (migration 023):

- ops.quality_findings: the checks ops.check_financials / ops.check_segments write after each
  gold refresh (gaps, overlapping or colliding quarters, annual sums, sign flips, outliers,
  impossible values, segment sums, restatements). detail holds dates, filings and relative
  sizes, never values.
- gold.quarterly_financials: quarters derived from year-to-date differences (mostly fiscal Q4).
- silver.filings (public SEC index) + the accession column of silver.financial_facts: 10-K /
  10-Q filings on EDGAR, filed on or after the latest filing in gold, whose XBRL the SEC's
  companyfacts API doesn't carry yet ("companyfacts behind EDGAR").

Findings are about values, so they are NOT public metadata: the company route calls
require(financials) and the cross-company route takes the companies from readable(); then
Store runs every query as query_api with app.user_id set, so RLS (ops.quality_findings,
ops.quality_companies, gold.quarterly_financials) filters again.

What a finding says is decided here, what it finds isn't: no check is suppressed, every finding
still reaches a note (ops.check_financials' rule: fix causes, never hide findings).
- A stock split isn't a restatement. Restated share counts and EPS move by reciprocal factors in the
  same filing (NVIDIA's weighted shares x10 and EPS x0.1, filed 2024-08-28, for the quarter ending
  2023-07-30). Those findings become one `stock_split` note, saying how many it covers. A clean
  factor without the other side stays a restatement, with `may_be_split`: real restatements are
  never folded into a split on a guess.
- A factor of exactly 1,000 or 1,000,000 is the filing's unit changing (NVIDIA's 2011 shares), said
  as `unit_change`.
- A one-quarter spike is `seasonal` when the same quarter peaks in another year too (Deckers'
  December operating cash flow), instead of being called a scale error, and drops to info.

Statement callouts (F5, `callouts`, on /financials with callouts=true): the same tests placed on
the statement's own cells, so a figure far outside its series is said where it is read. A quarter
is called out when, against the quarters beside it, it is
- an `outlier`: ops.check_financials' (041) outlier test, OUTLIER_FACTOR times the larger of the two
  quarters beside it, which agree (same sign, within NEIGHBOUR_SPREAD of each other); `seasonal`
  when it is the company's habit, by the test above;
- a `sign` change: 041's sign_flip test (the quarters beside it share a sign, it has the other), on
  041's cost metrics and operating cash flow, where a change of sign is news. Not on the cash flow
  subtotals or the change in cash, which change sign as a matter of course.
The newest quarter has no quarter after it, so 041 never tests it; here it is tested against the two
quarters before it (`edge`), which is how a step change shows (NVIDIA's dividends: 6,047M after 243M
and 242M). A callout says what the figure is next to, never that it is wrong: every one is as filed.

Ranked, grouped and summarised (F7a, M2: the notes cried wolf; on hermes 2026-11-09 the median
company had 34 findings, the 90th percentile 114, Vertiv 24 segment-sum findings for one revenue
series). No finding is dropped; what changes is how many notes they make and which come first:
- Repeats are one note: findings of one company, kind, metric (and segment axis, rule, unit
  change, possible split, season) are one note per series, dated by its newest, with `repeats`,
  the first and last period and up to MAX_PERIODS period ends in `detail`, and the newest filings
  (MAX_FILINGS).
- Each note has a `rank`: `attention`, `note` or `minor`, from a score (`_score`): severity
  (error 2, warning 1, info 0) + materiality (1: a restatement or annual mismatch of
  MATERIAL_CHANGE or more, any 10x spike, an impossible value) + recency (+1 within RECENT_YEARS
  of today, -1 older than OLD_YEARS) + a headline metric (HEADLINE: revenue, earnings, cash flow,
  debt, cash, equity). 4 or more is attention, 3 a note, less minor. Some kinds have a fixed
  place: filings missing from the figures are always attention; derived quarters, stock-based
  compensation benefits (050), unit changes and seasonal peaks are minor; segment sums and
  figures gold left out (mistagged, net debt gap) are at most a note.
- Notes come attention first, then note, then minor, each newest first (severity breaks ties); a
  group's rank is its most important member's. Both routes add `summary`: notes per rank and the
  findings behind them, so a page can say "3 need attention" before listing 40 minor ones.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Path, Query

from query_api.authorizer import Readable
from query_api.shapes import filing_url
from query_api.store import Store

CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]

# Kinds of the notes that don't come from ops.quality_findings.
DERIVED, BEHIND = "derived_quarters", "companyfacts_behind"
# Restatements of these, moving by reciprocal factors, are a stock split (or reverse split).
SHARE_METRICS = {"weighted_shares_basic", "weighted_shares_diluted", "shares_outstanding"}
EPS_METRICS = {"eps_basic", "eps_diluted"}
SPLIT = "stock_split"
# Split ratios companies use, old shares to new: 3-for-2, 5-for-4, 4-for-3 and whole numbers.
SPLIT_RATIOS: tuple[tuple[float, str], ...] = (
    (1.25, "5-for-4"), (4 / 3, "4-for-3"), (1.5, "3-for-2"),
    *((float(n), f"{n}-for-1") for n in range(2, 101)),
)
RATIO_TOLERANCE = 0.015  # a share count restated to within this of a clean ratio
# EPS is rounded to cents, so its side of the pair is loose: $0.29 / 10 reads $0.03, 3% off.
EPS_TOLERANCE = 0.15
SPLIT_EVENT_DAYS = 550  # restatements of one split arrive with comparatives for about a year
UNIT_FACTORS = (1e3, 1e6)
PEAK = 3  # another year's quarter this many times its neighbours: the spike is that quarter's habit
SEASON_YEARS = 2
# ops.check_financials' outlier test (migration 041), for the statement callouts: a quarter at least
# OUTLIER_FACTOR times the larger of the quarters beside it, which agree within NEIGHBOUR_SPREAD
OUTLIER_FACTOR = 10
NEIGHBOUR_SPREAD = 2
# 041's sign_flip metrics (costs, where the sign is a convention) and operating cash flow (F5)
SIGN_METRICS = frozenset({
    "cost_of_revenue", "research_and_development", "sga", "interest_expense", "capex",
    "depreciation_amortization", "stock_based_compensation", "dividends_paid", "share_buybacks",
    "operating_cash_flow"})

# detail keys that name a filing, and the role the UI shows for each
FILING_KEYS = {"accession_no": "source", "annual_accession": "annual",
               "original_accession": "original", "restated_accession": "restated",
               "total_accession": "total"}
FILING_LIST_KEYS = {"quarter_accessions": "quarter"}


def note_date(n: dict) -> date | None:
    """When the note happened, for "recent" ordering: the restating or missing filing's date, a
    mistagged fact's filing (its period end can be 2033, and would top every list until then), else
    the period end."""
    d = n.get("detail") or {}
    for key in ("restated_filed", "missing_filed", "filed"):
        if d.get(key):
            return _as_date(d[key])
    return _as_date(n.get("period_end"))


def accessions_of(n: dict) -> list[tuple[str, str]]:
    """(role, accession_no) of every filing a finding's detail names, in a stable order."""
    d = n.get("detail") or {}
    out = [(role, d[k]) for k, role in FILING_KEYS.items() if isinstance(d.get(k), str)]
    for k, role in FILING_LIST_KEYS.items():
        out += [(role, a) for a in d.get(k) or [] if isinstance(a, str)]
    return out


def split_ratio(factor: float) -> tuple[float, str, bool] | None:
    """(ratio, its name, reverse) when a share count's restatement factor is a clean split ratio."""
    if factor <= 0:
        return None
    reverse = factor < 1
    size = 1 / factor if reverse else factor
    for ratio, label in SPLIT_RATIOS:
        if abs(size - ratio) <= RATIO_TOLERANCE * ratio:
            if reverse:
                new, old = label.split("-for-")
                return ratio, f"{old}-for-{new}", True
            return ratio, label, False
    return None


def _change(r: dict) -> float | None:
    v = (r.get("detail") or {}).get("change")
    return float(v) if isinstance(v, int | float | str) and v != "" else None


def _eps_matches(change: float, factor: float) -> bool:
    """Whether EPS moved by the reciprocal of the share factor. `change` is (new - old) / |old|, so
    the factor is 1 + change on a profit and 1 - change on a loss; either will do."""
    return any(abs(f - 1 / factor) <= EPS_TOLERANCE / factor for f in (1 + change, 1 - change))


def _unit_change(change: float) -> bool:
    f = abs(1 + change)
    return any(abs(f - u) <= RATIO_TOLERANCE * u or abs(f * u - 1) <= RATIO_TOLERANCE
               for u in UNIT_FACTORS)


def _is_outlier_seasonal(series: list[tuple[date, float]], at: date) -> bool:
    """Whether this metric peaks in the same quarter of other years: within 20 days of a year, two
    or three apart, at least SEASON_YEARS of them have the same sign and PEAK times the quarters
    beside them. A scale error doesn't repeat; one coincidence of a lumpy buyback isn't a season."""
    value = next((v for d, v in series if d == at), None)
    if value is None:
        return False
    years = 0
    for i, (d, v) in enumerate(series):
        apart = abs((d - at).days)
        if d == at or not any(abs(apart - 365.25 * y) <= 20 for y in (1, 2, 3)):
            continue
        beside = [abs(series[j][1]) for j in (i - 1, i + 1) if 0 <= j < len(series)]
        if beside and v * value > 0 and abs(v) >= PEAK * max(beside):
            years += 1
    return years >= SEASON_YEARS


def is_outlier(value: float, a: float, b: float) -> bool:
    """041's outlier test: the quarters beside it agree (same sign, the larger at most
    NEIGHBOUR_SPREAD times the smaller) and it is at least OUTLIER_FACTOR times the larger."""
    if a == 0 or b == 0 or (a > 0) != (b > 0):
        return False
    big, small = max(abs(a), abs(b)), min(abs(a), abs(b))
    return big <= NEIGHBOUR_SPREAD * small and abs(value) >= OUTLIER_FACTOR * big


def is_sign_change(value: float, a: float, b: float) -> bool:
    """041's sign_flip test: none is zero, the quarters beside it share a sign, it has the other."""
    return value != 0 and a != 0 and b != 0 and (a > 0) == (b > 0) and (value > 0) != (a > 0)


def callouts(metric: str, series: list[tuple[date, float]]) -> list[dict]:
    """The statement callouts of one metric's quarters, oldest first (module docstring): interior
    quarters against the quarters either side, the newest against the two before it."""
    out: list[dict] = []
    n = len(series)
    for i, (end, value) in enumerate(series):
        if 0 < i < n - 1:
            beside, edge = (series[i - 1], series[i + 1]), False
        elif i == n - 1 and n >= 3:
            beside, edge = (series[i - 2], series[i - 1]), True
        else:
            continue
        a, b = beside[0][1], beside[1][1]
        if is_outlier(value, a, b):
            kind = "outlier"
        elif metric in SIGN_METRICS and is_sign_change(value, a, b):
            kind = "sign"
        else:
            continue
        out.append({
            "metric": metric, "period_end": end, "kind": kind, "edge": edge,
            "value": value,
            "factor": round(abs(value) / max(abs(a), abs(b)), 1) if kind == "outlier" else None,
            "beside": [{"period_end": d, "value": v} for d, v in beside],
            "seasonal": kind == "outlier" and _is_outlier_seasonal(series, end),
        })
    return out


def _split_events(findings: list[dict]) -> list[dict]:
    """Stock splits established by the findings themselves: for one company, restatements whose
    share counts moved by a clean ratio and whose EPS moved by its reciprocal in the same period and
    filing. Each event: {company_id, ratio, label, reverse, findings: every finding it explains}."""
    by_company: dict[str, list[dict]] = {}
    for r in findings:
        if r["check_name"] == "restatement" and r["metric"] in SHARE_METRICS | EPS_METRICS \
                and _change(r) is not None:
            by_company.setdefault(r["company_id"], []).append(r)
    events: list[dict] = []
    for company_id, rows in by_company.items():
        groups: dict[tuple[float, bool], list[dict]] = {}
        for r in rows:
            if r["metric"] in SHARE_METRICS and (s := split_ratio(1 + _change(r))):  # type: ignore[operator]
                groups.setdefault((s[0], s[2]), []).append(r)
        for (ratio, reverse), shares in groups.items():
            factor = 1 / ratio if reverse else ratio
            label = next(s[1] for r in shares
                         if (s := split_ratio(1 + _change(r))))  # type: ignore[operator]
            eps = [r for r in rows if r["metric"] in EPS_METRICS
                   and _eps_matches(_change(r), factor)]  # type: ignore[arg-type]
            for cluster in _clusters([*shares, *eps]):
                pairs = {(r["period_end"], r["detail"].get("restated_accession"))
                         for r in cluster if r["metric"] in SHARE_METRICS}
                if any((r["period_end"], r["detail"].get("restated_accession")) in pairs
                       for r in cluster if r["metric"] in EPS_METRICS):
                    events.append({"company_id": company_id, "ratio": ratio, "label": label,
                                   "reverse": reverse, "findings": cluster})
    return events


def _clusters(rows: list[dict]) -> Iterable[list[dict]]:
    """Restatements of one split: restating filings no more than SPLIT_EVENT_DAYS apart."""
    ordered = sorted(rows, key=lambda r: str(r["detail"].get("restated_filed") or ""))
    cluster: list[dict] = []
    last: date | None = None
    for r in ordered:
        filed = _as_date(r["detail"].get("restated_filed"))
        if cluster and filed and last and (filed - last).days > SPLIT_EVENT_DAYS:
            yield cluster
            cluster = []
        cluster.append(r)
        last = filed or last
    if cluster:
        yield cluster


def _plain_restatement(r: dict) -> dict:
    """What a restatement that isn't part of an established split can honestly add."""
    change = _change(r)
    if change is None:
        return {}
    if _unit_change(change):
        return {"unit_change": True}
    metric = r["metric"]
    factor = None
    if metric in SHARE_METRICS:
        factor = 1 + change
    elif metric in EPS_METRICS:  # the split that would explain it: EPS x 1/f, profit or loss
        factor = next((1 / f for f in (1 + change, 1 - change)
                       if f > 0 and split_ratio(1 / f)), None)
    s = split_ratio(factor) if factor else None
    return {"may_be_split": s[1]} if s else {}


def shape(findings: list[dict], derived: list[dict], behind: list[dict],
          filings: dict[str, dict],
          series: dict[tuple[str, str], list[tuple[date, float]]] | None = None,
          now: datetime | None = None) -> list[dict]:
    """One list of notes, most important first (module docstring, M2): findings as checked (a
    stock split's as one note, a series' repeats as one), a derived-quarters note per metric, and
    a companyfacts-behind note per company (listing every missing filing), each with its `rank`.
    `series`: (company, metric) → its quarters, oldest first, for the outliers."""

    def cite(role: str, acc: str) -> dict:
        f = filings.get(acc, {})
        return {"role": role, "accession_no": acc, "form": f.get("form"),
                "filed": f.get("filed"), "filing_url": filing_url(f.get("cik"), acc)}

    notes: list[dict] = []
    events = _split_events(findings)
    in_event = {id(r) for e in events for r in e["findings"]}
    for e in events:
        rows = e["findings"]
        restated = sorted({r["detail"]["restated_accession"] for r in rows
                           if isinstance(r["detail"].get("restated_accession"), str)})
        ends = sorted(r["period_end"] for r in rows if r["period_end"])
        notes.append({
            "company_id": e["company_id"], "kind": SPLIT, "severity": "info", "metric": None,
            "period_end": ends[-1] if ends else None,
            "detail": {"split": e["label"], "reverse": e["reverse"], "findings": len(rows),
                       "periods": len({r["period_end"] for r in rows}),
                       "metrics": sorted({r["metric"] for r in rows}),
                       "first_period_end": _iso(ends[0]) if ends else None,
                       "last_period_end": _iso(ends[-1]) if ends else None,
                       "restated_filed": max((str(r["detail"].get("restated_filed") or "")
                                              for r in rows), default=None)},
            "checked_at": rows[0].get("checked_at"),
            "filings": sorted((cite("restated", a) for a in restated),
                              key=lambda c: str(c["filed"] or ""), reverse=True),
        })
    for r in findings:
        if id(r) in in_event:
            continue
        n = {"company_id": r["company_id"], "kind": r["check_name"], "severity": r["severity"],
             "metric": r["metric"], "period_end": r["period_end"],
             "detail": dict(r["detail"] or {}),
             "checked_at": r.get("checked_at")}
        if n["kind"] == "restatement":
            n["detail"].update(_plain_restatement(r))
        elif n["kind"] == "outlier" and series and _is_outlier_seasonal(
                series.get((r["company_id"], r["metric"]), []), r["period_end"]):
            n["detail"]["seasonal"] = True
            n["severity"] = "info"
        n["filings"] = [cite(role, a) for role, a in accessions_of(n)]
        notes.append(n)
    for r in derived:
        notes.append({
            "company_id": r["company_id"], "kind": DERIVED, "severity": "info",
            "metric": r["metric"], "period_end": r["latest_period_end"],
            "detail": {"quarters": r["quarters"],
                       "first_period_end": _iso(r["first_period_end"]),
                       "latest_period_end": _iso(r["latest_period_end"]),
                       # gross profit that is revenue less cost, not a year-to-date difference (039)
                       **({"computed": True} if r.get("computed") else {})},
            "checked_at": None,
            "filings": [cite("source", r["latest_accession"])],
        })
    by_company: dict[str, list[dict]] = {}
    for r in behind:
        by_company.setdefault(r["company_id"], []).append(r)
    for company_id, rows in by_company.items():
        newest = max(rows, key=lambda r: (r["filing_date"], r["accession_no"]))
        notes.append({
            "company_id": company_id, "kind": BEHIND, "severity": "warning", "metric": None,
            "period_end": None,
            "detail": {"missing_filings": len(rows),
                       "missing_filed": _iso(newest["filing_date"]),
                       "facts_latest_filed": _iso(newest["facts_filed"])},
            "checked_at": None,
            "filings": [{"role": "missing", "accession_no": r["accession_no"],
                         "form": r["form"], "filed": r["filing_date"],
                         "filing_url": filing_url(r["cik"], r["accession_no"])}
                        for r in sorted(rows, key=lambda r: r["filing_date"], reverse=True)],
        })
    today = (now or datetime.now(UTC)).date()
    for n in notes:
        n["date"] = note_date(n)
        n["rank"] = rank(n, today)
    return order(group_repeats(notes))


# --- ranking and grouping (module docstring, M2) ---
RANKS = ("attention", "note", "minor")
HEADLINE = frozenset({
    "revenue", "gross_profit", "operating_income", "net_income", "eps_basic", "eps_diluted",
    "operating_cash_flow", "free_cash_flow", "capex", "cash", "long_term_debt",
    "long_term_debt_current", "net_debt", "total_assets", "total_liabilities",
    "stockholders_equity"})
MATERIAL_CHANGE = 0.05  # a restatement or a year's quarters off by 5% or more
RECENT_YEARS, OLD_YEARS = 2, 5
MAX_PERIODS, MAX_FILINGS = 60, 6
ALWAYS_MINOR = frozenset({DERIVED})
AT_MOST_NOTE = frozenset({"mistagged_fact", "net_debt_gap", "calendar_collision",
                          "overlapping_quarters"})


def _years_ago(d: date | None, today: date) -> float | None:
    return None if d is None else (today - d).days / 365.25


def _score(n: dict, today: date) -> int:
    d = n["detail"]
    score = {"error": 2, "warning": 1}.get(n["severity"], 0)
    change = d.get("change") if n["kind"] == "restatement" else d.get("deviation")
    if n["kind"] in ("outlier", "impossible_value") or (
            isinstance(change, int | float) and abs(change) >= MATERIAL_CHANGE
            and not d.get("unit_change")):
        score += 1
    age = _years_ago(n.get("date"), today)
    if age is not None:
        score += 1 if age <= RECENT_YEARS else -1 if age > OLD_YEARS else 0
    if n["metric"] is None or n["metric"] in HEADLINE:
        score += 1
    return score


def rank(n: dict, today: date) -> str:
    """attention | note | minor for one note (module docstring)."""
    kind, d = n["kind"], n["detail"]
    if kind == BEHIND:
        return "attention"
    if kind in ALWAYS_MINOR or d.get("seasonal") is True or d.get("rule") == "benefit" \
            or d.get("unit_change") is True:
        return "minor"
    score = _score(n, today)
    r = "attention" if score >= 4 else "note" if score >= 3 else "minor"
    if (kind in AT_MOST_NOTE or kind.startswith("segment_sum_")) and r == "attention":
        return "note"
    return r


def _group_key(n: dict) -> tuple | None:
    if n["kind"] in (BEHIND, SPLIT, DERIVED):
        return None  # already one note each
    d = n["detail"]
    return (n["company_id"], n["kind"], n["metric"], d.get("axis"), d.get("rule"),
            d.get("unit_change"), d.get("may_be_split"), d.get("seasonal"))


def group_repeats(notes: list[dict]) -> list[dict]:
    """Findings of one series as one note (module docstring): the newest note's text and
    detail, plus `repeats`, `first_period_end`, `last_period_end` and `periods` when there are
    several; the group's most important rank and highest severity."""
    groups: dict[tuple, list[dict]] = {}
    out: list[dict] = []
    for n in notes:
        key = _group_key(n)
        if key is None:
            out.append(n)
        else:
            groups.setdefault(key, []).append(n)
    for members in groups.values():
        if len(members) == 1:
            out.append(members[0])
            continue
        members.sort(key=lambda m: (m["date"] or date.min, _SEVERITY.get(m["severity"], 0)),
                     reverse=True)
        head = dict(members[0])
        ends = sorted({m["period_end"] for m in members if m["period_end"]})
        filings: list[dict] = []
        for m in members:
            for f in m["filings"]:
                if all(f["accession_no"] != g["accession_no"] for g in filings):
                    filings.append(f)
        head["detail"] = {**members[0]["detail"], "repeats": len(members),
                          "first_period_end": _iso(ends[0]) if ends else None,
                          "last_period_end": _iso(ends[-1]) if ends else None,
                          "periods": [_iso(e) for e in ends[-MAX_PERIODS:]]}
        head["filings"] = filings[:MAX_FILINGS]
        head["severity"] = max((m["severity"] for m in members),
                               key=lambda v: _SEVERITY.get(v, 0))
        head["rank"] = min((m["rank"] for m in members), key=RANKS.index)
        out.append(head)
    return out


def order(notes: list[dict]) -> list[dict]:
    """Attention, note, minor; each newest first, then by severity."""
    return sorted(notes, key=lambda n: (-RANKS.index(n.get("rank", "note")),
                                        n["date"] or date.min,
                                        _SEVERITY.get(n["severity"], 0)), reverse=True)


def summary(notes: list[dict], findings: int) -> dict:
    """Notes per rank, and the findings and other notes behind them."""
    out: dict[str, int] = {r: 0 for r in RANKS}
    for n in notes:
        out[n.get("rank", "note")] += 1
    return {**out, "findings": findings}


COMPANY_FINDINGS = 1000  # newest findings per company page (restatements can be many)
MAX_COMPANIES = 50  # as /v1/filing-index: a watchlist; beyond it the URL and the id list get long
SPLIT_ROOM, MIN_RAW_FINDINGS = 10, 200
_SEVERITY = {"error": 2, "warning": 1, "info": 0}


def add_notes_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                     require: Callable[[str, str, str], None],
                     readable: Callable[[str], Readable]) -> None:
    User = Annotated[str, Depends(current_user)]

    def load(user_id: str, company_ids: list[str] | None, derived: bool,
             limit: int) -> tuple[list[dict], Any, int]:
        raw = store.data_notes(user_id, company_ids, derived, limit)
        accs = sorted({a for r in raw["findings"]
                       for _, a in accessions_of({"detail": r["detail"]})}
                      | {r["latest_accession"] for r in raw["derived"]})
        filings = store.filing_refs(user_id, accs) if accs else {}
        spikes = [r for r in raw["findings"] if r["check_name"] == "outlier"]
        series = store.metric_series(user_id, sorted({r["company_id"] for r in spikes}),
                                     sorted({r["metric"] for r in spikes})) if spikes else {}
        return (shape(raw["findings"], raw["derived"], raw["behind"], filings, series),
                raw["checked_at"], len(raw["findings"]))

    @app.get("/v1/companies/{company_id}/data-notes", response_model=dict)
    def company_notes(user_id: User, company_id: CompanyId) -> dict:
        """Data-quality and freshness notes for one company: gaps, overlapping quarters,
        restatements, derived quarters, filings the SEC's XBRL API doesn't reflect yet. Each
        names its filings. Needs the financials tier (the notes are about values)."""
        require(user_id, "financials", company_id)
        notes, checked, findings = load(user_id, [company_id], True, COMPANY_FINDINGS)
        return {"company_id": company_id, "as_of": _now(), "checked_at": checked,
                "summary": summary(notes, findings), "notes": notes}

    @app.get("/v1/data-notes", response_model=dict)
    def recent_notes(user_id: User,
                     limit: Annotated[int, Query(ge=1, le=100)] = 20,
                     company_id: Annotated[list[str] | None, Query(
                         description=f"only these companies (at most {MAX_COMPANIES}): a "
                                     "watchlist; default: the companies you can read")] = None,
                     ) -> dict:
        """The newest notes across the companies whose financials the caller can read, or those
        of them named (without the standing derived-quarters notes)."""
        if company_id is not None and (
                len(company_id) > MAX_COMPANIES
                or any(len(c) != 10 or not c.isdigit() for c in company_id)):
            raise HTTPException(422, f"company_id: at most {MAX_COMPANIES} 10-digit ids")
        access = readable(user_id)
        granted = access.ids("financials")
        if company_id is not None:
            granted = sorted(c for c in set(company_id) if "financials" in access.get(c, set()))
        # several findings can read as one note, so ask for enough to fill the page
        raw_limit = max(limit * SPLIT_ROOM, MIN_RAW_FINDINGS)
        notes, checked, findings = (load(user_id, granted, False, raw_limit) if granted != []
                                    else ([], None, 0))
        # layer 1 on every note, also when no id list went into the query
        notes = [n for n in notes if "financials" in access.get(n["company_id"], set())]
        companies = len(granted) if granted is not None else sum(
            "financials" in tiers for tiers in access.values())
        return {"as_of": _now(), "checked_at": checked, "companies": companies,
                "summary": summary(notes, findings), "notes": notes[:limit]}


def _as_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, date) else v


def _now() -> str:
    return datetime.now(UTC).isoformat()
