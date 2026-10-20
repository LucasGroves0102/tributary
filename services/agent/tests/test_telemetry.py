"""Agent metrics and request-ID forwarding to the Query API."""

from types import SimpleNamespace as NS

import anthropic
import httpx
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from tributary_agent.agent import Agent
from tributary_agent.server import Limits, create_app
from tributary_agent.tools import QueryApi
from tributary_telemetry.http import forward_request_id

MODEL = "claude-haiku-5-5"
NVDA = "0001045810"


def response(stop, *content):
    return NS(stop_reason=stop, content=list(content),
              usage=NS(input_tokens=1_000, output_tokens=200, cache_read_input_tokens=0,
                       cache_creation_input_tokens=0))


class Scripted:
    def __init__(self, *responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def tool_round():
    return response("tool_use", NS(type="tool_use", id="t1", name="get_financials",
                                   input={"company_id": NVDA}))


def app(messages, seen):
    def handler(request):
        seen.append(request.headers.get("X-Request-ID"))
        return httpx.Response(200, json={"rows": []})

    http = httpx.Client(base_url="http://query-api", transport=httpx.MockTransport(handler),
                        event_hooks={"request": [forward_request_id]})
    return TestClient(create_app(
        lambda tok: Agent(messages, QueryApi("http://query-api", tok, http=http), MODEL),
        lambda tok: "alice", Limits(per_hour=100, daily_budget_usd=100)))


def ask(c, headers=None):
    return c.post("/v1/ask", json={"question": "NVDA revenue?"},
                  headers={"Authorization": "Bearer alice-token", **(headers or {})})


def sample(name, labels):
    return REGISTRY.get_sample_value(name, labels) or 0


def test_answer_records_question_tokens_cost_and_tool_calls():
    before = {
        "questions": sample("tributary_agent_questions_total", {"stop": "end_turn"}),
        "input": sample("tributary_agent_tokens_total", {"model": MODEL, "kind": "input"}),
        "output": sample("tributary_agent_tokens_total", {"model": MODEL, "kind": "output"}),
        "cost": sample("tributary_agent_cost_usd_total", {"model": MODEL}),
        "tools": sample("tributary_agent_tool_calls_total",
                        {"tool": "get_financials", "result": "ok"}),
    }
    ask(app(Scripted(tool_round(), response("end_turn", NS(type="text", text="None."))), []))
    after = {
        "questions": sample("tributary_agent_questions_total", {"stop": "end_turn"}),
        "input": sample("tributary_agent_tokens_total", {"model": MODEL, "kind": "input"}),
        "output": sample("tributary_agent_tokens_total", {"model": MODEL, "kind": "output"}),
        "cost": sample("tributary_agent_cost_usd_total", {"model": MODEL}),
        "tools": sample("tributary_agent_tool_calls_total",
                        {"tool": "get_financials", "result": "ok"}),
    }
    delta = {k: after[k] - before[k] for k in before}
    assert delta["questions"] == 1 and delta["tools"] == 1
    assert (delta["input"], delta["output"]) == (2_000, 400)
    assert abs(delta["cost"] - (2_000 * 0.10 + 400 * 0.50) / 1e6) < 1e-12


def test_model_errors_are_counted():
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "http://model"))
    before = sample("tributary_agent_questions_total", {"stop": "error"})
    ask(app(Scripted(error), []))
    assert sample("tributary_agent_questions_total", {"stop": "error"}) == before + 1


def test_request_id_is_forwarded_to_the_query_api():
    seen = []
    c = app(Scripted(tool_round(), response("end_turn", NS(type="text", text="None."))), seen)
    r = ask(c, {"X-Request-ID": "web-1"})
    assert r.headers["X-Request-ID"] == "web-1"
    assert seen == ["web-1"]


def test_request_id_is_generated_when_missing():
    seen = []
    c = app(Scripted(tool_round(), response("end_turn", NS(type="text", text="None."))), seen)
    rid = ask(c).headers["X-Request-ID"]
    assert len(rid) == 32 and seen == [rid]
