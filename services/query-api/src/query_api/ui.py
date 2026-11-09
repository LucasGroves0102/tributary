"""Routes for the research workspace UI: screener, watchlist, expected filings, filing recaps,
peers. Same two layers as every route (app.py): OpenFGA first (`require`/`allowed`/`readable`),
then Store, which runs every query as query_api with app.user_id set (RLS).

Public metadata (no tier needed): company identity and metadata, the SEC filing index (form,
dates, period of report) and what is derived only from them (expected filing dates). Financial
values need `financials` on that company, section text `filings`.
"""

import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Annotated, Literal, get_args
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.responses import Response

from query_api.authorizer import Readable
from query_api.csv_export import csv_response
from query_api.peers import FALLBACK_METHOD as PEER_FALLBACK_METHOD
from query_api.peers import TEXT_MAX_AGE_DAYS as PEER_TEXT_DAYS
from query_api.peers import Candidate as PeerCandidate
from query_api.peers import fallback_reason as peer_fallback_reason
from query_api.peers import rank as rank_peers
from query_api.peers import sic_match
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
    notes_of,
    parse_filter,
    shape,
)
from query_api.shapes import document_url, filing_url, newest_windows, period
from query_api.store import Store
from query_api.watchlist import MAX_ENTRIES, UnknownUser, Watchlists

log = logging.getLogger("query_api")
CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]
EXPECTED_METHOD = (
    "Estimate, not a filing. Period end: the last reported fiscal period end plus one quarter. "
    "Expected date: period end plus the company's median filing lag over its last 8 10-Qs or "
    "4 10-Ks (at least 2 needed), capped at the SEC deadline; otherwise the deadline. SEC "
    "deadlines by filer category: 10-K 60/75/90 days and 10-Q 40/40/45 days after the period "
    "end for large accelerated/accelerated/non-accelerated filers, moved off weekends (holidays "
    "not modelled).")
Accession = Annotated[str, Path(pattern=r"^\d{10}-\d{2}-\d{6}$")]
RECAP_METRICS: tuple[str, ...] = (
    "revenue", "gross_profit", "operating_income", "net_income", "eps_diluted",
    "operating_cash_flow", "free_cash_flow", "gross_margin", "operating_margin", "net_margin",
    "cash", "total_assets", "long_term_debt", "stockholders_equity")
RECAP_FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A")
Band = Literal["lt100m", "100m-1b", "1b-10b", "10b-100b", "gte100b"]
Format = Annotated[Literal["json", "csv"], Query(alias="format")]
MAX_JSON_ROWS = 500
JOINT_TTL = 3600  # seconds a process keeps the co-registrant pairs
MAX_COMPANY_IDS = 21  # a comps table: a target and up to 20 peers
DEFAULT_PEER_COLUMNS: tuple[str, ...] = ("revenue_ttm", "revenue_growth_ttm", "gross_margin_ttm",
                        "operating_margin_ttm", "net_margin_ttm", "free_cash_flow_ttm",
                        "debt_to_equity_q")
# a bank's peers compare banks (048): revenue first, so the size band still reads it
BANK_PEER_COLUMNS: tuple[str, ...] = ("revenue_ttm", "net_interest_income_ttm",
                                      "efficiency_ratio_ttm", "provision_for_credit_losses_ttm",
                                      "loans_q", "deposits_q", "roe_ttm", "net_margin_ttm")
WATCHLIST_COLUMNS: tuple[str, ...] = ("revenue_ttm", "revenue_growth_q", "operating_margin_ttm",
                     "free_cash_flow_ttm")
