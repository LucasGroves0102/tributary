"""Tributary Query API. Read-only; every data route is permission-checked twice:
OpenFGA first (layer 1), then Postgres row-level security (layer 2, in Store)."""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import Counter
from pydantic import BaseModel, Field

from query_api.auth import Unauthenticated, mint, verify
from query_api.authorizer import Authorizer, AuthzUnavailable
from query_api.csv_export import csv_response
from query_api.demo import add_sandbox_routes
from query_api.formula import (
    CATALOG,
    Analysis,
    FormulaError,
    Node,
    alignment,
    analyze,
    compile_formula,
    parse,
    suggest,
    to_dict,
)
from query_api.notes import add_notes_routes
from query_api.research import add_research_routes
from query_api.sandbox import SandboxConfig, Sandboxes, is_sandbox_user
from query_api.shapes import filing_url
from query_api.shapes import period as _period
from query_api.store import QUERY_TIMEOUT, Store
from query_api.ui import add_ui_routes
from query_api.views import Views, add_view_routes
from query_api.watchlist import Watchlists
from query_api.workspace_api import add_workspace_routes
from query_api.workspaces import Workspaces
from tributary_telemetry.http import instrument

FINANCIALS_CSV = ["metric", "view", "period_start", "period_end", "fiscal_period",
                  "calendar_period", "value", "unit", "value_source", "accession_no", "form",
                  "filed", "xbrl_tag", "source_accessions", "filing_url", "ingested_at"]
SEGMENTS_CSV = ["axis", "member", "member_label", "metric", "view", "period_start", "period_end",
                "fiscal_period", "calendar_period", "value", "unit", "value_source",
                "accession_no", "form", "filed", "xbrl_tag", "xbrl_axis", "source_accessions",
                "filing_url", "ingested_at"]
SegmentAxis = Literal["business_segment", "geography", "product_service", "reconciliation"]
CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]


DEMO_TOKEN_TTL = 8 * 3600
SANDBOX_OFF = SandboxConfig()

PERMISSION_CHECKS = Counter("tributary_permission_checks_total",
                            "OpenFGA checks by tier (list: readable companies) and result "
                            "(allowed, denied, error)", ["tier", "result"])

log = logging.getLogger("query_api")


class DemoLogin(BaseModel):
    user_id: str


class FormulaRequest(BaseModel):
    formula: str = Field(max_length=2000, description="e.g. net_income / revenue")
    default_company: str | None = Field(None, pattern=r"^\d{10}$",
                                        description="company_id of metrics without a ticker")
    basis: Literal["fiscal", "calendar"] = "fiscal"
    view: Literal["quarter", "ttm"] = "quarter"
    start: date | None = Field(None, alias="from", description="earliest period end")
    end: date | None = Field(None, alias="to", description="latest period end")


