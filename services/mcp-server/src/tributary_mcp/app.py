"""The ASGI app: Tributary's MCP server over Streamable HTTP at /mcp, with /health and /metrics.

Stateless, JSON responses: each POST to /mcp is answered on its own with application/json, no
session is kept between requests and nothing about a caller stays in memory, so any replica can
answer any request and a key revoked in Settings stops working on its next tool call. (A GET
stream for server-sent notifications isn't offered: the tools send none.)

In front: telemetry's RequestMiddleware (request id from nginx's X-Request-ID, access log and
HTTP metrics: method, path and status, never a header), then KeyGate (auth.py), then the SDK's
transport, which also checks Host and Origin (DNS rebinding protection) against
`allowed_hosts` / `allowed_origins`."""

from collections.abc import Sequence

from mcp.server.transport_security import TransportSecuritySettings
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from tributary_mcp.api import QueryApi
from tributary_mcp.auth import MAX_BODY, KeyGate
from tributary_mcp.results import RESULT_CHARS
from tributary_mcp.tools import build_server
from tributary_telemetry.http import RequestMiddleware

MCP_PATH = "/mcp"
# behind nginx the Host is the Service's ("mcp-server:8002"); locally localhost
DEFAULT_HOSTS = ("mcp-server:*", "localhost:*", "127.0.0.1:*")
DEFAULT_ORIGINS = ("https://tributary.lucasgroves.dev",)


def build_app(api: QueryApi, *, allowed_hosts: Sequence[str] = DEFAULT_HOSTS,
              allowed_origins: Sequence[str] = DEFAULT_ORIGINS,
              cap: int = RESULT_CHARS, check_keys: bool = True) -> ASGIApp:
    """The whole app. `check_keys`: ask the Query API about the key on `initialize`."""
    mcp = build_server(api, cap=cap)

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/metrics", methods=["GET"], include_in_schema=False)
    async def metrics(request: Request) -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    transport = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH, stateless_http=True, json_response=True,
        max_request_body_size=MAX_BODY,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=list(allowed_hosts),
            allowed_origins=list(allowed_origins)))
    gated = KeyGate(transport, MCP_PATH, check=api.key_check if check_keys else None)
    return RequestMiddleware(gated)
