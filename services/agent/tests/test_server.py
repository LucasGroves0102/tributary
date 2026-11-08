"""Agent HTTP API with a scripted model and a fake identity check."""

import json
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tributary_agent.agent import Agent
from tributary_agent.server import (
    LimitExceeded,
    Limits,
    create_app,
    query_api_daily_count,
    query_api_quota,
    query_api_refund,
)


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class ScriptedMessages:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return NS(stop_reason="end_turn", content=[NS(type="text", text="hello")],
                  usage=NS(input_tokens=1_000_000, output_tokens=0))


class NoTools:
    def run(self, name, args):
        raise AssertionError("no tools expected")


def who_is(token):
    if token != "alice-token":
        raise HTTPException(401, "invalid token")
    return "alice"


def client(limits=None, messages=None):
    messages = messages or ScriptedMessages()
    app = create_app(lambda tok: Agent(messages, NoTools(), "claude-haiku-5-5"), who_is,
                     limits or Limits(per_hour=10, per_ip_per_hour=10, daily_budget_usd=5))
    return TestClient(app), messages


def ask(c, body=None, token="alice-token", ip=None):
    headers = {"Authorization": f"Bearer {token}"}
    if ip:
        headers["X-Client-IP"] = ip
    return c.post("/v1/ask", json=body or {"question": "hi"}, headers=headers)


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ")]


def test_streams_events_ending_in_done():
    c, _ = client()
    r = ask(c)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    done = events(r)[-1]
    assert done["type"] == "done"
    assert done["answer"]["text"] == "hello"
    assert done["answer"]["cost_usd"] == pytest.approx(0.10)


def test_requires_a_valid_token():
    c, _ = client()
    assert c.post("/v1/ask", json={"question": "hi"}).status_code == 401
    assert ask(c, token="forged").status_code == 401


def test_history_is_validated_and_passed_through():
    c, messages = client()
    history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    ask(c, {"question": "c", "history": history})
    assert messages.requests[0]["messages"][:2] == history
    assert ask(c, {"question": "c", "history": [{"role": "system", "content": "x"}]}) \
        .status_code == 422
    assert ask(c, {"question": "c", "history": history * 6}).status_code == 422


def test_rate_limit_per_user():
    clock = Clock()
    c, _ = client(Limits(per_hour=2, per_ip_per_hour=100, daily_budget_usd=100, clock=clock))
    assert [ask(c, ip=f"203.0.113.{i}").status_code for i in range(3)] == [200, 200, 429]
    assert "limit of 2 questions per hour" in ask(c, ip="203.0.113.9").json()["detail"]
    clock.t += 3601
    assert ask(c, ip="203.0.113.1").status_code == 200


def test_rate_limit_per_client_ip():
    clock = Clock()
    c, _ = client(Limits(per_hour=100, per_ip_per_hour=2, daily_budget_usd=100, clock=clock))
    assert [ask(c, ip="203.0.113.1").status_code for _ in range(3)] == [200, 200, 429]
    r = ask(c, ip="203.0.113.1")
    assert r.json()["detail"] == "Limit of 2 questions per hour reached. Try again later."
    assert ask(c, ip="203.0.113.2").status_code == 200
    clock.t += 3601
    assert ask(c, ip="203.0.113.1").status_code == 200


def test_rejection_by_ip_spends_no_user_quota():
    limits = Limits(per_hour=2, per_ip_per_hour=1, daily_budget_usd=100, clock=Clock())
    limits.acquire("alice", "203.0.113.1")
    for _ in range(3):
        with pytest.raises(LimitExceeded):
            limits.acquire("alice", "203.0.113.1")
    limits.acquire("alice", "203.0.113.2")


def test_client_ip_falls_back_to_peer_address():
    c, _ = client(Limits(per_hour=100, per_ip_per_hour=1, daily_budget_usd=100))
    assert ask(c).status_code == 200
    assert ask(c, ip=" ").status_code == 429
    assert ask(c, ip="203.0.113.1").status_code == 200


def test_client_ip_header_is_configurable():
    app = create_app(lambda tok: Agent(ScriptedMessages(), NoTools(), "claude-haiku-5-5"),
                     who_is, Limits(per_hour=100, per_ip_per_hour=1, daily_budget_usd=100),
                     client_ip_header="X-Real-Visitor")
    c = TestClient(app)
    headers = {"Authorization": "Bearer alice-token", "X-Real-Visitor": "203.0.113.1"}
    assert c.post("/v1/ask", json={"question": "hi"}, headers=headers).status_code == 200
    assert c.post("/v1/ask", json={"question": "hi"}, headers=headers).status_code == 429
    assert ask(c, ip="203.0.113.1").status_code == 200


def test_idle_windows_are_swept():
    clock = Clock()
    limits = Limits(per_hour=100, per_ip_per_hour=100, daily_budget_usd=100, clock=clock)
    limits.acquire("alice", "203.0.113.1")
    clock.t += 3601
    limits.acquire("bob", "203.0.113.2")
    assert list(limits._by_user) == ["bob"] and list(limits._by_ip) == ["203.0.113.2"]