def create_app(authorizer: Authorizer, store: Store, jwt_secret: str,
               demo_users: frozenset[str] = frozenset(),
               today: Callable[[], date] | None = None,
               watchlists: Watchlists | None = None,
               sandboxes: Sandboxes | None = None,
               sandbox_config: SandboxConfig = SANDBOX_OFF,
               clock: Callable[[], datetime] | None = None,
               workspaces: Workspaces | None = None,
               views: Views | None = None) -> FastAPI:
    """demo_users: accounts anyone may sign in as without a password (empty disables it).
    today: the current US Eastern date (EDGAR's), injectable for tests.
    watchlists: the app database's watchlists (None: watchlist routes answer 503).
    sandboxes, sandbox_config: "Try the demo" sandboxes (query_api.sandbox); off unless
    sandbox_config.enabled and sandboxes are given. clock: the current UTC time.
    workspaces: saved work in the app database (None: those routes answer 503).
    views: per-user view preferences in the app database (None: those routes answer 503)."""
    app = FastAPI(title="Tributary Query API", version="1")
    instrument(app)

    @app.exception_handler(psycopg.errors.QueryCanceled)
    def too_slow(_request, _exc) -> JSONResponse:
        """Store's statement_timeout tripped: the request asked for too much at once."""
        return JSONResponse({"detail": f"query took longer than {QUERY_TIMEOUT}; narrow it "
                                       "(fewer companies, columns or periods)"}, status_code=503)
    bearer = HTTPBearer(auto_error=False)

    def current_user(
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> str:
        if creds is None:
            raise HTTPException(401, "missing bearer token")
        try:
            return verify(creds.credentials, jwt_secret)
        except Unauthenticated:
            raise HTTPException(401, "invalid token") from None

    User = Annotated[str, Depends(current_user)]

    def allowed(user_id: str, tier: str, company_id: str) -> bool:
        """Layer 1 check; OpenFGA unreachable → 503 (fail closed)."""
        fields = {"user_id": user_id, "tier": tier, "company_id": company_id}
        try:
            ok = authorizer.can(user_id, tier, company_id)
        except AuthzUnavailable as e:
            PERMISSION_CHECKS.labels(tier, "error").inc()
            log.warning("authorization unavailable: %s", e, extra=fields)
            raise HTTPException(503, "authorization unavailable") from None
        if not ok:
            PERMISSION_CHECKS.labels(tier, "denied").inc()
            log.info("permission denied", extra=fields)
            return False
        PERMISSION_CHECKS.labels(tier, "allowed").inc()
        return True

    def require(user_id: str, tier: str, company_id: str) -> None:
        """Layer 1. Same answer whether the company doesn't exist or isn't permitted."""
        if not allowed(user_id, tier, company_id):
            raise HTTPException(403, f"not permitted to read {tier} for this company")

    def readable(user_id: str) -> dict[str, set[str]]:
        try:
            access = authorizer.readable(user_id)
        except AuthzUnavailable as e:
            PERMISSION_CHECKS.labels("list", "error").inc()
            log.warning("authorization unavailable: %s", e, extra={"user_id": user_id})
            raise HTTPException(503, "authorization unavailable") from None
        PERMISSION_CHECKS.labels("list", "allowed").inc()
        return access

    add_ui_routes(app, store=store, current_user=current_user, require=require,
                  allowed=allowed, readable=readable, today=today, watchlists=watchlists)
    add_research_routes(app, store=store, current_user=current_user, require=require,
                        readable=readable)
    add_notes_routes(app, store=store, current_user=current_user, require=require,
                     readable=readable)

    add_workspace_routes(app, store=store, workspaces=workspaces, current_user=current_user)
    add_view_routes(app, views=views, current_user=current_user)

    sandbox_info = add_sandbox_routes(
        app, store=store, sandboxes=sandboxes, config=sandbox_config, jwt_secret=jwt_secret,
        current_user=current_user, clock=clock or (lambda: datetime.now(UTC)))

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/demo/users")
    def demo_user_list() -> dict:
        if not demo_users:
            raise HTTPException(404)
        return {"users": sorted(demo_users)}

    @app.post("/v1/demo/token")
    def demo_token(body: DemoLogin) -> dict:
        # a sandbox user's only token is the one its creation returned (it can't be renewed)
        if body.user_id not in demo_users or is_sandbox_user(body.user_id):
            raise HTTPException(404)
        return {"token": mint(body.user_id, jwt_secret, DEMO_TOKEN_TTL),
                "expires_in": DEMO_TOKEN_TTL}

    @app.get("/v1/me")
    def me(user_id: User) -> dict:
        access = readable(user_id)
        profiles = store.profiles(user_id, sorted(access)) if access else {}
        sandbox = sandbox_info(user_id)
        return {
            "user_id": user_id,
            "companies": [{"company_id": c, **_PROFILE, **profiles.get(c, {}), "tiers": sorted(t)}
                          for c, t in sorted(access.items())],
            **({"sandbox": sandbox} if sandbox else {}),
        }

    @app.get("/v1/companies/search")
    def search(user_id: User, q: Annotated[str, Query(min_length=1, max_length=100)]) -> dict:
        """Company identity is public metadata; `access` says what the caller can read."""
        access = readable(user_id)
        return {"results": [
            {**r, "access": sorted(access.get(r["company_id"], set()))}
            for r in store.search(user_id, q)
        ]}

    known: dict[str, list[str]] = {}

    def metric_names() -> dict[str, list[str]]:
        """Read once from the database: metrics change only with a migration (and a deploy)."""
        if not known:
            known.update(store.metric_names())
        return known

    @app.get("/v1/metrics")
    def metrics() -> dict:
        """Metric names financials accepts: reported (silver.metrics) and derived."""
        return metric_names()

    @app.get("/v1/companies/{company_id}/financials", response_model=dict)
    def financials(
        user_id: User, company_id: CompanyId,
        metrics: Annotated[list[str] | None, Query(description="default: all")] = None,
        quarters: Annotated[int, Query(ge=1, le=40)] = 4,
        view: Annotated[Literal["quarter", "ttm"], Query(
            description="quarter, or trailing twelve months ending each quarter")] = "quarter",
        basis: Annotated[Literal["fiscal", "calendar"], Query(
            description="fiscal: every fiscal quarter; calendar: one per calendar quarter")]
            = "fiscal",
        fmt: Annotated[Literal["json", "csv"], Query(
            alias="format", description="csv: one row per value, with its source filing")]
            = "json",
    ) -> dict | Response:
        names = metric_names()
        every = names["metrics"] + names["derived_metrics"]
        unknown = set(metrics or []) - set(every)
        if unknown:
            raise HTTPException(422, f"unknown metrics: {sorted(unknown)}")
        require(user_id, "financials", company_id)
        rows = store.financials(user_id, company_id, metrics or every, quarters, view, basis)
        meta = {"company_id": company_id, "view": view, "basis": basis, "as_of": _now()}
        if fmt == "csv":
            return csv_response(f"{company_id}-financials-{view}-{basis}.csv", FINANCIALS_CSV, ([
                r["metric"], view, r["period_start"], r["period_end"],
                _period("FY", r.get("fiscal_year"), r.get("fiscal_quarter")),
                _period("CY", r.get("calendar_year"), r.get("calendar_quarter")),
                r["value"], r["unit"], r["value_source"], r["accession_no"], r["form"],
                r["filed"], r["source_tag"], r["source_accessions"],
                filing_url(r.get("filer_cik"), r["accession_no"]), r["ingested_at"],
            ] for r in rows))
        return {**meta, "rows": [
            {
                "metric": r["metric"], "period_start": r["period_start"],
                "period_end": r["period_end"],
                "fiscal_period": _period("FY", r.get("fiscal_year"), r.get("fiscal_quarter")),
                "calendar_period": _period("CY", r.get("calendar_year"),
                                           r.get("calendar_quarter")),
                "value": r["value"], "unit": r["unit"], "value_source": r["value_source"],
                **({"inputs": r["inputs"]} if r.get("inputs") else {}),
                "citation": {"accession_no": r["accession_no"], "form": r["form"],
                             "filed": r["filed"], "xbrl_tag": r["source_tag"],
                             "source_accessions": r["source_accessions"],
                             "filing_url": filing_url(r.get("filer_cik"), r["accession_no"])},
                "freshness": {"filed": r["filed"], "ingested_at": r["ingested_at"]},
            } for r in rows
        ]}

    segment_known: list[str] = []

    def segment_metric_names() -> list[str]:
        """Read once from the database (silver.segment_metrics): changed only by migrations."""
        if not segment_known:
            segment_known.extend(store.segment_metric_names())
        return segment_known

    @app.get("/v1/companies/{company_id}/segments", response_model=dict)
    def segments(
        user_id: User, company_id: CompanyId,
        axis: Annotated[SegmentAxis | None, Query(
            description="business_segment, geography, product_service or reconciliation"
                        " (corporate, eliminations); default: all")] = None,
        metric: Annotated[str | None, Query(
            description="e.g. revenue, operating_income; default: all")] = None,
        quarters: Annotated[int, Query(ge=1, le=40, description="latest periods")] = 8,
        view: Annotated[Literal["quarter", "ttm"], Query(
            description="quarter, or trailing twelve months ending each quarter")] = "quarter",
        basis: Annotated[Literal["fiscal", "calendar"], Query(
            description="fiscal: every fiscal quarter; calendar: one per calendar quarter")]
            = "fiscal",
        fmt: Annotated[Literal["json", "csv"], Query(
            alias="format", description="csv: one row per value, with its source filing")]
            = "json",
    ) -> dict | Response:
        """Revenue, operating income and other metrics per segment member, from the filings'
        XBRL dimensions. Values are exact decimal strings; each cites its filing."""
        names = segment_metric_names()
        if metric is not None and metric not in names:
            raise HTTPException(422, f"unknown segment metric; one of {names}")
        require(user_id, "financials", company_id)
        rows = store.segments(user_id, company_id, axis, metric, quarters, view, basis)
        if fmt == "csv":
            return csv_response(f"{company_id}-segments-{view}-{basis}.csv", SEGMENTS_CSV, ([
                r["axis"], r["member"], r["member_label"], r["metric"], view,
                r["period_start"], r["period_end"],
                _period("FY", r.get("fiscal_year"), r.get("fiscal_quarter")),
                _period("CY", r.get("calendar_year"), r.get("calendar_quarter")),
                r["value"], r["unit"], r["value_source"], r["accession_no"], r["form"],
                r["filed"], r["source_tag"], r["axis_qname"], r["source_accessions"],
                filing_url(r.get("filer_cik"), r["accession_no"]), r["ingested_at"],
            ] for r in rows))
        return {"company_id": company_id, "view": view, "basis": basis, "as_of": _now(),
                "axis": axis, "metric": metric, "rows": [
            {
                "axis": r["axis"], "member": r["member"], "member_label": r["member_label"],
                "member_aliases": r.get("member_aliases") or [],
                "metric": r["metric"], "period_start": r["period_start"],
                "period_end": r["period_end"],
                "fiscal_period": _period("FY", r.get("fiscal_year"), r.get("fiscal_quarter")),
                "calendar_period": _period("CY", r.get("calendar_year"),
                                           r.get("calendar_quarter")),
                "value": str(r["value"]), "unit": r["unit"], "value_source": r["value_source"],
                "citation": {"accession_no": r["accession_no"], "form": r["form"],
                             "filed": r["filed"], "xbrl_tag": r["source_tag"],
                             "xbrl_axis": r["axis_qname"], "xbrl_member": r["member"],
                             "source_accessions": r["source_accessions"],
                             "filing_url": filing_url(r.get("filer_cik"), r["accession_no"])},
                "freshness": {"filed": r["filed"], "ingested_at": r["ingested_at"]},
            } for r in rows
        ]}

    @app.get("/v1/companies/{company_id}/filings")
    def filings(user_id: User, company_id: CompanyId,
                limit: Annotated[int, Query(ge=1, le=20)] = 8,
                accession_no: Annotated[str | None, Query(
                    pattern=r"^\d{10}-\d{2}-\d{6}$",
                    description="only this filing (any age): the reader's outline")] = None,
                ) -> dict:
        """Parsed filings, newest first, with their sections (titles and sizes, no text)."""
        require(user_id, "filings", company_id)
        return {"company_id": company_id, "as_of": _now(),
                "filings": store.filings(user_id, company_id, limit, accession_no)}

    @app.get("/v1/filings/latest")
    def latest_filings(user_id: User,
                       limit: Annotated[int, Query(ge=1, le=50)] = 20) -> dict:
        """Newest parsed filings across the companies the caller can read filings for."""
        companies = sorted(c for c, tiers in readable(user_id).items() if "filings" in tiers)
        rows = store.latest_filings(user_id, companies, limit) if companies else []
        return {"as_of": _now(), "filings": rows}

    @app.get("/v1/companies/{company_id}/sections/{section_key}")
    def sections(
        user_id: User, company_id: CompanyId,
        section_key: Annotated[str, Path(pattern=r"^[A-Z0-9.?-]{1,8}$",
                                         description='10-K "1A", 10-Q "II-1A", 8-K "2.02"')],
        form: Annotated[str | None, Query(pattern=r"^(10-K|10-Q|8-K)$")] = None,
        accession_no: Annotated[str | None, Query(pattern=r"^\d{10}-\d{2}-\d{6}$")] = None,
        limit: Annotated[int, Query(ge=1, le=5)] = 1,
        max_chars: Annotated[int, Query(ge=100, le=50_000)] = 8_000,
    ) -> dict:
        require(user_id, "filings", company_id)
        rows = store.sections(user_id, company_id, section_key, form, accession_no, limit,
                              max_chars)
        return {"company_id": company_id, "as_of": _now(), "sections": [
            {
                "section_key": r["section_key"], "title": r["title"], "text": r["text"],
                "truncated": r["total_chars"] > len(r["text"]),
                "total_chars": r["total_chars"],
                "citation": {"accession_no": r["accession_no"], "form": r["form"],
                             "filed": r["filed"], "period_of_report": r["period_of_report"],
                             "section_key": r["section_key"], "document_url": r["document_url"],
                             "char_start": r["char_start"], "char_end": r["char_end"]},
                "freshness": {"filed": r["filed"], "ingested_at": r["ingested_at"]},
            } for r in rows
        ]}

    def checked(body: FormulaRequest) -> tuple[Node, Analysis]:
        try:
            ast = parse(body.formula)
            a = analyze(ast, body.view)
        except FormulaError as e:
            raise HTTPException(422, {"errors": [e.to_dict()]}) from None
        if a.bare and not body.default_company:
            raise HTTPException(422, {"errors": [_no_default(a.bare).to_dict()]})
        return ast, a

    def not_yours(user_id: str, tickers: dict[str, tuple[int, int]]) -> list[dict]:
        """The same error for a ticker that matches nothing and one the caller can't read;
        suggestions come only from the caller's own companies."""
        mine = sorted(c for c, tiers in readable(user_id).items() if "financials" in tiers)
        names = [t for ts in (store.tickers(user_id, mine) if mine else {}).values() for t in ts]
        out = []
        for t, (pos, length) in tickers.items():
            s = suggest(t, names)
            out.append(FormulaError(f"{t} isn't one of your companies", pos, length,
                                    f"Did you mean {s}?" if s else
                                    "Only companies granted to you can be charted",
                                    code="company").to_dict())
        return out

    @app.post("/v1/formulas/validate")
    def validate_formula(user_id: User, body: FormulaRequest) -> dict:
        """Parse and check a formula: names, units, limits, and that every ticker is one of
        the caller's companies. Reads no financial data."""
        try:
            ast, a = checked(body)
        except HTTPException as e:
            return {"valid": False, **e.detail}
        resolved = store.resolve_tickers(user_id, sorted(a.tickers)) if a.tickers else {}
        if a.tickers:
            mine = {c for c, tiers in readable(user_id).items() if "financials" in tiers}
            bad = {t: p for t, p in a.tickers.items() if resolved.get(t) not in mine}
            if bad:
                return {"valid": False, "errors": not_yours(user_id, bad)}
        ids = {resolved[t] for t in a.tickers} | ({body.default_company} if a.bare else set())
        return {"valid": True, "unit": a.unit, "alignment": alignment(body.basis, ids),
                "ast": to_dict(ast), "inputs": _inputs(a, {**resolved, None: body.default_company})}

    @app.post("/v1/formulas/evaluate")
    def evaluate_formula(user_id: User, body: FormulaRequest) -> dict:
        """A formula's series. Every company it references is checked (layer 1) before the
        query, which runs under RLS like every Store query."""
        ast, a = checked(body)
        resolved = store.resolve_tickers(user_id, sorted(a.tickers)) if a.tickers else {}
        companies: dict[str | None, str] = {t: resolved[t] for t in a.tickers if t in resolved}
        if a.bare and body.default_company:
            companies[None] = body.default_company
            require(user_id, "financials", body.default_company)
        checks: dict[str, bool] = {}
        denied = {}
        for t, p in a.tickers.items():
            cid = resolved.get(t)
            if cid is not None and cid not in checks:
                checks[cid] = allowed(user_id, "financials", cid)
            if cid is None or not checks[cid]:
                denied[t] = p
        if denied:
            raise HTTPException(403, {"errors": not_yours(user_id, denied)})
        compiled = compile_formula(ast, a, companies, body.basis, body.start, body.end)
        rows = store.formula(user_id, compiled)
        return {
            "formula": body.formula, "unit": a.unit, "basis": body.basis, "view": body.view,
            "alignment": compiled.alignment, "as_of": _now(),
            "inputs": _inputs(a, companies),
            "points": [_point(r, compiled.alignment) for r in rows],
        }

    return app


def _no_default(at: tuple[int, int]) -> FormulaError:
    return FormulaError("Metrics without a ticker need a default company", at[0], at[1],
                        "Pass default_company, or write TICKER.metric", code="company")


def _inputs(a: Analysis, companies: dict[str | None, str | None]) -> list[dict]:
    return [{"input": i, "ref": inp.ref, "ticker": inp.ticker,
             "company_id": companies.get(inp.ticker), "metric": inp.metric, "span": inp.span,
             "lag_quarters": inp.lag, "unit": CATALOG[inp.metric][0]}
            for i, inp in enumerate(a.inputs)]


def _point(r: dict, align: str) -> dict:
    year, quarter = divmod(r["period_key"], 4)
    prefix = "FY" if align == "fiscal" and r["fiscal_key"] else "CY"
    inputs = [{
        "input": x["input"], "period_start": x["period_start"], "period_end": x["period_end"],
        "value": x["value"], "value_source": x["value_source"],
        "citation": {"accession_no": x["accession_no"], "form": x["form"], "filed": x["filed"],
                     "source_accessions": x["source_accessions"],
                     "filing_url": filing_url(x.get("filer_cik"), x["accession_no"])},
        "freshness": {"filed": x["filed"], "ingested_at": x["ingested_at"]},
    } for x in r["inputs"]]
    return {"period": f"{prefix}{year} Q{quarter + 1}", "period_key": r["period_key"],
            "period_end": r["period_end"], "value": r["value"], "inputs": inputs,
            "source_accessions": sorted({a for x in r["inputs"] for a in x["source_accessions"]})}


_PROFILE = {"name": None, "sic": None, "industry": None, "exchanges": None}


def _now() -> str:
    return datetime.now(UTC).isoformat()
