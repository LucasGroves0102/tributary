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
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Path, Query

from query_api.shapes import filing_url
from query_api.store import Store

CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]

# Kinds of the notes that don't come from ops.quality_findings.
DERIVED, BEHIND = "derived_quarters", "companyfacts_behind"

# detail keys that name a filing, and the role the UI shows for each
FILING_KEYS = {"accession_no": "source", "annual_accession": "annual",
               "original_accession": "original", "restated_accession": "restated"}
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


def shape(findings: list[dict], derived: list[dict], behind: list[dict],
          filings: dict[str, dict]) -> list[dict]:
    """One list of notes, newest first: findings as checked, a derived-quarters note per
    metric, and a companyfacts-behind note per company (listing every missing filing)."""

    def cite(role: str, acc: str) -> dict:
        f = filings.get(acc, {})
        return {"role": role, "accession_no": acc, "form": f.get("form"),
                "filed": f.get("filed"), "filing_url": filing_url(f.get("cik"), acc)}

    notes: list[dict] = []
    for r in findings:
        n = {"company_id": r["company_id"], "kind": r["check_name"], "severity": r["severity"],
             "metric": r["metric"], "period_end": r["period_end"], "detail": r["detail"] or {},
             "checked_at": r.get("checked_at")}
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
_SEVERITY = {"error": 2, "warning": 1, "info": 0}


def add_notes_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                     require: Callable[[str, str, str], None],
                     readable: Callable[[str], dict[str, set[str]]]) -> None:
    User = Annotated[str, Depends(current_user)]

    def load(user_id: str, company_ids: list[str], derived: bool,
             limit: int) -> tuple[list[dict], Any]:
        raw = store.data_notes(user_id, company_ids, derived, limit)
        accs = sorted({a for r in raw["findings"]
                       for _, a in accessions_of({"detail": r["detail"]})}
                      | {r["latest_accession"] for r in raw["derived"]})
        filings = store.filing_refs(user_id, accs) if accs else {}
        return shape(raw["findings"], raw["derived"], raw["behind"], filings), raw["checked_at"]

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
                     limit: Annotated[int, Query(ge=1, le=100)] = 20) -> dict:
        """The newest notes across the companies whose financials the caller can read
        (without the standing derived-quarters notes)."""
        granted = sorted(c for c, tiers in readable(user_id).items() if "financials" in tiers)
        notes, checked = load(user_id, granted, False, limit) if granted else ([], None)
        return {"as_of": _now(), "checked_at": checked, "companies": len(granted),
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
