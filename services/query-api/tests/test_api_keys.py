"""API keys (query_api/api_keys.py) through the routes, with an in-memory store that keeps
AppApiKeys' rules (test_api_keys_db.py runs the real one): who a key is, what it may reach, its
limits, and that the key is shown once and never again, in an answer or a log.

Adversarial cases (a key is the new way in, so it gets the same attacks as a token): a key for
another user's data or saved work, the user named in a parameter, header or body, a key in the
query string or another header, a key one character off, a revoked, expired or malformed key
(one 401, the same as a bad token), a read key writing, any key on an account, demo, sign-in,
report or thesis-check route, and a key reaching a route added after this list was written."""

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from query_api.api_keys import (
    EXPIRY_DAYS,
    KEY,
    KEY_ROUTES,
    MAX_ACTIVE,
    PUBLIC_ROUTES,
    READ_ROUTES,
    SHOWN,
    WRITE_ROUTES,
    KeyUse,
    Limits,
    TooManyKeys,
    key_hash,
    new_key,
)
from query_api.app import create_app

from .conftest import SECRET
from .test_api import NVDA, TSLA, FakeAuthorizer
from .test_ui_api import FakeWatchlists, UIStore
from .test_workspaces import CHART, FakeWorkspaces, MetaStore

NOW = datetime(2026, 11, 15, 12, 0, 30, tzinfo=UTC)
ALICE, BOB, SANDBOX = "alice", "bob", "demo-0123456789abcdef"


class FakeKeys:
    """AppApiKeys' rules in memory: a key by its hash, owner-only reads, the cap, the limits
    counted per key per UTC day and clock minute, refused requests not counted."""

    def __init__(self, users=(ALICE, BOB)):
        self.users = set(users)
        self.rows: dict[str, dict] = {}  # key_id -> row
        self.usage: dict[tuple[str, object], dict] = {}  # (key_id, day) -> counts
        self.disabled: set[str] = set()  # password accounts disabled

    def use(self, h, now, limits):
        row = next((r for r in self.rows.values() if r["key_hash"] == h), None)
        if (row is None or row["revoked_at"] is not None or row["expires_at"] <= now
                or row["user_id"] in self.disabled):
            return None
        minute = now.replace(second=0, microsecond=0)
        u = self.usage.setdefault((row["key_id"], now.date()),
                                  {"requests": 0, "limited": 0, "minute": minute, "mreq": 0})
        if minute > u["minute"]:
            u["minute"], u["mreq"] = minute, 0
        outcome, retry = "ok", 0
        if u["requests"] >= limits.per_day:
            outcome, retry = "day", 3600
        elif u["mreq"] >= limits.per_minute:
            outcome = "minute"
            retry = max(1, int((u["minute"] + timedelta(minutes=1) - now).total_seconds()))
        if outcome == "ok":
            u["requests"] += 1
            u["mreq"] += 1
            row["last_used_at"] = now
        else:
            u["limited"] += 1
        return KeyUse(key_id=row["key_id"], user_id=row["user_id"], name=row["name"],
                      prefix=row["prefix"], scopes=tuple(row["scopes"]),
                      expires_at=row["expires_at"], outcome=outcome, retry_after=retry,
                      minute_left=max(0, limits.per_minute - u["mreq"]),
                      day_left=max(0, limits.per_day - u["requests"]))

    def create(self, user_id, name, scopes, days, now):
        active = [r for r in self.rows.values() if r["user_id"] == user_id
                  and r["revoked_at"] is None and r["expires_at"] > now]
        if len(active) >= MAX_ACTIVE:
            raise TooManyKeys
        key = new_key()
        row = {"key_id": str(uuid.uuid4()), "user_id": user_id, "name": name,
               "prefix": key[:SHOWN], "key_hash": key_hash(key), "scopes": list(scopes),
               "created_at": now, "expires_at": now + timedelta(days=days),
               "last_used_at": None, "revoked_at": None}
        self.rows[row["key_id"]] = row
        return {**row, "requests_today": 0, "limited_today": 0}, key

    def list(self, user_id, now):
        out = []
        for r in sorted(self.rows.values(), key=lambda r: r["created_at"], reverse=True):
            if r["user_id"] != user_id:
                continue
            u = self.usage.get((r["key_id"], now.date()), {"requests": 0, "limited": 0})
            out.append({**r, "requests_today": u["requests"], "limited_today": u["limited"]})
        return out

    def revoke(self, user_id, key_id, now):
        r = self.rows.get(key_id)
        if r is None or r["user_id"] != user_id or r["revoked_at"] is not None:
            return False
        r["revoked_at"] = now
        return True


