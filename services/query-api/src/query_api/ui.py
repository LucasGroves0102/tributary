"""Routes for the research workspace UI: screener, watchlist, expected filings, filing recaps,
peers. Same two layers as every route (app.py): OpenFGA first (`require`/`allowed`/`readable`),
then Store, which runs every query as query_api with app.user_id set (RLS).

Public metadata (no tier needed): company identity and metadata, the SEC filing index (form,
dates, period of report) and what is derived only from them (expected filing dates). Financial
values need `financials` on that company, section text `filings`.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, get_args

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import Response

from query_api.csv_export import csv_response
from query_api.screener import (
    COLUMNS,
    FILER_CATEGORIES,
    FilerCategory,
    Screen,
    ScreenError,
    band_filters,
    column_info,
    compile_screen,
    parse_filter,
    shape,
)
from query_api.store import Store

Band = Literal["lt100m", "100m-1b", "1b-10b", "10b-100b", "gte100b"]
Format = Annotated[Literal["json", "csv"], Query(alias="format")]
MAX_JSON_ROWS = 500
META_CSV = ["company_id", "name", "tickers", "exchanges", "sic", "industry", "fiscal_year_end",
            "filer_category", "state_of_incorporation"]


def add_ui_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                  require: Callable[[str, str, str], None],
                  allowed: Callable[[str, str, str], bool],
                  readable: Callable[[str], dict[str, set[str]]]) -> None:

    def run_screen(user_id: str, screen: Screen, access: dict[str, set[str]]):
        granted = sorted(c for c, t in access.items() if "financials" in t)
        try:
            compiled = compile_screen(screen, granted)
        except ScreenError as e:
            raise HTTPException(422, str(e)) from None
        rows = store.screen(user_id, compiled)
        shaped = [{**shape(r, compiled, r["company_id"] in granted),
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

    @app.get("/v1/screener/columns")
    def screener_columns() -> dict:
        return {"columns": column_info(sorted(COLUMNS)),
                "filer_categories": list(FILER_CATEGORIES),
                "bands": list(get_args(Band))}


def _now() -> str:
    return datetime.now(UTC).isoformat()
