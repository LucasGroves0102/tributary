"""Reported vs standardised (F5): what one standardised figure is made of, the FactSet/Koyfin
pattern of a line you can open into its parts.
GET /v1/companies/{id}/components?metric=&period_end=&view=

What "made of" means, by how gold built the figure (`how`):
- `derived`: a gold.derived_financials metric (net debt, cash and investments, free cash flow,
  gross margin, ...). Its components are its stored `inputs` (the values the rule used, migrations
  014/040/049), each with its own gold figure and role (+, -, numerator, denominator). An input the
  rule left out says why (`note`): net debt's and cash and investments' short-term investments are
  `inside_cash` when gold has them at that date but 040's rule counted them in cash, and
  `not_reported` when it hasn't.
- `ttm`: a trailing twelve months, the four quarters it sums (or the year a 10-K states, 040).
- `sum`: a reported line a filing tags only as parts (038's silver.metric_sums: a bank's net
  revenue, NVIDIA's marketable securities, contract liabilities current + noncurrent): the parts.
- `computed`: gross profit where the filing prints none (039): revenue less cost of revenue.
- `ytd`: a quarter derived from year-to-date figures (a fiscal Q4 = the 10-K's year less nine
  months): the two facts.
- `reported`: a figure as tagged.
For each figure that comes from facts, `facts` is the fact selection (039's gold.best_facts):
every mapped tag with a value for that period, its priority, whether it is a line or only part of
a sum, and which one gold used. Read through gold.figure_facts (049, SECURITY DEFINER under
authz.readable_companies), so a company the caller can't read gives nothing even past require().

require(financials) first, then Store under RLS; one company, one period: never catalog ids.
Values are str(Decimal) like /segments, so nothing is rounded on the way.
"""

from collections.abc import Callable
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query

from query_api.shapes import filing_url, period
from query_api.store import Store

CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]
Accession = Annotated[str, Path(pattern=r"^\d{10}-\d{2}-\d{6}$")]

# each derived metric's inputs in the order a statement reads them, with their role
ROLES: dict[str, list[tuple[str, str]]] = {
    "net_debt": [("long_term_debt", "+"), ("long_term_debt_current", "+"), ("cash", "-"),
                 ("short_term_investments", "-")],
    "cash_and_investments": [("cash", "+"), ("short_term_investments", "+")],
    "free_cash_flow": [("operating_cash_flow", "+"), ("capex", "-")],
    "gross_margin": [("gross_profit", "numerator"), ("revenue", "denominator")],
    "operating_margin": [("operating_income", "numerator"), ("revenue", "denominator")],
    "net_margin": [("net_income", "numerator"), ("revenue", "denominator")],
    "fcf_margin": [("operating_cash_flow", "numerator"), ("capex", "numerator"),
                   ("revenue", "denominator")],
    "debt_to_equity": [("long_term_debt", "numerator"), ("long_term_debt_current", "numerator"),
                       ("stockholders_equity", "denominator")],
    "current_ratio": [("current_assets", "numerator"), ("current_liabilities", "denominator")],
}
# inputs a rule may leave out at a date, and the metrics whose rule it is (040, 049)
INVESTMENTS_RULE = frozenset({"net_debt", "cash_and_investments"})
SAME_PERIOD_DAYS = 7  # a fact starting within a week of gold's start is the same quarter (031)
COMPUTED = "derived:revenue-cost_of_revenue"


def how_built(row: dict) -> str:
    tag = row.get("source_tag") or ""
    if row.get("inputs") and "quarters" in row["inputs"]:
        return "ttm"
    if row.get("inputs"):
        return "derived"
    if tag == COMPUTED:
        return "computed"
    if "+" in tag:
        return "sum"
    if row.get("value_source") == "derived":
        return "ytd"
    return "reported"


def _num(v: Any) -> str | None:
    return None if v is None else str(v)


def _figure(r: dict) -> dict:
    return {
        "metric": r["metric"], "period_start": r["period_start"], "period_end": r["period_end"],
        "fiscal_period": period("FY", r.get("fiscal_year"), r.get("fiscal_quarter")),
        "value": _num(r["value"]), "unit": r["unit"], "value_source": r["value_source"],
        "how": how_built(r),
        "citation": {"accession_no": r["accession_no"], "form": r["form"], "filed": r["filed"],
                     "xbrl_tag": r["source_tag"], "source_accessions": r["source_accessions"],
                     "filing_url": filing_url(r.get("filer_cik"), r["accession_no"])},
    }


def _fact(f: dict, used: bool, role: str | None = None) -> dict:
    return {
        "tag": f["tag"], "taxonomy": f["taxonomy"], "priority": f["priority"],
        "line": f["gold_line"], "period_start": f["period_start"], "period_end": f["period_end"],
        "value": _num(f["value"]), "accession_no": f["accession_no"], "form": f["form"],
        "filed": f["filed"], "filing_url": filing_url(f.get("filer_cik"), f["accession_no"]),
        "filings": f["filings"], "used": used, **({"role": role} if role else {}),
    }


def _same_period(f: dict, start: date | None) -> bool:
    if start is None or f["period_start"] is None:
        return start is None and f["period_start"] is None
    return abs((f["period_start"] - start).days) <= SAME_PERIOD_DAYS