class KeyStore(UIStore, MetaStore):
    """The screener's fake (the watchlist reads through it) and saved work's."""


class Clock:
    def __init__(self, at=NOW):
        self.at = at

    def __call__(self):
        return self.at


def make_world(limits):
    keys, ws, clock = FakeKeys(), FakeWorkspaces(), Clock()
    authz = FakeAuthorizer({ALICE: {NVDA: {"financials", "filings"}},
                            BOB: {TSLA: {"financials", "filings"}}})
    store = KeyStore()
    watch = FakeWatchlists({ALICE: [NVDA], BOB: [TSLA]})
    app = create_app(authz, store, SECRET, workspaces=ws, watchlists=watch, clock=clock,
                     api_keys=keys, api_key_limits=limits)
    return TestClient(app), keys, ws, store, clock


@pytest.fixture
def world():
    """Small limits (5 a minute, 12 a day), so the tests reach them."""
    return make_world(Limits(per_minute=5, per_day=12))


@pytest.fixture
def roomy():
    """The default limits, for tests that make many requests."""
    return make_world(Limits())


def make_key(client, token, user=ALICE, scopes=("read",), days=90, name="script"):
    r = client.post("/v1/api-keys", json={"name": name, "scopes": list(scopes),
                                          "expires_in_days": days}, headers=token(user))
    assert r.status_code == 201, r.text
    return r.json()


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


def other_key(key):
    """The key with its last character changed (a 32-byte key's last character is one of 16,
    "A" among them, so never append a fixed one)."""
    return key[:-1] + ("B" if key[-1] != "B" else "C")


# --- the key itself -------------------------------------------------------------------------

def test_a_key_is_trb_and_32_random_bytes():
    keys = {new_key() for _ in range(50)}
    assert len(keys) == 50
    for k in keys:
        assert KEY.fullmatch(k) and len(k) == 47
    assert key_hash("trb_x") == hashlib.sha256(b"trb_x").digest()


def test_creating_a_key_shows_it_once(world, token):
    client, keys, *_ = world
    r = client.post("/v1/api-keys", json={"name": "  notebook  ", "scopes": ["read"]},
                    headers=token(ALICE))
    assert r.status_code == 201
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    key = body["key"]
    assert KEY.fullmatch(key)
    made = body["api_key"]
    assert made["name"] == "notebook" and made["prefix"] == key[:8] and made["scopes"] == ["read"]
    assert made["status"] == "active" and made["today"] == {"requests": 0, "limited": 0}
    assert made["expires_at"].startswith((NOW + timedelta(days=90)).date().isoformat())
    stored = keys.rows[made["key_id"]]
    assert stored["key_hash"] == key_hash(key) and key not in str(stored)
    # never again: not in the list, not in /v1/me, not in any later answer
    listed = client.get("/v1/api-keys", headers=token(ALICE))
    assert key not in listed.text and key_hash(key).hex() not in listed.text
    assert key[8:] not in listed.text
    me = client.get("/v1/me", headers=bearer(key))
    assert me.status_code == 200 and key not in me.text and key[8:] not in me.text


def test_the_list_says_what_can_be_chosen_and_each_keys_usage(world, token):
    client, *_ = world
    a = make_key(client, token, scopes=("read", "write"), days=30)
    for _ in range(3):
        assert client.get("/v1/watchlist", headers=bearer(a["key"])).status_code == 200
    body = client.get("/v1/api-keys", headers=token(ALICE)).json()
    assert body["allowed"] is True and body["max_active"] == MAX_ACTIVE
    assert body["expiry_days"] == list(EXPIRY_DAYS) and body["default_expiry_days"] == 90
    assert body["limits"] == {"per_minute": 5, "per_day": 12}
    assert body["scopes"] == ["read", "write"]
    [k] = body["keys"]
    assert set(k) == {"key_id", "name", "prefix", "scopes", "status", "created_at",
                      "expires_at", "last_used_at", "revoked_at", "today"}
    assert k["today"] == {"requests": 3, "limited": 0} and k["last_used_at"] is not None
    assert k["scopes"] == ["read", "write"]


