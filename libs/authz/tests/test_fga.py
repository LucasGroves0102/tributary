import json

import httpx

from tributary_authz import FgaClient, Tuple


def client_with(handler, api_token=None) -> FgaClient:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stores":
            return httpx.Response(200, json={"stores": [{"id": "S1", "name": "tributary"}]})
        return handler(request)
    http = httpx.Client(base_url="http://fga", transport=httpx.MockTransport(route))
    return FgaClient("http://fga", client=http, api_token=api_token)


def test_finds_store_by_name():
    assert client_with(lambda r: httpx.Response(404)).store_id == "S1"


def test_sends_api_token_on_every_request():
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"allowed": True})

    fga = client_with(handler, api_token="k1")
    fga.check("user:alice", "viewer", "company:1")
    assert seen == ["Bearer k1"]


def test_no_authorization_header_without_token():
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"allowed": True})

    client_with(handler).check("user:alice", "viewer", "company:1")
    assert seen == [None]


def test_from_env(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"stores": [{"id": "S1", "name": "tributary"}]})

    def http():
        return httpx.Client(base_url="http://fga", transport=httpx.MockTransport(handler))

    monkeypatch.setenv("FGA_API_URL", "http://fga")
    monkeypatch.setenv("FGA_API_TOKEN", "k2")
    assert FgaClient.from_env(http()).store_id == "S1"
    monkeypatch.setenv("FGA_API_TOKEN", "")
    FgaClient.from_env(http())
    assert seen == ["Bearer k2", None]


def test_write_chunks_at_100():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={})

    fga = client_with(handler)
    writes = {Tuple(f"user:u{i}", "viewer", "company:c") for i in range(150)}
    fga.write(writes=writes, deletes={Tuple("user:x", "viewer", "company:c")})
    sizes = [sum(len(b.get(k, {}).get("tuple_keys", [])) for k in ("writes", "deletes"))
             for b in bodies]
    assert sizes == [100, 51]
    assert bodies[0]["deletes"]["tuple_keys"] == [  # revocations go first
        {"user": "user:x", "relation": "viewer", "object": "company:c"}]


def test_read_all_follows_continuation():
    pages = iter([
        # shaped like real OpenFGA responses, including fields we ignore
        {"tuples": [{"key": {"user": "user:a", "relation": "viewer", "object": "company:1",
                             "condition": None}, "timestamp": "2026-10-17T18:00:00Z"}],
         "continuation_token": "next"},
        {"tuples": [{"key": {"user": "user:b", "relation": "viewer", "object": "company:1"}}],
         "continuation_token": ""},
    ])
    fga = client_with(lambda r: httpx.Response(200, json=next(pages)))
    assert fga.read_all() == {Tuple("user:a", "viewer", "company:1"),
                              Tuple("user:b", "viewer", "company:1")}
