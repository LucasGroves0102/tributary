"""The MCP server over the real Query API (in-process, ASGI): Ben's key and Ben's connected app
call every tool, those that take an id with Ann's ids, and never get anything of hers. A tool
given Ann's id answers as for an id nobody has; Ann's own key gets her things (the control).
The MCP server holds no credential of its own: what it can return is what the Query API gives
the caller, so this is the Query API's matrix seen through the tools a model calls."""

import asyncio
import json

import pytest

from access_world import ACC, CO, MARK, World, fill, normalized, refs

pytest.importorskip("mcp")
import httpx  # noqa: E402

from tributary_mcp.api import QueryApi  # noqa: E402
from tributary_mcp.app import build_app  # noqa: E402

BASE = "http://localhost:8002"
ITEMS = ("chart", "thesis", "screen", "metric", "model", "comps", "report")
# the tools that read the user's own things, each call naming Ann's where it takes an id
PRIVATE: list[tuple[str, dict]] = [
    ("whoami", {}), ("get_watchlist", {}), ("list_workspaces", {}), ("list_my_datasets", {}),
    ("get_workspace", {"workspace_id": "@ws"}), ("get_workspace", {"workspace_id": "@project"}),
    *[("get_workspace_item", {"item_id": f"@{k}"}) for k in ITEMS],
    ("read_my_dataset", {"dataset_id": "@dataset"}),
    ("screen_companies", {"metrics": ["@metric"]}),
    ("evaluate_formula", {"formula": "mine.@dataset_name.sales / revenue",
                          "default_company": CO}),
    ("validate_formula", {"formula": "mine.@dataset_name.sales / revenue",
                          "default_company": CO}),
]
# SEC data: nothing of Ann's may ride along
SEC: dict[str, dict] = {
    "find_companies": {"query": "TAC"}, "get_company_profile": {"company_id": CO},
    "get_financials": {"company_id": CO}, "get_segments": {"company_id": CO},
    "get_text_kpis": {"company_id": CO}, "get_data_notes": {"company_id": CO},
    "list_filings": {"company_id": CO}, "read_section": {"company_id": CO, "section_key": "1A"},
    "search_filings": {"query": "stores"},
    "get_material_changes": {"company_id": CO, "accession_no": ACC},
    "get_earnings_releases": {"company_id": CO}, "get_events_since_report": {"company_id": CO},
    "get_insider_filings": {"company_id": CO}, "get_peers": {"company_id": CO},
    "get_restatements": {"company_id": CO}, "list_metrics": {}, "list_sectors": {},
    "verify_figures": {"text": "Revenue was $150."},
}
CALLERS = ("ben_read", "ben_write", "ben_app")


def mcp_calls(world: World, secret: str, calls: list[tuple[str, dict]]) -> list[dict]:
    """tools/call each (name, arguments) with `secret` as the caller's credential: the
    JSON-RPC results, in order. One MCP app (one lifespan) over the world's Query API."""
    async def go() -> list[dict]:
        query_api = httpx.AsyncClient(transport=httpx.ASGITransport(app=world.app),
                                      base_url="http://query-api:8000")
        app = build_app(QueryApi(query_api))
        to_app: asyncio.Queue = asyncio.Queue()
        from_app: asyncio.Queue = asyncio.Queue()
        await to_app.put({"type": "lifespan.startup"})
        task = asyncio.create_task(app({"type": "lifespan", "asgi": {"version": "3.0"}},
                                       to_app.get, from_app.put))
        assert (await from_app.get())["type"] == "lifespan.startup.complete"
        out = []
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url=BASE) as c:
                for i, (name, args) in enumerate(calls):
                    r = await c.post("/mcp", content=json.dumps({
                        "jsonrpc": "2.0", "id": i, "method": "tools/call",
                        "params": {"name": name, "arguments": args}}), headers={
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                        "MCP-Protocol-Version": "2025-06-18",
                        "Authorization": f"Bearer {secret}"})
                    assert r.status_code == 200, (name, r.status_code, r.text)
                    out.append(r.json()["result"])
        finally:
            await to_app.put({"type": "lifespan.shutdown"})
            await from_app.get()
            await task
            await query_api.aclose()
        return out
    return asyncio.run(go())


def tool_names(world: World) -> set[str]:
    from tributary_mcp.tools import build_server

    server = build_server(QueryApi(httpx.AsyncClient()), cap=40_000)
    return {t.name for t in asyncio.run(server.list_tools())}


def text(res: dict) -> str:
    return json.dumps(res, sort_keys=True)


def test_every_tool_is_called_here(world: World) -> None:
    assert tool_names(world) == {n for n, _ in PRIVATE} | set(SEC)


@pytest.mark.parametrize("cred", CALLERS)
def test_tools_never_return_anns_things(world: World, cred: str) -> None:
    calls = [(n, fill(a, refs(world, "ann"))) for n, a in PRIVATE]
    naming = [(n, a) for n, a in PRIVATE if a]  # the calls that name one of her things
    nobodys = [(n, fill(a, refs(world, "missing"))) for n, a in naming]
    calls += list(SEC.items())
    with world.rollback():
        got = mcp_calls(world, world.keys[cred], calls + nobodys)
    theirs, missing = got[:len(calls)], got[len(calls):]
    for (name, args), res in zip(calls, theirs, strict=True):
        leaked = sorted(m for m in world.markers if m in text(res))
        assert not leaked, (name, args, leaked, res)
    mine, nobody = refs(world, "ann"), refs(world, "missing")
    answers = {json.dumps(c): r for c, r in zip(PRIVATE, theirs[:len(PRIVATE)], strict=True)}
    for (name, args), other in zip(naming, missing, strict=True):
        res = answers[json.dumps((name, args))]
        assert normalized(text(res), mine) == normalized(text(other), nobody), \
            (name, args, "an existence oracle", res, other)


def test_anns_own_key_gets_her_things(world: World) -> None:
    """The control: the same tools with her key return what the others never see."""
    calls = [(n, fill(a, refs(world, "ann"))) for n, a in PRIVATE
             if n in ("get_workspace", "get_workspace_item", "read_my_dataset",
                      "list_workspaces", "list_my_datasets")]
    with world.rollback():
        got = mcp_calls(world, world.keys["ann_read"], calls)
    for (name, args), res in zip(calls, got, strict=True):
        assert not res.get("isError"), (name, args, res)
        assert MARK in text(res) or name == "read_my_dataset", (name, args, res)
    rows = got[[n for n, _ in calls].index("read_my_dataset")]
    assert "1500" in text(rows), rows
