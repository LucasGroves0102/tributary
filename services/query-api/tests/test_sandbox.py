"""Demo sandboxes ("Try the demo"): what the creation route grants, its limits, the agent
quota, and the adversarial cases (picking companies or plan, extending a sandbox, reading a
company outside it, expired tokens). In-memory fakes; test_sandbox_db.py runs AppSandboxes
against the real app schema."""

from datetime import UTC, date, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.auth import mint
from query_api.sandbox import (
    DEMO_PLAN,
    DEMO_TICKERS,
    DEMO_WATCHLIST,
    DEMO_WORKSPACE,
    NewSandbox,
    QuotaExceeded,
    Refused,
    SandboxConfig,
    SandboxGone,
    ip_key,
    sandbox_workspaces,
)

from .conftest import SECRET

# The sandbox's JWTs expire at NOW + 24 h and the app verifies exp against the real clock, so
# NOW must be the real hour, not a fixed date (a fixed one failed every run a day later).
NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
# fictional ids: ticker → company_id, as silver.resolve_company would answer
IDS = {t: f"{i + 1:010d}" for i, t in enumerate(DEMO_TICKERS)}
TSLA = "0001318605"  # not a demo company
CONFIG = SandboxConfig(enabled=True, creations_per_ip_per_hour=3, max_live=5,
                       agent_questions_per_user_per_day=2, agent_questions_per_day=3)


class FakeStore:
    def __init__(self, ids: dict[str, str] = IDS):
        self.ids = ids
        self.resolves = 0

    def resolve_tickers(self, user_id, tickers):
        self.resolves += 1
        return {t: self.ids[t] for t in tickers if t in self.ids}

    def metric_names(self):
        return {"metrics": ["revenue"], "derived_metrics": []}

    def profiles(self, user_id, company_ids):
        return {}

    def financials(self, user_id, company_id, *args):
        return []


class FakeSandboxes:
    """Same rules as AppSandboxes, in memory; `grants` is what authz-sync would derive."""

    def __init__(self):
        self.sandboxes: dict[str, NewSandbox] = {}
        self.creations: list[tuple[str, datetime]] = []
        self.usage: dict[tuple[str, date], int] = {}
        self.daily: dict[date, int] = {}

    def create(self, sandbox, ip_key, per_ip_per_hour, max_live):
        recent = [t for k, t in self.creations
                  if k == ip_key and t > sandbox.created_at - timedelta(hours=1)]
        if len(recent) >= per_ip_per_hour:
            raise Refused("ip")
        live = [s for s in self.sandboxes.values() if s.expires_at > sandbox.created_at]
        if len(live) >= max_live:
            raise Refused("full")
        self.sandboxes[sandbox.user_id] = sandbox
        self.creations.append((ip_key, sandbox.created_at))

    def expires_at(self, user_id):
        s = self.sandboxes.get(user_id)
        return s.expires_at if s else None

    def take_question(self, user_id, now, per_user, per_day, questions=1):
        s = self.sandboxes.get(user_id)
        if s is None or s.expires_at <= now:
            raise SandboxGone(user_id)
        day = now.date()
        mine, total = self.usage.get((user_id, day), 0), self.daily.get(day, 0)
        if mine + questions > per_user:
            raise QuotaExceeded("user", per_user - mine)
        if total + questions > per_day:
            raise QuotaExceeded("day", per_day - total)
        self.usage[(user_id, day)] = mine + questions
        self.daily[day] = total + questions
        return min(per_user - mine, per_day - total) - questions

    def delete_expired(self, now):
        raise NotImplementedError


class SyncedAuthorizer:
    """OpenFGA as authz-sync leaves it after a sandbox is created: pro tiers on its grants."""

    def __init__(self, sandboxes: FakeSandboxes):
        self.sandboxes = sandboxes

    def readable(self, user_id):
        s = self.sandboxes.sandboxes.get(user_id)
        return {c: {"financials", "filings"} for c in s.company_ids} if s else {}

    def can(self, user_id, tier, company_id):
        return tier in self.readable(user_id).get(company_id, set())


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def world():
    sandboxes, store, clock = FakeSandboxes(), FakeStore(), Clock()
    app = create_app(SyncedAuthorizer(sandboxes), store, SECRET, sandboxes=sandboxes,
                     sandbox_config=CONFIG, clock=clock)
    return TestClient(app), sandboxes, store, clock


