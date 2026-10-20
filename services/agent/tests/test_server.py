"""Agent HTTP API with a scripted model and a fake identity check."""

import json
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tributary_agent.agent import Agent
from tributary_agent.server import LimitExceeded, Limits, create_app


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
    assert "Demo user alice" in ask(c, ip="203.0.113.9").json()["detail"]
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
