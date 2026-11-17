"""Connected apps (query_api/oauth.py): the OAuth 2.1 authorization server for the MCP server,
through the routes, with an in-memory store that keeps AppOAuth's rules (test_oauth_db.py runs
the real one against the app database).

Adversarial cases first (an app's token is a new way in, so it gets every attack a key or a
token gets, plus OAuth's own): a code used twice (the grant and its tokens revoked), a code for
another client, a redirect URI one character off (exact match), a missing or wrong PKCE verifier,
plain PKCE, a token for another resource, a refresh token used twice (the family revoked), a
disconnected or revoked app's tokens, an app's token on an account route, the assistant or a
write, a demo sandbox connecting, open redirects (an unregistered redirect URI, an unknown
client), CSRF on the consent (no nonce cookie, another request's, a forged or expired request,
approving twice), registration with javascript:, data: or non-loopback http URIs, the codes,
tokens and secrets never in a log or a later answer, and one user's token never reading another
user's datasets, saved work or portfolio."""

import base64
import hashlib
import json
import logging
import secrets
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from query_api.api_keys import READ_ROUTES, Limits
from query_api.app import create_app
from query_api.client_metadata import DocumentRefused, Fetched
from query_api.oauth import (
    ACCESS_TTL,
    CODE_TTL,
    MAX_GRANTS,
    NONCE_COOKIE,
    NOT_FOR_APPS,
    REFRESH_TTL,
    REQUEST_TTL,
    AlreadyDecided,
    Client,
    Issued,
    OAuthConfig,
    Presented,
    TokenUse,
    TooManyGrants,
    canonical_resource,
    digest,
    pkce_ok,
    redirect_matches,
    redirect_ok,
)

from .conftest import SECRET
from .test_api import NVDA, TSLA, FakeAuthorizer
from .test_api_keys import FakeKeys, KeyStore
from .test_ui_api import FakeWatchlists
from .test_workspaces import CHART, FakeWorkspaces

NOW = datetime(2026, 11, 17, 9, 0, 30, tzinfo=UTC)
ALICE, BOB, SANDBOX = "alice", "bob", "demo-0123456789abcdef"
ISSUER = "https://tributary.example"
RESOURCE = f"{ISSUER}/mcp"
CLAUDE = "https://claude.ai/api/mcp/auth_callback"
DOC_URL = "https://claude.ai/oauth/mcp-oauth-client-metadata"
CONFIG = OAuthConfig(ISSUER, RESOURCE)


# --- the fakes -----------------------------------------------------------------------------------

class FakeOAuth:
    """AppOAuth's rules in memory: clients by id; grants, codes and tokens by hash, each spent
    once; a second presentation revokes the grant; tokens of a revoked grant are refused."""

    def __init__(self):
        self.clients: dict[str, Client] = {}
        self.grants: dict[str, dict] = {}
        self.codes: dict[bytes, dict] = {}
        self.tokens: dict[bytes, dict] = {}
        self.usage: dict[tuple[str, object], dict] = {}
        self.consents: set[bytes] = set()
        self.disabled: set[str] = set()

    def client(self, client_id):
        return self.clients.get(client_id)

    def register(self, client_id, name, client_uri, redirect_uris, auth_method, secret_hash, now):
        c = Client(str(uuid.uuid4()), client_id, "registered", name, client_uri,
                   tuple(redirect_uris), auth_method, secret_hash, now, now, None)
        self.clients[client_id] = c
        return c

    def save_document(self, url, name, client_uri, redirect_uris, now, fresh_until):
        old = self.clients.get(url)
        c = Client(old.client_key if old else str(uuid.uuid4()), url, "metadata_document", name,
                   client_uri, tuple(redirect_uris), "none", None,
                   old.created_at if old else now, now, fresh_until)
        self.clients[url] = c
        return c

    def live(self, grant_id):
        g = self.grants.get(grant_id)
        return g if g and g["revoked_at"] is None and g["user_id"] not in self.disabled else None

    def approve(self, user_id, client_key, resource, redirect_uri, code_challenge, code_hash,
                consent_hash, now):
        assert not user_id.startswith("demo-")  # the table's CHECK
        if consent_hash in self.consents:
            raise AlreadyDecided
        if sum(1 for g in self.grants.values()
               if g["user_id"] == user_id and g["revoked_at"] is None) >= MAX_GRANTS:
            raise TooManyGrants
        self.consents.add(consent_hash)
        grant_id = str(uuid.uuid4())
        self.grants[grant_id] = {"grant_id": grant_id, "user_id": user_id,
                                 "client_key": client_key, "resource": resource,
                                 "scopes": ("read",), "created_at": now, "revoked_at": None,
                                 "last_used_at": None}
        self.codes[code_hash] = {"grant_id": grant_id, "redirect_uri": redirect_uri,
                                 "code_challenge": code_challenge, "expires_at": now + CODE_TTL,
                                 "used": False}
        return grant_id

    def _issue(self, grant_id, tokens, now):
        g = self.grants[grant_id]
        self.tokens[digest(tokens.access)] = {"grant_id": grant_id, "kind": "access",
                                              "expires_at": now + ACCESS_TTL, "used": False,
                                              "user_id": g["user_id"]}
        self.tokens[digest(tokens.refresh)] = {"grant_id": grant_id, "kind": "refresh",
                                               "expires_at": now + REFRESH_TTL, "used": False,
                                               "user_id": g["user_id"]}

    def exchange_code(self, code_hash, now, check, tokens):
        code = self.codes.get(code_hash)
        if code is None:
            return Issued("invalid")
        g = self.grants[code["grant_id"]]
        if code["used"]:
            g["revoked_at"] = g["revoked_at"] or now
            return Issued("reused", g["grant_id"], g["user_id"])
        code["used"] = True
        if not self.live(g["grant_id"]) or code["expires_at"] <= now or not check(Presented(
                g["client_key"], g["resource"], g["scopes"], code["expires_at"],
                code["redirect_uri"], code["code_challenge"])):
            return Issued("invalid")
        self._issue(g["grant_id"], tokens, now)
        return Issued("ok", g["grant_id"], g["user_id"])

    def refresh(self, token_hash, now, check, tokens):
        t = self.tokens.get(token_hash)
        if t is None or t["kind"] != "refresh":
            return Issued("invalid")
        g = self.grants[t["grant_id"]]
        if t["used"]:
            g["revoked_at"] = g["revoked_at"] or now
            return Issued("reused", g["grant_id"], g["user_id"])
        t["used"] = True
        if not self.live(g["grant_id"]) or t["expires_at"] <= now or not check(Presented(
                g["client_key"], g["resource"], g["scopes"], t["expires_at"])):
            return Issued("invalid")
        self._issue(g["grant_id"], tokens, now)
        return Issued("ok", g["grant_id"], g["user_id"])

    def revoke(self, token_hash, client_key, now):
        t = self.tokens.get(token_hash)
        if t is None:
            return False
        g = self.grants[t["grant_id"]]
        if g["client_key"] != client_key or g["revoked_at"] is not None:
            return False
        g["revoked_at"] = now
        return True

    def use(self, token_hash, now, limits):
        t = self.tokens.get(token_hash)
        if t is None or t["kind"] != "access" or t["expires_at"] <= now:
            return None
        g = self.live(t["grant_id"])
        if g is None:
            return None
        minute = now.replace(second=0, microsecond=0)
        u = self.usage.setdefault((g["grant_id"], now.date()),
                                  {"requests": 0, "limited": 0, "minute": minute, "mreq": 0})
        if minute > u["minute"]:
            u["minute"], u["mreq"] = minute, 0
        outcome, retry = "ok", 0
        if u["requests"] >= limits.per_day:
            outcome, retry = "day", 3600
        elif u["mreq"] >= limits.per_minute:
            outcome, retry = "minute", 30
        if outcome == "ok":
            u["requests"] += 1
            u["mreq"] += 1
            g["last_used_at"] = now
        else:
            u["limited"] += 1
        name = next(c.name for c in self.clients.values() if c.client_key == g["client_key"])
        return TokenUse(g["grant_id"], g["user_id"], name, g["scopes"], g["resource"],
                        t["expires_at"], outcome, retry,
                        max(0, limits.per_minute - u["mreq"]),
                        max(0, limits.per_day - u["requests"]))

    def connections(self, user_id, now):
        out: dict[str, dict] = {}
        for g in self.grants.values():
            if g["user_id"] != user_id or not self.live(g["grant_id"]):
                continue
            if not any(t["grant_id"] == g["grant_id"] and t["kind"] == "refresh"
                       and not t["used"] and t["expires_at"] > now
                       for t in self.tokens.values()):
                continue
            c = next(c for c in self.clients.values() if c.client_key == g["client_key"])
            row = out.setdefault(c.client_key, {
                "client_key": c.client_key, "client_id": c.client_id, "kind": c.kind,
                "client_name": c.name, "redirect_uris": list(c.redirect_uris),
                "connected_at": g["created_at"], "last_used_at": g["last_used_at"]})
            row["connected_at"] = min(row["connected_at"], g["created_at"])
        return list(out.values())

    def disconnect(self, user_id, client_key, now):
        n = 0
        for g in self.grants.values():
            if g["user_id"] == user_id and g["client_key"] == client_key \
                    and g["revoked_at"] is None:
                g["revoked_at"] = now
                n += 1
        return n


