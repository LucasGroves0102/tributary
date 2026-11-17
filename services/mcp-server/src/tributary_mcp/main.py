"""Run the MCP server. Env: QUERY_API_URL (required), PORT (8002), LOG_LEVEL, MCP_ALLOWED_HOSTS and
MCP_ALLOWED_ORIGINS (comma-separated; app.py's defaults), MCP_RESULT_CHARS (40000), OAUTH_ISSUER
(the site's origin, as the Query API's: connected apps; unset, only API keys and no resource
metadata) and MCP_RESOURCE (this server's public URL; default OAUTH_ISSUER + /mcp). No secrets:
each request brings its user's own credential."""

import logging
import os
from collections.abc import Mapping

import httpx
import uvicorn
from starlette.types import ASGIApp

from tributary_mcp.api import QueryApi
from tributary_mcp.app import (
    DEFAULT_HOSTS,
    DEFAULT_ORIGINS,
    MCP_PATH,
    OAuthResource,
    build_app,
)
from tributary_mcp.results import RESULT_CHARS
from tributary_telemetry import configure_logging


def _list(env: Mapping[str, str], name: str, default: tuple[str, ...]) -> list[str]:
    return [v.strip() for v in env.get(name, "").split(",") if v.strip()] or list(default)


def oauth_from_env(env: Mapping[str, str]) -> OAuthResource | None:
    issuer = env.get("OAUTH_ISSUER", "").strip().rstrip("/")
    if not issuer:
        return None
    if not issuer.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise ValueError("OAUTH_ISSUER must be the site's https:// origin")
    return OAuthResource(issuer, env.get("MCP_RESOURCE", "").strip() or issuer + MCP_PATH)


def app_from_env(env: Mapping[str, str]) -> tuple[ASGIApp, int]:
    """(the app, its port) as the environment configures them."""
    http = httpx.AsyncClient(base_url=env["QUERY_API_URL"], timeout=httpx.Timeout(30, connect=5),
                             limits=httpx.Limits(max_connections=20))
    app = build_app(QueryApi(http), allowed_hosts=_list(env, "MCP_ALLOWED_HOSTS", DEFAULT_HOSTS),
                    allowed_origins=_list(env, "MCP_ALLOWED_ORIGINS", DEFAULT_ORIGINS),
                    cap=int(env.get("MCP_RESULT_CHARS") or RESULT_CHARS),
                    oauth=oauth_from_env(env))
    return app, int(env.get("PORT") or 8002)


def main() -> None:
    configure_logging()
    # httpx logs every URL at INFO: search words and the like, not ours to keep
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # "Terminating session: None" on every stateless request
    logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)
    app, port = app_from_env(os.environ)
    uvicorn.run(app, host="0.0.0.0", port=port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
