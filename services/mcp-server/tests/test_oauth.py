"""Connected apps (OAuth) at the MCP server: a 401 that tells a client where to sign in (RFC 9728
`resource_metadata`), the protected resource metadata itself, an app's access token forwarded per
request like a key, an app's token refused at connect or mid-call answering 401 (so the client
refreshes or signs in again), and the token never in a log or an answer."""

import asyncio
import json
import logging

import httpx

from tributary_mcp.api import QueryApi
from tributary_mcp.app import OAuthResource, build_app
from tributary_mcp.auth import APP_REFUSED, NO_KEY, NOT_A_KEY, bearer
from tributary_mcp.main import oauth_from_env

ISSUER = "https://tributary.example"
OAUTH = OAuthResource(ISSUER, f"{ISSUER}/mcp")
METADATA = f"{ISSUER}/.well-known/oauth-protected-resource/mcp"
APP = "tro_" + "A" * 43
OTHER_APP = "tro_" + "B" * 43
KEY = "trb_" + "A" * 43
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "test", "version": "1"}}}
CALL = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "list_sectors", "arguments": {}}}


def app(h, **kw):
    return build_app(QueryApi(h.fake.client()), oauth=OAUTH, **kw)


def get(asgi, path, headers=None, method="GET"):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi),
                                     base_url="http://localhost:8002") as c:
            return await c.request(method, path, headers=headers or {})
    return asyncio.run(go())


def test_no_credential_is_a_401_that_says_where_to_sign_in(h):
    hdrs = h.headers(None)
    r = h.post([LIST], hdrs, app(h))[0]
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == (
        f'Bearer realm="Tributary", resource_metadata="{METADATA}", scope="read"')
    assert r.json()["error"]["message"] == NO_KEY
    assert h.fake.seen == []


def test_a_bad_credential_says_invalid_token_and_where_to_sign_in(h):
    r = h.post([LIST], h.headers(None, Authorization="Bearer tro_short"), app(h))[0]
    assert r.status_code == 401 and r.json()["error"]["message"] == NOT_A_KEY
    assert r.headers["www-authenticate"] == (
        f'Bearer realm="Tributary", error="invalid_token", resource_metadata="{METADATA}", '
        'scope="read"')
    # a refresh token is never sent here: refused before anything runs
    r = h.post([LIST], h.headers("trr_" + "A" * 43), app(h))[0]
    assert r.status_code == 401 and h.fake.seen == []


def test_the_protected_resource_metadata(h):
    a = app(h)
    for path in ("/.well-known/oauth-protected-resource/mcp",
                 "/.well-known/oauth-protected-resource"):
        r = get(a, path, {"Host": "mcp-server:8002"})  # as nginx sends it
        assert r.status_code == 200, path
        assert r.headers["access-control-allow-origin"] == "*"
        assert r.json() == {"resource": f"{ISSUER}/mcp", "authorization_servers": [ISSUER],
                            "scopes_supported": ["read"], "bearer_methods_supported": ["header"],
                            "resource_name": "Tributary",
                            "resource_documentation": f"{ISSUER}/#/api"}
    assert get(a, "/.well-known/oauth-protected-resource/mcp", method="OPTIONS") \
        .status_code == 204
    # without OAuth there is none, and the 401 names no metadata (keys only, as before)
    plain = build_app(QueryApi(h.fake.client()))
    assert get(plain, "/.well-known/oauth-protected-resource/mcp").status_code == 404
    r = h.post([LIST], h.headers(None), plain)[0]
    assert "resource_metadata" not in r.headers["www-authenticate"]


def test_an_apps_token_is_forwarded_per_request_like_a_key(h):
    h.fake.answer("/v1/sectors", {"sectors": []})
    a = app(h)
    replies = h.exchange([(CALL, h.headers(t)) for t in (APP, OTHER_APP, KEY)], a)
    assert [r.status_code for r in replies] == [200, 200, 200]
    assert [s.headers["authorization"] for s in h.fake.seen] == \
        [f"Bearer {APP}", f"Bearer {OTHER_APP}", f"Bearer {KEY}"]
    assert bearer({"authorization": f"bearer {APP}"}) == f"Bearer {APP}"


def test_an_apps_token_refused_at_connect_is_a_401_with_the_challenge(h):
    h.fake.answer("/v1/me", {"detail": "invalid token"}, status=401)
    r = h.post([INIT], h.headers(APP), app(h))[0]
    assert r.status_code == 401 and r.json()["error"]["message"] == APP_REFUSED
    assert f'resource_metadata="{METADATA}"' in r.headers["www-authenticate"]
    assert 'error="invalid_token"' in r.headers["www-authenticate"]


def test_an_apps_token_refused_mid_call_is_a_401_so_the_client_refreshes(h):
    h.fake.answer("/v1/sectors", {"detail": "invalid token"}, status=401)
    r = h.post([CALL], h.headers(APP), app(h))[0]
    assert r.status_code == 401
    assert r.json()["error"]["message"] == APP_REFUSED
    assert 'error="invalid_token"' in r.headers["www-authenticate"]
    assert f'resource_metadata="{METADATA}"' in r.headers["www-authenticate"]
    # a key refused mid-call stays a tool error the model reads (as before)
    r = h.post([CALL], h.headers(KEY), app(h))[0]
    assert r.status_code == 200 and r.json()["result"]["isError"] is True
    # another refusal (403) is a tool error for an app too
    h.fake.answer("/v1/sectors", {"detail": "not permitted"}, status=403)
    r = h.post([CALL], h.headers(APP), app(h))[0]
    assert r.status_code == 200 and r.json()["result"]["isError"] is True


def test_the_apps_token_never_reaches_a_log_or_an_answer(h, caplog):
    caplog.set_level(logging.DEBUG)
    h.fake.answer("/v1/sectors", {"detail": "invalid token"}, status=401)
    replies = h.post([INIT, LIST, CALL], h.headers(APP), app(h))
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records
                       if not r.name.startswith("httpx"))
    for part in (APP, APP[4:20], APP[:8]):
        assert part not in logged
        assert all(part not in r.text and part not in json.dumps(dict(r.headers))
                   for r in replies)


def test_oauth_from_env():
    assert oauth_from_env({}) is None
    o = oauth_from_env({"OAUTH_ISSUER": "https://tributary.lucasgroves.dev/"})
    assert o == OAuthResource("https://tributary.lucasgroves.dev",
                              "https://tributary.lucasgroves.dev/mcp")
    assert o.metadata_url == \
        "https://tributary.lucasgroves.dev/.well-known/oauth-protected-resource/mcp"
    try:
        oauth_from_env({"OAUTH_ISSUER": "http://tributary.example"})
    except ValueError:
        pass
    else:
        raise AssertionError("an http issuer off localhost is refused")