def create(client, ip="203.0.113.7", **kwargs):
    return client.post("/v1/demo/sandbox", headers={"X-Client-IP": ip}, **kwargs)


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_creates_a_real_user_with_the_fixed_plan_companies_and_watchlist(world):
    client, sandboxes, _, _ = world
    r = create(client)
    assert r.status_code == 201
    body = r.json()
    user = body["user_id"]
    assert user.startswith("demo-") and len(user) == len("demo-") + 16
    s = sandboxes.sandboxes[user]
    assert s.company_ids == [IDS[t] for t in DEMO_TICKERS] and len(s.company_ids) == 20
    assert s.watchlist == [IDS[t] for t in DEMO_WATCHLIST]
    assert s.created_at == NOW and s.expires_at == NOW + timedelta(hours=24)
    assert body["plan"] == DEMO_PLAN == "pro"
    assert [c["ticker"] for c in body["companies"]] == list(DEMO_TICKERS)
    assert body["expires_at"] == (NOW + timedelta(hours=24)).isoformat()
    # a normal JWT: sub is the new user, exp is the sandbox's expiry
    claims = jwt.decode(body["token"], SECRET, algorithms=["HS256"],
                        options={"verify_exp": False})
    assert claims["sub"] == user
    assert claims["exp"] == int((NOW + timedelta(hours=24)).timestamp())


def test_offer_describes_the_sandbox(world):
    client, *_ = world
    offer = client.get("/v1/demo/sandbox").json()
    assert offer["tickers"] == list(DEMO_TICKERS)
    assert offer["ttl_hours"] == 24 and offer["agent_questions_per_day"] == 2


def test_each_creation_is_a_new_user(world):
    client, sandboxes, _, _ = world
    a, b = create(client).json(), create(client).json()
    assert a["user_id"] != b["user_id"]
    assert len(sandboxes.sandboxes) == 2


@pytest.mark.parametrize("kwargs", [
    {"json": {"companies": [TSLA], "tickers": ["TSLA"], "plan": "enterprise",
              "user_id": "alice", "ttl_hours": 9999, "expires_at": "2099-01-01T00:00:00Z"}},
    {"params": {"companies": TSLA, "plan": "enterprise", "user_id": "alice"}},
    {"content": b"not json", "headers": {"Content-Type": "application/json"}},
])
def test_creation_cannot_pick_companies_plan_user_or_lifetime(world, kwargs):
    client, sandboxes, _, _ = world
    headers = {"X-Client-IP": "203.0.113.7", **kwargs.pop("headers", {})}
    r = client.post("/v1/demo/sandbox", headers=headers, **kwargs)
    assert r.status_code == 201
    s = sandboxes.sandboxes[r.json()["user_id"]]
    assert TSLA not in s.company_ids
    assert s.company_ids == [IDS[t] for t in DEMO_TICKERS]
    assert s.expires_at - s.created_at == timedelta(hours=24)
    assert r.json()["user_id"] != "alice" and r.json()["plan"] == "pro"


def test_demo_user_cannot_read_a_company_outside_the_sandbox(world):
    client, *_ = world
    tok = create(client).json()["token"]
    assert client.get(f"/v1/companies/{IDS['PEP']}/financials",
                      headers=bearer(tok)).status_code == 200
    assert client.get(f"/v1/companies/{TSLA}/financials",
                      headers=bearer(tok)).status_code == 403
    me = client.get("/v1/me", headers=bearer(tok)).json()
    assert TSLA not in {c["company_id"] for c in me["companies"]}
    assert len(me["companies"]) == 20


def test_me_shows_the_sandbox_only_for_sandbox_users(world):
    client, *_ = world
    tok = create(client).json()["token"]
    me = client.get("/v1/me", headers=bearer(tok)).json()
    assert me["sandbox"]["expires_at"] == (NOW + timedelta(hours=24)).isoformat()
    assert me["sandbox"]["companies"] == 20
    alice = client.get("/v1/me", headers=bearer(mint("alice", SECRET))).json()
    assert "sandbox" not in alice


def test_sandbox_cannot_be_extended_or_renewed(world):
    client, sandboxes, _, clock = world
    body = create(client).json()
    user, tok = body["user_id"], body["token"]
    # the named-user token route never mints for a sandbox user, even if misconfigured
    for app_users in (frozenset(), frozenset({user})):
        c = TestClient(create_app(SyncedAuthorizer(sandboxes), FakeStore(), SECRET,
                                  demo_users=app_users, sandboxes=sandboxes,
                                  sandbox_config=CONFIG, clock=clock))
        assert c.post("/v1/demo/token", json={"user_id": user}).status_code == 404
    # no route updates a sandbox
    for method in ("PUT", "PATCH", "DELETE"):
        assert client.request(method, "/v1/demo/sandbox", headers=bearer(tok)).status_code == 405
    # creating again (even with the old token) makes a different user; the old one is unchanged
    again = client.post("/v1/demo/sandbox", headers={**bearer(tok), "X-Client-IP": "198.51.100.1"})
    assert again.json()["user_id"] != user
    assert sandboxes.sandboxes[user].expires_at == NOW + timedelta(hours=24)


