"""Analyst test 4 (Vertiv, findings 9 and 10): the thesis check of an analysis project. Its peers
are in scope; claims made of the user's own model are "your model", not "can't verify" or
"contradicted"; reported and adjusted figures are compared like with like; and the figures the
model page shows (TTM free cash flow, debt/equity's total debt) verify against the same API
answers. A scripted model and a mock Query API whose model route is the real
query_api.model_maths over Vertiv's production inputs of 2026-11-09 and the tester's saved
model; never Anthropic."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest

from query_api.model_maths import run as run_model
from query_api.thesis_checks import validate_check
from query_api.workspaces import validate_state
from tributary_agent.thesis_check import CHECK_RULES, Checker, check_agent, model_claim
from tributary_agent.tools import TOOLS, QueryApi

FIXTURES = Path(__file__).parents[2] / "query-api/tests/fixtures/model"
INPUTS = json.loads((FIXTURES / "vrt.json").read_text())

WS = "11b6056f-ec9f-44a0-90d5-64d9408718e8"  # the tester's VRT analysis (pw-morgan4)
THESIS = "7e125e13-828a-474a-92eb-961098d40458"
MODEL_ID = "8c25bc6a-8874-440b-86e4-7a17543a197d"
VRT, ETN, JCI, AAON, NVT = ("0001674101", "0001551182", "0000833444", "0000824142",
                            "0001720635")
PEERS = [ETN, JCI, AAON, NVT]
IDENTITY = {VRT: ("Vertiv Holdings Co", "VRT"), ETN: ("Eaton Corp plc", "ETN"),
            JCI: ("Johnson Controls International plc", "JCI"), AAON: ("AAON, Inc.", "AAON"),
            NVT: ("nVent Electric plc", "NVT")}
Q3, RELEASE = "0001628280-26-067893", "0001674101-26-000071"
AS_OF = "2026-11-11T05:00:00+00:00"
NOW = datetime(2026, 11, 11, 5, 14, tzinfo=UTC)

# the tester's saved model (app DB, read-only, 2026-11-14): Q4 at the guidance midpoint, the
# $34.1M contingent-consideration charge taken out of Q3, $185 typed, bear and bull saved
MODEL = {"ytd": True, "price": {"date": "2026-11-10", "value": 185.0}, "companyId": VRT,
         "historyYears": 8,
         "scenarios": {"bear": [{"growth": 45.0, "margin": 21.0}, {"growth": 5.0, "margin": 18.0},
                                {"growth": -8.0, "margin": 15.0}],
                       "bull": [{"growth": 60.0, "margin": 24.0}, {"growth": 40.0, "margin": 25.0},
                                {"growth": 22.0, "margin": 25.5}]},
         "adjustments": [{"fy": 2026, "kind": "one_off", "quarter": 3, "amount": -34100000.0,
                          "name": "Contingent consideration charge (Q3 FY26)"}],
         "assumptions": [{"growth": 53.2, "margin": 23.0,
                          "note": "Q4 at guidance midpoint ($4,413M)"},
                         {"growth": 28.0, "margin": 22.5}, {"growth": 15.0, "margin": 22.0}]}

MARKDOWN = (
    "# Vertiv Holdings (VRT): long, 18-month horizon\n\n"
    "Q3 FY2026 net sales were $4.31B, up 61.2% on the same quarter a year earlier. Management "
    "raised full-year 2026 guidance to $14,650M at the midpoint (+38% organic). My base case "
    "puts FY2027 revenue at $18.75B and diluted EPS at $8.52.\n\n"
    f"[[embed:{MODEL_ID}]]\n\n"
    "Base case: operating margin 21.0%, 22.5% and 22.0%. At $185 the stock is 29x my FY2026E "
    "EPS and 22x FY2027E. My FY2027 EPS of $9.10 is conservative.\n\n"
    "Trailing-twelve-month free cash flow is $3.15B, a 24.0% FCF margin, about double the peer "
    "median of 12.1% (Eaton 11.9%, nVent 12.1%, JCI 12.7%). The company had net cash at the "
    "quarter end and debt to equity is 0.53x.\n")


def api_row(r: dict) -> dict:
    """A Store row of the fixture as /financials returns it."""
    return {"metric": r["metric"], "period_end": r["period_end"],
            "fiscal_period": f"FY{r['fiscal_year']} Q{r['fiscal_quarter']}",
            "value": r["value"],
            "citation": {"accession_no": r["accession_no"], "form": r["form"],
                         "filed": r["filed"], "xbrl_tag": "x"}}


TTM = [api_row(r) for r in INPUTS["ttm"]]
Q3_CITE = {"accession_no": Q3, "form": "10-Q", "filed": "2026-10-28", "xbrl_tag": "derived"}
DEBT = {"long_term_debt": 2925100000, "long_term_debt_current": 3400000}
BALANCE = [  # Sep 30, 2026 as /financials gives the derived rows: with their inputs
    {"metric": "debt_to_equity", "period_end": "2026-09-30", "fiscal_period": "FY2026 Q3",
     "value": "0.5322", "inputs": {**DEBT, "stockholders_equity": 5502000000},
     "citation": Q3_CITE},
    {"metric": "net_debt", "period_end": "2026-09-30", "fiscal_period": "FY2026 Q3",
     "value": "-783200000",
     "inputs": {**DEBT, "cash": 3512900000, "short_term_investments": 198800000},
     "citation": Q3_CITE}]
QUARTER = [api_row(r) for r in INPUTS["quarters"] if r["metric"] == "revenue"] + BALANCE
RELEASE_TEXT = ("Vertiv raises full-year 2026 guidance: net sales of $14,650 million at the "
                "midpoint, organic growth of 38%.")


class FakeVrtApi:
    """The Query API as the tester sees it: the open catalog (every company, both tiers) unless
    `grants` closes it to those companies; her analysis project with its peers; every request
    recorded."""

    def __init__(self, grants: dict[str, list[str]] | None = None):
        self.grants = grants
        self.seen: list[httpx.Request] = []
        self.saved: list[dict] = []

    def readable(self, cid: str) -> list[str]:
        if self.grants is None:
            return ["financials", "filings"]
        return self.grants.get(cid, [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path, method = request.url.path, request.method
        item = {"workspace_id": WS, "created_at": "2026-11-11T00:00:00Z",
                "updated_at": "2026-11-11T00:00:00Z", "opened_at": None,
                "workspace_name": "Vertiv Holdings"}
        if path == f"/v1/workspace-items/{THESIS}":
            return httpx.Response(200, json={**item, "item_id": THESIS, "kind": "thesis",
                                             "name": "VRT thesis",
                                             "state": {"companyId": VRT, "markdown": MARKDOWN}})
        if path == f"/v1/workspaces/{WS}":
            return httpx.Response(200, json={
                "workspace_id": WS, "name": "Vertiv Holdings",
                "project": {"companyId": VRT, "peers": PEERS, "notes": "", "done": []},
                "items": [{**item, "item_id": THESIS, "kind": "thesis", "name": "VRT thesis",
                           "state": None},
                          {**item, "item_id": MODEL_ID, "kind": "model", "name": "VRT model",
                           "state": MODEL}]})
        if path == "/v1/me":
            if self.grants is None:
                return httpx.Response(200, json={"user_id": "pw-morgan4", "companies": [],
                                                 "catalog": {"public": True,
                                                             "tiers": ["financials", "filings"]}})
            return httpx.Response(200, json={"user_id": "pw-morgan4", "companies": [
                {"company_id": c, "name": None, "tiers": t} for c, t in self.grants.items()]})
        if path == "/v1/companies/search":
            q = request.url.params["q"]
            by_ticker = {t: c for c, (_, t) in IDENTITY.items()}
            cid = q if q in IDENTITY else by_ticker.get(q)
            if cid is None:
                return httpx.Response(200, json={"results": []})
            name, ticker = IDENTITY[cid]
            return httpx.Response(200, json={"results": [{
                "company_id": cid, "name": name, "tickers": [ticker],
                "matched_on": "cik" if q == cid else "ticker", "matched_alias": q,
                "access": self.readable(cid)}]})
        m = path.split("/")
        if len(m) > 4 and m[1:3] == ["v1", "companies"] and not self.readable(m[3]):
            return httpx.Response(403, json={"detail": "not permitted"})  # require() first
        if path == f"/v1/companies/{VRT}/model" and method == "POST":
            body = run_model(INPUTS, validate_state("model", json.loads(request.content)))
            return httpx.Response(200, json=json.loads(json.dumps(body, default=str)))
        if path == f"/v1/companies/{VRT}/financials":
            want = set(request.url.params.get_list("metrics"))
            if request.url.params.get("view") == "ttm":
                rows = [r for r in TTM if not want or r["metric"] in want]
                return httpx.Response(200, json={"company_id": VRT, "view": "ttm",
                                                 "as_of": AS_OF, "rows": rows})
            return httpx.Response(200, json={"company_id": VRT, "view": "quarter",
                                             "as_of": AS_OF, "rows": QUARTER})
        if path == f"/v1/companies/{VRT}/sections/EX-99.1":
            return httpx.Response(200, json={"company_id": VRT, "as_of": AS_OF, "sections": [{
                "accession_no": RELEASE, "form": "8-K", "filed": "2026-10-28",
                "section_key": "EX-99.1", "title": "Press release", "text": RELEASE_TEXT,
                "truncated": False}]})
        if path.startswith("/v1/companies/") and path.endswith("/financials"):
            return httpx.Response(200, json={"company_id": m[3], "as_of": AS_OF, "rows": []})
        if path == f"/v1/thesis-checks/{THESIS}" and method == "POST":
            body = json.loads(request.content)
            validate_check(body["result"])  # what the Query API would refuse fails here
            self.saved.append(body)
            return httpx.Response(201, json={
                "check_id": "abababab-abab-4bab-8bab-abababababab", "item_id": THESIS,
                "text_hash": body["result"]["thesis"]["text_hash"], "model": "claude-haiku-5-5",
                "cost_usd": 0.01, "counts": {}, "created_at": "2026-11-11T05:14:00Z"})
        return httpx.Response(404, json={"detail": f"unexpected {method} {path}"})


def response(stop, *content):
    return NS(stop_reason=stop, content=list(content),
              usage=NS(input_tokens=1000, output_tokens=500, cache_read_input_tokens=0,
                       cache_creation_input_tokens=0))


def tool_use(id_, name, input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


class FakeMessages:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._responses.pop(0)


def claim(quote, status, explanation, *cites):
    return {"quote": quote, "status": status, "explanation": explanation,
            "citations": list(cites)}


# what the check said in production (stored result, 2026-11-11), the claims findings 9 and 10 name
CLAIMS = [
    claim("Q3 FY2026 net sales were $4.31B, up 61.2% on the same quarter a year earlier",
          "supported", "Q3 FY2026 net sales were $4,312.8M.", {"accession_no": Q3}),
    claim("Management raised full-year 2026 guidance to $14,650M at the midpoint (+38% organic)",
          "supported", "The release gives $14,650M at the midpoint.",
          {"accession_no": RELEASE, "section_key": "EX-99.1"}),
    claim("My base case puts FY2027 revenue at $18.75B and diluted EPS at $8.52", "supported",
          "The model gives FY2027 revenue of $18.75B (FY2026 $14.65B +28%) and diluted EPS of "
          "$8.52.", {"accession_no": RELEASE, "section_key": "EX-99.1",
                     "figure": "FY2026 base $14,650M midpoint"}),
    claim("operating margin 21.0%, 22.5% and 22.0%", "contradicted",
          "The model's FY2026 operating margin is 21.24%, not 21.0%.", {"accession_no": Q3}),
    claim("At $185 the stock is 29x my FY2026E EPS and 22x FY2027E", "unverifiable",
          "The $185 share price is a user input. The arithmetic gives 29.5x ($185 / $6.27)."),
    claim("My FY2027 EPS of $9.10 is conservative", "model", "Your model's FY2027 EPS."),
    claim("Trailing-twelve-month free cash flow is $3.15B, a 24.0% FCF margin", "supported",
          "TTM free cash flow is $3,147.2M on $13,116.6M of revenue, 24.0%.",
          {"accession_no": Q3, "figure": "TTM FCF $3.15B"}),
    claim("debt to equity is 0.53x", "supported",
          "Long-term debt of $2,925.1M plus $3.4M current, over stockholders' equity of "
          "$5,502.0M, gives 0.53x.",
          {"accession_no": Q3, "figure": "0.53x (debt $2,928.5M / equity $5,502.0M)"}),
]
SUBMISSION = {"summary": "Checked.", "claims": CLAIMS, "assumptions": [], "not_mentioned": []}


def check(fake, submission=SUBMISSION, lookups=None):
    http = httpx.Client(base_url="http://query-api", transport=httpx.MockTransport(fake))
    api = QueryApi("http://query-api", "tester-token", http=http)
    lookups = lookups or [
        tool_use("t1", "get_financials", {"company_id": VRT, "metrics": ["revenue", "net_debt",
                                                                          "debt_to_equity"]}),
        tool_use("t2", "get_section", {"company_id": VRT, "section_key": "EX-99.1"})]
    messages = FakeMessages(response("tool_use", *lookups),
                            response("tool_use", tool_use("s", "submit_check", submission)),
                            response("end_turn", NS(type="text", text="Done.")))
    checker = Checker(api, lambda tools: check_agent(messages, tools, "claude-haiku-5-5",
                                                     tool_list=TOOLS), clock=lambda: NOW)
    job = checker.prepare(THESIS)
    events = [e for e in checker.events(job) if e["type"] != "usage"]
    return job, events, messages


def data_of(messages) -> dict:
    return json.loads(messages.requests[0]["messages"][0]["content"][1]["text"].split("\n")[2])


# --- scope: the analysis's own peers (finding 9) ---------------------------------------------

def test_a_projects_peers_are_in_the_checks_scope_by_name():
    """Eaton and nVent were "not among the companies in scope": the thesis names them by name,
    not ticker. The project's peers are now in scope, each with its name and ticker (the open
    catalog's /v1/me names no company), its role and the user's access."""
    fake = FakeVrtApi()
    job, events, messages = check(fake)
    assert job.allowed == {VRT, ETN, JCI, AAON, NVT}
    companies = {c["company_id"]: c for c in data_of(messages)["companies"]}
    assert companies[ETN] == {"company_id": ETN, "name": "Eaton Corp plc", "tickers": ["ETN"],
                              "role": "peer", "access": ["filings", "financials"]}
    assert companies[VRT]["role"] == "thesis" and companies[NVT]["name"] == "nVent Electric plc"
    assert companies[JCI]["role"] == "peer"  # a peer the thesis also names by ticker
    assert "look there before calling it out of scope" in CHECK_RULES
    assert [e["type"] for e in events][-2:] == ["check", "done"]


def test_a_peer_the_user_cant_read_is_asked_as_the_user_and_refused():
    """Scope only lets the tool ask: the Query API still answers as the user (require() and
    RLS), so a peer outside the user's grants is a 403, never data."""
    fake = FakeVrtApi(grants={VRT: ["financials", "filings"], ETN: ["financials"]})
    job, _, messages = check(fake, lookups=[
        tool_use("t1", "get_financials", {"company_id": NVT}),
        tool_use("t2", "get_financials", {"company_id": ETN})])
    companies = {c["company_id"]: c for c in data_of(messages)["companies"]}
    assert companies[NVT]["access"] == [] and companies[ETN]["access"] == ["financials"]
    results = messages.requests[1]["messages"][-1]["content"]
    assert results[0]["is_error"] and results[0]["content"].startswith("permission denied")
    assert not results[1]["is_error"]
    asked = [r for r in fake.seen if r.url.path == f"/v1/companies/{NVT}/financials"]
    assert len(asked) == 1 and asked[0].headers["authorization"] == "Bearer tester-token"


