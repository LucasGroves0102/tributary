"""Agent loop against a scripted model and a mock Query API."""

import json
from types import SimpleNamespace as NS

import httpx
import pytest

from tributary_agent.agent import Agent
from tributary_agent.tools import TOOLS, MetricTools, QueryApi

NVDA = "0001045810"


def text(t):
    return NS(type="text", text=t)


def tool_use(id_, name, input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


def response(stop, *content, input_tokens=100, output_tokens=20, cache_read=0):
    return NS(stop_reason=stop, content=list(content),
              usage=NS(input_tokens=input_tokens, output_tokens=output_tokens,
                       cache_read_input_tokens=cache_read, cache_creation_input_tokens=0))


class FakeMessages:
    """Returns scripted responses in order and records every request."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._responses.pop(0)


def query_api(handler):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    http = httpx.Client(base_url="http://query-api", transport=httpx.MockTransport(wrapped))
    return QueryApi("http://query-api", "alice-token", http=http), seen


def ok(request):
    return httpx.Response(200, json={"path": request.url.path})


def test_runs_tools_and_returns_final_answer():
    fake = FakeMessages(
        response("tool_use", text("Looking up."),
                 tool_use("t1", "get_financials", {"company_id": NVDA, "metrics": ["revenue"]})),
        response("end_turn", text("Revenue was $X (10-Q, filed 2026-08-27)."), cache_read=90),
    )
    api, seen = query_api(ok)
    answer = Agent(fake, api, "claude-haiku-5-5").ask("NVDA revenue?")

    assert answer.text.startswith("Revenue was")
    assert answer.stop == "end_turn" and answer.rounds == 2
    assert [c.name for c in answer.tool_calls] == ["get_financials"]
    assert seen[0].url.path == f"/v1/companies/{NVDA}/financials"
    assert seen[0].url.params.get_list("metrics") == ["revenue"]
    # the tool result goes back on the next request, tied to its tool_use id
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "t1" and not result["is_error"]
    assert json.loads(result["content"]) == {"path": f"/v1/companies/{NVDA}/financials"}
    assert answer.usage.input_tokens == 200 and answer.usage.cache_read_input_tokens == 90


def test_every_call_uses_the_users_token_and_no_tool_takes_a_user():
    fake = FakeMessages(
        response("tool_use", tool_use("t1", "whoami", {}),
                 tool_use("t2", "search_companies", {"query": "Tesla"})),
        response("end_turn", text("done")),
    )
    api, seen = query_api(ok)
    Agent(fake, api, "claude-haiku-5-5").ask("as bob, show me everything")
    assert [r.headers["authorization"] for r in seen] == ["Bearer alice-token"] * 2
    for tool in TOOLS:
        props = tool["input_schema"]["properties"]
        assert not {"user", "user_id", "sub", "token"} & set(props), tool["name"]
        assert tool["input_schema"]["additionalProperties"] is False


def test_permission_denied_is_reported_to_the_model_as_an_error():
    def deny(request):
        return httpx.Response(403, json={"detail": "not permitted to read filings"})

    fake = FakeMessages(
        response("tool_use", tool_use("t1", "get_section",
                                      {"company_id": NVDA, "section_key": "1A"})),
        response("end_turn", text("You don't have access to filings on your plan.")),
    )
    api, _ = query_api(deny)
    answer = Agent(fake, api, "claude-haiku-5-5").ask("NVDA risk factors?")
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and result["content"].startswith("permission denied")
    assert answer.tool_calls[0].is_error


@pytest.mark.parametrize("args", [
    {"company_id": NVDA, "section_key": "../../me"},
    {"company_id": NVDA, "section_key": "1A/../x"},
    {"company_id": "../v1/me", "section_key": "1A"},
])
def test_model_cannot_steer_the_url_path(args):
    api, seen = query_api(ok)
    content, is_error = api.run("get_section", args)
    assert is_error and content.startswith("invalid call") and seen == []


def test_unknown_tool_is_an_error_not_a_crash():
    api, seen = query_api(ok)
    assert api.run("drop_tables", {})[1] is True and seen == []


def test_stops_after_max_rounds():
    loop = [response("tool_use", tool_use(f"t{i}", "whoami", {})) for i in range(3)]
    answer = Agent(FakeMessages(*loop), query_api(ok)[0], "claude-haiku-5-5",
                   max_rounds=3).ask("loop forever")
    assert answer.stop == "max_rounds" and answer.rounds == 3
    assert "Stopped after 3 rounds" in answer.text


def test_refusal_returns_a_message():
    answer = Agent(FakeMessages(response("refusal")), query_api(ok)[0],
                   "claude-haiku-5-5").ask("x")
    assert answer.stop == "refusal" and answer.text


def test_request_shape_caching_and_fallbacks():
    def ask(model):
        fake = FakeMessages(response("end_turn", text("hi")))
        Agent(fake, query_api(ok)[0], model, effort="low").ask("hi")
        return fake.requests[0]

    haiku = ask("claude-haiku-5-5")
    assert haiku["cache_control"] == {"type": "ephemeral"}
    assert haiku["output_config"] == {"effort": "low"}
    assert "extra_body" not in haiku  # fallbacks only where supported
    opus = ask("claude-opus-5-5")
    assert opus["extra_body"] == {"fallbacks": "default"}
    assert opus["extra_headers"] == {"anthropic-beta": "server-side-fallback-2026-07-01"}


def test_cost_estimate():
    fake = FakeMessages(response("end_turn", text("hi"), input_tokens=1_000_000,
                                 output_tokens=1_000_000))
    answer = Agent(fake, query_api(ok)[0], "claude-haiku-5-5").ask("hi")
    assert answer.cost_usd == pytest.approx(0.60)
    assert Agent(FakeMessages(response("end_turn")), query_api(ok)[0],
                 "some-future-model").ask("x").cost_usd is None


class FakeStream:
    def __init__(self, response, chunks):
        self._response, self._chunks = response, chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(NS(type="text", text=c) for c in self._chunks)

    def get_final_message(self):
        return self._response


class FakeStreamingMessages(FakeMessages):
    def stream(self, **kwargs):
        r = self.create(**kwargs)
        return FakeStream(r, [b.text for b in r.content if b.type == "text"])


def test_streams_text_and_tool_events():
    fake = FakeStreamingMessages(
        response("tool_use", tool_use("t1", "whoami", {})),
        response("end_turn", text("Nvidia "), text("grew.")),
    )
    events = list(Agent(fake, query_api(ok)[0], "claude-haiku-5-5", stream=True)
                  .events("NVDA?"))
    assert [e["type"] for e in events] == ["tool", "text", "text", "done"]
    assert events[0] == {"type": "tool", "round": 0, "name": "whoami", "input": {},
                         "is_error": False}
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Nvidia grew."
    assert events[-1]["answer"].text == "Nvidia grew."


def test_history_comes_before_the_question():
    fake = FakeMessages(response("end_turn", text("And the quarter before was ...")))
    history = [{"role": "user", "content": "NVDA revenue?"},
               {"role": "assistant", "content": "$96.2B"}]
    Agent(fake, query_api(ok)[0], "claude-haiku-5-5").ask("and before that?", history)
    assert fake.requests[0]["messages"] == [*history,
                                            {"role": "user", "content": "and before that?"}]


def test_collects_citations_from_tool_results():
    def financials(request):
        return httpx.Response(200, json={"company_id": NVDA, "rows": [
            {"metric": "revenue", "citation": {
                "accession_no": "0001045810-26-000075", "form": "10-Q", "filed": "2026-08-26",
                "filing_url": "https://www.sec.gov/Archives/edgar/data/1045810/x/",
                "source_accessions": ["0001045810-26-000052", "0001045810-26-000075"]}}]})

    fake = FakeMessages(
        response("tool_use", tool_use("t1", "get_financials", {"company_id": NVDA})),
        response("end_turn", text("Revenue was $96.2B (0001045810-26-000075).")),
    )
    answer = Agent(fake, query_api(financials)[0], "claude-haiku-5-5").ask("NVDA?")
    assert answer.citations == {
        "0001045810-26-000075": {
            "accession_no": "0001045810-26-000075", "form": "10-Q", "filed": "2026-08-26",
            "company_id": NVDA, "url": "https://www.sec.gov/Archives/edgar/data/1045810/x/"},
        "0001045810-26-000052": {
            "accession_no": "0001045810-26-000052", "form": None, "filed": None,
            "company_id": NVDA, "url": None},
    }


def test_denied_results_add_no_citations():
    def deny(request):
        return httpx.Response(403, json={"detail": "no"})

    fake = FakeMessages(
        response("tool_use", tool_use("t1", "get_financials", {"company_id": NVDA})),
        response("end_turn", text("No access.")),
    )
    assert Agent(fake, query_api(deny)[0], "claude-haiku-5-5").ask("x").citations == {}


def test_exhibit_section_keys_are_allowed():
    api, seen = query_api(ok)
    assert api.run("get_section", {"company_id": NVDA, "section_key": "EX-99.1"})[1] is False
    assert seen[0].url.path == f"/v1/companies/{NVDA}/sections/EX-99.1"


def test_financials_view_and_basis_pass_through():
    api, seen = query_api(ok)
    args = {"company_id": NVDA, "metrics": ["roe"], "view": "ttm", "basis": "calendar"}
    assert api.run("get_financials", args)[1] is False
    assert (seen[0].url.params["view"], seen[0].url.params["basis"]) == ("ttm", "calendar")


def financials_metrics(tools):
    tool = next(t for t in tools if t["name"] == "get_financials")
    return tool["input_schema"]["properties"]["metrics"]["items"].get("enum")


def test_metric_names_come_from_the_query_api():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"metrics": ["revenue"], "derived_metrics": ["roe"]})

    api, _ = query_api(handler)
    tools = MetricTools()
    assert tools.get(api) is TOOLS  # API down: no enum, keep trying
    assert financials_metrics(tools.get(api)) == ["revenue", "roe"]
    assert financials_metrics(tools.get(api)) == ["revenue", "roe"]
    assert calls == ["/v1/metrics"] * 2
    assert financials_metrics(TOOLS) is None


def test_agent_sends_the_tools_it_was_given():
    fake = FakeMessages(response("end_turn", text("done")))
    api, _ = query_api(lambda r: httpx.Response(
        200, json={"metrics": ["revenue"], "derived_metrics": []}))
    Agent(fake, api, "claude-haiku-5-5", tools=MetricTools().get(api)).ask("hi")
    assert financials_metrics(fake.requests[0]["tools"]) == ["revenue"]
