"""Tributary Query API. Read-only; every data route is permission-checked twice:
OpenFGA first (layer 1), then Postgres row-level security (layer 2, in Store)."""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import Counter
from pydantic import BaseModel

from query_api.auth import Unauthenticated, mint, verify
from query_api.authorizer import Authorizer, AuthzUnavailable
from query_api.store import Store
from tributary_telemetry.http import instrument

METRICS = [
    # income statement
    "revenue", "cost_of_revenue", "gross_profit", "research_and_development", "sga",
    "operating_income", "interest_expense", "pretax_income", "income_tax", "net_income",
    "eps_basic", "eps_diluted", "weighted_shares_basic", "weighted_shares_diluted",
    # balance sheet
    "cash", "short_term_investments", "receivables", "inventory", "current_assets",
    "total_assets", "current_liabilities", "long_term_debt_current", "long_term_debt",
    "total_liabilities", "stockholders_equity", "shares_outstanding",
    # cash flow
    "operating_cash_flow", "capex", "depreciation_amortization", "stock_based_compensation",
    "dividends_paid", "share_buybacks",
]
EDGAR = "https://www.sec.gov/Archives/edgar/data"
CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]


DEMO_TOKEN_TTL = 8 * 3600

PERMISSION_CHECKS = Counter("tributary_permission_checks_total",
                            "OpenFGA checks by tier (list: readable companies) and result "
                            "(allowed, denied, error)", ["tier", "result"])

log = logging.getLogger("query_api")


class DemoLogin(BaseModel):
    user_id: str


def create_app(authorizer: Authorizer, store: Store, jwt_secret: str,
               demo_users: frozenset[str] = frozenset()) -> FastAPI:
    """demo_users: accounts anyone may sign in as without a password (empty disables it)."""
    app = FastAPI(title="Tributary Query API", version="1")
    instrument(app)
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

    def require(user_id: str, tier: str, company_id: str) -> None:
        """Layer 1. Same answer whether the company doesn't exist or isn't permitted."""
        fields = {"user_id": user_id, "tier": tier, "company_id": company_id}
        try:
            allowed = authorizer.can(user_id, tier, company_id)
        except AuthzUnavailable as e:
            PERMISSION_CHECKS.labels(tier, "error").inc()
            log.warning("authorization unavailable: %s", e, extra=fields)
            raise HTTPException(503, "authorization unavailable") from None  # fail closed
        if not allowed:
            PERMISSION_CHECKS.labels(tier, "denied").inc()
            log.info("permission denied", extra=fields)
            raise HTTPException(403, f"not permitted to read {tier} for this company")
        PERMISSION_CHECKS.labels(tier, "allowed").inc()

    def readable(user_id: str) -> dict[str, set[str]]:
        try:
            access = authorizer.readable(user_id)
        except AuthzUnavailable as e:
            PERMISSION_CHECKS.labels("list", "error").inc()
            log.warning("authorization unavailable: %s", e, extra={"user_id": user_id})
            raise HTTPException(503, "authorization unavailable") from None
        PERMISSION_CHECKS.labels("list", "allowed").inc()
        return access

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
        if body.user_id not in demo_users:
            raise HTTPException(404)
        return {"token": mint(body.user_id, jwt_secret, DEMO_TOKEN_TTL),
                "expires_in": DEMO_TOKEN_TTL}

    @app.get("/v1/me")
    def me(user_id: User) -> dict:
        access = readable(user_id)
        names = store.names(user_id, sorted(access)) if access else {}
        return {
            "user_id": user_id,
            "companies": [{"company_id": c, "name": names.get(c), "tiers": sorted(t)}
                          for c, t in sorted(access.items())],
        }

    @app.get("/v1/companies/search")
    def search(user_id: User, q: Annotated[str, Query(min_length=1, max_length=100)]) -> dict:
        """Company identity is public metadata; `access` says what the caller can read."""
        access = readable(user_id)
        return {"results": [
            {**r, "access": sorted(access.get(r["company_id"], set()))}
            for r in store.search(user_id, q)
        ]}

    @app.get("/v1/metrics")
    def metrics() -> dict:
        return {"metrics": METRICS}

    @app.get("/v1/companies/{company_id}/financials")
    def financials(
        user_id: User, company_id: CompanyId,
        metrics: Annotated[list[str] | None, Query(description="default: all")] = None,
        quarters: Annotated[int, Query(ge=1, le=40)] = 4,
    ) -> dict:
        unknown = set(metrics or []) - set(METRICS)
        if unknown:
            raise HTTPException(422, f"unknown metrics: {sorted(unknown)}")
        require(user_id, "financials", company_id)
        rows = store.financials(user_id, company_id, metrics or METRICS, quarters)
        return {"company_id": company_id, "as_of": _now(), "rows": [
            {
                "metric": r["metric"], "period_start": r["period_start"],
                "period_end": r["period_end"], "value": r["value"], "unit": r["unit"],
                "value_source": r["value_source"],
                "citation": {"accession_no": r["accession_no"], "form": r["form"],
                             "filed": r["filed"], "xbrl_tag": r["source_tag"],
                             "source_accessions": r["source_accessions"],
                             "filing_url": filing_url(r.get("filer_cik"), r["accession_no"])},
                "freshness": {"filed": r["filed"], "ingested_at": r["ingested_at"]},
            } for r in rows
        ]}

    @app.get("/v1/companies/{company_id}/filings")
    def filings(user_id: User, company_id: CompanyId,
                limit: Annotated[int, Query(ge=1, le=20)] = 8) -> dict:
        require(user_id, "filings", company_id)
        return {"company_id": company_id, "as_of": _now(),
                "filings": store.filings(user_id, company_id, limit)}

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

    return app


def filing_url(cik: str | None, accession_no: str) -> str | None:
    """EDGAR filing index, e.g. https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/"""
    if not cik:
        return None
    return f"{EDGAR}/{int(cik)}/{accession_no.replace('-', '')}/"


def _now() -> str:
    return datetime.now(UTC).isoformat()