@pytest.mark.parametrize("body", [
    {"name": "", "scopes": ["read"]},
    {"name": "   ", "scopes": ["read"]},
    {"name": "x" * 61, "scopes": ["read"]},
    {"name": "tab\there", "scopes": ["read"]},
    {"name": "next\u0085line", "scopes": ["read"]},
    {"name": "right\u202eto left", "scopes": ["read"]},
    {"name": "ok", "scopes": ["write"]},
    {"name": "ok", "scopes": []},
    {"name": "ok", "scopes": ["read", "read"]},
    {"name": "ok", "scopes": ["admin"]},
    {"name": "ok", "scopes": ["read"], "expires_in_days": 0},
    {"name": "ok", "scopes": ["read"], "expires_in_days": 3650},
    {"name": "ok", "scopes": ["read"], "user_id": BOB},
])
def test_a_key_needs_a_name_known_scopes_and_an_offered_expiry(world, token, body):
    client, keys, *_ = world
    r = client.post("/v1/api-keys", json=body, headers=token(ALICE))
    assert r.status_code == 422
    assert keys.rows == {}


def test_at_most_five_active_keys(world, token):
    client, keys, _, _, clock = world
    made = [make_key(client, token, name=f"k{i}") for i in range(MAX_ACTIVE)]
    r = client.post("/v1/api-keys", json={"name": "sixth"}, headers=token(ALICE))
    assert r.status_code == 409 and "at most 5" in r.json()["detail"]
    # bob's count is his own
    make_key(client, token, user=BOB)
    # a revoked one frees a place, and so does an expired one
    assert client.delete(f"/v1/api-keys/{made[0]['api_key']['key_id']}",
                         headers=token(ALICE)).status_code == 200
    make_key(client, token, name="sixth")
    assert client.post("/v1/api-keys", json={"name": "7"}, headers=token(ALICE)).status_code == 409
    clock.at = NOW + timedelta(days=91)
    make_key(client, token, name="after expiry")


def test_a_demo_sandbox_cant_have_keys(world, token):
    client, keys, *_ = world
    r = client.get("/v1/api-keys", headers=token(SANDBOX))
    assert r.status_code == 200 and r.json()["allowed"] is False and r.json()["keys"] == []
    r = client.post("/v1/api-keys", json={"name": "x"}, headers=token(SANDBOX))
    assert r.status_code == 403 and "permanent account" in r.json()["detail"]
    assert keys.rows == {}


# --- who a key is ---------------------------------------------------------------------------

def test_a_key_is_its_owner_and_nobody_else(world, token):
    client, *_ = world
    key = make_key(client, token)["key"]
    me = client.get("/v1/me", headers=bearer(key)).json()
    assert me["user_id"] == ALICE
    assert me["api_key"]["scopes"] == ["read"] and me["api_key"]["prefix"] == key[:8]
    assert [c["company_id"] for c in me["companies"]] == [NVDA]
    # a sign-in's /v1/me has no api_key (the agent's whoami reads it)
    assert "api_key" not in client.get("/v1/me", headers=token(ALICE)).json()
    # the user named anywhere else changes nothing
    for extra in ({"params": {"user_id": BOB}}, {"headers": {**bearer(key), "X-User-Id": BOB}}):
        r = client.get("/v1/me", **{"headers": bearer(key), **extra})
        assert r.json()["user_id"] == ALICE


