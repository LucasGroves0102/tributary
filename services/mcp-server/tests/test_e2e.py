"""End to end in-process: the SDK's own MCP client over Streamable HTTP against the real ASGI app
(no network: httpx2's ASGI transport), listing the tools and calling one; and the handshake a
client on an earlier protocol version makes, message by message."""

import asyncio
import json

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

KEY = "trb_" + "A" * 43
NVDA = "0001045810"
ACC = "0001045810-26-000021"

SEARCH = {"results": [{"company_id": NVDA, "name": "NVIDIA CORP", "tickers": ["NVDA"],
                       "access": ["filings", "financials"]}]}
EVENTS = {"company_id": NVDA, "events": [{"form": "8-K", "items": ["2.02"], "citation": {
    "accession_no": ACC, "form": "8-K", "filed": "2026-08-27", "section_key": "EX-99.1"}}]}


def test_an_mcp_client_lists_the_tools_and_calls_one(h):
    h.fake.answer("/v1/companies/search", SEARCH)
    h.fake.answer(f"/v1/companies/{NVDA}/events", EVENTS)
    app = h.app()

    async def go():
        async with h.lifespan(app):
            http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                      headers={"Authorization": f"Bearer {KEY}"})
            async with http, Client(streamable_http_client(
                    "http://localhost:8002/mcp", http_client=http)) as client:
                tools = await client.list_tools()
                found = await client.call_tool("find_companies", {"query": "NVDA"})
                events = await client.call_tool("get_events_since_report",
                                                 {"company_id": NVDA})
                refused = await client.call_tool("get_events_since_report",
                                                 {"company_id": "NVDA"})
                return tools, found, events, refused

    tools, found, events, refused = asyncio.run(go())
    names = {t.name for t in tools.tools}
    assert {"find_companies", "get_financials", "read_section", "search_filings",
            "verify_figures", "get_workspace_item"} <= names
    assert all(t.annotations and t.annotations.read_only_hint for t in tools.tools)
    assert not found.is_error
    assert found.structured_content == {"data": SEARCH, "citations": []}
    assert json.loads(found.content[0].text) == found.structured_content  # type: ignore[union-attr]
    assert events.structured_content["citations"] == [{  # type: ignore[index]
        "accession_no": ACC, "form": "8-K", "filed": "2026-08-27", "section_key": "EX-99.1",
        "company_id": NVDA}]
    assert refused.is_error  # the schema's pattern: never sent on
    assert [s.headers["authorization"] for s in h.fake.seen] == [f"Bearer {KEY}"] * 2


def test_a_client_on_a_handshake_protocol_version(h):
    h.fake.answer("/v1/companies/search", SEARCH)
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "claude-code", "version": "2"}}}
    replies = h.post([
        init,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "find_companies", "arguments": {"query": "NVDA"}}},
    ], h.headers())
    assert [r.status_code for r in replies] == [200, 202, 200, 200]
    result = replies[0].json()["result"]
    assert result["serverInfo"]["name"] == "tributary"
    assert "Text quoted from filings" in result["instructions"]
    assert result["capabilities"]["tools"] is not None
    assert "mcp-session-id" not in replies[0].headers  # stateless: nothing kept
    assert len(replies[2].json()["result"]["tools"]) == 26
    call = replies[3].json()["result"]
    assert call["structuredContent"]["data"] == SEARCH
    # initialize asked the Query API who the key is (once), then the tool call
    assert [s.path for s in h.fake.seen] == ["/v1/me", "/v1/companies/search"]
