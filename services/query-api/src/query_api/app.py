"""Tributary Query API. Read-only; every data route is permission-checked twice:
OpenFGA first (layer 1), then Postgres row-level security (layer 2, in Store)."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from query_api.auth import Unauthenticated, verify
from query_api.authorizer import Authorizer, AuthzUnavailable
from query_api.store import Store

METRICS = ["revenue", "gross_profit", "operating_income", "net_income", "eps_diluted",
           "operating_cash_flow", "total_assets", "cash"]
CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]


def create_app(authorizer: Authorizer, store: Store, jwt_secret: str) -> FastAPI:
    app = FastAPI(title="Tributary Query API", version="1")
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
        try:
            allowed = authorizer.can(user_id, tier, company_id)
        except AuthzUnavailable:
            raise HTTPException(503, "authorization unavailable") from None  # fail closed
        if not allowed:
            raise HTTPException(403, f"not permitted to read {tier} for this company")

    def readable(user_id: str) -> dict[str, set[str]]:
        try:
            return authorizer.readable(user_id)
        except AuthzUnavailable:
            raise HTTPException(503, "authorization unavailable") from None

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

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
                             "source_accessions": r["source_accessions"]},
                "freshness": {"filed": r["filed"], "ingested_at": r["ingested_at"]},
            } for r in rows
        ]}

    @app.get("/v1/companies/{company_id}/filings")
    def filings(user_id: User, company_id: CompanyId,
                limit: Annotated[int, Query(ge=1, le=20)] = 8) -> dict:
        require(user_id, "filings", company_id)
        return {"company_id": company_id, "as_of": _now(),
                "filings": store.filings(user_id, company_id, limit)}

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


def _now() -> str:
    return datetime.now(UTC).isoformat()