class FakeDocs:
    """Client ID metadata documents by URL; counts fetches."""

    def __init__(self, docs=None, max_age=3600):
        self.docs = docs if docs is not None else {DOC_URL: {
            "client_id": DOC_URL, "client_name": "Claude", "redirect_uris": [CLAUDE],
            "token_endpoint_auth_method": "none"}}
        self.max_age = max_age
        self.fetched: list[str] = []

    def fetch(self, url):
        self.fetched.append(url)
        doc = self.docs.get(url)
        if doc is None:
            raise DocumentRefused("status")
        return Fetched(json.dumps(doc).encode(), self.max_age)


class FakeUserData:
    """Owner-only datasets (the real store filters on the user under forced RLS)."""

    def __init__(self, rows):
        self.rows = rows  # dataset_id -> {"user_id", ...}

    def all(self, user_id):
        return [{k: v for k, v in r.items() if k != "user_id"}
                for r in self.rows.values() if r["user_id"] == user_id]

    def get(self, user_id, dataset_id):
        r = self.rows.get(dataset_id)
        return {k: v for k, v in r.items() if k != "user_id"} \
            if r and r["user_id"] == user_id else None

    def rows_of(self, user_id, dataset_id):
        return self.get(user_id, dataset_id)

    def rows(self, user_id, dataset_id, *args):
        return [] if self.get(user_id, dataset_id) else None

    def catalog(self, user_id, names=None):
        return {}


class Clock:
    def __init__(self, at=NOW):
        self.at = at

    def __call__(self):
        return self.at


BOBS_DATASET = "5f0c6a3e-1b2d-4c3e-8f9a-0b1c2d3e4f50"


class World:
    def __init__(self, limits=None, docs=None, config=CONFIG):
        limits = limits or Limits(per_minute=30, per_day=500)
        self.oauth, self.docs, self.clock = FakeOAuth(), docs or FakeDocs(), Clock()
        self.keys, self.ws = FakeKeys(users=(ALICE, BOB, SANDBOX)), FakeWorkspaces()
        self.store = KeyStore()
        self.data = FakeUserData({BOBS_DATASET: {"user_id": BOB, "dataset_id": BOBS_DATASET,
                                                 "name": "bobs_numbers"}})
        authz = FakeAuthorizer({ALICE: {NVDA: {"financials", "filings"}},
                                BOB: {TSLA: {"financials", "filings"}},
                                SANDBOX: {NVDA: {"financials", "filings"}}})
        self.app = create_app(authz, self.store, SECRET, workspaces=self.ws,
                              watchlists=FakeWatchlists({ALICE: [NVDA], BOB: [TSLA]}),
                              clock=self.clock, api_keys=self.keys, api_key_limits=limits,
                              userdata=self.data, oauth=self.oauth, oauth_config=config,
                              client_documents=self.docs)
        self.client = TestClient(self.app, base_url="https://tributary.example")


@pytest.fixture
def w():
    return World()


# --- helpers -------------------------------------------------------------------------------------

def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()) \
        .rstrip(b"=").decode()
    return verifier, challenge


def bearer(value):
    return {"Authorization": f"Bearer {value}"}


