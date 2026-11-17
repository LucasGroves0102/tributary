"""The environment configures the app: Query API URL, port, allowed hosts and origins."""

import json

from tributary_mcp.main import app_from_env

LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def test_the_environment_sets_the_port_and_the_hosts_and_origins_it_accepts(h):
    app, port = app_from_env({"QUERY_API_URL": "http://query-api:8000",
                              "MCP_ALLOWED_HOSTS": " mcp.example:* , ",
                              "MCP_ALLOWED_ORIGINS": "https://site.example",
                              "MCP_RESULT_CHARS": "20000"})
    assert port == 8002
    host_ok = h.headers(Host="mcp.example:8002", Origin="https://site.example")
    replies = h.exchange([(LIST, h.headers()), (LIST, host_ok),
                          (LIST, {**host_ok, "Origin": "https://tributary.lucasgroves.dev"})],
                         app=app)
    assert [r.status_code for r in replies] == [421, 200, 403]
    assert len(json.loads(replies[1].text)["result"]["tools"]) == 26


def test_defaults(h):
    app, port = app_from_env({"QUERY_API_URL": "http://query-api:8000", "PORT": "9000"})
    assert port == 9000
    replies = h.exchange([(LIST, h.headers(Host="mcp-server:8002"))], app=app)
    assert replies[0].status_code == 200