def test_a_key_reads_only_what_its_owner_may(world, token):
    client, _, _, store, _ = world
    key = make_key(client, token)["key"]
    assert client.get(f"/v1/companies/{NVDA}/financials", headers=bearer(key)).status_code == 200
    r = client.get(f"/v1/companies/{TSLA}/financials", headers=bearer(key))
    assert r.status_code == 403  # bob's company: layer 1 refuses, as for alice's sign-in
    assert ("financials", ALICE, TSLA) not in store.calls
    bob = make_key(client, token, user=BOB)["key"]
    assert client.get(f"/v1/companies/{TSLA}/financials", headers=bearer(bob)).status_code == 200
    assert client.get(f"/v1/companies/{NVDA}/financials", headers=bearer(bob)).status_code == 403


def test_a_key_sees_only_its_owners_saved_work(world, token):
    client, *_ = world
    w = client.post("/v1/workspaces", json={"name": "Bob's"}, headers=token(BOB)).json()
    item = client.post(f"/v1/workspaces/{w['workspace_id']}/items",
                       json={"kind": "chart", "name": "Rev", "state": CHART},
                       headers=token(BOB)).json()
    alice = make_key(client, token, scopes=("read", "write"))["key"]
    assert client.get("/v1/workspaces", headers=bearer(alice)).json()["workspaces"] == []
    assert client.get(f"/v1/workspaces/{w['workspace_id']}",
                      headers=bearer(alice)).status_code == 404
    assert client.get(f"/v1/workspace-items/{item['item_id']}",
                      headers=bearer(alice)).status_code == 404
    r = client.put(f"/v1/workspace-items/{item['item_id']}", json={"state": CHART},
                   headers=bearer(alice))
    assert r.status_code == 404
    assert client.get("/v1/watchlist", headers=bearer(alice)).json()["companies"][0][
        "company_id"] == NVDA


def test_unknown_expired_revoked_and_malformed_keys_get_one_401(world, token):
    client, keys, _, _, clock = world
    good = make_key(client, token)["key"]
    revoked = make_key(client, token, name="old")
    client.delete(f"/v1/api-keys/{revoked['api_key']['key_id']}", headers=token(ALICE))
    expiring = make_key(client, token, days=30, name="short")["key"]
    keys.disabled.add(BOB)
    disabled = make_key(client, token, user=BOB)["key"]
    clock.at = NOW + timedelta(days=30)
    bad_token = client.get("/v1/me", headers={"Authorization": "Bearer not.a.jwt"})
    answers = [client.get("/v1/me", headers=bearer(k)) for k in (
        "trb_" + "A" * 43,                    # well formed, unknown
        other_key(good),                      # one character off
        good[:-1],                            # truncated
        good + "A",                           # too long
        "trb_" + "!" * 43,                    # not base64url
        revoked["key"], expiring, disabled)]
    for r in answers:
        assert (r.status_code, r.json(), r.headers.get("www-authenticate")) == (
            bad_token.status_code, bad_token.json(), bad_token.headers.get("www-authenticate"))
        assert "x-ratelimit-remaining-day" not in r.headers
    assert bad_token.status_code == 401 and bad_token.json() == {"detail": "invalid token"}
    assert client.get("/v1/me", headers=bearer(good)).status_code == 200


def test_revoking_takes_effect_on_the_next_request(world, token):
    client, *_ = world
    made = make_key(client, token)
    assert client.get("/v1/me", headers=bearer(made["key"])).status_code == 200
    r = client.delete(f"/v1/api-keys/{made['api_key']['key_id']}", headers=token(ALICE))
    assert r.json() == {"key_id": made["api_key"]["key_id"], "revoked": True}
    assert client.get("/v1/me", headers=bearer(made["key"])).status_code == 401
    listed = client.get("/v1/api-keys", headers=token(ALICE)).json()["keys"]
    assert listed[0]["status"] == "revoked" and listed[0]["revoked_at"] is not None


def test_revoking_another_users_key_is_the_same_404_as_none(world, token):
    client, *_ = world
    bobs = make_key(client, token, user=BOB)
    for key_id in (bobs["api_key"]["key_id"], str(uuid.uuid4())):
        r = client.delete(f"/v1/api-keys/{key_id}", headers=token(ALICE))
        assert (r.status_code, r.json()) == (404, {"detail": "no such key"})
    assert client.get("/v1/me", headers=bearer(bobs["key"])).status_code == 200
    assert client.delete("/v1/api-keys/not-a-uuid", headers=token(ALICE)).status_code == 422