def fact_selection(row: dict, facts: list[dict], earlier: list[dict]) -> list[dict]:
    """The facts behind one gold row, chosen one(s) marked: `facts` end on the row's period end,
    `earlier` on the day before its start (a year-to-date difference's other half)."""
    how = how_built(row)
    tag = (row.get("source_tag") or "").split(":", 1)[-1]
    if how == "ytd":
        # the later year to date (this filing) and the earlier one with the same start
        mine = [f for f in facts if f["tag"] == tag and f["accession_no"] == row["accession_no"]
                and not _same_period(f, row["period_start"])]
        prior = set(row.get("source_accessions") or []) - {row["accession_no"]}
        for f in sorted(mine, key=lambda f: f["period_start"] or date.min):
            twin = next((e for e in earlier if e["tag"] == tag
                         and e["period_start"] == f["period_start"]
                         and (not prior or e["accession_no"] in prior)), None)
            if twin:
                return [_fact(f, True, "+"), _fact(twin, True, "-")]
        return []
    parts = set(tag.split("+")) if how == "sum" else {tag}
    out = [_fact(f, f["tag"] in parts and f["accession_no"] == row["accession_no"])
           for f in facts if _same_period(f, row["period_start"])]
    return sorted(out, key=lambda f: (not f["used"], f["priority"], f["tag"]))


def shape(company_id: str, metric: str, span: str, row: dict, rows: dict[str, dict],
          facts: dict[tuple[str, date], list[dict]], definition: str | None,
          present: set[str]) -> dict:
    """The answer, from the figure's gold row, its components' gold rows (`rows`, by metric),
    figure_facts per (metric, period end) and which metrics gold has at the date (`present`)."""
    end = row["period_end"]

    def selection(r: dict) -> list[dict]:
        if span != "quarter" or how_built(r) in ("derived", "ttm", "computed"):
            return []
        before = r["period_start"] - timedelta(days=1) if r["period_start"] else end
        return fact_selection(r, facts.get((r["metric"], r["period_end"]), []),
                              facts.get((r["metric"], before), []))

    def parts_of(r: dict) -> list[dict]:
        if how_built(r) == "computed":
            return [{**_figure(rows[m]), "role": role} for m, role in
                    (("revenue", "+"), ("cost_of_revenue", "-")) if m in rows]
        if how_built(r) == "ttm":
            return [{"period_start": q.get("period_start"), "period_end": q.get("period_end"),
                     "value": _num(q.get("value")), "accession_no": q.get("accession_no"),
                     "role": "+"} for q in r["inputs"]["quarters"]]
        return []

    how = how_built(row)
    components: list[dict] = []
    if how == "derived":
        inputs = row["inputs"]
        order = ROLES.get(metric, [])
        keys = [k for k, _ in order] + sorted(k for k in inputs if k not in dict(order))
        for k in keys:
            role = dict(order).get(k)
            if k not in inputs:
                if k == "short_term_investments" and metric in INVESTMENTS_RULE:
                    components.append({"metric": k, "role": role, "value": None, "used": False,
                                       "note": "inside_cash" if k in present else "not_reported"})
                continue
            r = rows.get(k)
            components.append({
                "metric": k, "role": role or ("year_ago" if k.endswith("_year_ago") else None),
                "value": _num(inputs[k]), "used": True, "note": None,
                "figure": _figure(r) if r else None,
                "parts": parts_of(r) if r else [],
                "facts": selection(r) if r else [],
            })
    return {
        "company_id": company_id, "metric": metric, "span": span, "period_end": end,
        "figure": _figure(row), "how": how, "definition": definition,
        "components": components,
        "parts": parts_of(row),
        "facts": selection(row),
    }


def add_components_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                          require: Callable[[str, str, str], None],
                          metric_names: Callable[[], dict[str, list[str]]]) -> None:
    User = Annotated[str, Depends(current_user)]

    @app.get("/v1/companies/{company_id}/components", response_model=dict)
    def components(
        user_id: User, company_id: CompanyId,
        metric: Annotated[str, Query(max_length=64)],
        period_end: Annotated[date, Query(description="the figure's period end")],
        view: Annotated[Literal["quarter", "ttm"], Query()] = "quarter",
    ) -> dict:
        """What one figure is made of: a derived figure's inputs, a TTM's quarters, a sum's parts,
        and the tags and filings behind each (the fact selection). Needs the financials tier."""
        names = metric_names()
        if metric not in names["metrics"] + names["derived_metrics"]:
            raise HTTPException(422, f"unknown metric: {metric}")
        require(user_id, "financials", company_id)
        got = store.components(user_id, company_id, view, metric, period_end)
        if got["row"] is None:
            raise HTTPException(404, "no figure for that period")
        return shape(company_id, metric, view, got["row"], got["rows"], got["facts"],
                     got["definition"], got["present"])

    @app.get("/v1/companies/{company_id}/filings/{accession_no}/figures", response_model=dict)
    def filing_figures(user_id: User, company_id: CompanyId, accession_no: Accession) -> dict:
        """What one filing itself says: its facts as gold reads them (the best tag per metric
        and period, `reported`) and the net debt, cash and investments and free cash flow
        computed from that filing alone by gold's rules (`computed`, with their inputs). The
        thesis check compares a claimed computed figure with these (H2). Empty when the
        filing has no facts for this company. Needs the financials tier."""
        require(user_id, "financials", company_id)
        rows = store.filing_figures(user_id, company_id, accession_no)
        return {"company_id": company_id, "accession_no": accession_no,
                "figures": [filing_figure(r) for r in rows]}


def filing_figure(r: dict) -> dict:
    inputs = r.get("inputs") or {}
    return {"metric": r["metric"], "period_start": r["period_start"],
            "period_end": r["period_end"], "value": _num(r["value"]), "how": r["how"],
            **({"xbrl_tag": r["source_tag"]} if r.get("source_tag") else {}),
            **({"inputs": {k: _num(v) for k, v in inputs.items()}} if inputs else {}),
            # 057: the figure's unit ("USD", "EUR", "EUR/shares"), so a reader says the currency
            **({"unit": r["unit"]} if r.get("unit") else {})}
