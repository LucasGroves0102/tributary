"""Agent loop against a scripted model and a mock Query API."""

import json
from types import SimpleNamespace as NS

import httpx
import pytest

from tributary_agent.agent import CLOSING, Agent
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


def financials(request):
    return httpx.Response(200, json={"company_id": NVDA, "as_of": "2026-11-06T23:01:51Z",
                                     "rows": [{"metric": "revenue", "period_end": "2026-07-26",
                                               "value": 96221000000}]})


def test_at_the_round_limit_it_answers_from_what_it_found():
    """C9: after max_rounds tool rounds one closing call answers from the results (no tools:
    tool_choice none), saying what it couldn't finish; it never discards them."""
    loop = [response("tool_use", tool_use(f"t{i}", "get_financials", {"company_id": NVDA}))
            for i in range(3)]
    closing = response("end_turn", text("I couldn't compare it with Microsoft. Nvidia's revenue "
                                        "was $96.2B. Freshness: as of 2026-11-06."))
    fake = FakeMessages(*loop, closing)
    answer = Agent(fake, query_api(financials)[0], "claude-haiku-5-5",
                   max_rounds=3).ask("Compare everything")
    assert answer.stop == "max_rounds" and answer.rounds == 4 and len(answer.tool_calls) == 3
    assert answer.text.startswith("I couldn't compare") and "narrower" not in answer.text
    assert answer.verification["ok"] and answer.verification["figures"][0]["status"] == "verified"
    last = fake.requests[-1]
    assert last["tool_choice"] == {"type": "none"} and last["tools"]  # tools stay defined
    assert last["messages"][-1]["content"][0]["text"] == CLOSING
    assert last["messages"][-2]["content"][0]["type"] == "tool_result"
    assert all("tool_choice" not in r for r in fake.requests[:-1])


def test_a_closing_call_with_nothing_to_say_still_answers():
    loop = [response("tool_use", tool_use(f"t{i}", "whoami", {})) for i in range(2)]
    answer = Agent(FakeMessages(*loop, response("end_turn")), query_api(ok)[0],
                   "claude-haiku-5-5", max_rounds=2).ask("loop forever")
    assert answer.stop == "max_rounds"
    assert "2 rounds" in answer.text and "narrower" not in answer.text


def test_a_closing_call_may_keep_its_tools():
    """The thesis check's closing call can still submit: it keeps tool_choice auto, runs what
    it calls, then stops."""
    calls = []

    class Runner:
        def run(self, name, args):
            calls.append(name)
            return "Recorded.", False

    loop = [response("tool_use", tool_use("t0", "whoami", {})),
            response("tool_use", tool_use("t1", "submit_check", {"claims": []}))]
    fake = FakeMessages(*loop)
    answer = Agent(fake, Runner(), "claude-haiku-5-5", max_rounds=1, closing="Submit now.",
                   closing_tools=True).ask("check")
    assert calls == ["whoami", "submit_check"] and answer.stop == "max_rounds"
    assert "tool_choice" not in fake.requests[-1]
    assert fake.requests[-1]["messages"][-1]["content"][0]["text"] == "Submit now."


def test_a_reply_cut_off_at_max_tokens_is_retried_once_when_asked():
    """C1: the cut-off reply (an incomplete tool call) is dropped and the model asked once
    more, more briefly; a second cut-off ends the run."""
    calls = []

    class Runner:
        def run(self, name, args):
            calls.append((name, args))
            return "Recorded.", False

    cut = response("max_tokens", tool_use("t0", "submit_check", {"claims": [{"quote": "Rev"}]}),
                   output_tokens=8000)
    fake = FakeMessages(cut, response("tool_use", tool_use("t1", "submit_check", {"claims": []})),
                        response("end_turn", text("Done.")))
    answer = Agent(fake, Runner(), "claude-haiku-5-5", retry="Shorter, please.").ask("check")
    assert answer.stop == "end_turn" and answer.rounds == 3
    assert calls == [("submit_check", {"claims": []})]
    second = fake.requests[1]["messages"]
    assert second[-1]["content"][0]["text"] == "Shorter, please."
    assert all(m["role"] == "user" for m in second)  # the cut-off reply isn't in the history

    twice = FakeMessages(cut, cut)
    answer = Agent(twice, Runner(), "claude-haiku-5-5", retry="Shorter.").ask("check")
    assert answer.stop == "max_tokens" and answer.rounds == 2