def test_a_key_is_taken_only_from_the_authorization_header(world, token):
    client, *_ = world
    key = make_key(client, token)["key"]
    for attempt in ({"params": {"api_key": key}}, {"params": {"token": key}},
                    {"headers": {"X-Api-Key": key}}, {"headers": {"Authorization": key}},
                    {"headers": {"Authorization": f"Token {key}"}},
                    {"headers": {"Cookie": f"token={key}"}}):
        r = client.get("/v1/me", **attempt)
        assert r.status_code == 401, attempt
    assert client.get("/v1/me", headers={"Authorization": f"bearer {key}"}).status_code == 200


# --- what a key may reach -------------------------------------------------------------------

def test_a_read_key_cant_write(world, token):
    client, _, ws, *_ = world
    key = make_key(client, token)["key"]
    for method, path, body in [
        ("POST", "/v1/workspaces", {"name": "x"}),
        ("PUT", f"/v1/watchlist/{TSLA}", None),
        ("DELETE", f"/v1/watchlist/{NVDA}", None),
    ]:
        r = client.request(method, path, json=body, headers=bearer(key))
        assert r.status_code == 403, path
        assert r.json() == {"detail": "this API key can't write: it has the read scope"}
    assert ws.workspaces == {}
    assert client.get("/v1/watchlist", headers=bearer(key)).status_code == 200


def test_a_write_key_creates_and_edits_its_owners_work_but_doesnt_delete_it(roomy, token):
    client, _, ws, *_ = roomy
    key = make_key(client, token, scopes=("read", "write"))["key"]
    r = client.post("/v1/workspaces", json={"name": "From a script"}, headers=bearer(key))
    assert r.status_code == 201
    wid = r.json()["workspace_id"]
    assert ws.workspaces[wid]["user_id"] == ALICE
    item = client.post(f"/v1/workspaces/{wid}/items",
                       json={"kind": "chart", "name": "Rev", "state": CHART},
                       headers=bearer(key))
    assert item.status_code == 201
    iid = item.json()["item_id"]
    assert client.put(f"/v1/workspace-items/{iid}", json={"state": {**CHART, "range": 5}},
                      headers=bearer(key)).status_code == 200
    assert client.patch(f"/v1/workspaces/{wid}", json={"name": "Renamed"},
                        headers=bearer(key)).status_code == 200
    assert client.put(f"/v1/watchlist/{TSLA}", headers=bearer(key)).status_code == 200
    # deleting saved work is a sign-in's (it can't be restored)
    assert client.delete(f"/v1/workspace-items/{iid}", headers=bearer(key)).status_code == 403
    assert client.delete(f"/v1/workspaces/{wid}", headers=bearer(key)).status_code == 403
    assert wid in ws.workspaces


ACCOUNT = [
    ("GET", "/v1/api-keys"), ("POST", "/v1/api-keys"),
    ("DELETE", "/v1/api-keys/0b6f8a52-63f4-4d6e-9d0e-2f4d3b8a1c11"),
    ("GET", "/v1/alerts/settings"), ("POST", "/v1/alerts/webhooks"),
    ("GET", "/v1/alerts/webhooks"), ("GET", "/v1/alerts/notifications"),
    ("GET", "/v1/views"), ("PUT", "/v1/views/home"), ("GET", "/v1/connections"),
    ("POST", "/v1/demo/agent-quota"), ("GET", "/v1/demo/agent-quota"),
    ("POST", "/v1/thesis-checks/0b6f8a52-63f4-4d6e-9d0e-2f4d3b8a1c11"),
    ("POST", "/v1/workspaces/0b6f8a52-63f4-4d6e-9d0e-2f4d3b8a1c11/reports"),
    ("GET", "/v1/recent"),
]


@pytest.mark.parametrize("method, path", ACCOUNT)
def test_no_key_manages_the_account_or_reaches_agent_routes(world, token, method, path):
    client, keys, *_ = world
    key = make_key(client, token, scopes=("read", "write"))["key"]
    before = {k: dict(r) for k, r in keys.rows.items()}
    r = client.request(method, path, json={"name": "x"}, headers=bearer(key))
    assert r.status_code == 403
    assert r.json() == {"detail": "API keys can't use this route: sign in to Tributary"}
    assert keys.rows.keys() == before.keys()


