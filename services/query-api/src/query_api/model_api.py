"""`POST /v1/companies/{company_id}/model`: run a model (F2). The body is a model item's state
(workspaces.ModelState, strict: what the web app saves, what agent-api's thesis check reads from
a saved item, what MCP will send); the answer is the history, the current year's actuals, the
defaults and every case's projection, the base case's check and the valuation, all computed by
query_api.model_maths, the one implementation (its docstring is the spec).

Like every data route: the user is the JWT `sub`, require(financials) runs first, then Store reads
the company's gold rows under RLS as that user. Nothing is written: a run stores nothing, and the
price in the body is the user's own, never fetched or kept here."""

import json
import math
import re
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, HTTPException, Path, Request
from fastapi.responses import JSONResponse

from query_api.model_maths import (
    HISTORY_QUARTERS,
    LATEST_METRICS,
    LINE_METRICS,
    MAX_HISTORY,
    YTD_QUARTERS,
    run,
)
from query_api.shapes import filing_url
from query_api.store import Store
from query_api.workspaces import StateError, validate_state

CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]
MODEL_PATH = re.compile(r"^/v1/companies/\d{10}/model$")
MAX_MODEL_BODY = 131_072
ANNUAL_YEARS = MAX_HISTORY + 2


def _urls(rows: list[dict]) -> list[dict]:
    for r in rows:
        if r.get("accession_no"):
            r["filing_url"] = filing_url(r.get("filer_cik"), r["accession_no"])
    return rows


def _finite(value: Any) -> Any:
    """JSON has no NaN or infinity: a figure that isn't finite is absent."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


def add_model_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                     require: Callable[[str, str, str], None]) -> None:
    User = Annotated[str, Depends(current_user)]

    @app.middleware("http")
    async def cap_model_body(request: Request, call_next):
        """A model state is small JSON: refuse a large or unsized body before reading it."""
        if request.method == "POST" and MODEL_PATH.match(request.url.path):
            size = request.headers.get("content-length")
            if size is None or not size.isdigit():
                if request.headers.get("transfer-encoding"):
                    return JSONResponse({"detail": "send a Content-Length"}, status_code=411)
            elif int(size) > MAX_MODEL_BODY:
                return JSONResponse({"detail": f"body larger than {MAX_MODEL_BODY} bytes"},
                                    status_code=413)
        return await call_next(request)

    @app.post("/v1/companies/{company_id}/model", response_model=dict)
    def run_model(user_id: User, company_id: CompanyId,
                  state: Annotated[dict[str, Any], Body(description="a model item's state")],
                  ) -> dict:
        """A model run: history (reported and adjusted), the TTM, the current fiscal year's
        reported quarters, statistics, run rate, per-case defaults and projections (bear and bull
        previewed from the base case when absent), the base case's check and the user's-price
        valuation. Rules: query_api/model_maths.py."""
        try:
            model = validate_state("model", state)
        except StateError as e:
            raise HTTPException(422, f"invalid model: {e}") from None
        if model["companyId"] != company_id:
            raise HTTPException(422, "the model's companyId isn't this company")
        require(user_id, "financials", company_id)
        inputs = store.model_inputs(user_id, company_id, LINE_METRICS, HISTORY_QUARTERS,
                                    YTD_QUARTERS, ANNUAL_YEARS, LATEST_METRICS)
        for key in ("ttm", "quarters", "annual"):
            _urls(inputs[key])
        _urls(list(inputs["latest"].values()))
        out = run(inputs, model)
        # round-trip through JSON: dates and Decimals as strings, no NaN
        return json.loads(json.dumps(_finite(out), default=str))