def test_a_workspace_that_isnt_a_project_adds_no_peers():
    class Folder(FakeVrtApi):
        def __call__(self, request):
            r = super().__call__(request)
            if request.url.path == f"/v1/workspaces/{WS}":
                return httpx.Response(200, json={**r.json(), "project": None})
            return r

    job, _, _ = check(Folder())
    assert job.allowed == {VRT, JCI}  # the thesis's company and the ticker it names


# --- your model (finding 9) and like with like (finding 10) ----------------------------------

def stored(fake) -> dict:
    return validate_check(fake.saved[0]["result"])


def test_claims_of_the_users_own_model_are_your_model():
    fake = FakeVrtApi()
    check(fake)
    result = stored(fake)
    by = {c["quote"]: c for c in result["claims"]}
    revenue = by["My base case puts FY2027 revenue at $18.75B and diluted EPS at $8.52"]
    assert revenue["status"] == "model" and not revenue["downgraded"]
    assert revenue["model"]["item_id"] == MODEL_ID and revenue["model"]["name"] == "VRT model"
    assert [(m["figure"], m["line"], m["fy"]) for m in revenue["model"]["matches"]] == [
        ("$18.75B", "revenue", 2027), ("$8.52", "diluted EPS", 2027)]
    assert revenue["explanation"].startswith(
        "Matches your model (VRT model): FY2027E revenue $18.75B; FY2027E diluted EPS $8.52.")
    assert revenue["citations"] == []  # the release it cited for "supported" isn't its source
    # finding 10: 21.0% is the reported margin (21.24% is the adjusted one): the model's, not
    # contradicted; the LLM's "21.24%, not 21.0%" is replaced
    margins = by["operating margin 21.0%, 22.5% and 22.0%"]
    assert margins["status"] == "model"
    assert [(m["figure"], m["line"], m["fy"]) for m in margins["model"]["matches"]] == [
        ("21.0%", "operating margin, reported", 2026), ("22.5%", "operating margin", 2027),
        ("22.0%", "operating margin", 2028)]
    assert "21.24" not in margins["explanation"]
    # multiples at the user's price: 29.2x and 21.7x on adjusted EPS, as the model page shows
    pe = by["At $185 the stock is 29x my FY2026E EPS and 22x FY2027E"]
    assert pe["status"] == "model"
    assert [m["line"] for m in pe["model"]["matches"]] == [
        "your price", "P/E at your price", "P/E at your price"]
    # a figure called the model's that the model doesn't give is said to be off, not kept
    wrong = by["My FY2027 EPS of $9.10 is conservative"]
    assert wrong["status"] == "unverifiable" and wrong["downgraded"]
    assert wrong["explanation"].endswith("(Marked unverifiable: $9.10 is not in your saved "
                                         "model.)")


