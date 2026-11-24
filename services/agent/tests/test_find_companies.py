"""find_companies (companies by Tributary's sector grouping), get_financials saying which asked
metrics have no figures, and the screen action's checks: names from /v1/sectors, and a screen
that lists no company is never applied. All against httpx.MockTransport."""

import json

import httpx
import pytest

from tributary_agent.report import ScopedTools
from tributary_agent.tools import TOOLS, MetricTools, QueryApi, SectorCatalog
from tributary_agent.ui import UiActions, UiContext

NVDA, CEG, VST, CCJ = "0001045810", "0001868275", "0001692819", "0001009001"
SECTORS = {"sectors": [
    {"name": "Materials", "companies": 300, "industries": [
        {"name": "Chemicals", "sic": ["2800-2829"], "companies": 120},
        {"name": "Metals & mining", "sic": ["1000-1099", "1200-1220"], "companies": 180}]},
    {"name": "Utilities", "companies": 150, "industries": [
        {"name": "Electric utilities", "sic": ["4910-4911"], "companies": 90},
        {"name": "Independent power & renewables", "sic": ["4991"], "companies": 60}]},
], "note": "Tributary's own grouping of the SEC's SIC codes (not GICS)"}


def screener_row(cid, name, revenue, *, readable=True, sector="Utilities",
                 industry="Electric utilities", other=None):
    values = {"revenue_ttm": revenue, "revenue_growth_ttm": 0.12, "operating_margin_ttm": 0.2,
              "total_assets_q": 5e10}
    readings = {"revenue_ttm": {"status": "other_currency", "unit": other, "reason": None}} \
        if other else {}
    return {"company_id": cid, "name": name, "tickers": [name[:3].upper()],
            "exchanges": ["NYSE"], "sic": "4911", "industry": "ELECTRIC SERVICES",
            "fiscal_year_end": "1231", "filer_category": "large_accelerated",
            "filer_categories": [], "state_of_incorporation": "PA", "sector": sector,
            "sector_industry": industry, "access": ["financials"] if readable else [],
            "values": values if readable else {k: None for k in values},
            "sources": {"revenue_ttm": {"accession_no": "0001868275-26-000040", "form": "10-Q",
                                        "filed": "2026-11-04", "period_end": "2026-09-30"}}
            if readable else {k: None for k in values},
            "readings": readings, "latest_period": "2026-09-30", "confidence": None,
            "not_applicable": [], "co_registrants": []}