def register(w, uris=(CLAUDE,), method="none", name="Claude", **extra):
    r = w.client.post("/oauth/register", json={
        "redirect_uris": list(uris), "client_name": name, "token_endpoint_auth_method": method,
        "grant_types": ["authorization_code", "refresh_token"], **extra})
    assert r.status_code == 201, r.text
    return r.json()


def authorize(w, client_id, redirect_uri=CLAUDE, challenge=None, state="state-1",
              resource=RESOURCE, method="S256", **extra):
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
              "code_challenge": challenge if challenge is not None else pkce()[1],
              "code_challenge_method": method, "state": state, "resource": resource,
              "scope": "read", **extra}
    return w.client.get("/oauth/authorize", params={k: v for k, v in params.items()
                                                    if v is not None},
                        follow_redirects=False)


def location(r):
    assert r.status_code == 302, r.text
    return r.headers["location"]


def query_of(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def fragment_query(url):
    assert url.startswith("/#/connect?"), url
    return {k: v[0] for k, v in parse_qs(url.split("?", 1)[1]).items()}


def nonce_of(r):
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{NONCE_COOKIE}="), cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Path=/api/query/v1/oauth/" in cookie
    return cookie.split(";")[0].split("=", 1)[1]


def started(w, client_id, **kw):
    """(sealed request, nonce cookie value) of an authorize that went to the consent page."""
    r = authorize(w, client_id, **kw)
    return fragment_query(location(r))["request"], nonce_of(r)


def consent_call(w, token, what, sealed, nonce, user=ALICE):
    headers = {**token(user)}
    if nonce is not None:
        headers["Cookie"] = f"{NONCE_COOKIE}={nonce}"
    suffix = "" if what == "describe" else f"/{what}"
    return w.client.post(f"/v1/oauth/consent{suffix}", json={"request": sealed},
                         headers=headers)


def approve(w, token, client_id, redirect_uri=CLAUDE, user=ALICE, **kw):
    """The code the app gets back after the user approves, and the PKCE verifier."""
    verifier, challenge = pkce()
    sealed, nonce = started(w, client_id, redirect_uri=redirect_uri, challenge=challenge, **kw)
    r = consent_call(w, token, "approve", sealed, nonce, user)
    assert r.status_code == 200, r.text
    back = r.json()["redirect"]
    assert back.startswith(redirect_uri + "?")
    q = query_of(back)
    assert q["iss"] == ISSUER and q["state"] == kw.get("state", "state-1")
    return q["code"], verifier


def form(w, data, auth=None):
    return w.client.post("/oauth/token", data=data, headers=auth or {})


def exchange(w, code, verifier, client_id, redirect_uri=CLAUDE, resource=RESOURCE, **extra):
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "code_verifier": verifier, "client_id": client_id, "resource": resource, **extra}
    return form(w, {k: v for k, v in data.items() if v is not None})


def connect(w, token, user=ALICE):
    """A public client registered, approved and exchanged: (client_id, token answer)."""
    client_id = register(w)["client_id"]
    code, verifier = approve(w, token, client_id, user=user)
    r = exchange(w, code, verifier, client_id)
    assert r.status_code == 200, r.text
    return client_id, r.json()


def refresh(w, refresh_token, client_id, **extra):
    return form(w, {"grant_type": "refresh_token", "refresh_token": refresh_token,
                    "client_id": client_id, **extra})


def error_of(r):
    assert r.status_code in (400, 401), r.text
    return r.json()["error"]


# --- the rules on their own ----------------------------------------------------------------------

@pytest.mark.parametrize("uri", [
    "https://claude.ai/api/mcp/auth_callback", "https://chatgpt.com/connector_platform_oauth_redirect",
    "http://localhost/callback", "http://127.0.0.1:33418/callback", "http://[::1]:8080/cb",
    "https://app.example/cb?x=1"])
def test_redirect_rule_takes_https_and_loopback_http(uri):
    assert redirect_ok(uri)


@pytest.mark.parametrize("uri", [
    "javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "http://evil.example/cb",
    "http://127.0.0.1.evil.example/cb", "https://evil.example/cb#frag",
    "https://user:pass@evil.example/cb", "cursor://callback", "ftp://example.com/cb",
    "//evil.example/cb", "/relative", "https://", "https://evil.example/cb with space",
    "https://evil.example\\@good.example/", "", "http://localhost.evil.example/cb",
    "https://evil.example:99999/cb", "http://[fe80::1%25eth0]/cb"])
def test_redirect_rule_refuses_everything_else(uri):
    assert not redirect_ok(uri)


def test_redirects_match_exactly_but_a_loopback_port_may_change():
    reg = ("https://claude.ai/api/mcp/auth_callback", "http://localhost/callback",
           "http://127.0.0.1/callback")
    assert redirect_matches(reg, "https://claude.ai/api/mcp/auth_callback")
    for near in ("https://claude.ai/api/mcp/auth_callback/", "https://Claude.ai/api/mcp/auth_callback",
                 "https://claude.ai/api/mcp/auth_callback?x", "https://claude.ai:443/api/mcp/auth_callback",
                 "https://claude.ai/api/mcp/auth_callbac", "https://claude.ai/api/mcp/auth_callback#x",
                 "http://localhost:4000/callback/", "http://localhost:4000/other",
                 "http://[::1]:4000/callback", "https://localhost:4000/callback"):
        assert redirect_matches(reg, near) is None, near
    assert redirect_matches(reg, "http://localhost:4000/callback")
    assert redirect_matches(reg, "http://127.0.0.1:51234/callback")


def test_pkce_is_s256_of_a_proper_verifier():
    v, c = pkce()
    assert pkce_ok(v, c)
    assert not pkce_ok(v + "x", c)
    assert not pkce_ok(c, c)  # plain: the challenge as its own verifier
    assert not pkce_ok("short", c) and not pkce_ok(None, c)


def test_resources_compare_canonically():
    assert canonical_resource("HTTPS://Tributary.Example:443/mcp/") == canonical_resource(RESOURCE)
    assert canonical_resource("https://tributary.example/mcp2") != canonical_resource(RESOURCE)
    assert canonical_resource("https://tributary.example/mcp#x") is None
    assert canonical_resource("tributary.example/mcp") is None


# --- metadata ------------------------------------------------------------------------------------