def test_the_data_still_decides_claims_about_the_filings():
    """A reported quarter and the release's guidance stay supported even where the model is
    built on them; the page's TTM FCF and debt/equity verify against the same API answers."""
    fake = FakeVrtApi()
    check(fake)
    by = {c["quote"]: c for c in stored(fake)["claims"]}
    assert by["Q3 FY2026 net sales were $4.31B, up 61.2% on the same quarter a year earlier"][
        "status"] == "supported"
    assert by["Management raised full-year 2026 guidance to $14,650M at the midpoint (+38% "
              "organic)"]["status"] == "supported"
    fcf = by["Trailing-twelve-month free cash flow is $3.15B, a 24.0% FCF margin"]
    assert fcf["status"] == "supported", fcf["explanation"]
    assert {f["text"]: f["status"] for f in fcf["figures"]} == {
        "$3,147.2M": "verified", "$13,116.6M": "verified"}
    debt = by["debt to equity is 0.53x"]
    assert debt["status"] == "supported", debt["explanation"]
    assert debt["citations"][0]["figure_status"] == "verified"  # total debt, rule 5


def test_the_prefetch_reads_the_ttm_columns_free_cash_flow():
    fake = FakeVrtApi()
    check(fake)
    ttm = [r for r in fake.seen if r.url.path.endswith("/financials")
           and r.url.params.get("view") == "ttm"]
    assert ttm[0].url.params.get_list("metrics") == ["revenue", "operating_income",
                                                     "free_cash_flow"]


