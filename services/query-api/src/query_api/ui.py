"""Routes for the research workspace UI: screener, watchlist, expected filings, filing recaps,
peers. Same two layers as every route (app.py): OpenFGA first (`require`/`allowed`/`readable`),
then Store, which runs every query as query_api with app.user_id set (RLS).

Public metadata (no tier needed): company identity and metadata, the SEC filing index (form,
dates, period of report) and what is derived only from them (expected filing dates). Financial
values need `financials` on that company, section text `filings`.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Annotated, Literal, get_args
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.responses import Response

from query_api.csv_export import csv_response
from query_api.screener import (
    COLUMNS,
    FILER_CATEGORIES,
    FilerCategory,
    Screen,
    ScreenError,
    band_filters,
    band_label,
    band_of,
    column_info,
    compile_screen,
    parse_filter,
    shape,
)
from query_api.shapes import filing_url, period
from query_api.store import Store
from query_api.watchlist import MAX_ENTRIES, UnknownUser, Watchlists

CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]
EXPECTED_METHOD = (
    "Estimate, not a filing. Period end: the last reported fiscal period end plus one quarter. "
    "Expected date: period end plus the company's median filing lag over its last 8 10-Qs or "
    "4 10-Ks (at least 2 needed), capped at the SEC deadline; otherwise the deadline. SEC "
    "deadlines by filer category: 10-K 60/75/90 days and 10-Q 40/40/45 days after the period "
    "end for large accelerated/accelerated/non-accelerated filers, moved off weekends (holidays "
    "not modelled).")
Accession = Annotated[str, Path(pattern=r"^\d{10}-\d{2}-\d{6}$")]
RECAP_METRICS = ("revenue", "gross_profit", "operating_income", "net_income", "eps_diluted",
                 "operating_cash_flow", "free_cash_flow", "gross_margin", "operating_margin",
                 "net_margin", "cash", "total_assets", "long_term_debt", "stockholders_equity")
RECAP_FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A")
Band = Literal["lt100m", "100m-1b", "1b-10b", "10b-100b", "gte100b"]
Format = Annotated[Literal["json", "csv"], Query(alias="format")]
MAX_JSON_ROWS = 500
DEFAULT_PEER_COLUMNS = ("revenue_ttm", "revenue_growth_ttm", "gross_margin_ttm",
                        "operating_margin_ttm", "net_margin_ttm", "free_cash_flow_ttm",
                        "debt_to_equity_q")
WATCHLIST_COLUMNS = ("revenue_ttm", "revenue_growth_q", "operating_margin_ttm",
                     "free_cash_flow_ttm")
META_CSV = ["company_id", "name", "tickers", "exchanges", "sic", "industry", "fiscal_year_end",
            "filer_category", "state_of_incorporation"]


def add_ui_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                  require: Callable[[str, str, str], None],
                  allowed: Callable[[str, str, str], bool],
                  readable: Callable[[str], dict[str, set[str]]],
                  today: Callable[[], date] | None = None,
                  watchlists: Watchlists | None = None) -> None:
    clock = today or _today_eastern

    def saved(user_id: str) -> list[dict]:
        if watchlists is None:
            raise HTTPException(503, "watchlists unavailable")
        return watchlists.list(user_id)

    def run_screen(user_id: str, screen: Screen, access: dict[str, set[str]]):
        granted = sorted(c for c, t in access.items() if "financials" in t)
        try:
            compiled = compile_screen(screen, granted)
        except ScreenError as e:
            raise HTTPException(422, str(e)) from None
        rows = store.screen(user_id, compiled)
        can_read = set(granted)
        shaped = [{**shape(r, compiled, r["company_id"] in can_read),
                   "access": sorted(access.get(r["company_id"], set()))} for r in rows]
        total = rows[0]["total"] if rows else (0 if screen.offset == 0 else None)
        return compiled, shaped, total

    @app.get("/v1/screener", response_model=dict)
    def screener(
        user_id: Annotated[str, Depends(current_user)],
        columns: Annotated[list[str] | None, Query(
            description="column keys (<metric>_ttm or <metric>_q); default: a standard set")]
            = None,
        sic: Annotated[list[str] | None, Query(description="4-digit SIC codes")] = None,
        sic_prefix: Annotated[str | None, Query(pattern=r"^\d{1,3}$",
                                                description="2-3 digit SIC group")] = None,
        industry: Annotated[str | None, Query(min_length=1, max_length=100,
                                              description="SIC description contains")] = None,
        q: Annotated[str | None, Query(min_length=1, max_length=100,
                                       description="name contains, or exact ticker")] = None,
        exchange: Annotated[list[str] | None, Query(description="e.g. NYSE, Nasdaq")] = None,
        fiscal_year_end_month: Annotated[list[int] | None, Query()] = None,
        filer_category: Annotated[list[FilerCategory] | None, Query()] = None,
        revenue_band: Annotated[Band | None, Query(description="TTM revenue")] = None,
        assets_band: Annotated[Band | None, Query(description="latest total assets")] = None,
        where: Annotated[list[str] | None, Query(
            description="<column>:<gt|gte|lt|lte>:<number>, ratios as fractions "
                        "(operating_margin_ttm:gte:0.2)")] = None,
        access: Annotated[Literal["all", "financials"], Query(
            description="all: every company with financials (values only where you have "
                        "access); financials: only companies you can read")] = "all",
        sort: Annotated[str, Query(pattern=r"^-?[a-z0-9_]{1,64}$",
                                   description="column key or name/sic/...; - for descending")]
            = "-revenue_ttm",
        limit: Annotated[int, Query(ge=1, le=5000)] = 50,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
        fmt: Format = "json",
    ) -> dict | Response:
        """Companies with financials. Metadata is public; values only for companies the caller
        can read financials for. Value filters exclude companies without readable values."""
        if fmt == "json" and limit > MAX_JSON_ROWS:
            raise HTTPException(422, f"limit: at most {MAX_JSON_ROWS} rows as JSON")
        if any(len(s) != 4 or not s.isdigit() for s in sic or []):
            raise HTTPException(422, "sic: 4-digit codes")
        if any(not 1 <= m <= 12 for m in fiscal_year_end_month or []):
            raise HTTPException(422, "fiscal_year_end_month: 1-12")
        if any(len(e) > 20 for e in exchange or []) or len(where or []) > 10:
            raise HTTPException(422, "too many or too long filters")
        try:
            filters = [parse_filter(w) for w in where or []]
            if revenue_band:
                filters += band_filters("revenue_ttm", revenue_band)
            if assets_band:
                filters += band_filters("total_assets_q", assets_band)
        except ScreenError as e:
            raise HTTPException(422, str(e)) from None
        screen = Screen(
            columns=columns if columns is not None else Screen().columns,
            sic=sic or [], sic_prefix=sic_prefix, industry=industry, q=q,
            exchanges=exchange or [], fye_months=fiscal_year_end_month or [],
            filer_categories=list(filer_category or []), filters=filters,
            granted_only=access == "financials", sort=sort, limit=limit, offset=offset)
        compiled, rows, total = run_screen(user_id, screen, readable(user_id))
        if fmt == "csv":
            return csv_response("screener.csv", [*META_CSV, "access", *compiled.columns], (
                [*(r[k] for k in META_CSV), r["access"],
                 *(r["values"][k] for k in compiled.columns)] for r in rows))
        return {"as_of": _now(), "total": total, "limit": limit, "offset": offset, "sort": sort,
                "columns": column_info(compiled.columns), "rows": rows}

    @app.get("/v1/companies/{company_id}/expected-filings", response_model=dict)
    def expected_filings(
        user_id: Annotated[str, Depends(current_user)], company_id: CompanyId,
        periods: Annotated[int, Query(ge=1, le=4)] = 2,
    ) -> dict:
        """Estimated next 10-Q/10-K filings. Public metadata: derived only from the SEC filing
        index and filer category, so no tier is needed (still queried as query_api)."""
        rows = store.expected_filings(user_id, [company_id], clock(), periods)
        return {"company_id": company_id, "as_of": clock(), "method": EXPECTED_METHOD,
                "expected": [_expected(r) for r in rows]}

    @app.get("/v1/watchlist", response_model=dict)
    def watchlist(
        user_id: Annotated[str, Depends(current_user)],
        columns: Annotated[list[str] | None, Query(description="screener column keys")] = None,
        sort: Annotated[str, Query(pattern=r"^-?[a-z0-9_]{1,64}$")] = "name",
        fmt: Format = "json",
    ) -> dict | Response:
        """The caller's watchlist: public metadata, last periodic report and next expected
        filing for every entry; metric values only for companies the caller can read
        financials for. Watching a company grants nothing."""
        entries = {e["company_id"]: e["added_at"] for e in saved(user_id)}
        ids = sorted(entries)
        access = readable(user_id)
        screen = Screen(columns=columns if columns is not None else list(WATCHLIST_COLUMNS),
                        company_ids=ids, coverage=False, sort=sort, limit=max(len(ids), 1))
        compiled, rows, _ = run_screen(user_id, screen, access)
        last = store.last_reports(user_id, ids) if ids else {}
        nxt = {r["company_id"]: _expected(r)
               for r in (store.expected_filings(user_id, ids, clock(), 1) if ids else [])}
        rows = [{**r, "added_at": entries.get(r["company_id"]),
                 "last_filing": last.get(r["company_id"]),
                 "next_expected": nxt.get(r["company_id"])} for r in rows]
        if fmt == "csv":
            header = [*META_CSV, "access", "added_at", "last_form", "last_filing_date",
                      "last_period_end", "next_form", "next_period_end", "next_expected_date",
                      "next_expected_basis", *compiled.columns]
            return csv_response("watchlist.csv", header, ([
                *(r[k] for k in META_CSV), r["access"], r["added_at"],
                *_pick(r["last_filing"], "form", "filing_date", "period_end"),
                *_pick(r["next_expected"], "form", "period_end", "expected_date", "basis"),
                *(r["values"][k] for k in compiled.columns)] for r in rows))
        return {"as_of": _now(), "columns": column_info(compiled.columns),
                "expected_method": EXPECTED_METHOD, "companies": rows}

    @app.put("/v1/watchlist/{company_id}")
    def watch(user_id: Annotated[str, Depends(current_user)], company_id: CompanyId) -> dict:
        """Add a company (public identity: an unknown company is a 404). Grants nothing."""
        if watchlists is None:
            raise HTTPException(503, "watchlists unavailable")
        if not store.profiles(user_id, [company_id]):
            raise HTTPException(404, "unknown company")
        if watchlists.count(user_id) >= MAX_ENTRIES:
            raise HTTPException(409, f"a watchlist holds at most {MAX_ENTRIES} companies")
        try:
            added = watchlists.add(user_id, company_id)
        except UnknownUser:
            raise HTTPException(409, "no account to save a watchlist to") from None
        return {"company_id": company_id, "added": added}

    @app.delete("/v1/watchlist/{company_id}")
    def unwatch(user_id: Annotated[str, Depends(current_user)], company_id: CompanyId) -> dict:
        if watchlists is None:
            raise HTTPException(503, "watchlists unavailable")
        return {"company_id": company_id, "removed": watchlists.remove(user_id, company_id)}

    @app.get("/v1/calendar/expected-filings", response_model=dict)
    def calendar(
        user_id: Annotated[str, Depends(current_user)],
        days: Annotated[int, Query(ge=1, le=180)] = 30,
        scope: Annotated[Literal["watchlist", "readable"], Query(
            description="watchlist: your watchlist; readable: companies you can read")]
            = "watchlist",
        fmt: Format = "json",
    ) -> dict | Response:
        """Expected 10-Q/10-K filings in the next `days` days, plus ones already due or
        overdue (period over, not filed yet). Estimates from public metadata."""
        ids = sorted({e["company_id"] for e in saved(user_id)} if scope == "watchlist"
                     else readable(user_id))
        start = clock()
        end = date.fromordinal(start.toordinal() + days)
        rows = [r for r in (store.expected_filings(user_id, ids, start, 2) if ids else [])
                if r["expected_date"] is not None and r["expected_date"] <= end]
        profiles = store.profiles(user_id, sorted({r["company_id"] for r in rows})) if rows \
            else {}
        rows.sort(key=lambda r: (r["expected_date"], profiles.get(r["company_id"], {})
                                 .get("name") or "", r["company_id"]))
        items = [{"company_id": r["company_id"],
                  "name": profiles.get(r["company_id"], {}).get("name"), **_expected(r)}
                 for r in rows]
        if fmt == "csv":
            keys = ["company_id", "name", "form", "period_end", "expected_date", "deadline",
                    "basis", "typical_lag_days", "status"]
            return csv_response("expected-filings.csv", keys,
                                ([i[k] for k in keys] for i in items))
        return {"as_of": start, "through": end, "scope": scope, "method": EXPECTED_METHOD,
                "filings": items}

    @app.get("/v1/filings/{accession_no}/recap", response_model=dict)
    def recap(
        user_id: Annotated[str, Depends(current_user)], accession_no: Accession,
        metrics: Annotated[list[str] | None, Query(description="default: main metrics")]
            = None,
    ) -> dict:
        """A 10-Q/10-K's metrics vs the prior quarter and the year-ago quarter, each value cited;
        plus one-time-item flags from its MD&A when the caller can read filings."""
        unknown = set(metrics or []) - set(m for m, _, _ in COLUMNS.values())
        if unknown:
            raise HTTPException(422, f"unknown metrics: {sorted(unknown)}")
        filing = store.filing(user_id, accession_no)
        # an unknown accession gets the same answer as a company the caller can't read
        if filing is None:
            raise HTTPException(403, "not permitted to read financials for this company")
        company_id = filing["company_id"]
        require(user_id, "financials", company_id)
        if filing["form"] not in RECAP_FORMS:
            raise HTTPException(422, "recaps are for 10-K and 10-Q filings")
        rows = store.recap(user_id, company_id, accession_no, filing["period_end"],
                           list(metrics or RECAP_METRICS))
        flags = None
        if allowed(user_id, "filings", company_id):
            flags = [_flag(f, accession_no) for f in store.filing_flags(user_id, accession_no)]
        return {"accession_no": accession_no, "company_id": company_id, "form": filing["form"],
                "filed": filing["filing_date"], "period_end": filing["period_end"],
                "as_of": _now(),
                "note": "Values as currently reported: a restated value cites the restating "
                        "filing. pct_change is null for ratios (change is in ratio points) and "
                        "when the earlier value is 0.",
                "metrics": _recap_metrics(rows, filing.get("cik")),
                "flags": flags,
                **({} if flags is not None else
                   {"flags_unavailable": "one-time-item flags need filings access"})}

    @app.get("/v1/companies/{company_id}/peers", response_model=dict)
    def peers(
        user_id: Annotated[str, Depends(current_user)], company_id: CompanyId,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        columns: Annotated[list[str] | None, Query(description="screener column keys")] = None,
    ) -> dict:
        """Suggested peers: same SIC (then 3- and 2-digit groups), then the same revenue size
        band, then the same filer category. Metadata is public; metric values and size bands
        only for companies the caller can read financials for (the company's own revenue is
        used only if the caller can read it)."""
        access = readable(user_id)
        cols = columns if columns is not None else list(DEFAULT_PEER_COLUMNS)
        _, target, _ = run_screen(user_id, Screen(columns=cols, company_ids=[company_id],
                                                  coverage=False, sort="name", limit=1), access)
        if not target:
            raise HTTPException(404, "unknown company")
        revenue = target[0]["values"].get("revenue_ttm") if "revenue_ttm" in cols else None
        granted = sorted(c for c, t in access.items() if "financials" in t)
        if revenue is None and company_id in granted:
            _, own, _ = run_screen(user_id, Screen(columns=["revenue_ttm"],
                                                   company_ids=[company_id], coverage=False,
                                                   sort="name", limit=1), access)
            revenue = own[0]["values"]["revenue_ttm"] if own else None
        ranked = store.peers(user_id, company_id, granted, revenue, limit)
        ids = [p["company_id"] for p in ranked]
        compiled, rows, _ = run_screen(user_id, Screen(columns=cols, company_ids=ids,
                                                       coverage=False, sort="company_id",
                                                       limit=max(len(ids), 1)), access)
        by_id = {r["company_id"]: r for r in rows}
        own_band = band_of(revenue)
        return {
            "company_id": company_id, "as_of": _now(), "columns": column_info(compiled.columns),
            "company": {**target[0], "revenue_band": band_label(own_band)},
            "method": "Same 4-digit SIC first, then 3- and 2-digit SIC groups; within a level, "
                      "the same TTM revenue size band (companies you can read), the same SEC "
                      "filer category, then closest revenue.",
            "peers": [{**by_id[p["company_id"]],
                       "match": {"sic_level": p["sic_level"],
                                 "same_filer_category": p["same_filer_category"],
                                 "revenue_band": band_label(p["revenue_band"]),
                                 "same_revenue_band": own_band is not None
                                 and p["revenue_band"] == own_band}}
                      for p in ranked if p["company_id"] in by_id],
        }

    @app.get("/v1/screener/columns")
    def screener_columns() -> dict:
        return {"columns": column_info(sorted(COLUMNS)),
                "filer_categories": list(FILER_CATEGORIES),
                "bands": list(get_args(Band))}


def _recap_metrics(rows: list[dict], cik: str | None) -> list[dict]:
    """Rows (one per comparison) → one entry per metric and span with changes vs current."""
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        entry = out.setdefault((r["metric"], r["span"]), {
            "metric": r["metric"], "span": r["span"], "unit": r["unit"],
            "current": None, "prior_quarter": None, "year_ago": None})
        entry[r["comparison"]] = _recap_value(r, cik)
    for e in out.values():
        cur = e["current"]
        for k in ("prior_quarter", "year_ago"):
            if cur and e[k]:
                e[k].update(_change(cur["value"], e[k]["value"], e["unit"]))
    return [e for e in out.values() if e["current"]]


def _recap_value(r: dict, cik: str | None) -> dict:
    return {"value": r["value"], "period_start": r["period_start"],
            "period_end": r["period_end"],
            "fiscal_period": period("FY", r["fiscal_year"], r["fiscal_quarter"]),
            "calendar_period": period("CY", r["calendar_year"], r["calendar_quarter"]),
            "value_source": r["value_source"],
            "citation": {"accession_no": r["accession_no"], "form": r["form"],
                         "filed": r["filed"], "xbrl_tag": r["source_tag"],
                         "source_accessions": r["source_accessions"],
                         "filing_url": filing_url(cik, r["accession_no"])},
            "freshness": {"filed": r["filed"], "ingested_at": r["ingested_at"]}}


def _change(current: Decimal, earlier: Decimal, unit: str) -> dict:
    """change = current - earlier (exact); pct_change = change / |earlier|, 6 decimals."""
    change = current - earlier
    pct = None
    if unit != "ratio" and earlier != 0:
        with localcontext() as ctx:
            ctx.prec = 60
            try:
                pct = (change / abs(earlier)).quantize(Decimal("0.000001"))
            except InvalidOperation:
                pct = None
    return {"change": change, "pct_change": pct}


def _flag(f: dict, accession_no: str) -> dict:
    return {"flag": f["flag"], "section_key": f["section_key"], "title": f["title"],
            "matches": f["matches"], "snippet": f["snippet"],
            "citation": {"accession_no": accession_no, "form": f["form"], "filed": f["filed"],
                         "section_key": f["section_key"], "char_start": f["char_start"],
                         "document_url": f["document_url"]}}


def _pick(d: dict | None, *keys: str) -> list:
    return [d.get(k) if d else None for k in keys]


def _expected(r: dict) -> dict:
    return {"form": r["form"], "period_end": r["period_end"],
            "expected_date": r["expected_date"], "deadline": r["deadline"],
            "deadline_days": r["deadline_days"], "filer_category": r["filer_category"],
            "basis": r["basis"], "typical_lag_days": r["typical_lag_days"],
            "lag_samples": r["lag_samples"], "status": r["status"], "estimate": True,
            "last_period_end": r["last_period_end"], "last_filed": r["last_filed"]}


def _today_eastern() -> date:
    """EDGAR dates are US Eastern."""
    return datetime.now(ZoneInfo("America/New_York")).date()


def _now() -> str:
    return datetime.now(UTC).isoformat()
