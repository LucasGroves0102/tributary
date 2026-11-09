"""A saved model's numbers for the thesis check, from the Query API (F2). The maths live in one
place, the Query API's `query_api/model_maths.py` (its docstring is the spec): this module only
asks `POST /v1/companies/{id}/model` with the model's saved state, as the user, and reads the
answer. Before F2 it was a line-for-line port of the web app's `models/model.ts`, kept equal by a
shared fixture; that fixture now tests the Query API's implementation.

What the check uses from a run (`cases.base`): `check` (B2: the base year, the 5-year CAGR,
average, lowest and highest operating margin, and per forecast year its full-year growth and
margin with flags: growth more than 10 points off the CAGR, margin outside the range), `user_set`
(B7: per year, whether growth and margin differ from Tributary's defaults, so the check never
endorses a default as the user's view), `years` (each year's EPS, FCF, share change, which rates
are still defaults, and for a year built from reported quarters how many), plus the run's
`history`, `run_rate` and `current` year. The base case is the only one checked: bear and bull
never reach the data.

The history the check cites is still read with `get_financials` (view ttm, the same 40 quarters
the run reads) as a tool result, so figures from it verify like any other."""

from typing import Any

from tributary_agent.ui import Http

MAX_HISTORY = 10
HISTORY_METRICS = ["revenue", "operating_income"]
HISTORY_QUARTERS = 40
SCENARIOS = ("bear", "base", "bull")
MAX_YEARS = 10
MAX_NOTE = 500


def _finite(value: Any) -> bool:
    """JavaScript's `typeof v === "number" && Number.isFinite(v)`: no booleans, no strings."""
    return (isinstance(value, int | float) and not isinstance(value, bool)
            and value == value and value not in (float("inf"), float("-inf")))


def _years(raw: Any) -> list[dict]:
    out = []
    for a in (raw if isinstance(raw, list) else [])[:MAX_YEARS]:
        if not isinstance(a, dict) or not _finite(a.get("growth")) or not _finite(a.get("margin")):
            continue
        note = a.get("note").strip()[:MAX_NOTE] if isinstance(a.get("note"), str) else ""
        out.append({"growth": float(a["growth"]), "margin": float(a["margin"]),
                    **({"note": note} if note else {})})
    return out


def scenario_assumptions(state: Any, scenario: str = "base") -> list[dict] | None:
    """One case's typed growth and margin from a saved model state (untrusted): `base` is
    `assumptions`; `bear` / `bull` come from `scenarios` and count only with as many years as
    the base case. None when the state has no such case. The thesis check uses it to pick the
    models worth running and to quote the user's notes; the run itself validates strictly."""
    if not isinstance(state, dict) or scenario not in SCENARIOS:
        return None
    base = _years(state.get("assumptions"))
    if not base:
        return None
    if scenario == "base":
        return base
    cases = state.get("scenarios") if isinstance(state.get("scenarios"), dict) else {}
    raw = cases.get(scenario)
    case = _years(raw)
    return case if case and isinstance(raw, list) and len(raw) == len(case) == len(base) \
        else None


RUN_ERRORS = {403: "no access to this company's financials",
              404: "the Query API can't run models yet",  # an older one, during a deploy
              422: "the saved model isn't one the Query API accepts"}


def run_model(api: Http, company_id: str, state: dict) -> tuple[dict | None, str | None]:
    """The model run as the token's user: (answer, None), or (None, why not)."""
    r = api.call("POST", f"/v1/companies/{company_id}/model", json_body=state)
    if r is None:
        return None, "couldn't run this model"
    if r.status_code != 200:
        return None, RUN_ERRORS.get(r.status_code, "couldn't run this model")
    try:
        body = r.json()
    except ValueError:
        return None, "couldn't run this model"
    base = (body.get("cases") or {}).get("base") if isinstance(body, dict) else None
    if not isinstance(base, dict) or not isinstance(base.get("check"), dict):
        return None, "couldn't run this model"
    return body, None