def test_the_models_figures_arent_reported_as_missing_from_the_data():
    """The banner listed $18.75B, $8.52, $6.27 and $34.1M as "not found in the data": they are
    the model's (its forecast, the user's adjustment). Status `model`, and the check's
    verification still holds. ($14.65B is the release's $14,650 million here: quoted.)"""
    fake = FakeVrtApi()
    sub = {**SUBMISSION, "summary": "FY2026 EPS of $6.27 after the $34.1M charge; FY2028 "
                                    "revenue $21.56B. Not $99.9B, and not $70B either."}
    check(fake, sub)
    figures = {f["text"]: f["status"] for f in stored(fake)["verification"]["figures"]}
    assert {k: figures[k] for k in ("$6.27", "$34.1M", "$21.56B", "$18.75B", "$8.52")} == {
        "$6.27": "model", "$34.1M": "model", "$21.56B": "model", "$18.75B": "model",
        "$8.52": "model"}
    assert figures["$99.9B"] == "unverified"
    # two significant digits are within rounding of too many of the model's ~150 amounts
    assert figures["$70B"] == "unverified"  # EV at the price is $70.4B


def test_the_check_data_gives_both_bases_and_the_page_figures():
    """Finding 10: the data said only the adjusted margins (21.24% FY2026, 20.4% TTM), so the
    check read the user's reported 21.0% and 20.1% against them."""
    _, _, messages = check(FakeVrtApi())
    model = data_of(messages)["models"][0]
    assert model["ttm_margin_pct"] == 20.4 and model["ttm_margin_reported_pct"] == 20.14
    assert model["ttm_free_cash_flow"] == 3147200000.0 and model["ttm_fcf_margin_pct"] == 23.99
    assert model["adjustments"] == [{"name": "Contingent consideration charge (Q3 FY26)",
                                     "kind": "one_off", "fiscal_year": 2026, "quarter": 3,
                                     "amount": -34100000.0}]
    assert "reported is as filed" in model["basis"]
    assert model["price"] == {"value": 185.0, "date": "2026-11-10"}
    y1 = model["forecast"][0]
    assert (y1["margin_pct"], y1["margin_reported_pct"]) == (21.24, 21.0)
    assert (y1["eps_diluted"], y1["eps_diluted_adjusted"], y1["pe_at_price"]) == (6.27, 6.34,
                                                                                 29.18)
    assert model["forecast"][1]["revenue"] == pytest.approx(18750412800.0)
    assert "Compare reported with reported and adjusted with adjusted" in CHECK_RULES


