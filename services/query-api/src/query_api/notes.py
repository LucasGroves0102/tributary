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

# detail keys that name a filing, and the role the UI shows for each
FILING_KEYS = {"accession_no": "source", "annual_accession": "annual",
               "original_accession": "original", "restated_accession": "restated",
               "total_accession": "total"}
FILING_LIST_KEYS = {"quarter_accessions": "quarter"}


def note_date(n: dict) -> date | None:
    """When the note happened, for "recent" ordering: the restating or missing filing's date,
    else the period end."""
    d = n.get("detail") or {}
    for key in ("restated_filed", "missing_filed"):
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
          series: dict[tuple[str, str], list[tuple[date, float]]] | None = None) -> list[dict]:
    """One list of notes, newest first: findings as checked (a stock split's as one note), a
    derived-quarters note per metric, and a companyfacts-behind note per company (listing every
    missing filing). `series`: (company, metric) → its quarters, oldest first, for the outliers."""

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
                       "latest_period_end": _iso(r["latest_period_end"])},
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
    for n in notes:
        n["date"] = note_date(n)
    notes.sort(key=lambda n: (n["date"] or date.min, _SEVERITY.get(n["severity"], 0)),
               reverse=True)
    return notes


COMPANY_FINDINGS = 1000  # newest findings per company page (restatements can be many)
MAX_COMPANIES = 50  # as /v1/filing-index: a watchlist; beyond it the URL and the id list get long
SPLIT_ROOM, MIN_RAW_FINDINGS = 10, 200
_SEVERITY = {"error": 2, "warning": 1, "info": 0}


def add_notes_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                     require: Callable[[str, str, str], None],
                     readable: Callable[[str], Readable]) -> None:
    User = Annotated[str, Depends(current_user)]

    def load(user_id: str, company_ids: list[str] | None, derived: bool,
             limit: int) -> tuple[list[dict], Any]:
        raw = store.data_notes(user_id, company_ids, derived, limit)
        accs = sorted({a for r in raw["findings"]
                       for _, a in accessions_of({"detail": r["detail"]})}
                      | {r["latest_accession"] for r in raw["derived"]})
        filings = store.filing_refs(user_id, accs) if accs else {}
        spikes = [r for r in raw["findings"] if r["check_name"] == "outlier"]
        series = store.metric_series(user_id, sorted({r["company_id"] for r in spikes}),
                                     sorted({r["metric"] for r in spikes})) if spikes else {}
        return (shape(raw["findings"], raw["derived"], raw["behind"], filings, series),
                raw["checked_at"])

    @app.get("/v1/companies/{company_id}/data-notes", response_model=dict)
    def company_notes(user_id: User, company_id: CompanyId) -> dict:
        """Data-quality and freshness notes for one company: gaps, overlapping quarters,
        restatements, derived quarters, filings the SEC's XBRL API doesn't reflect yet. Each
        names its filings. Needs the financials tier (the notes are about values)."""
        require(user_id, "financials", company_id)
        notes, checked = load(user_id, [company_id], True, COMPANY_FINDINGS)
        return {"company_id": company_id, "as_of": _now(), "checked_at": checked,
                "notes": notes}

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
        notes, checked = load(user_id, granted, False, raw_limit) if granted != [] else ([], None)
        # layer 1 on every note, also when no id list went into the query
        notes = [n for n in notes if "financials" in access.get(n["company_id"], set())]
        companies = len(granted) if granted is not None else sum(
            "financials" in tiers for tiers in access.values())
        return {"as_of": _now(), "checked_at": checked, "companies": companies,
                "notes": notes[:limit]}


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