def test_a_text_answer_cut_off_stands_and_a_cut_while_thinking_is_retried():
    fake = FakeMessages(response("max_tokens", text("Revenue was")))
    answer = Agent(fake, query_api(ok)[0], "claude-haiku-5-5", retry="Again.").ask("x")
    assert (answer.stop, answer.text, answer.rounds) == ("max_tokens", "Revenue was", 1)
    thinking = response("max_tokens", NS(type="thinking", thinking=""))
    fake = FakeMessages(thinking, response("end_turn", text("Done.")))
    answer = Agent(fake, query_api(ok)[0], "claude-haiku-5-5", retry="Again.").ask("x")
    assert (answer.stop, answer.text, answer.rounds) == ("end_turn", "Done.", 2)


def test_without_retry_a_cut_off_reply_is_the_answer():
    fake = FakeMessages(response("max_tokens", text("Revenue was")))
    answer = Agent(fake, query_api(ok)[0], "claude-haiku-5-5").ask("x")
    assert (answer.stop, answer.text, answer.rounds) == ("max_tokens", "Revenue was", 1)


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
    assert [e["type"] for e in events] == ["usage", "tool", "text", "text", "usage", "done"]
    # each model call that returned, as it returns: what the server records as spent even if
    # a later call fails (100 input + 20 output tokens at Haiku's prices)
    assert events[0] == {"type": "usage", "round": 0, "cost_usd": pytest.approx(2e-5)}
    assert events[1] == {"type": "tool", "round": 0, "name": "whoami", "input": {},
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
            "company_id": NVDA, "url": "https://www.sec.gov/Archives/edgar/data/1045810/x/",
            "sections": []},
        "0001045810-26-000052": {
            "accession_no": "0001045810-26-000052", "form": None, "filed": None,
            "company_id": NVDA, "url": None, "sections": []},
    }


def test_citations_keep_the_sections_a_tool_returned():
    """A section or search result cites its section, so the app can open the reader there;
    anything that isn't a section key is dropped."""
    def search(request):
        cite = {"accession_no": "0001045810-26-000075", "form": "10-Q", "filed": "2026-08-26",
                "document_url": "https://www.sec.gov/Archives/edgar/data/1045810/x/d.htm"}
        return httpx.Response(200, json={"results": [
            {"company_id": NVDA, "citation": {**cite, "section_key": "II-1A"}},
            {"company_id": NVDA, "citation": {**cite, "section_key": "I-2"}},
            {"company_id": NVDA, "citation": {**cite, "section_key": "II-1A"}},
            {"company_id": NVDA, "citation": {**cite, "section_key": "../../v1/me"}}]})

    fake = FakeMessages(
        response("tool_use", tool_use("t1", "search_filings", {"query": "export controls"})),
        response("end_turn", text("See 0001045810-26-000075.")),
    )
    answer = Agent(fake, query_api(search)[0], "claude-haiku-5-5").ask("export controls?")
    assert answer.citations["0001045810-26-000075"]["sections"] == ["II-1A", "I-2"]


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


def test_financials_quarters_stay_within_the_schema():
    """Review blocker 1: the Query API takes 120 quarters (the web's Max) and the model's
    `quarters` went through unclamped; 45 metrics x 120 quarters is megabytes, enough to push
    the next model call past its context. The schema the model sees is unchanged (1-40)."""
    api, seen = query_api(ok)
    for asked, sent in ((120, "40"), (10_000, "40"), (0, "1"), (-5, "1"), ("8", "8"), (12, "12")):
        assert api.run("get_financials", {"company_id": NVDA, "quarters": asked})[1] is False
        assert seen[-1].url.params["quarters"] == sent
    content, is_error = api.run("get_financials", {"company_id": NVDA, "quarters": None})
    assert is_error and content.startswith("invalid call") and len(seen) == 6
    schema = next(t for t in TOOLS if t["name"] == "get_financials")["input_schema"]
    assert schema["properties"]["quarters"] == {"type": "integer", "minimum": 1, "maximum": 40,
                                                "description": "default 4"}