def test_expired_sandbox_token_is_rejected(world):
    client, _, _, clock = world
    clock.now = datetime.now(UTC) - timedelta(hours=25)  # created a day and an hour ago
    tok = create(client).json()["token"]
    clock.now = datetime.now(UTC)
    assert client.get("/v1/me", headers=bearer(tok)).status_code == 401
    assert client.post("/v1/demo/agent-quota", headers=bearer(tok)).status_code == 401
    assert client.get(f"/v1/companies/{IDS['PEP']}/financials",
                      headers=bearer(tok)).status_code == 401


def test_token_for_a_sandbox_user_signed_with_another_secret_is_rejected(world):
    client, *_ = world
    user = create(client).json()["user_id"]
    forged = mint(user, "another-secret-" + "y" * 32)
    assert client.get("/v1/me", headers=bearer(forged)).status_code == 401


def test_creations_per_ip_per_hour(world):
    client, _, _, clock = world
    for _ in range(3):
        assert create(client, ip="203.0.113.7").status_code == 201
    r = create(client, ip="203.0.113.7")
    assert r.status_code == 429 and "your network" in r.json()["detail"]
    assert create(client, ip="198.51.100.9").status_code == 201  # another client
    clock.now = NOW + timedelta(hours=1, seconds=1)
    assert create(client, ip="203.0.113.7").status_code == 201


def test_the_ip_is_never_stored(world):
    client, sandboxes, _, _ = world
    create(client, ip="203.0.113.7")
    key = sandboxes.creations[0][0]
    assert key == ip_key(SECRET, "203.0.113.7") and "203.0.113" not in key


def test_full_refuses_politely(world):
    client, *_ = world
    for i in range(5):
        assert create(client, ip=f"198.51.100.{i}").status_code == 201
    r = create(client, ip="198.51.100.99")
    assert r.status_code == 429
    assert r.json()["detail"].startswith("The demo is full right now.")


def test_disabled_sandboxes_404():
    for config, sandboxes in ((SandboxConfig(), FakeSandboxes()), (CONFIG, None)):
        c = TestClient(create_app(SyncedAuthorizer(FakeSandboxes()), FakeStore(), SECRET,
                                  sandboxes=sandboxes, sandbox_config=config))
        assert c.get("/v1/demo/sandbox").status_code == 404
        assert c.post("/v1/demo/sandbox").status_code == 404


def test_unresolved_tickers_are_skipped_and_resolution_is_cached_once_complete():
    partial = {t: c for t, c in IDS.items() if t != "KDP"}
    sandboxes, store = FakeSandboxes(), FakeStore(partial)
    c = TestClient(create_app(SyncedAuthorizer(sandboxes), store, SECRET, sandboxes=sandboxes,
                              sandbox_config=CONFIG, clock=Clock()))
    body = create(c).json()
    assert len(body["companies"]) == 19 and IDS["KDP"] not in sandboxes.sandboxes[
        body["user_id"]].company_ids
    create(c, ip="1.1.1.1")
    assert store.resolves == 2  # incomplete: tried again
    store.ids = IDS
    create(c, ip="1.1.1.2")
    create(c, ip="1.1.1.3")
    assert store.resolves == 3  # complete: cached


def test_no_demo_company_resolves_503():
    sandboxes = FakeSandboxes()
    c = TestClient(create_app(SyncedAuthorizer(sandboxes), FakeStore({}), SECRET,
                              sandboxes=sandboxes, sandbox_config=CONFIG))
    assert create(c).status_code == 503
    assert sandboxes.sandboxes == {}


def test_agent_quota_per_user_and_per_day(world):
    client, *_ = world
    a = create(client).json()["token"]
    b = create(client).json()["token"]
    assert client.post("/v1/demo/agent-quota", headers=bearer(a)).json() == {
        "demo": True, "remaining_today": 1}
    assert client.post("/v1/demo/agent-quota", headers=bearer(a)).status_code == 200
    r = client.post("/v1/demo/agent-quota", headers=bearer(a))
    assert r.status_code == 429 and "used its 2 assistant questions" in r.json()["detail"]
    assert client.post("/v1/demo/agent-quota", headers=bearer(b)).status_code == 200
    r = client.post("/v1/demo/agent-quota", headers=bearer(b))  # 4th today: global cap is 3
    assert r.status_code == 429 and "all the demo questions" in r.json()["detail"]


