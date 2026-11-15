"""The key on an MCP request: refused before any MCP handling when missing or not a Tributary
key, checked with the Query API at initialize, forwarded per request, never kept."""

import httpx
import pytest

from tributary_mcp.auth import NO_KEY, NOT_A_KEY, REFUSED, bearer

KEY = "trb_" + "A" * 43
OTHER_KEY = "trb_" + "B" * 43
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "test", "version": "1"}}}


@pytest.mark.parametrize(("authorization", "message"), [
    (None, NO_KEY),
    ("", NO_KEY),
    ("Bearer", NOT_A_KEY),
    ("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2ln", NOT_A_KEY),  # a sign-in JWT
    (f"Basic {KEY}", NOT_A_KEY),
    ("Bearer trb_short", NOT_A_KEY),
    (f"Bearer {KEY}x", NOT_A_KEY),
    ("Bearer trb_" + "!" * 43, NOT_A_KEY),
])
def test_no_usable_key_is_a_clear_401_before_anything_runs(h, authorization, message):
    hdrs = h.headers(None)
    if authorization is not None:
        hdrs["Authorization"] = authorization
    r = h.post([LIST], hdrs)[0]
    assert r.status_code == 401
    assert r.json() == {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32001, "message": message}}
    assert r.headers["www-authenticate"].startswith("Bearer realm=\"Tributary\"")
    assert r.headers["cache-control"] == "no-store"
    assert h.fake.seen == []
    if authorization:
        assert authorization.split(" ", 1)[-1] not in r.text or authorization == "Bearer"


def test_the_scheme_is_case_insensitive_and_the_key_goes_on_as_the_same_credential(h):
    assert bearer({"authorization": f"bearer {KEY}"}) == f"Bearer {KEY}"
    assert bearer({"authorization": f"  Bearer   {KEY}  "}) == f"Bearer {KEY}"
    assert bearer({"authorization": f"Token {KEY}"}) is None
    assert bearer(None) is None


def test_initialize_asks_the_query_api_once_and_a_refused_key_fails_at_connect(h):
    h.fake.answer("/v1/me", {"detail": "invalid token"}, status=401)
    r = h.post([INIT], h.headers())[0]
    assert r.status_code == 401
    assert r.json()["error"]["message"] == REFUSED
    [me] = h.fake.seen
    assert (me.path, me.headers["authorization"]) == ("/v1/me", f"Bearer {KEY}")


def test_initialize_goes_ahead_when_the_query_api_takes_the_key_or_cant_say(h):
    r = h.post([INIT], h.headers())[0]  # the fake's /v1/me: 200
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "tributary"
    h.fake.answer("/v1/me", {"detail": "down"}, status=503)
    r = h.post([INIT], h.headers())[0]
    assert r.status_code == 200 and "result" in r.json()


def test_other_messages_dont_ask_the_query_api(h):
    r = h.post([LIST], h.headers())[0]
    assert r.status_code == 200
    assert h.fake.seen == []


def test_each_request_forwards_its_own_key_never_one_from_before(h):
    h.fake.answer("/v1/sectors", {"sectors": []})
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "list_sectors", "arguments": {}}}
    replies = h.exchange([(message, h.headers(key)) for key in (KEY, OTHER_KEY, KEY)])
    assert [r.status_code for r in replies] == [200, 200, 200]
    assert [s.headers["authorization"] for s in h.fake.seen] == \
        [f"Bearer {KEY}", f"Bearer {OTHER_KEY}", f"Bearer {KEY}"]


def test_health_and_metrics_need_no_key(h):
    assert h.get("/health").json() == {"status": "ok"}
    r = h.get("/metrics")
    assert r.status_code == 200 and "tributary_mcp_tool_calls_total" in r.text


def test_a_host_or_origin_from_elsewhere_is_refused(h):
    r = h.post([LIST], h.headers(Origin="https://evil.example"))[0]
    assert r.status_code == 403
    r = h.post([LIST], h.headers(Host="tributary.evil.example"))[0]
    assert r.status_code == 421
    r = h.post([LIST], h.headers(Origin="https://tributary.lucasgroves.dev"))[0]
    assert r.status_code == 200


def test_an_oversized_request_is_refused_before_the_key_is_checked_with_the_query_api(h):
    big = {**INIT, "params": {**INIT["params"], "pad": "x" * (1024 * 1024 + 10)}}
    r = h.post([big], h.headers())[0]
    assert r.status_code == 413
    assert h.fake.seen == []


def test_a_get_stream_isnt_offered(h):
    r = h.post([{}], h.headers(), method="GET")[0]
    assert r.status_code == 405
    assert isinstance(r, httpx.Response)