def test_null_or_wrong_numbers_are_a_validation_message_not_a_crash():
    """F7a: the model sends null for "no preference"; int(None) raised a TypeError out of run()."""
    api, seen = query_api(ok)
    acc = "0001045810-26-000021"
    assert api.run("get_section", {"company_id": NVDA, "section_key": "1A",
                                   "max_chars": None})[1] is False
    assert seen[-1].url.params["max_chars"] == "8000"
    assert api.run("get_section", {"company_id": NVDA, "section_key": "1A",
                                   "max_chars": 50000})[1] is False
    assert seen[-1].url.params["max_chars"] == "20000"
    assert api.run("search_filings", {"query": "x", "limit": None, "offset": None})[1] is False
    assert dict(seen[-1].url.params) == {"q": "x", "limit": "10", "offset": "0"}
    assert api.run("get_section_changes", {"company_id": NVDA, "accession_no": acc,
                                           "max_changes": None})[1] is False
    for name, args in (("get_section", {"company_id": NVDA, "section_key": "1A",
                                        "max_chars": "lots"}),
                       ("search_filings", {"query": "x", "limit": [3]}),
                       ("get_section_changes", {"company_id": NVDA, "accession_no": acc,
                                                "max_changes": "all"})):
        before = len(seen)
        content, is_error = api.run(name, args)
        assert is_error and content.startswith(f"invalid call to {name}: "), content
        assert "whole number" in content and len(seen) == before


def test_section_changes_tool_validates_and_routes():
    api, seen = query_api(ok)
    acc = "0001045810-26-000021"
    for bad in ({"company_id": NVDA, "accession_no": "../me"},
                {"company_id": NVDA, "accession_no": acc, "section_key": "1A/../x"}):
        content, is_error = api.run("get_section_changes", bad)
        assert is_error and content.startswith("invalid call")
    assert seen == []
    args = {"company_id": NVDA, "accession_no": acc, "section_key": "II-1A",
            "basis": "year_ago", "kind": "new"}
    assert api.run("get_section_changes", args)[1] is False
    assert seen[0].url.path == f"/v1/companies/{NVDA}/filings/{acc}/changes"
    assert dict(seen[0].url.params) == {"basis": "year_ago", "section": "II-1A", "kind": "new"}


def test_section_changes_tool_keeps_the_first_changes():
    def changes(request):
        return httpx.Response(200, json={"sections": [
            {"section_key": "1A", "changes": [{"kind": "new"}] * 3},
            {"section_key": "7", "changes": [{"kind": "removed"}] * 2}]})

    api, _ = query_api(changes)
    content, _ = api.run("get_section_changes", {"company_id": NVDA, "max_changes": 4,
                                                 "accession_no": "0001045810-26-000021"})
    a, b = json.loads(content)["sections"]
    assert (len(a["changes"]), "changes_omitted" in a) == (3, False)
    assert (len(b["changes"]), b["changes_omitted"]) == (1, 1)


def test_material_changes_tool_validates_and_routes():
    """F3: a new tool (get_section_changes' schema is unchanged): the ranked view of /changes."""
    api, seen = query_api(ok)
    acc = "0001045810-26-000021"
    for bad in ({"company_id": NVDA, "accession_no": "../me"},
                {"company_id": NVDA, "accession_no": acc, "section_key": "1A/../x"},
                {"company_id": NVDA, "accession_no": acc, "basis": "forever"},
                {"company_id": NVDA, "accession_no": acc, "topic": "x"},
                {"company_id": NVDA, "accession_no": acc, "topic": "x" * 101}):
        content, is_error = api.run("get_material_changes", bad)
        assert is_error and content.startswith("invalid call"), bad
    assert seen == []
    assert api.run("get_material_changes", {"company_id": NVDA, "accession_no": acc})[1] is False
    assert seen[0].url.path == f"/v1/companies/{NVDA}/filings/{acc}/changes"
    assert dict(seen[0].url.params) == {"view": "ranked"}  # the API picks the basis
    args = {"company_id": NVDA, "accession_no": acc, "basis": "year_ago", "section_key": "II-1A",
            "topic": "  export   controls ", "new_figures": True}
    assert api.run("get_material_changes", args)[1] is False
    assert dict(seen[1].url.params) == {"view": "ranked", "basis": "year_ago",
                                        "section": "II-1A", "q": "export controls",
                                        "numbers": "new"}
    schema = next(t for t in TOOLS if t["name"] == "get_material_changes")["input_schema"]
    assert schema["required"] == ["company_id", "accession_no"]
    old = next(t for t in TOOLS if t["name"] == "get_section_changes")["input_schema"]
    assert set(old["properties"]) == {"company_id", "accession_no", "section_key", "basis",
                                      "kind", "max_changes"}


