"""A fake Query API (httpx.MockTransport) and helpers that call the real ASGI app in-process: no
network, no model. Keys are obviously fake (gitleaks)."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from starlette.types import ASGIApp

from tributary_mcp.api import QueryApi
from tributary_mcp.app import build_app

KEY = "trb_" + "A" * 43
OTHER_KEY = "trb_" + "B" * 43
BASE = "http://localhost:8002"
NVDA = "0001045810"
ACC = "0001045810-26-000021"
PRIOR = "0001045810-25-000207"


@dataclass
class Seen:
    method: str
    path: str
    params: dict[str, list[str]]
    headers: dict[str, str]
    body: Any


@dataclass
class FakeQueryApi:
    """Answers by (method, path); records every request it gets."""
    answers: dict[tuple[str, str], Any] = field(default_factory=dict)
    seen: list[Seen] = field(default_factory=list)

    def answer(self, path: str, body: Any = None, *, status: int = 200, method: str = "GET",
               headers: dict[str, str] | None = None) -> None:
        self.answers[(method, path)] = (status, body, headers or {})

    def handler(self, request: httpx.Request) -> httpx.Response:
        params: dict[str, list[str]] = {}
        for k, v in request.url.params.multi_items():
            params.setdefault(k, []).append(v)
        body = json.loads(request.content) if request.content else None
        self.seen.append(Seen(request.method, request.url.path, params,
                              {k.lower(): v for k, v in request.headers.items()}, body))
        found = self.answers.get((request.method, request.url.path))
        if found is None:
            if request.url.path == "/v1/me":
                return httpx.Response(200, json={"user_id": "gh-1", "catalog": {"public": True}})
            return httpx.Response(404, json={"detail": "no such route in the fake"})
        status, answer, headers = found
        if callable(answer):
            answer = answer(request)
        return httpx.Response(status, json=answer, headers=headers)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler),
                                 base_url="http://query-api:8000")

    def paths(self) -> list[str]:
        return [s.path for s in self.seen]


@pytest.fixture
def fake() -> FakeQueryApi:
    return FakeQueryApi()


@asynccontextmanager
async def lifespan(app: ASGIApp) -> AsyncIterator[None]:
    """Run the app's ASGI lifespan (the SDK's session manager) around a block."""
    to_app: asyncio.Queue = asyncio.Queue()
    from_app: asyncio.Queue = asyncio.Queue()
    await to_app.put({"type": "lifespan.startup"})
    async def run() -> None:
        await app({"type": "lifespan", "asgi": {"version": "3.0"}}, to_app.get, from_app.put)

    task = asyncio.create_task(run())
    started = await from_app.get()
    assert started["type"] == "lifespan.startup.complete", started
    try:
        yield
    finally:
        await to_app.put({"type": "lifespan.shutdown"})
        await from_app.get()
        await task


def headers(key: str | None = KEY, **extra: str) -> dict[str, str]:
    out = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
           "MCP-Protocol-Version": "2025-06-18", "X-Request-ID": "req-test-1"}
    if key is not None:
        out["Authorization"] = f"Bearer {key}"
    return {**out, **extra}


def data(res: dict) -> dict:
    """A successful result's structured content, after checking the text content says the same."""
    assert not res.get("isError"), res
    text = json.loads(res["content"][0]["text"])
    assert text == res["structuredContent"]
    return res["structuredContent"]


def error_text(res: dict) -> str:
    assert res.get("isError"), res
    return res["content"][0]["text"]


class Harness:
    """The real app over `fake`, called in-process (test files can't import each other: pytest
    runs with --import-mode=importlib, so the helpers come as this fixture)."""

    headers = staticmethod(headers)
    data = staticmethod(data)
    error_text = staticmethod(error_text)
    lifespan = staticmethod(lifespan)

    def __init__(self, fake: FakeQueryApi) -> None:
        self.fake = fake

    def app(self, **kw: Any) -> ASGIApp:
        return build_app(QueryApi(self.fake.client()), **kw)

    def post(self, messages: list[Any], hdrs: dict[str, str], app: ASGIApp | None = None,
             method: str = "POST") -> list[httpx.Response]:
        """Send each JSON-RPC message to /mcp in turn, within one lifespan."""
        return self.exchange([(m, hdrs) for m in messages], app, method)

    def exchange(self, requests: list[tuple[Any, dict[str, str]]], app: ASGIApp | None = None,
                 method: str = "POST") -> list[httpx.Response]:
        """Each (message, headers) to /mcp in turn, within one lifespan of one app."""
        app = app or self.app()

        async def go() -> list[httpx.Response]:
            async with lifespan(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url=BASE) as c:
                    return [await c.request(method, "/mcp", content=json.dumps(m),
                                            headers=hdrs) for m, hdrs in requests]
        return asyncio.run(go())

    def get(self, path: str, hdrs: dict[str, str] | None = None) -> httpx.Response:
        app = self.app()

        async def go() -> httpx.Response:
            async with lifespan(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url=BASE) as c:
                    return await c.get(path, headers=hdrs or {})
        return asyncio.run(go())

    def call(self, name: str, args: dict | None = None, *, key: str | None = KEY,
             app: ASGIApp | None = None) -> dict:
        """tools/call `name`: the JSON-RPC result (content, structuredContent, isError)."""
        r = self.post([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": args or {}}}], headers(key), app)[0]
        assert r.status_code == 200, r.text
        body = r.json()
        assert "result" in body, body
        return body["result"]


@pytest.fixture
def h(fake: FakeQueryApi) -> Harness:
    return Harness(fake)