def test_daily_budget():
    clock = Clock()
    limits = Limits(per_hour=100, per_ip_per_hour=100, daily_budget_usd=0.25, clock=clock)
    limits.spend(0.25)
    with pytest.raises(LimitExceeded):
        limits.acquire("alice", "203.0.113.1")
    clock.t += 86400
    limits.acquire("alice", "203.0.113.1")


def test_demo_quota_is_taken_after_the_in_process_limits():
    taken = []

    def take_quota(tok, questions=1):
        taken.append(tok)
        if len(taken) > 1:
            raise HTTPException(429, "This demo sandbox has used its 1 assistant questions.")

    messages = ScriptedMessages()
    app = create_app(lambda tok: Agent(messages, NoTools(), "claude-haiku-5-5"), who_is,
                     Limits(per_hour=10, per_ip_per_hour=10, daily_budget_usd=5),
                     take_quota=take_quota)
    c = TestClient(app)
    assert ask(c).status_code == 200
    r = ask(c)
    assert r.status_code == 429 and "demo sandbox has used" in r.json()["detail"]
    assert len(messages.requests) == 1  # no model call once the quota says no
    assert ask(c, token="forged").status_code == 401
    assert len(taken) == 2  # an invalid token never reaches the quota


def allowance(c, token="alice-token", ip=None):
    headers = {"Authorization": f"Bearer {token}"}
    if ip:
        headers["X-Client-IP"] = ip
    return c.get("/v1/allowance", headers=headers)


def test_allowance_says_what_is_left_and_spends_nothing():
    clock = Clock()
    limits = Limits(per_hour=3, per_ip_per_hour=10, daily_budget_usd=5, clock=clock)
    c, messages = client(limits)
    for _ in range(2):
        body = allowance(c).json()
        assert (body["per_hour"], body["left_this_hour"]) == (3, 3)
    assert messages.requests == []
    assert body["paused"] is False
    # what a report and a check take, so the page can say; no daily count without the Query API's
    assert (body["report_questions"], body["check_questions"]) == (3, 4)
    assert (body["per_day"], body["left_today"]) == (None, None)
    assert ask(c).status_code == 200
    assert allowance(c).json()["left_this_hour"] == 2
    clock.t += 3601
    assert allowance(c).json()["left_this_hour"] == 3


def test_allowance_is_the_tighter_of_user_and_network():
    limits = Limits(per_hour=30, per_ip_per_hour=2, daily_budget_usd=5)
    c, _ = client(limits)
    limits.acquire("someone-else", "203.0.113.9", 2)
    r = allowance(c, ip="203.0.113.9").json()
    assert (r["left_this_hour"], r["per_hour"], r["network_per_hour"]) == (0, 30, 2)
    assert allowance(c, ip="203.0.113.10").json()["left_this_hour"] == 2


def test_allowance_says_when_the_budget_has_run_out():
    limits = Limits(per_hour=30, per_ip_per_hour=30, daily_budget_usd=5)
    c, _ = client(limits)
    limits.spend(6)
    assert allowance(c).json()["paused"] is True


def test_allowance_needs_a_token():
    c, _ = client()
    assert c.get("/v1/allowance").status_code == 401
    assert allowance(c, token="forged").status_code == 401


def test_allowance_carries_the_query_apis_daily_count():
    c = TestClient(create_app(lambda tok: None, who_is, Limits(30, 30, 5),
                              daily_count=lambda tok: {"per_day": 10, "left_today": 7}))
    r = allowance(c).json()
    assert (r["per_day"], r["left_today"]) == (10, 7)


@pytest.mark.parametrize("status,body,want", [
    (200, {"capped": True, "per_day": 10, "left_today": 7}, (10, 7)),
    (200, {"capped": False}, (None, None)),
    (404, {}, (None, None)),
    (500, {}, (None, None)),
])
def test_query_api_daily_count(status, body, want):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=body)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    r = query_api_daily_count("http://q", http=http)("tok")
    assert (r["per_day"], r["left_today"]) == want
    assert seen[0].method == "GET" and seen[0].headers["authorization"] == "Bearer tok"


def test_query_api_daily_count_unreachable_shows_no_count():
    def handler(request):
        raise httpx.ConnectError("down")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert query_api_daily_count("http://q", http=http)("tok")["per_day"] is None


def quota_with(status, body=None):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=body if body is not None else {})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return query_api_quota("http://query-api:8000", http=http), seen


def test_query_api_quota_passes_the_token_and_accepts_200():
    take, seen = quota_with(200, {"demo": False})
    take("tok")
    assert seen[0].url.path == "/v1/demo/agent-quota" and seen[0].method == "POST"
    assert seen[0].headers["Authorization"] == "Bearer tok"


@pytest.mark.parametrize("status, expected", [(429, 429), (401, 401), (500, 503)])
def test_query_api_quota_refusals(status, expected):
    take, _ = quota_with(status, {"detail": "The assistant is turned off for demo sandboxes."})
    with pytest.raises(HTTPException) as e:
        take("tok")
    assert e.value.status_code == expected
    if status == 429:
        assert e.value.detail == "The assistant is turned off for demo sandboxes."