def test_model_claim_rules():
    """Unit rules of model_claim over the tester's model run."""
    fake = FakeVrtApi()
    job, _, _ = check(fake)
    models = job.models
    # one figure of the model with nothing saying it is the model: left to the data
    assert model_claim("Revenue was $18.75B.", "supported", models) is None
    # ... but named as a forecast year, or "my", it is
    assert model_claim("FY2027 revenue of $18.75B", "supported", models) is not None
    assert model_claim("my revenue estimate of $18.75B", "unverifiable", models) is not None
    # a year it names must be the figure's: FY2026's revenue isn't $18.75B
    assert model_claim("FY2026 revenue of $18.75B", "supported", models) is None
    # a past year is never the model's
    assert model_claim("operating margin 3.9% in FY2021, 12.7% in FY2022", "contradicted",
                       models) is None
    # every figure must be the model's
    assert model_claim("My FY2027 revenue of $18.75B and EPS of $9.10", "model", models) is None
    # a dollar claim the tools returned stays supported, even when the model shows it too
    assert model_claim("FY2027 revenue of $18.75B", "supported", models, in_data=True) is None
    # bear and bull as saved: the bear case's FY2028 P/E at the user's price
    bear = model_claim("Bear: 44x FY2028 at $185", "unverifiable", models)
    assert bear is not None
    assert [(m["line"], m["case"]) for m in bear["matches"]] == [
        ("your price", "base"), ("bear case: P/E at your price", "bear")]


def test_a_projects_model_counts_when_the_thesis_doesnt_embed_it():
    """A project's thesis is about the project's model: checked (after any embedded ones) and
    matched, and the data says it isn't embedded."""
    class NoEmbed(FakeVrtApi):
        def __call__(self, request):
            r = super().__call__(request)
            if request.url.path == f"/v1/workspace-items/{THESIS}":
                body = r.json()
                body["state"]["markdown"] = MARKDOWN.replace(f"[[embed:{MODEL_ID}]]\n\n", "")
                return httpx.Response(200, json=body)
            return r

    fake = NoEmbed()
    job, _, messages = check(fake)
    assert [(m.item_id, m.embedded) for m in job.models] == [(MODEL_ID, False)]
    assert data_of(messages)["models"][0]["embedded"] is False
    by = {c["quote"]: c for c in stored(fake)["claims"]}
    assert by["operating margin 21.0%, 22.5% and 22.0%"]["status"] == "model"