FORM_TYPE = r"^[A-Z0-9][A-Z0-9 ./-]{0,19}$"  # SEC form types: 10-K, 8-K/A, SC 13G, DEF 14A
MAX_FORMS = 20
MAX_INDEX_COMPANIES = 50
# /v1/filing-index with the catalog public: filing-date windows to try, in days back. Each query
# is a few hundred ms to seconds; 3 days fills a page on any normal day.
INDEX_WINDOW_DAYS = (3, 14, 90, 365)
META_CSV = ["company_id", "name", "tickers", "exchanges", "sic", "industry", "fiscal_year_end",
            "filer_category", "state_of_incorporation"]


def add_ui_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                  require: Callable[[str, str, str], None],
                  allowed: Callable[[str, str, str], bool],
                  readable: Callable[[str], Readable],
                  today: Callable[[], date] | None = None,
                  watchlists: Watchlists | None = None) -> None:
    clock = today or today_eastern

    def saved(user_id: str) -> list[dict]:
        if watchlists is None:
            raise HTTPException(503, "watchlists unavailable")
        return watchlists.list(user_id)

    joint_cache: dict = {}

    def co_registrants(user_id: str) -> dict[str, list[dict]]:
        """company_id -> the companies it files one combined 10-K with (#135), public index
        metadata cached JOINT_TTL per process (silver.co_registrants is ~190 ms cold)."""
        now = datetime.now(UTC)
        if joint_cache.get("at") is None or (now - joint_cache["at"]).total_seconds() > JOINT_TTL:
            try:
                rows = store.co_registrants(user_id)
            except psycopg.Error as e:  # optional: never take the screener down for a note
                log.warning("co-registrants unavailable: %s", type(e).__name__)
                return joint_cache.get("map", {})
            joint: dict[str, list[dict]] = {}
            for r in rows:
                joint.setdefault(r["company_id"], []).append(
                    {"company_id": r["other_id"], "name": r["other_name"],
                     "accession_no": r["accession_no"], "filed": r["filing_date"],
                     "filed_by": r["filed_by"]})
            joint_cache.update(at=now, map=joint)
        return joint_cache["map"]

    def run_screen(user_id: str, screen: Screen, access: Readable):
        try:
            compiled = compile_screen(screen, access.ids("financials"))
        except ScreenError as e:
            raise HTTPException(422, str(e)) from None
        rows = store.screen(user_id, compiled)
        # layer 1 again on every row, also when no id list went into the query
        shaped = [{**shape(r, compiled, "financials" in access.get(r["company_id"], set())),
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
        company_id: Annotated[list[str] | None, Query(
            description=f"only these companies (at most {MAX_COMPANY_IDS}), with or without "
                        "financials: a comps table or a tearsheet")] = None,
        exchange: Annotated[list[str] | None, Query(description="e.g. NYSE, Nasdaq")] = None,
        fiscal_year_end_month: Annotated[list[int] | None, Query()] = None,
        filer_category: Annotated[list[FilerCategory] | None, Query()] = None,
        sector: Annotated[list[str] | None, Query(
            max_length=40, description="sector names (/v1/sectors)")] = None,
        sector_industry: Annotated[list[str] | None, Query(
            max_length=60, description="industry names within sectors (/v1/sectors)")] = None,
        issue: Annotated[str | None, Query(
            pattern=r"^[a-z_]{1,64}$", description="a data-quality check's name "
            "(the status page's rows): only companies you can read with a finding of it")] = None,
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
        """Companies with a figure in at least one of these columns (naming a company or a data
        check lists it whatever it has). Metadata is public; values only for companies the caller
        can read financials for. Value filters exclude companies without readable values. A value
        that reads as not meaningful, not applicable or old (`readings`, migration 035) is still
        returned, exact, but sorts last and never passes a value filter."""
        if fmt == "json" and limit > MAX_JSON_ROWS:
            raise HTTPException(422, f"limit: at most {MAX_JSON_ROWS} rows as JSON")
        if any(len(s) != 4 or not s.isdigit() for s in sic or []):
            raise HTTPException(422, "sic: 4-digit codes")
        if any(not 1 <= m <= 12 for m in fiscal_year_end_month or []):
            raise HTTPException(422, "fiscal_year_end_month: 1-12")
        if any(len(e) > 20 for e in exchange or []) or len(where or []) > 10 \
                or len(sector or []) > 12 or len(sector_industry or []) > 20:
            raise HTTPException(422, "too many or too long filters")
        if company_id is not None and (
                len(company_id) > MAX_COMPANY_IDS
                or any(len(c) != 10 or not c.isdigit() for c in company_id)):
            raise HTTPException(422, f"company_id: at most {MAX_COMPANY_IDS} 10-digit ids")
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
            company_ids=sorted(set(company_id)) if company_id is not None else None,
            coverage=company_id is None,
            hide_blank=company_id is None and q is None and issue is None,
            sic=sic or [], sic_prefix=sic_prefix, industry=industry, q=q,
            exchanges=exchange or [], fye_months=fiscal_year_end_month or [],
            filer_categories=list(filer_category or []), issue=issue, filters=filters,
            sectors=sector or [], sector_industries=sector_industry or [],
            granted_only=access == "financials", sort=sort, limit=limit, offset=offset)
        compiled, rows, total = run_screen(user_id, screen, readable(user_id))
        joint = co_registrants(user_id)
        for r in rows:
            r["co_registrants"] = joint.get(r["company_id"], [])
        if fmt == "csv":
            return csv_response("screener.csv", [*META_CSV, "access", *compiled.columns, "notes"], (
                [*(r[k] for k in META_CSV), r["access"],
                 *(r["values"][k] for k in compiled.columns), notes_of(r)] for r in rows))
        # `issue` echoed: an API that ignored it (older than the filter) would list everyone, and
        # the web app can tell the two apart
        return {"as_of": _now(), "total": total, "limit": limit, "offset": offset, "sort": sort,
                "columns": column_info(compiled.columns), "rows": rows,
                **({"issue": issue} if issue else {})}

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

    def filing_index(user_id: str, ids: list[str] | None, sections_for: list[str] | None,
                     forms: list[str] | None, start: date | None, end: date | None,
                     limit: int, offset: int) -> dict:
        forms = sorted(set(forms or []))
        if len(forms) > MAX_FORMS or any(not re.match(FORM_TYPE, f) for f in forms):
            raise HTTPException(422, f"form: at most {MAX_FORMS} SEC form types such as 10-K")
        rows: list[dict] = []
        if ids is None:  # every readable company, catalog public: newest first, stop when full
            for lower in newest_windows(INDEX_WINDOW_DAYS, start, end, clock()):
                rows = store.filing_index(user_id, None, forms, lower, end, limit + 1, offset,
                                          sections_for)
                if len(rows) > limit:
                    break
        elif ids:
            rows = store.filing_index(user_id, ids, forms, start, end, limit + 1, offset,
                                      sections_for)
        return {"as_of": _now(), "forms": forms, "from": start, "to": end, "limit": limit,
                "offset": offset, "has_more": len(rows) > limit,
                "filings": [_index_row(r) for r in rows[:limit]]}

    @app.get("/v1/companies/{company_id}/filing-index", response_model=dict)
    def company_filing_index(
        user_id: Annotated[str, Depends(current_user)], company_id: CompanyId,
        form: Annotated[list[str] | None, Query(
            description="SEC form types, exact (10-K, 10-K/A, 8-K, DEF 14A, 4); default: all")]
            = None,
        start: Annotated[date | None, Query(alias="from", description="filed on or after")]
            = None,
        end: Annotated[date | None, Query(alias="to", description="filed on or before")] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    ) -> dict:
        """Every filing of a company in the SEC filing index, newest first: public metadata
        (form, dates, period, EDGAR links), so no tier is needed. `sections` (parsed sections
        the reader can open) is filled only when the caller can read this company's filings
        (OpenFGA check here, RLS again in the count); never any text."""
        sections_for = [company_id] if allowed(user_id, "filings", company_id) else []
        return {"company_id": company_id,
                **filing_index(user_id, [company_id], sections_for, form, start, end, limit,
                               offset)}

    @app.get("/v1/filing-index", response_model=dict)
    def filing_index_many(
        user_id: Annotated[str, Depends(current_user)],
        company_id: Annotated[list[str] | None, Query(
            description=f"at most {MAX_INDEX_COMPANIES} company ids (e.g. your watchlist); "
                        "default: the companies you can read")] = None,
        form: Annotated[list[str] | None, Query(description="SEC form types, exact")] = None,
        start: Annotated[date | None, Query(alias="from", description="filed on or after")]
            = None,
        end: Annotated[date | None, Query(alias="to", description="filed on or before")] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    ) -> dict:
        """The SEC filing index across companies, newest first (public metadata). `sections`
        only for companies whose filings the caller can read (OpenFGA list, then RLS)."""
        if company_id is not None and (
                len(company_id) > MAX_INDEX_COMPANIES
                or any(len(c) != 10 or not c.isdigit() for c in company_id)):
            raise HTTPException(422, f"company_id: at most {MAX_INDEX_COMPANIES} 10-digit ids")
        access = readable(user_id)
        if company_id is None and access.public:
            # Public metadata over the whole catalog: no id list, RLS decides the section counts
            sections_for = None if "filings" in access.public else []
            page = filing_index(user_id, None, sections_for, form, start, end, limit, offset)
            for f in page["filings"]:  # layer 1 on every row, as with a list of ids
                if "filings" not in access.get(f["company_id"], set()):
                    f["sections"], f["sections_available"] = None, False
            return page
        ids = sorted(set(company_id) if company_id is not None else access)
        sections_for = sorted(c for c in ids if "filings" in access.get(c, set()))
        return filing_index(user_id, ids, sections_for, form, start, end, limit, offset)

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
                      "next_expected_basis", *compiled.columns, "notes"]
            return csv_response("watchlist.csv", header, ([
                *(r[k] for k in META_CSV), r["access"], r["added_at"],
                *_pick(r["last_filing"], "form", "filing_date", "period_end"),
                *_pick(r["next_expected"], "form", "period_end", "expected_date", "basis"),
                *(r["values"][k] for k in compiled.columns), notes_of(r)] for r in rows))
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
        ids: list[str] | None
        if scope == "watchlist":
            ids = sorted({e["company_id"] for e in saved(user_id)})
        else:
            access = readable(user_id)
            ids = None if access.public else sorted(access)
        start = clock()
        end = date.fromordinal(start.toordinal() + days)
        found = store.expected_filings(user_id, ids, start, 2) if ids != [] else []
        rows = [r for r in found
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
        flags_note = "one-time-item flags need filings access"
        if allowed(user_id, "filings", company_id):
            try:
                flags = [_flag(f, accession_no) for f in store.filing_flags(user_id, accession_no)]
            except psycopg.Error as e:  # optional: the metrics above still answer
                log.warning("recap flags unavailable: %s", type(e).__name__,
                            extra={"accession_no": accession_no})
                flags_note = "one-time-item flags couldn't be read just now"
        return {"accession_no": accession_no, "company_id": company_id, "form": filing["form"],
                "filed": filing["filing_date"], "period_end": filing["period_end"],
                "as_of": _now(),
                "note": "Values as currently reported: a restated value cites the restating "
                        "filing. pct_change is null for ratios (change is in ratio points) and "
                        "when the earlier value is 0.",
                "metrics": _recap_metrics(rows, filing.get("cik")),
                "flags": flags,
                **({} if flags is not None else {"flags_unavailable": flags_note})}

    @app.get("/v1/companies/{company_id}/peers", response_model=dict)
    def peers(
        user_id: Annotated[str, Depends(current_user)], company_id: CompanyId,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        columns: Annotated[list[str] | None, Query(
            description="screener column keys; default: a standard set, a bank's own for a "
                        "bank")] = None,
        access_filter: Annotated[Literal["all", "financials"], Query(
            alias="access", description="all: any company; financials: only companies you can "
                                        "read (a demo sandbox lists nothing else)")] = "all",
    ) -> dict:
        """Suggested peers by business (query_api/peers.py is the spec): similarity of the two
        10-Ks' Item 1, competitors either one names, SIC division and size as weak signals; by
        SIC and size when there is no business text to compare. require(financials) first;
        candidates, mentions and revenue are read under RLS (filings for the text, financials
        for revenue); a company the caller can't read never gets a size."""
        require(user_id, "financials", company_id)
        access = readable(user_id)
        bank_cols = None
        if columns is None:
            _, probe, _ = run_screen(user_id, Screen(columns=["revenue_ttm"],
                                                     company_ids=[company_id], coverage=False,
                                                     sort="name", limit=1), access)
            bank_cols = bool(probe and probe[0]["not_applicable"])
        cols = columns if columns is not None else list(
            BANK_PEER_COLUMNS if bank_cols else DEFAULT_PEER_COLUMNS)
        _, target, _ = run_screen(user_id, Screen(columns=cols, company_ids=[company_id],
                                                  coverage=False, sort="name", limit=1), access)
        if not target:
            raise HTTPException(404, "unknown company")

        def current(row: dict) -> Decimal | None:
            # the size band's revenue is read as the screener sorts it (035): an old or not
            # meaningful TTM has no band, like a peer's (Store.peers)
            reading = row.get("readings", {}).get("revenue_ttm") or {}
            return None if reading.get("status") not in (None, "behind") \
                else row["values"].get("revenue_ttm")

        revenue = current(target[0]) if "revenue_ttm" in cols else None
        granted = access.ids("financials")
        if "revenue_ttm" not in cols and "financials" in access.get(company_id, set()):
            _, own, _ = run_screen(user_id, Screen(columns=["revenue_ttm"],
                                                   company_ids=[company_id], coverage=False,
                                                   sort="name", limit=1), access)
            revenue = current(own[0]) if own else None
        own_band = band_of(revenue)
        only_readable = access_filter == "financials"

        business = store.business_peers(user_id, company_id)
        profile = business["profile"]
        matches: dict[str, dict] = {}
        if profile is not None:
            mentions = business["mentions"]
            ids = sorted({p["peer_id"] for p in business["peers"]}
                         | {m["named_id"] for m in mentions if m["company_id"] == company_id}
                         | {m["company_id"] for m in mentions if m["named_id"] == company_id})
            facts = store.peer_facts(user_id, ids, granted) if ids else {}
            stored = {p["peer_id"]: p for p in business["peers"]}
            candidates = []
            for cid in ids:
                f = facts.get(cid)
                if f is None:
                    continue  # not a company (any more)
                can_read = "financials" in access.get(cid, set())
                if only_readable and not can_read:
                    continue
                p = stored.get(cid, {})
                candidates.append(PeerCandidate(
                    cid, f["sic"], float(p.get("similarity") or 0), list(p.get("terms") or []),
                    named_by_target=any(m["company_id"] == company_id and m["named_id"] == cid
                                        for m in mentions),
                    names_target=any(m["company_id"] == cid for m in mentions),
                    revenue=f["revenue"] if can_read else None, readable=can_read,
                    size_unknown=can_read and bool(f.get("revenue_other_currency"))))
            target_sic = target[0]["sic"]
            ranked_business = rank_peers(target_sic, revenue, candidates)[:limit]
            for c in ranked_business:
                f = facts[c.company_id]
                matches[c.company_id] = {
                    "score": round(c.score, 4), "similarity": round(c.similarity, 4),
                    "terms": c.terms,
                    "named_by_target": _cite(next((m for m in mentions
                                                   if m["company_id"] == company_id
                                                   and m["named_id"] == c.company_id), None)),
                    "names_target": _cite(next((m for m in mentions
                                                if m["company_id"] == c.company_id
                                                and m["named_id"] == company_id), None)),
                    "sic_level": sic_match(target_sic, c.sic) or 0,
                    "same_industry": bool(f["sector_industry"])
                    and f["sector_industry"] == target[0].get("sector_industry"),
                    "same_sector": bool(f["sector"]) and f["sector"] == target[0].get("sector"),
                    "same_filer_category": f["filer_category"] is not None
                    and f["filer_category"] == target[0]["filer_category"],
                    "revenue_band": band_label(band_of(c.revenue)) if c.readable else None,
                    "same_revenue_band": own_band is not None and c.readable
                    and band_of(c.revenue) == own_band,
                }
        basis = "business" if matches else "sic"
        if basis == "sic":
            ranked = store.peers(user_id, company_id, granted, revenue, limit,
                                 granted_only=only_readable)
            for p in ranked:
                matches[p["company_id"]] = {
                    "sic_level": p["sic_level"], "same_filer_category": p["same_filer_category"],
                    "revenue_band": band_label(p["revenue_band"]),
                    "same_revenue_band": own_band is not None and p["revenue_band"] == own_band}
        ids = list(matches)
        compiled, rows, _ = run_screen(user_id, Screen(columns=cols, company_ids=ids,
                                                       coverage=False, sort="company_id",
                                                       limit=max(len(ids), 1)), access)
        by_id = {r["company_id"]: r for r in rows}
        reason = None
        if basis == "business":
            method = ("By business: how alike the two 10-Ks' Business sections (Item 1) are, "
                      "competitors either one names there, then SIC division and TTM revenue size "
                      "(companies you can read) as weak signals.")
        else:
            # why there is no business text to rank by (peers.py's docstring); the 10-K's Item 1
            # is read only with the filings tier, under RLS
            can_read_text = allowed(user_id, "filings", company_id)
            text = (store.business_text(user_id, company_id, PEER_TEXT_DAYS)
                    if profile is None and can_read_text else None)
            reason = peer_fallback_reason(profile is not None, can_read_text, text or None)
            method = (f"By SIC and size: {PEER_FALLBACK_METHOD[reason]} Same 4-digit SIC first, "
                      "then 3- and 2-digit groups; within a level, the same TTM revenue size band "
                      "(companies you can read), the same SEC filer category, then closest "
                      "revenue.")
        return {
            "company_id": company_id, "as_of": _now(), "columns": column_info(compiled.columns),
            "company": {**target[0], "revenue_band": band_label(own_band)},
            "basis": basis, "method": method, "basis_reason": reason,
            "business": None if profile is None else {
                "accession_no": profile["accession_no"], "section_key": profile["section_key"],
                "filed": profile["filed"], "terms": profile["terms"][:10],
                "computed_at": profile["computed_at"]},
            "peers": [{**by_id[cid], "match": matches[cid]} for cid in ids if cid in by_id],
        }

    @app.get("/v1/companies/{company_id}/peers/check/{candidate_id}", response_model=dict)
    def peer_check(user_id: Annotated[str, Depends(current_user)], company_id: CompanyId,
                   candidate_id: CompanyId) -> dict:
        """Can this company join the comps of `company_id`, and if not, why: it has no figures
        here because it files 20-F/40-F annual reports (a foreign private issuer, IFRS), or its
        10-K's XBRL isn't in yet, or it files no annual report. require(financials) on the comps'
        company; the answer is public filing-index metadata about the candidate."""
        require(user_id, "financials", company_id)
        info = store.annual_reports(user_id, candidate_id)
        if not info.get("sic") and not info.get("annual") and not info.get("has_financials"):
            raise HTTPException(404, "unknown company")
        annual = info.get("annual") or {}
        if info.get("has_financials"):
            reason = None if "financials" in readable(user_id).get(candidate_id, set()) \
                else "metadata_only"
        elif annual.get("20-F") or annual.get("40-F"):
            reason = "foreign_filer"
        elif annual.get("10-K"):
            reason = "no_xbrl"
        else:
            reason = "no_annual_report"
        latest = max(annual.items(), key=lambda kv: kv[1]) if annual else None
        return {"company_id": company_id, "candidate_id": candidate_id,
                "ok": reason in (None, "metadata_only"), "reason": reason,
                "annual_form": latest[0] if latest else None,
                "annual_filed": latest[1] if latest else None}

    sector_cache: dict = {}

    @app.get("/v1/sectors", response_model=dict)
    def sectors(user_id: Annotated[str, Depends(current_user)]) -> dict:
        """The open sector and industry grouping over SIC codes (migration 046), with each
        industry's SIC ranges and how many companies with financials it holds. A classification,
        not company data: it changes only with a migration, so a process reads it once."""
        if "rows" not in sector_cache:
            sector_cache["rows"] = store.sectors(user_id)
        out: dict[str, dict[str, dict]] = {}
        for r in sector_cache["rows"]:
            ind = out.setdefault(r["sector"], {}).setdefault(
                r["industry"], {"name": r["industry"], "sic": [], "companies": 0})
            ind["sic"].append(r["sic_from"] if r["sic_from"] == r["sic_to"]
                              else f"{r['sic_from']}-{r['sic_to']}")
            ind["companies"] += r["companies"]
        return {"sectors": [{"name": s, "companies": sum(i["companies"] for i in inds.values()),
                             "industries": sorted(inds.values(), key=lambda i: i["name"])}
                            for s, inds in sorted(out.items())],
                "note": "Tributary's own grouping of the SEC's SIC codes (not GICS); the SIC "
                        "code stays the filing's own classification."}

    @app.get("/v1/screener/columns")
    def screener_columns() -> dict:
        return {"columns": column_info(sorted(COLUMNS)),
                "filer_categories": list(FILER_CATEGORIES),
                "bands": list(get_args(Band))}


def _cite(m: dict | None) -> dict | None:
    """A competitor mention as a citation: the sentence's place in the 10-K's Item 1."""
    if m is None:
        return None
    return {"accession_no": m["accession_no"], "section_key": m["section_key"],
            "char_start": m["char_start"], "char_end": m["char_end"]}


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
    return {"value": r["value"], "not_meaningful": r.get("not_meaningful"),
            "period_start": r["period_start"],
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


def _index_row(r: dict) -> dict:
    sections = r["sections"]
    return {"accession_no": r["accession_no"], "company_id": r["company_id"], "name": r["name"],
            "form": r["form"], "filed": r["filed"], "accepted_at": r["accepted_at"],
            "period_of_report": r["period_of_report"],
            "filing_url": filing_url(r["cik"], r["accession_no"]),
            "document_url": document_url(r["cik"], r["accession_no"], r["primary_document"]),
            "sections": sections, "sections_available": bool(sections)}


def _pick(d: dict | None, *keys: str) -> list:
    return [d.get(k) if d else None for k in keys]


def _expected(r: dict) -> dict:
    return {"form": r["form"], "period_end": r["period_end"],
            "expected_date": r["expected_date"], "deadline": r["deadline"],
            "deadline_days": r["deadline_days"], "filer_category": r["filer_category"],
            "basis": r["basis"], "typical_lag_days": r["typical_lag_days"],
            "lag_samples": r["lag_samples"], "status": r["status"], "estimate": True,
            "last_period_end": r["last_period_end"], "last_filed": r["last_filed"]}


def today_eastern() -> date:
    """EDGAR dates are US Eastern."""
    return datetime.now(ZoneInfo("America/New_York")).date()


def _now() -> str:
    return datetime.now(UTC).isoformat()
