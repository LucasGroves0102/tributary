import json

import httpx

from tributary_authz import FgaClient, Tuple


def client_with(handler) -> FgaClient:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stores":
            return httpx.Response(200, json={"stores": [{"id": "S1", "name": "tributary"}]})
        return handler(request)
    http = httpx.Client(base_url="http://fga", transport=httpx.MockTransport(route))
    return FgaClient("http://fga", client=http)


def test_finds_store_by_name():
    assert client_with(lambda r: httpx.Response(404)).store_id == "S1"


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