def every_signed_in_route(app):
    for r in app.routes:
        if isinstance(r, APIRoute) and any(
                getattr(d.call, "__name__", "") == "current_user"
                for d in r.dependant.dependencies):
            for m in r.methods:
                yield m, r.path


def test_every_other_signed_in_route_refuses_a_write_key(roomy, token):
    """Default deny: a route that isn't in KEY_ROUTES (one added after this list, too) refuses
    every key before the route runs, whatever its path parameters."""
    client, *_ = roomy
    key = make_key(client, token, scopes=("read", "write"))["key"]
    routes = set(every_signed_in_route(client.app))
    assert len(routes) > len(KEY_ROUTES)
    for method, path in sorted(routes - KEY_ROUTES.keys()):
        url = path.replace("{company_id}", NVDA).replace("{", "").replace("}", "")
        r = client.request(method, url, headers=bearer(key))
        assert r.status_code == 403, (method, path, r.text)


def test_every_listed_route_exists_and_is_signed_in():
    app = create_app(FakeAuthorizer({}), MetaStore(), SECRET, workspaces=FakeWorkspaces(),
                     watchlists=FakeWatchlists({}), api_keys=FakeKeys())
    routes = set(every_signed_in_route(app))
    assert set(KEY_ROUTES) <= routes, set(KEY_ROUTES) - routes
    assert READ_ROUTES.isdisjoint(WRITE_ROUTES)
    assert all(m == "GET" or (m, p) in WRITE_ROUTES or p.startswith(("/v1/formulas/",))
               or p.endswith("/model") for m, p in READ_ROUTES)
    public = {(m, r.path) for r in app.routes if isinstance(r, APIRoute) for m in r.methods}
    assert PUBLIC_ROUTES <= public and PUBLIC_ROUTES.isdisjoint(routes)
    for _, p in KEY_ROUTES:
        assert not p.startswith(("/v1/api-keys", "/v1/alerts", "/v1/demo", "/v1/auth",
                                 "/v1/views", "/v1/connections", "/v1/thesis-checks"))
        assert "/reports" not in p and "/checks" not in p


# --- limits ---------------------------------------------------------------------------------

def test_over_the_minute_limit_429_with_retry_after_then_again_next_minute(world, token):
    client, keys, _, _, clock = world
    key = make_key(client, token)["key"]
    for n in range(5):
        r = client.get("/v1/me", headers=bearer(key))
        assert r.status_code == 200
        assert r.headers["x-ratelimit-remaining-minute"] == str(4 - n)
        assert r.headers["x-ratelimit-limit-day"] == "12"
    r = client.get("/v1/me", headers=bearer(key))
    assert r.status_code == 429 and r.headers["retry-after"] == "30"
    assert "5 requests this minute" in r.json()["detail"]
    assert r.headers["x-ratelimit-remaining-minute"] == "0"
    # refused requests aren't counted, only noted
    today = client.get("/v1/api-keys", headers=token(ALICE)).json()["keys"][0]["today"]
    assert today == {"requests": 5, "limited": 1}
    clock.at = NOW + timedelta(seconds=30)
    assert client.get("/v1/me", headers=bearer(key)).status_code == 200


def test_over_the_day_limit_429_until_midnight(world, token):
    client, _, _, _, clock = world
    key = make_key(client, token)["key"]
    for minute in range(3):
        clock.at = NOW + timedelta(minutes=minute)
        for _ in range(4):
            assert client.get("/v1/me", headers=bearer(key)).status_code == 200
    clock.at = NOW + timedelta(minutes=5)
    r = client.get("/v1/me", headers=bearer(key))
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert "12 requests this day (UTC)" in r.json()["detail"]
    # another key of the same user has its own limits
    other = make_key(client, token, name="other")["key"]
    assert client.get("/v1/me", headers=bearer(other)).status_code == 200