def test_query_api_quota_asks_for_several_questions_at_once():
    take, seen = quota_with(200, {"demo": True, "remaining_today": 4})
    take("tok", 3)
    assert json.loads(seen[0].content) == {"questions": 3}
    take("tok")
    assert seen[1].content == b""  # one question: the body older Query APIs expect


def test_a_report_takes_several_questions_or_none():
    clock = Clock()
    limits = Limits(per_hour=4, per_ip_per_hour=10, daily_budget_usd=5, clock=clock)
    limits.acquire("alice", "203.0.113.1", 3)
    with pytest.raises(LimitExceeded, match="A report counts as 3 questions. You have 1 of 4"):
        limits.acquire("alice", "203.0.113.1", 3)
    limits.acquire("alice", "203.0.113.1")  # the refused report took nothing
    with pytest.raises(LimitExceeded, match="reached the limit of 4"):
        limits.acquire("alice", "203.0.113.1", 3)
    ip_limits = Limits(per_hour=10, per_ip_per_hour=2, daily_budget_usd=5, clock=clock)
    with pytest.raises(LimitExceeded, match="A report counts as 3 questions. Limit of 2"):
        ip_limits.acquire("bob", "203.0.113.2", 3)


def test_query_api_quota_unreachable_fails_closed():
    def handler(request):
        raise httpx.ConnectError("down")

    take = query_api_quota("http://q", http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(HTTPException) as e:
        take("tok")
    assert e.value.status_code == 503


# --- charging: what a run that produced nothing gives back ------------------------------------

class FailingMessages:
    """The model API fails on the first call: nothing was produced."""

    def __init__(self, text_first=False):
        self.text_first = text_first

    def create(self, **kwargs):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api"))

    def stream(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        if self.text_first:
            yield NS(type="text", text="Nvidia's revenue was")
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api"))


def charged_client(messages, stream=True):
    ledger = []

    def take_quota(tok, questions=1):
        ledger.append(questions)

    def refund_quota(tok, questions=1):
        ledger.append(-questions)

    limits = Limits(per_hour=2, per_ip_per_hour=10, daily_budget_usd=5)
    app = create_app(lambda tok: Agent(messages, NoTools(), "claude-haiku-5-5", stream=stream),
                     who_is, limits, take_quota=take_quota, refund_quota=refund_quota)
    return TestClient(app), ledger, limits


def test_a_question_the_model_never_answered_is_given_back():
    c, ledger, limits = charged_client(FailingMessages())
    evs = events(ask(c))
    assert [e["type"] for e in evs] == ["refunded", "error"] and evs[0]["questions"] == 1
    assert ledger == [1, -1]
    assert allowance(c).json()["left_this_hour"] == 2  # the hour's allowance is back too
    limits.acquire("alice", "testclient", 2)


def test_an_answer_that_started_streaming_counts():
    """No free answers by failing late: text already streamed is a result."""
    c, ledger, _ = charged_client(FailingMessages(text_first=True))
    evs = events(ask(c))
    assert [e["type"] for e in evs] == ["text", "error"] and ledger == [1]


def test_an_answer_counts():
    c, ledger, _ = charged_client(ScriptedMessages(), stream=False)
    assert [e["type"] for e in events(ask(c))] == ["done"] and ledger == [1]


def test_a_refused_quota_gives_the_hours_allowance_back():
    def take_quota(tok, questions=1):
        raise HTTPException(429, "Your account has used its 10 assistant questions for today.")

    limits = Limits(per_hour=2, per_ip_per_hour=10, daily_budget_usd=5)
    c = TestClient(create_app(lambda tok: Agent(ScriptedMessages(), NoTools(), "m"), who_is,
                              limits, take_quota=take_quota))
    assert ask(c).status_code == 429 and ask(c).status_code == 429
    assert allowance(c).json()["left_this_hour"] == 2


def test_release_gives_back_only_what_that_request_took():
    clock = Clock()
    limits = Limits(per_hour=10, per_ip_per_hour=10, daily_budget_usd=5, clock=clock)
    first = limits.acquire("alice", "ip", 4, what="A thesis check")
    clock.t += 1
    limits.acquire("alice", "ip", 1)
    limits.release("alice", "ip", 4, first)
    limits.release("alice", "ip", 4, first)  # twice: nothing more
    assert limits.left("alice", "ip")["left_this_hour"] == 9


def test_query_api_refund_never_raises():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(500)

    refund = query_api_refund("http://q", http=httpx.Client(transport=httpx.MockTransport(handler)))
    refund("tok", 4)
    assert seen[0].url.path == "/v1/demo/agent-quota/refund"
    assert json.loads(seen[0].content) == {"questions": 4}
    assert seen[0].headers["Authorization"] == "Bearer tok"

    def down(request):
        raise httpx.ConnectError("down")

    query_api_refund("http://q", http=httpx.Client(transport=httpx.MockTransport(down)))("t", 1)