def test_metadata_says_what_claude_and_chatgpt_look_for(w):
    r = w.client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
    m = r.json()
    assert m["issuer"] == ISSUER  # exactly the protected resource metadata's entry
    assert m["code_challenge_methods_supported"] == ["S256"]  # no plain
    assert m["client_id_metadata_document_supported"] is True
    assert "none" in m["token_endpoint_auth_methods_supported"]
    assert m["authorization_response_iss_parameter_supported"] is True
    assert m["registration_endpoint"] == f"{ISSUER}/oauth/register"
    assert m["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert m["scopes_supported"] == ["read"]
    assert w.client.options("/oauth/token").status_code == 204


def test_off_without_an_issuer_every_route_is_404_and_a_token_is_refused():
    w = World(config=None)
    for method, path in (("GET", "/.well-known/oauth-authorization-server"),
                         ("GET", "/oauth/authorize"), ("POST", "/oauth/token"),
                         ("POST", "/oauth/register"), ("POST", "/oauth/revoke")):
        assert w.client.request(method, path).status_code == 404, path
    r = w.client.get("/v1/me", headers=bearer("tro_" + "A" * 43))
    assert r.status_code == 401 and r.json() == {"detail": "invalid token"}


# --- the whole flow ------------------------------------------------------------------------------

def test_a_connected_app_reads_as_its_user_and_nobody_else(w, token):
    _, t = connect(w, token)
    assert t["token_type"] == "Bearer" and t["expires_in"] == 3600 and t["scope"] == "read"
    assert t["access_token"].startswith("tro_") and t["refresh_token"].startswith("trr_")
    me = w.client.get("/v1/me", headers=bearer(t["access_token"]))
    assert me.status_code == 200
    body = me.json()
    assert body["user_id"] == ALICE and body["connected_app"]["name"] == "Claude"
    assert "api_key" not in body
    assert me.headers["X-RateLimit-Limit-Minute"] == "30"
    assert w.client.get(f"/v1/companies/{NVDA}/financials",
                        headers=bearer(t["access_token"])).status_code == 200
    # bob's company: layer 1 refuses, as for alice's own sign-in
    assert w.client.get(f"/v1/companies/{TSLA}/financials",
                        headers=bearer(t["access_token"])).status_code == 403
    # the user named anywhere else changes nothing
    for extra in ({"params": {"user_id": BOB}},
                  {"headers": {**bearer(t["access_token"]), "X-User-Id": BOB}}):
        r = w.client.get("/v1/me", **{"headers": bearer(t["access_token"]), **extra})
        assert r.json()["user_id"] == ALICE


def test_one_users_token_never_reads_another_users_datasets_work_or_portfolio(w, token):
    ws = w.client.post("/v1/workspaces", json={"name": "Bob's"}, headers=token(BOB)).json()
    item = w.client.post(f"/v1/workspaces/{ws['workspace_id']}/items",
                         json={"kind": "chart", "name": "Rev", "state": CHART},
                         headers=token(BOB)).json()
    _, t = connect(w, token)
    h = bearer(t["access_token"])
    assert w.client.get(f"/v1/workspace-items/{item['item_id']}", headers=h).status_code == 404
    assert w.client.get(f"/v1/workspaces/{ws['workspace_id']}", headers=h).status_code == 404
    assert w.client.get(f"/v1/datasets/{BOBS_DATASET}", headers=h).status_code == 404
    assert w.client.get("/v1/datasets", headers=h).json()["datasets"] == []
    # bob's own token reads his
    _, tb = connect(w, token, user=BOB)
    assert w.client.get(f"/v1/datasets/{BOBS_DATASET}",
                        headers=bearer(tb["access_token"])).status_code == 200
    # the portfolio is never an app's, not even the user's own
    for path in ("/v1/portfolio", f"/v1/portfolio/position/{NVDA}", "/v1/portfolio/journal"):
        r = w.client.get(path, headers=h)
        assert r.status_code == 403 and r.json() == {"detail": NOT_FOR_APPS}, path


# --- codes ---------------------------------------------------------------------------------------

def test_a_code_works_once_and_a_second_use_revokes_what_it_gave(w, token):
    client_id = register(w)["client_id"]
    code, verifier = approve(w, token, client_id)
    first = exchange(w, code, verifier, client_id)
    assert first.status_code == 200
    again = exchange(w, code, verifier, client_id)
    assert error_of(again) == "invalid_grant"
    t = first.json()
    assert w.client.get("/v1/me", headers=bearer(t["access_token"])).status_code == 401
    assert error_of(refresh(w, t["refresh_token"], client_id)) == "invalid_grant"


def test_a_code_for_another_client_is_refused_and_spent(w, token):
    a = register(w)["client_id"]
    b = register(w, name="Evil")["client_id"]
    code, verifier = approve(w, token, a)
    assert error_of(exchange(w, code, verifier, b)) == "invalid_grant"
    # spent: the right client can't use it after that either
    assert error_of(exchange(w, code, verifier, a)) == "invalid_grant"


def test_a_code_needs_its_exact_redirect_uri(w, token):
    client_id = register(w, uris=(CLAUDE, "https://claude.ai/other"))["client_id"]
    code, verifier = approve(w, token, client_id)
    assert error_of(exchange(w, code, verifier, client_id,
                             redirect_uri="https://claude.ai/other")) == "invalid_grant"
    code, verifier = approve(w, token, client_id)
    assert error_of(exchange(w, code, verifier, client_id,
                             redirect_uri=CLAUDE + "/")) == "invalid_grant"


def test_a_code_needs_its_pkce_verifier(w, token):
    client_id = register(w)["client_id"]
    code, verifier = approve(w, token, client_id)
    assert error_of(exchange(w, code, None, client_id)) == "invalid_request"
    other, _ = pkce()
    assert error_of(exchange(w, code, other, client_id)) == "invalid_grant"
    assert error_of(exchange(w, code, verifier, client_id)) == "invalid_grant"  # spent


def test_a_code_expires_after_60_seconds(w, token):
    client_id = register(w)["client_id"]
    code, verifier = approve(w, token, client_id)
    w.clock.at = NOW + CODE_TTL + timedelta(seconds=1)
    assert error_of(exchange(w, code, verifier, client_id)) == "invalid_grant"


@pytest.mark.parametrize("method, challenge", [
    ("plain", None), (None, None), ("S256", ""), ("S256", "too-short"), ("s256", None)])
def test_authorize_refuses_plain_or_missing_pkce(w, method, challenge):
    client_id = register(w)["client_id"]
    _, good = pkce()
    r = authorize(w, client_id, method=method, challenge=good if challenge is None else challenge)
    back = location(r)
    assert back.startswith(CLAUDE + "?")
    q = query_of(back)
    assert q["error"] == "invalid_request" and q["state"] == "state-1" and q["iss"] == ISSUER
    assert "set-cookie" not in r.headers


def test_authorize_needs_response_type_code(w):
    client_id = register(w)["client_id"]
    q = query_of(location(authorize(w, client_id, response_type="token")))
    assert q["error"] == "unsupported_response_type" and q["iss"] == ISSUER


# --- resources -----------------------------------------------------------------------------------

def test_tokens_are_only_for_this_mcp_server(w, token):
    client_id = register(w)["client_id"]
    q = query_of(location(authorize(w, client_id, resource="https://evil.example/mcp")))
    assert q["error"] == "invalid_target" and q["state"] == "state-1"
    q = query_of(location(authorize(w, client_id, resource=f"{ISSUER}/other")))
    assert q["error"] == "invalid_target"
    # the same resource spelled otherwise is the same resource
    code, verifier = approve(w, token, client_id, resource="HTTPS://TRIBUTARY.EXAMPLE/mcp/")
    assert error_of(exchange(w, code, verifier, client_id,
                             resource="https://evil.example/mcp")) == "invalid_target"
    code, verifier = approve(w, token, client_id, resource=None)  # absent: this server
    assert exchange(w, code, verifier, client_id, resource=None).status_code == 200


def test_a_token_for_another_resource_is_refused_where_it_is_used(w, token):
    _, t = connect(w, token)
    for g in w.oauth.grants.values():
        g["resource"] = "https://other.example/mcp"
    assert w.client.get("/v1/me", headers=bearer(t["access_token"])).status_code == 401


# --- refresh tokens ------------------------------------------------------------------------------

def test_refresh_rotates_and_a_reused_refresh_token_revokes_the_family(w, token):
    client_id, t = connect(w, token)
    w.clock.at = NOW + timedelta(minutes=50)
    r = refresh(w, t["refresh_token"], client_id)
    assert r.status_code == 200, r.text
    t2 = r.json()
    assert t2["refresh_token"] != t["refresh_token"] and t2["access_token"] != t["access_token"]
    assert w.client.get("/v1/me", headers=bearer(t2["access_token"])).status_code == 200
    # the old one again: someone else has a copy, so everything from this approval ends
    assert error_of(refresh(w, t["refresh_token"], client_id)) == "invalid_grant"
    assert w.client.get("/v1/me", headers=bearer(t2["access_token"])).status_code == 401
    assert w.client.get("/v1/me", headers=bearer(t["access_token"])).status_code == 401
    assert error_of(refresh(w, t2["refresh_token"], client_id)) == "invalid_grant"


def test_a_refresh_token_is_its_clients_only(w, token):
    client_id, t = connect(w, token)
    other = register(w, name="Other")["client_id"]
    assert error_of(refresh(w, t["refresh_token"], other)) == "invalid_grant"
    # spent by that attempt: the real client presenting it now looks like a stolen copy
    assert error_of(refresh(w, t["refresh_token"], client_id)) == "invalid_grant"
    assert w.client.get("/v1/me", headers=bearer(t["access_token"])).status_code == 401


def test_access_tokens_expire_after_an_hour_refresh_tokens_after_30_days(w, token):
    client_id, t = connect(w, token)
    w.clock.at = NOW + ACCESS_TTL + timedelta(seconds=1)
    assert w.client.get("/v1/me", headers=bearer(t["access_token"])).status_code == 401
    w.clock.at = NOW + REFRESH_TTL + timedelta(seconds=1)
    assert error_of(refresh(w, t["refresh_token"], client_id)) == "invalid_grant"


def test_a_refresh_token_is_not_an_access_token(w, token):
    _, t = connect(w, token)
    assert w.client.get("/v1/me", headers=bearer(t["refresh_token"])).status_code == 401


# --- revocation and Settings ---------------------------------------------------------------------

def test_disconnecting_in_settings_ends_every_token_at_once(w, token):
    client_id, t = connect(w, token)
    listed = w.client.get("/v1/oauth/connections", headers=token(ALICE)).json()
    assert listed["allowed"] is True
    [c] = listed["connections"]
    assert c["name"] == "Claude" and c["redirect_hosts"] == ["claude.ai"]
    assert c["connected_at"] and set(c) == {"connection_id", "name", "kind", "publisher",
                                            "redirect_hosts", "connected_at", "last_used_at"}
    # bob can't see or disconnect it: the same 404 as none
    assert w.client.get("/v1/oauth/connections", headers=token(BOB)).json()["connections"] == []
    assert w.client.delete(f"/v1/oauth/connections/{c['connection_id']}",
                           headers=token(BOB)).status_code == 404
    r = w.client.delete(f"/v1/oauth/connections/{c['connection_id']}", headers=token(ALICE))
    assert r.status_code == 200 and r.json()["disconnected"] is True
    assert w.client.get("/v1/me", headers=bearer(t["access_token"])).status_code == 401
    assert error_of(refresh(w, t["refresh_token"], client_id)) == "invalid_grant"
    assert w.client.get("/v1/oauth/connections",
                        headers=token(ALICE)).json()["connections"] == []
    assert w.client.delete(f"/v1/oauth/connections/{c['connection_id']}",
                           headers=token(ALICE)).status_code == 404


def test_the_revocation_endpoint_ends_the_grant_for_its_own_client_only(w, token):
    client_id, t = connect(w, token)
    other = register(w, name="Other")["client_id"]
    r = w.client.post("/oauth/revoke", data={"token": t["refresh_token"], "client_id": other})
    assert r.status_code == 200  # RFC 7009: no answer that says whose it is
    assert w.client.get("/v1/me", headers=bearer(t["access_token"])).status_code == 200
    r = w.client.post("/oauth/revoke", data={"token": t["access_token"], "client_id": client_id})
    assert r.status_code == 200
    assert w.client.get("/v1/me", headers=bearer(t["access_token"])).status_code == 401
    assert error_of(refresh(w, t["refresh_token"], client_id)) == "invalid_grant"
    unknown = w.client.post("/oauth/revoke", data={"token": "tro_" + "B" * 43,
                                                   "client_id": client_id})
    assert unknown.status_code == 200


# --- what an app's token can't do ----------------------------------------------------------------

ACCOUNT = [("GET", "/v1/api-keys"), ("POST", "/v1/api-keys"), ("GET", "/v1/alerts/settings"),
           ("GET", "/v1/oauth/connections"), ("POST", "/v1/oauth/consent"),
           ("POST", "/v1/oauth/consent/approve"), ("GET", "/v1/portfolio"),
           ("PUT", f"/v1/watchlist/{NVDA}"), ("POST", "/v1/workspaces"),
           ("POST", "/v1/demo/agent-quota"), ("GET", "/v1/connections")]


@pytest.mark.parametrize("method, path", ACCOUNT)
def test_an_apps_token_never_manages_the_account_or_writes(w, token, method, path):
    _, t = connect(w, token)
    r = w.client.request(method, path, json={"request": "x", "name": "x"},
                         headers=bearer(t["access_token"]))
    assert r.status_code in (403, 404), (path, r.text)
    if r.status_code == 403:
        assert r.json() == {"detail": NOT_FOR_APPS}


def every_signed_in_route(app):
    for r in app.routes:
        if isinstance(r, APIRoute) and any(
                getattr(d.call, "__name__", "") == "current_user"
                for d in r.dependant.dependencies):
            for m in r.methods:
                yield m, r.path


def test_every_route_but_the_read_routes_refuses_an_apps_token(token):
    """Default deny: a route that isn't a read route (one added later, too) refuses an app's
    token before it runs."""
    w = World(limits=Limits(per_minute=100_000, per_day=1_000_000))
    _, t = connect(w, token)
    routes = set(every_signed_in_route(w.app))
    assert READ_ROUTES <= routes
    for method, path in sorted(routes - READ_ROUTES):
        url = path.replace("{company_id}", NVDA).replace("{", "").replace("}", "")
        r = w.client.request(method, url, headers=bearer(t["access_token"]))
        assert r.status_code == 403, (method, path, r.text)


def test_keys_and_apps_tokens_cant_give_consent_or_list_connections(w, token):
    key = w.client.post("/v1/api-keys", json={"name": "s", "scopes": ["read", "write"],
                                              "expires_in_days": 30},
                        headers=token(ALICE)).json()["key"]
    client_id = register(w)["client_id"]
    sealed, nonce = started(w, client_id)
    r = w.client.post("/v1/oauth/consent/approve", json={"request": sealed},
                      headers={**bearer(key), "Cookie": f"{NONCE_COOKIE}={nonce}"})
    assert r.status_code == 403
    assert w.client.get("/v1/oauth/connections", headers=bearer(key)).status_code == 403
    assert not w.oauth.grants


def test_over_the_limit_429_with_retry_after(token):
    w = World(limits=Limits(per_minute=5, per_day=50))
    _, t = connect(w, token)
    for _ in range(5):
        assert w.client.get("/v1/me", headers=bearer(t["access_token"])).status_code == 200
    r = w.client.get("/v1/me", headers=bearer(t["access_token"]))
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


# --- consent: who, CSRF, sandboxes ---------------------------------------------------------------

def test_consent_shows_the_app_its_redirect_host_and_what_it_may_do(w, token):
    client_id = register(w)["client_id"]
    sealed, nonce = started(w, client_id)
    r = consent_call(w, token, "describe", sealed, nonce)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["allowed"] is True and body["scopes"] == ["read"]
    c = body["client"]
    assert c["name"] == "Claude" and c["kind"] == "registered" and c["publisher"] is None
    assert c["redirect_host"] == "claude.ai" and c["loopback"] is False
    assert not w.oauth.grants  # describing stores nothing


def test_a_loopback_app_is_flagged(w, token):
    client_id = register(w, uris=("http://127.0.0.1/callback",), name="Desktop")["client_id"]
    sealed, nonce = started(w, client_id, redirect_uri="http://127.0.0.1:5123/callback")
    c = consent_call(w, token, "describe", sealed, nonce).json()["client"]
    assert c["loopback"] is True and c["redirect_host"] == "127.0.0.1"


@pytest.mark.parametrize("case", ["no_cookie", "other_nonce", "forged", "expired", "twice"])
def test_consent_is_csrf_proof(w, token, case):
    client_id = register(w)["client_id"]
    sealed, nonce = started(w, client_id)
    other_sealed, other_nonce = started(w, client_id, state="other")
    if case == "no_cookie":
        r = consent_call(w, token, "approve", sealed, None)
    elif case == "other_nonce":  # a link to someone else's request, opened in this browser
        r = consent_call(w, token, "approve", sealed, other_nonce)
    elif case == "forged":
        body, sig = sealed.split(".")
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        payload["r"] = "https://evil.example/cb"
        forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        r = consent_call(w, token, "approve", f"{forged}.{sig}", nonce)
    elif case == "expired":
        w.clock.at = NOW + timedelta(seconds=REQUEST_TTL + 1)
        r = consent_call(w, token, "approve", sealed, nonce)
    else:
        assert consent_call(w, token, "approve", sealed, nonce).status_code == 200
        r = consent_call(w, token, "approve", sealed, nonce)
        assert len(w.oauth.grants) == 1
    assert r.status_code == 400, r.text
    assert "expired or was started in another browser" in r.json()["detail"]
    if case != "twice":
        assert not w.oauth.grants


def test_consent_needs_a_sign_in(w):
    client_id = register(w)["client_id"]
    sealed, nonce = started(w, client_id)
    r = w.client.post("/v1/oauth/consent/approve", json={"request": sealed},
                      headers={"Cookie": f"{NONCE_COOKIE}={nonce}"})
    assert r.status_code == 401 and not w.oauth.grants


def test_a_demo_sandbox_cant_connect_an_app(w, token):
    client_id = register(w)["client_id"]
    sealed, nonce = started(w, client_id)
    assert consent_call(w, token, "describe", sealed, nonce,
                        user=SANDBOX).json()["allowed"] is False
    r = consent_call(w, token, "approve", sealed, nonce, user=SANDBOX)
    assert r.status_code == 403 and "Demo sessions can't connect apps" in r.json()["detail"]
    assert not w.oauth.grants
    assert w.client.get("/v1/oauth/connections", headers=token(SANDBOX)).json() == {
        "allowed": False, "connections": []}
    # saying no is fine: the app hears access_denied
    r = consent_call(w, token, "deny", sealed, nonce, user=SANDBOX)
    q = query_of(r.json()["redirect"])
    assert q["error"] == "access_denied" and q["iss"] == ISSUER and q["state"] == "state-1"


def test_deny_sends_access_denied_with_state_and_iss(w, token):
    client_id = register(w)["client_id"]
    sealed, nonce = started(w, client_id)
    r = consent_call(w, token, "deny", sealed, nonce)
    back = r.json()["redirect"]
    assert back.startswith(CLAUDE + "?") and query_of(back)["error"] == "access_denied"
    assert not w.oauth.grants


def test_at_most_max_grants_live_connections(w, token):
    client_id = register(w)["client_id"]
    for _ in range(MAX_GRANTS):
        approve(w, token, client_id)
    sealed, nonce = started(w, client_id)
    r = consent_call(w, token, "approve", sealed, nonce)
    assert r.status_code == 409


# --- open redirects ------------------------------------------------------------------------------

@pytest.mark.parametrize("redirect", [
    "https://evil.example/cb", CLAUDE + "/", CLAUDE + "?next=https://evil.example",
    "https://claude.ai.evil.example/api/mcp/auth_callback", "javascript:alert(1)", None])
def test_an_unregistered_redirect_uri_is_never_followed(w, redirect):
    client_id = register(w)["client_id"]
    r = authorize(w, client_id, redirect_uri=redirect)
    assert location(r) == "/#/connect?error=redirect_uri"
    assert "set-cookie" not in r.headers


@pytest.mark.parametrize("client_id", [
    "trc_" + "A" * 32, "https://evil.example/client.json?x=1#y", "javascript:alert(1)", "",
    "http://claude.ai/oauth/doc", "https://127.0.0.1/doc", "https://localhost/doc",
    "https://intranet.svc.cluster.local/doc", "https://claude.ai/"])
def test_an_unknown_or_unfetchable_client_is_shown_on_tributarys_own_page(w, client_id):
    r = authorize(w, client_id, redirect_uri="https://evil.example/cb")
    assert location(r) == "/#/connect?error=client"
    assert w.docs.fetched in ([], ["https://evil.example/client.json?x=1#y"]) \
        and not any(h in u for u in w.docs.fetched for h in ("127.0.0.1", "localhost", ".svc"))


# --- client ID metadata documents ----------------------------------------------------------------

def test_a_metadata_document_client_connects_and_is_fetched_once(w, token):
    code, verifier = approve(w, token, DOC_URL)
    assert w.docs.fetched == [DOC_URL]
    assert exchange(w, code, verifier, DOC_URL).status_code == 200
    sealed, nonce = started(w, DOC_URL)
    c = consent_call(w, token, "describe", sealed, nonce).json()["client"]
    assert c["publisher"] == "claude.ai" and c["kind"] == "metadata_document"
    assert w.docs.fetched == [DOC_URL]  # cached for its max-age
    w.clock.at = NOW + timedelta(hours=2)
    started(w, DOC_URL)
    assert w.docs.fetched == [DOC_URL, DOC_URL]


@pytest.mark.parametrize("doc", [
    {"client_id": "https://claude.ai/other", "client_name": "Claude", "redirect_uris": [CLAUDE]},
    {"client_id": DOC_URL, "client_name": "Claude", "redirect_uris": ["javascript:alert(1)"]},
    {"client_id": DOC_URL, "client_name": "Claude", "redirect_uris": []},
    {"client_id": DOC_URL, "client_name": "Claude"},
    ["not", "an", "object"]])
def test_a_bad_metadata_document_is_refused(doc, token):
    w = World(docs=FakeDocs({DOC_URL: doc}))
    assert location(authorize(w, DOC_URL)) == "/#/connect?error=client"
    assert DOC_URL not in w.oauth.clients


def test_a_metadata_documents_redirect_uris_are_exact(w):
    assert location(authorize(w, DOC_URL, redirect_uri="https://evil.example/cb")) \
        == "/#/connect?error=redirect_uri"


def test_a_metadata_document_client_cant_be_registered_over(w):
    r = w.client.post("/oauth/register", json={"redirect_uris": ["https://evil.example/cb"],
                                               "client_id": DOC_URL, "client_name": "Claude"})
    assert r.status_code == 201 and r.json()["client_id"].startswith("trc_")


# --- registration --------------------------------------------------------------------------------

@pytest.mark.parametrize("uris", [
    ["javascript:alert(1)"], ["data:text/html,hi"], ["http://evil.example/cb"],
    ["https://evil.example/cb#x"], ["myapp://callback"], [], ["https://ok.example/cb"] * 11,
    "https://ok.example/cb", [42], ["https://ok.example/cb", "http://evil.example/cb"]])
def test_registration_refuses_unsafe_redirect_uris(w, uris):
    r = w.client.post("/oauth/register", json={"redirect_uris": uris, "client_name": "x",
                                               "token_endpoint_auth_method": "none"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"
    assert not w.oauth.clients


@pytest.mark.parametrize("body", [
    "not json", "[]", '{"redirect_uris": ["https://a.example/cb"], "grant_types": ["implicit"]}',
    '{"redirect_uris": ["https://a.example/cb"], "response_types": ["token"]}',
    '{"redirect_uris": ["https://a.example/cb"], "token_endpoint_auth_method": "private_key_jwt"}',
    '{"redirect_uris": ["https://a.example/cb"], "client_uri": "javascript:alert(1)"}'])
def test_registration_refuses_bad_metadata(w, body):
    r = w.client.post("/oauth/register", content=body,
                      headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_client_metadata"


def test_registration_takes_loopback_http_and_cleans_the_name(w):
    out = register(w, uris=("http://localhost/callback", "http://127.0.0.1/callback",
                            "http://[::1]/callback"), name="Claude‮\x00 Code")
    assert out["client_name"] == "Claude Code" and "client_secret" not in out
    assert out["client_id"].startswith("trc_")


def test_registration_is_rate_limited_per_address(w):
    for _ in range(30):
        register(w)
    r = w.client.post("/oauth/register", json={"redirect_uris": [CLAUDE]})
    assert r.status_code == 429 and r.json()["error"] == "temporarily_unavailable"
    other = w.client.post("/oauth/register", json={"redirect_uris": [CLAUDE]},
                          headers={"X-Client-IP": "203.0.113.9"})
    assert other.status_code == 201


def test_a_confidential_client_needs_its_secret(w, token):
    reg = register(w, method="client_secret_basic")
    client_id, secret = reg["client_id"], reg["client_secret"]
    assert secret.startswith("trs_") and reg["client_secret_expires_at"] == 0
    code, verifier = approve(w, token, client_id)
    no_secret = exchange(w, code, verifier, client_id)
    assert no_secret.status_code == 401 and no_secret.json()["error"] == "invalid_client"
    basic = base64.b64encode(f"{client_id}:{secret[:-1]}x".encode()).decode()
    r = form(w, {"grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE,
                 "code_verifier": verifier}, {"Authorization": f"Basic {basic}"})
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    r = form(w, {"grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE,
                 "code_verifier": verifier, "resource": RESOURCE},
             {"Authorization": f"Basic {basic}"})
    assert r.status_code == 200, r.text
    # the secret in the body works too
    code, verifier = approve(w, token, client_id)
    assert exchange(w, code, verifier, client_id, client_secret=secret).status_code == 200


def test_the_token_endpoint_reads_only_a_form(w, token):
    client_id = register(w)["client_id"]
    code, verifier = approve(w, token, client_id)
    r = w.client.post("/oauth/token", json={"grant_type": "authorization_code", "code": code,
                                            "redirect_uri": CLAUDE, "code_verifier": verifier,
                                            "client_id": client_id})
    assert error_of(r) == "invalid_request"
    r = w.client.post("/oauth/token", content=f"grant_type=authorization_code&code={code}"
                      f"&code={code}&client_id={client_id}",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert error_of(r) == "invalid_request"  # a repeated parameter
    assert error_of(form(w, {"grant_type": "password", "client_id": client_id})) \
        == "unsupported_grant_type"
    assert error_of(form(w, {"grant_type": "authorization_code", "code": code,
                             "redirect_uri": CLAUDE, "code_verifier": verifier,
                             "client_id": "trc_" + "Z" * 32})) == "invalid_client"


# --- nothing secret is kept or said again --------------------------------------------------------

def test_codes_tokens_and_secrets_never_reach_a_log_or_a_later_answer(w, token, caplog):
    caplog.set_level(logging.DEBUG)
    reg = register(w, method="client_secret_post")
    client_id, secret = reg["client_id"], reg["client_secret"]
    verifier, challenge = pkce()
    sealed, nonce = started(w, client_id, challenge=challenge, state="private-state-xyz")
    approved = consent_call(w, token, "approve", sealed, nonce).json()["redirect"]
    code = query_of(approved)["code"]
    t = exchange(w, code, verifier, client_id, client_secret=secret).json()
    t2 = refresh(w, t["refresh_token"], client_id, client_secret=secret).json()
    later = [w.client.get("/v1/oauth/connections", headers=token(ALICE)).text,
             w.client.get("/v1/me", headers=bearer(t2["access_token"])).text,
             w.client.get("/.well-known/oauth-authorization-server").text]
    secrets_ = [secret, code, verifier, t["access_token"], t["refresh_token"],
                t2["access_token"], t2["refresh_token"], nonce, "private-state-xyz"]
    # the app's own log lines (the test client's httpx logs the URLs it requests: not ours)
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records
                       if not r.name.startswith("httpx"))
    for s in secrets_:
        for part in (s, s[8:20]):
            assert part not in logged, part
            assert all(part not in text for text in later), part
    # only hashes are kept
    stored = repr(w.oauth.__dict__)
    for s in (secret, code, t["access_token"], t["refresh_token"]):
        assert s not in stored
    assert w.oauth.clients[client_id].secret_hash == digest(secret)


def test_dates_and_ids_only_in_the_connections_answer(w, token):
    connect(w, token)
    [c] = w.client.get("/v1/oauth/connections", headers=token(ALICE)).json()["connections"]
    assert "client_id" not in c and "redirect_uris" not in c
    uuid.UUID(c["connection_id"])


def test_a_client_changed_after_approval_cant_use_the_code(w, token):
    client_id = register(w)["client_id"]
    sealed, nonce = started(w, client_id)
    c = w.oauth.clients[client_id]
    w.oauth.clients[client_id] = replace(c, redirect_uris=("https://elsewhere.example/cb",))
    assert consent_call(w, token, "approve", sealed, nonce).status_code == 400


def test_nginx_serves_the_authorization_server_and_resource_metadata_at_the_sites_root():
    """MCP clients look for the metadata at the site's root (the issuer has no path): nginx routes
    them to the Query API and the MCP server, with the client address the limits count."""
    conf = (Path(__file__).parents[2] / "web/deploy/nginx.conf").read_text()
    meta = conf.split("location = /.well-known/oauth-authorization-server {", 1)[1].split("}", 1)[0]
    assert "proxy_pass http://query-api:8000/.well-known/oauth-authorization-server;" in meta
    oauth = conf.split("location /oauth/ {", 1)[1].split("}", 1)[0]
    assert "proxy_pass http://query-api:8000/oauth/;" in oauth
    assert "proxy_set_header X-Client-IP $client_ip;" in oauth
    prm = conf.split(r"location ~ ^/\.well-known/oauth-protected-resource(/mcp)?$ {", 1)[1]
    assert prm.split("}", 1)[0].strip().startswith("proxy_pass http://mcp-server:8002;")
    assert "location /.well-known/ {\n        return 404;" in conf


def test_the_longest_request_still_fits_the_consent_page(w, token):
    """Two 2,048-character URLs and a 1,024-character state: the sealed request still matches
    the pattern the web app and the consent routes take."""
    long_cb = "https://claude.ai/" + "c" * (2048 - len("https://claude.ai/"))
    client_id = register(w, uris=(long_cb,))["client_id"]
    sealed, nonce = started(w, client_id, redirect_uri=long_cb, state="s" * 1024)
    assert len(sealed) < 8100
    r = consent_call(w, token, "approve", sealed, nonce)
    assert r.status_code == 200, r.text
    assert query_of(r.json()["redirect"])["state"] == "s" * 1024