def test_a_refused_route_still_counts_and_a_sign_in_is_never_limited(world, token):
    client, *_ = world
    key = make_key(client, token)["key"]
    for _ in range(5):
        client.get("/v1/views", headers=bearer(key))
    assert client.get("/v1/me", headers=bearer(key)).status_code == 429
    for _ in range(20):
        assert client.get("/v1/me", headers=token(ALICE)).status_code == 200


# --- never shown again, never logged ----------------------------------------------------------

def test_the_key_never_reaches_a_log_or_a_later_answer(world, token, caplog):
    client, keys, _, _, clock = world
    caplog.set_level(logging.DEBUG)
    made = make_key(client, token, scopes=("read", "write"))
    key = made["key"]
    secret_parts = [key, key[8:], key[SHOWN:SHOWN + 12], key_hash(key).hex(),
                    key_hash(key).hex()[:16]]
    answers = [
        client.get("/v1/me", headers=bearer(key)),
        client.get("/v1/api-keys", headers=token(ALICE)),
        client.get("/v1/views", headers=bearer(key)),                       # 403 route
        client.get("/v1/me", headers=bearer(other_key(key))),              # 401
    ]
    for _ in range(6):
        answers.append(client.get("/v1/me", headers=bearer(key)))          # some 429
    keys.use = lambda *a: (_ for _ in ()).throw(psycopg.OperationalError(f"down {key}"))
    answers.append(client.get("/v1/me", headers=bearer(key)))              # 503
    answers.append(client.delete(f"/v1/api-keys/{made['api_key']['key_id']}",
                                 headers=token(ALICE)))
    assert answers[-2].status_code == 503
    assert {a.status_code for a in answers} >= {200, 401, 403, 429, 503}
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "api key" in logged  # the events are logged, by key id
    for part in secret_parts:
        assert part not in logged
        for a in answers:
            assert part not in a.text and part not in str(a.headers)


def test_a_store_failure_is_a_503_not_a_pass(world, token):
    client, keys, *_ = world
    key = make_key(client, token)["key"]
    keys.use = lambda *a: (_ for _ in ()).throw(psycopg.OperationalError("down"))
    r = client.get("/v1/me", headers=bearer(key))
    assert r.status_code == 503 and r.json() == {"detail": "sign-in check unavailable"}


def test_without_an_app_database_a_key_is_just_an_invalid_token(token):
    client = TestClient(create_app(FakeAuthorizer({}), MetaStore(), SECRET))
    r = client.get("/v1/me", headers=bearer("trb_" + "A" * 43))
    assert (r.status_code, r.json()) == (401, {"detail": "invalid token"})
    assert client.get("/v1/api-keys", headers=token(ALICE)).status_code == 503


# --- the key routes' OpenAPI ----------------------------------------------------------------

def test_the_key_routes_openapi_is_generated_from_the_app(world):
    client, *_ = world
    r = client.get("/v1/openapi-keys.json")
    assert r.status_code == 200
    doc = r.json()
    listed = {(m.upper(), p): op["x-tributary-scope"] for p, ops in doc["paths"].items()
              for m, op in ops.items()}
    assert listed == {**KEY_ROUTES, **{r: "public" for r in PUBLIC_ROUTES}}
    full = client.app.openapi()
    fin = doc["paths"]["/v1/companies/{company_id}/financials"]["get"]
    assert fin["parameters"] == full["paths"]["/v1/companies/{company_id}/financials"]["get"][
        "parameters"]
    assert fin["security"] == [{"apiKey": []}]
    assert "security" not in doc["paths"]["/v1/metrics"]["get"]
    assert doc["components"]["securitySchemes"]["apiKey"]["scheme"] == "bearer"
    text = r.text
    for ref in {x.split('"')[0] for x in text.split('"$ref":"#/components/schemas/')[1:]}:
        assert ref in doc["components"]["schemas"], ref
    assert "/v1/api-keys" not in text and "/v1/alerts" not in text and "/v1/demo" not in text
    assert "60 requests a minute" not in doc["info"]["description"]  # this app's own limits
    assert "5 requests a minute and 12 a day" in doc["info"]["description"]
    assert doc["info"]["x-tributary-limits"] == {"per_minute": 5, "per_day": 12}