def handler_for(screener_rows=None, total=None, sectors=SECTORS, financials=None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/v1/sectors":
            return httpx.Response(200, json=sectors) if sectors else httpx.Response(404)
        if path == "/v1/screener":
            rows = screener_rows if screener_rows is not None else []
            return httpx.Response(200, json={
                "as_of": "2026-11-23T12:00:00Z", "total": total if total is not None else len(rows),
                "limit": 15, "offset": 0, "sort": "-revenue_ttm", "columns": [], "rows": rows})
        if path.endswith("/financials") and financials is not None:
            return financials(request)
        return httpx.Response(404, json={"detail": "not found"})

    return handler, seen


def query_api(handler, sectors=None):
    http = httpx.Client(base_url="http://query-api", transport=httpx.MockTransport(handler))
    return QueryApi("http://query-api", "alice-token", http=http, sectors=sectors)


# --- find_companies ----------------------------------------------------------------------

def test_find_companies_lists_an_industrys_companies_as_the_user():
    rows = [screener_row(CEG, "Constellation Energy", 2.45e10),
            screener_row(VST, "Vistra", 1.8e10, readable=False),
            screener_row(CCJ, "Cameco", 3.1e9, other="CAD", sector="Materials",
                         industry="Metals & mining")]
    handler, seen = handler_for(rows, total=40)
    api = query_api(handler)
    content, is_error = api.run("find_companies", {
        "industries": ["Electric utilities", "Metals & mining"], "sectors": ["Utilities"]})
    assert not is_error, content
    r = seen[-1]
    assert r.url.path == "/v1/screener" and r.headers["authorization"] == "Bearer alice-token"
    assert r.url.params.get_list("sector_industry") == ["Electric utilities", "Metals & mining"]
    assert r.url.params.get_list("sector") == ["Utilities"]
    assert r.url.params.get_list("columns") == ["revenue_ttm", "revenue_growth_ttm",
                                                "operating_margin_ttm", "total_assets_q"]
    assert (r.url.params["sort"], r.url.params["limit"]) == ("-revenue_ttm", "15")
    body = json.loads(content)
    assert body["total"] == 40 and body["more"].startswith("37 more match")
    ceg, vst, ccj = body["companies"]
    assert ceg == {"company_id": CEG, "name": "Constellation Energy", "tickers": ["CON"],
                   "sector": "Utilities", "industry": "Electric utilities", "sic": "4911",
                   "sic_label": "ELECTRIC SERVICES", "revenue_ttm": 2.45e10,
                   "revenue_growth_ttm": 0.12, "operating_margin_ttm": 0.2,
                   "total_assets": 5e10, "currency": "USD",
                   "source": {"accession_no": "0001868275-26-000040", "form": "10-Q",
                              "filed": "2026-11-04", "period_end": "2026-09-30"}}
    # a company the user can't read: listed (public metadata), its values null as the API gave
    assert vst["name"] == "Vistra" and vst["currency"] is None
    assert [vst[k] for k in ("revenue_ttm", "revenue_growth_ttm", "operating_margin_ttm",
                             "total_assets")] == [None] * 4
    assert "source" not in vst
    assert ccj["currency"] == "CAD" and "readings" not in ccj  # the currency is the reading
    # the values come before any nested object (the adversarial harness reads them so)
    assert content.index('"revenue_ttm"') < content.index('"source"')


def test_find_companies_limits_sorts_and_names_are_held_to_the_schema():
    handler, seen = handler_for([])
    api = query_api(handler)
    assert not api.run("find_companies", {"name": "  energy  ", "limit": 400,
                                          "sort": "name", "revenue_band": "1b-10b"})[1]
    p = seen[-1].url.params
    assert (p["q"], p["limit"], p["sort"], p["revenue_band"]) == ("energy", "25", "name",
                                                                   "1b-10b")
    assert "sector" not in p and "sector_industry" not in p
    assert api.run("find_companies", {"limit": 0})[1] is False
    assert seen[-1].url.params["limit"] == "1"
    before = len(seen)
    for bad in ({"sort": "-market_cap"}, {"revenue_band": "huge"}, {"limit": "lots"},
                {"industries": "Electric utilities"}, {"sectors": ["x" * 41]},
                {"industries": ["Electric utilities"] * 21}):
        content, is_error = api.run("find_companies", bad)
        assert is_error and content.startswith("invalid call to find_companies"), bad
    assert all(r.url.path == "/v1/sectors" for r in seen[before:])
    schema = next(t for t in TOOLS if t["name"] == "find_companies")["input_schema"]
    assert schema["properties"]["limit"] == {"type": "integer", "minimum": 1, "maximum": 25,
                                             "description": "default 15"}
    assert schema["additionalProperties"] is False and "required" not in schema


def test_unknown_names_come_back_with_the_closest_or_the_whole_grouping():
    handler, seen = handler_for([])
    api = query_api(handler)
    content, is_error = api.run("find_companies", {"industries": ["Electric utility"]})
    assert is_error and "unknown industry 'Electric utility'; closest: Electric utilities" \
        in content
    # a theme is no industry's name: every sector with its industries, to choose from
    content, is_error = api.run("find_companies", {"industries": ["Uranium"]})
    assert is_error and "unknown industry 'Uranium'" in content
    assert "Materials: Chemicals, Metals & mining; Utilities: Electric utilities, " \
           "Independent power & renewables" in content
    content, is_error = api.run("find_companies", {"sectors": ["Energy"]})
    assert is_error and "unknown sector 'Energy'" in content
    assert all(r.url.path == "/v1/sectors" for r in seen)  # never asked the screener


def test_the_grouping_is_read_once_and_a_query_api_without_it_decides_itself():
    handler, seen = handler_for([])
    catalog = SectorCatalog()
    for _ in range(3):  # one QueryApi a question, one catalog a process
        assert not query_api(handler, catalog).run("find_companies",
                                                   {"industries": ["Chemicals"]})[1]
    assert [r.url.path for r in seen].count("/v1/sectors") == 1
    handler, seen = handler_for([], sectors=None)  # an older Query API: no /v1/sectors
    content, is_error = query_api(handler).run("find_companies", {"industries": ["Anything"]})
    assert not is_error and json.loads(content)["note"].startswith("No companies match")
    assert seen[-1].url.params["sector_industry"] == "Anything"


def test_the_sector_names_reach_the_schema_once_the_query_api_gives_them():
    def handler(request):
        if request.url.path == "/v1/metrics":
            return httpx.Response(200, json={"metrics": ["revenue"], "derived_metrics": []})
        if request.url.path == "/v1/sectors":
            return httpx.Response(200, json=SECTORS)
        return httpx.Response(404)

    def props(tools):
        return next(t for t in tools if t["name"] == "find_companies")["input_schema"][
            "properties"]

    api = query_api(handler)
    tools = MetricTools(SectorCatalog()).get(api)
    assert props(tools)["sectors"]["items"]["enum"] == ["Materials", "Utilities"]
    assert props(tools)["industries"]["items"]["enum"] == [
        "Chemicals", "Metals & mining", "Electric utilities", "Independent power & renewables"]
    assert "enum" not in props(TOOLS)["sectors"]["items"]
    assert "enum" not in props(MetricTools().get(api))["industries"]["items"]


def test_a_report_or_a_check_never_lists_companies_beyond_its_scope():
    handler, seen = handler_for([screener_row(CEG, "Constellation Energy", 2.45e10)])
    scoped = ScopedTools(query_api(handler), {NVDA})
    content, is_error = scoped.run("find_companies", {"industries": ["Electric utilities"]})
    assert is_error and content.startswith("not run: a report covers only the companies")
    assert seen == [] and scoped.results == []


# --- get_financials: metrics with no figures ----------------------------------------------

def financials_answer(rows_for):
    def respond(request):
        asked = request.url.params.get_list("metrics")
        rows = [{"metric": m, "period_end": "2026-09-30", "value": 1.0, "unit": "USD",
                 "citation": {"accession_no": "0002070000-26-000010", "form": "10-Q",
                              "filed": "2026-11-10"}}
                for m in rows_for(asked)]
        return httpx.Response(200, json={"company_id": "0002070000", "view": "quarter",
                                         "basis": "fiscal", "as_of": "x", "rows": rows})
    return respond


def test_a_metric_without_figures_is_said_plainly_when_the_company_has_others():
    # a company before revenue: net income, cash and assets, never revenue
    held = ["cash", "net_income", "total_assets"]
    handler, seen = handler_for(financials=financials_answer(
        lambda asked: [m for m in (asked or held) if m in held]))
    api = query_api(handler)
    content, is_error = api.run("get_financials", {"company_id": "0002070000",
                                                   "metrics": ["revenue", "net_income"]})
    body = json.loads(content)
    assert not is_error and [r["metric"] for r in body["rows"]] == ["net_income"]
    assert body["no_figures"]["metrics"] == ["revenue"]
    assert body["no_figures"]["note"].startswith("Tributary has no revenue figures for this "
                                                 "company: its filings may report none yet")
    assert body["no_figures"]["note"].endswith("the other metrics returned are its data.")
    assert len(seen) == 1
    # only revenue asked, so nothing came back: one more look says what it does have
    content, _ = api.run("get_financials", {"company_id": "0002070000", "metrics": ["revenue"],
                                            "quarters": 8, "view": "ttm"})
    body = json.loads(content)
    assert body["rows"] == [] and body["no_figures"]["metrics"] == ["revenue"]
    assert "it has figures for cash, net_income, total_assets" in body["no_figures"]["note"]
    probe = seen[-1].url.params
    assert (probe.get_list("metrics"), probe["quarters"], probe["view"]) == ([], "1", "ttm")
    assert "value" not in json.dumps(body["no_figures"])  # names, never another figure


def test_figures_for_everything_asked_or_nothing_at_all_change_nothing():
    handler, seen = handler_for(financials=financials_answer(lambda asked: asked))
    content, _ = query_api(handler).run("get_financials", {"company_id": "0002070000",
                                                          "metrics": ["revenue"]})
    assert "no_figures" not in json.loads(content) and len(seen) == 1
    handler, seen = handler_for(financials=financials_answer(lambda asked: []))
    content, _ = query_api(handler).run("get_financials", {"company_id": "0002070000",
                                                          "metrics": ["revenue"]})
    assert "no_figures" not in json.loads(content) and len(seen) == 2


# --- set_screen_filters --------------------------------------------------------------------

def ui(handler):
    http = httpx.Client(base_url="http://query-api", transport=httpx.MockTransport(handler))
    return UiActions(QueryApi("http://query-api", "alice-token", http=http))


def test_a_screen_by_the_grouping_is_checked_then_applied_on_the_companies_page():
    handler, seen = handler_for([screener_row(CCJ, "Cameco", 3.1e9)], total=34)
    actions = ui(handler)
    actions.observe(UiContext.model_validate({"page": "companies", "screen": {
        "sectors": ["Utilities"], "sort": "name"}}))
    content, is_error, action = actions.run({"action": "set_screen_filters", "filters": {
        "industries": ["Metals & mining", "Independent power & renewables"]}})
    assert not is_error, content
    assert action == {"action": "set_screen_filters", "reset": False,
                      "filters": {"industries": ["Metals & mining",
                                                 "Independent power & renewables"]},
                      "label": "Screen: industries Metals & mining, "
                               "Independent power & renewables"}
    assert "34 companies match" in json.loads(content)["note"]
    assert "Companies page" in json.loads(content)["note"]
    # checked as the app would show it: merged into the screen on the page, one row
    p = seen[-1].url.params
    assert (p.get_list("sector"), p.get_list("sector_industry"), p["limit"]) == (
        ["Utilities"], ["Metals & mining", "Independent power & renewables"], "1")
    assert seen[-1].headers["authorization"] == "Bearer alice-token"


def test_a_screen_that_lists_no_company_is_never_applied():
    handler, seen = handler_for([], total=0)
    actions = ui(handler)
    content, is_error, action = actions.run({"action": "set_screen_filters", "reset": True,
                                             "filters": {"industry": "uranium",
                                                         "band": "lt100m", "mine": True}})
    assert is_error and action is None
    assert content.startswith("not applied: no company matches the screen this would make "
                              "(industry uranium, band lt100m, mine True)")
    p = seen[-1].url.params
    assert (p["industry"], p["revenue_band"], p["access"]) == ("uranium", "lt100m", "financials")


@pytest.mark.parametrize("filters, message", [
    ({"industries": ["Uranium"]}, "unknown industry 'Uranium'"),
    ({"sectors": ["Utility"]}, "unknown sector 'Utility'; closest: Utilities"),
])
def test_screen_names_must_be_the_groupings(filters, message):
    handler, seen = handler_for([screener_row(CEG, "Constellation Energy", 2.45e10)])
    content, is_error, action = ui(handler).run({"action": "set_screen_filters",
                                                 "filters": filters})
    assert is_error and action is None and message in content
    assert all(r.url.path == "/v1/sectors" for r in seen)


def test_a_screener_that_cant_answer_doesnt_block_and_a_refusal_is_said():
    def down(request):
        if request.url.path == "/v1/screener":
            return httpx.Response(503, json={"detail": "busy"})
        return httpx.Response(404)

    content, is_error, action = ui(down).run({"action": "set_screen_filters",
                                              "filters": {"industry": "software"}})
    assert not is_error and action["filters"] == {"industry": "software"}

    def refuses(request):
        if request.url.path == "/v1/screener":
            return httpx.Response(422, json={"detail": "too many or too long filters"})
        return httpx.Response(404)

    content, is_error, _ = ui(refuses).run({"action": "set_screen_filters",
                                            "filters": {"industry": "software"}})
    assert is_error and "the screener refused it: too many or too long filters" in content


def test_screen_actions_in_one_answer_build_on_each_other_and_reset_starts_over():
    handler, seen = handler_for([screener_row(CEG, "Constellation Energy", 2.45e10)])
    actions = ui(handler)
    actions.observe(UiContext.model_validate({"page": "companies", "screen": {"margin": 10}}))
    assert not actions.run({"action": "set_screen_filters",
                            "filters": {"sectors": ["Utilities"]}})[1]
    assert not actions.run({"action": "set_screen_filters",
                            "filters": {"industries": ["Chemicals"], "sectors": []}})[1]
    p = seen[-1].url.params
    assert p.get_list("where") == ["operating_margin_ttm:gte:0.1"]
    assert "sector" not in p and p.get_list("sector_industry") == ["Chemicals"]
    _, _, action = actions.run({"action": "set_screen_filters", "reset": True,
                                "filters": {"sectors": ["Materials"]}})
    p = seen[-1].url.params
    assert "where" not in p and "sector_industry" not in p
    assert action["label"] == "Screen: cleared filters, sectors Materials"