def test_material_changes_tool_keeps_the_top_scores_and_drops_the_topic_echo():
    def changes(request):
        return httpx.Response(200, json={
            "view": "ranked", "filters": {"q": "$46.7B", "terms": ["$46.7b"]},
            "sections": [
                {"section_key": "II-1A", "changes": [{"score": 6.0}, {"score": 1.0}]},
                {"section_key": "I-2", "changes": [{"score": 7.0}, {"score": 5.0},
                                                   {"score": 0.5}]}]})

    api, _ = query_api(changes)
    content, _ = api.run("get_material_changes", {"company_id": NVDA, "max_changes": 3,
                                                  "accession_no": "0001045810-26-000021"})
    body = json.loads(content)
    assert "filters" not in body and "46.7" not in content  # the model's own words aren't evidence
    a, b = body["sections"]
    assert [c["score"] for c in a["changes"]] == [6.0] and a["changes_omitted"] == 1
    assert [c["score"] for c in b["changes"]] == [7.0, 5.0] and b["changes_omitted"] == 1


def test_search_tool_routes_filters():
    api, seen = query_api(ok)
    assert api.run("search_filings", {"query": "export controls"})[1] is False
    assert dict(seen[0].url.params) == {"q": "export controls", "limit": "10", "offset": "0"}
    args = {"query": "Groq", "company_id": NVDA, "form": "10-K", "section_key": "1A",
            "filed_from": "2026-01-01", "filed_to": "2026-12-31", "limit": 50}
    assert api.run("search_filings", args)[1] is False
    assert dict(seen[1].url.params) == {"q": "Groq", "limit": "20", "offset": "0",
                                        "company": NVDA, "form": "10-K", "section": "1A",
                                        "from": "2026-01-01", "to": "2026-12-31"}
    content, is_error = api.run("search_filings", {"query": "x", "filed_from": "last year"})
    assert is_error and len(seen) == 2


def test_earnings_and_events_tools_validate_and_route():
    api, seen = query_api(ok)
    for bad in ({"company_id": "../me"}, {"company_id": NVDA, "quarters": None},
                {"company_id": NVDA, "quarters": "latest"}):
        content, is_error = api.run("get_earnings_release", bad)
        assert is_error and content.startswith("invalid call")
    assert api.run("get_events_since_report", {"company_id": "1045810/x"})[1] is True
    assert seen == []
    for asked, sent in ((None, "1"), (3, "3"), (9, "4"), (0, "1")):
        args = {"company_id": NVDA} if asked is None else {"company_id": NVDA, "quarters": asked}
        assert api.run("get_earnings_release", args)[1] is False
        assert seen[-1].url.path == f"/v1/companies/{NVDA}/earnings"
        assert dict(seen[-1].url.params) == {"quarters": sent}
    assert api.run("get_events_since_report", {"company_id": NVDA})[1] is False
    assert seen[-1].url.path == f"/v1/companies/{NVDA}/events" and not seen[-1].url.params
    for name in ("get_earnings_release", "get_events_since_report"):
        schema = next(t for t in TOOLS if t["name"] == name)["input_schema"]
        assert schema["required"] == ["company_id"]


def test_earnings_tool_keeps_long_tables_to_their_citation():
    """A release's reconciliation tables can run to many thousand characters: whole up to
    ADJUSTMENT_CHARS a quarter, then heading and citation only (get_section reads the rest)."""
    table = {"kind": "reconciliation", "heading": "Reconciliation", "text": "x" * 4000,
             "citation": {"accession_no": "0001045810-26-000073", "section_key": "EX-99.1"}}

    def earnings(request):
        return httpx.Response(200, json={"quarters": [
            {"outlook": [{"text": "y" * 9000}], "adjustments": [dict(table), dict(table)]}]})

    api, _ = query_api(earnings)
    content, is_error = api.run("get_earnings_release", {"company_id": NVDA})
    [quarter] = json.loads(content)["quarters"]
    first, second = quarter["adjustments"]
    assert not is_error and len(quarter["outlook"][0]["text"]) == 9000  # outlook stays whole
    assert first["text"] == "x" * 4000 and "omitted" not in first
    assert second["text"] is None and "get_section" in second["omitted"]
    assert second["citation"]["section_key"] == "EX-99.1"