def test_a_report_takes_several_questions_or_none(world):
    client, sandboxes, *_ = world
    a = create(client).json()["token"]
    assert client.post("/v1/demo/agent-quota", headers=bearer(a)).status_code == 200
    # one question left (per user 2): a 2-question report is refused, all or nothing
    r = client.post("/v1/demo/agent-quota", headers=bearer(a), json={"questions": 2})
    assert r.status_code == 429
    assert "A report counts as 2 assistant questions" in r.json()["detail"]
    assert "1 left today" in r.json()["detail"]
    assert sum(sandboxes.usage.values()) == 1
    assert client.post("/v1/demo/agent-quota", headers=bearer(a)).status_code == 200
    b = create(client).json()["token"]
    r = client.post("/v1/demo/agent-quota", headers=bearer(b), json={"questions": 2})
    assert r.status_code == 429 and "all the demo questions" in r.json()["detail"]


@pytest.mark.parametrize("body", [{"questions": 0}, {"questions": 6}, {"questions": "x"},
                                  {"questions": 1, "user_id": "bob"}])
def test_agent_quota_body_is_strict(world, body):
    client, *_ = world
    a = create(client).json()["token"]
    assert client.post("/v1/demo/agent-quota", headers=bearer(a), json=body).status_code == 422


def test_agent_quota_does_not_limit_other_users(world):
    client, *_ = world
    for _ in range(5):
        assert client.post("/v1/demo/agent-quota",
                           headers=bearer(mint("alice", SECRET))).json() == {"demo": False}


def test_agent_quota_needs_a_token(world):
    client, *_ = world
    assert client.post("/v1/demo/agent-quota").status_code == 401


def test_agent_can_be_switched_off_for_sandboxes():
    sandboxes = FakeSandboxes()
    config = SandboxConfig(enabled=True, agent_enabled=False)
    c = TestClient(create_app(SyncedAuthorizer(sandboxes), FakeStore(), SECRET,
                              sandboxes=sandboxes, sandbox_config=config, clock=Clock()))
    tok = create(c).json()["token"]
    r = c.post("/v1/demo/agent-quota", headers=bearer(tok))
    assert r.status_code == 429 and "turned off" in r.json()["detail"]
    assert c.get("/v1/me", headers=bearer(tok)).json()["sandbox"]["agent"] is False


def test_config_from_env():
    assert SandboxConfig.from_env({}) == SandboxConfig()
    c = SandboxConfig.from_env({"DEMO_SANDBOX_ENABLED": "true", "DEMO_SANDBOX_TTL_HOURS": "12",
                                "DEMO_SANDBOX_PER_IP_PER_HOUR": "1", "DEMO_SANDBOX_MAX_LIVE": "50",
                                "DEMO_AGENT_ENABLED": "false",
                                "DEMO_AGENT_QUESTIONS_PER_USER_PER_DAY": "5",
                                "DEMO_AGENT_QUESTIONS_PER_DAY": "40"})
    assert c == SandboxConfig(True, 12, 1, 50, False, 5, 40)
    defaults = SandboxConfig()
    assert (defaults.ttl_hours, defaults.agent_questions_per_user_per_day,
            defaults.agent_questions_per_day) == (24, 10, 200)
    for bad in ({"DEMO_SANDBOX_ENABLED": "maybe"}, {"DEMO_SANDBOX_MAX_LIVE": "-1"},
                {"DEMO_SANDBOX_ENABLED": "1", "DEMO_SANDBOX_TTL_HOURS": "0"}):
        with pytest.raises(ValueError):
            SandboxConfig.from_env(bad)


def test_demo_tables_are_not_streamed():
    """Nothing in migration 003 grants access, so Debezium doesn't capture it."""
    import json
    from pathlib import Path

    connector = json.loads((Path(__file__).parents[3] / "streaming/debezium/app-connector.json")
                           .read_text())
    assert "demo_" not in connector["table.include.list"]


def test_sandbox_starts_with_the_pepsico_vs_coca_cola_workspace(world):
    client, sandboxes, _, _ = world
    body = create(client).json()
    assert body["workspaces"] == [DEMO_WORKSPACE] == ["PepsiCo vs Coca-Cola"]
    (ws,) = sandboxes.sandboxes[body["user_id"]].workspaces
    chart, comps = ws["items"]
    assert chart[0] == "chart"
    assert {(x["companyId"], x["metric"]) for x in chart[2]["layers"]} == {
        (IDS[t], m) for t in ("PEP", "KO") for m in ("gross_margin", "operating_margin")}
    assert comps == ("comps", "PEP vs KO, KDP, MNST",
                     {"target": IDS["PEP"], "peers": [IDS["KO"], IDS["KDP"], IDS["MNST"]]})


def test_starter_workspace_skips_what_does_not_resolve():
    assert sandbox_workspaces({"KO": IDS["KO"]}) == []
    (ws,) = sandbox_workspaces({t: IDS[t] for t in ("PEP", "KO", "MNST")})
    assert ws["items"][1][1] == "PEP vs KO, MNST"
