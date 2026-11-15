"""The API key on an MCP request: what a well-formed one looks like, and the gate in front of /mcp.

A client sends its user's Tributary API key as `Authorization: Bearer trb_...` on every HTTP
request to /mcp. This server never checks it against anything itself and keeps nothing: each tool
forwards it, unchanged, to the Query API, which finds the key's user and applies OpenFGA and
row-level security as for that user (query_api/api_keys.py). The gate only refuses, before any MCP
handling, a request with no credential or one that isn't a Tributary key (a sign-in token, another
service's key), with a 401 that says what to send. On `initialize` (the first request of a session
in the handshake protocol versions) it asks the Query API once who the key belongs to, so an
unknown, expired or revoked key fails at connect, not at the first tool call.

Never logged, echoed or stored: the key, its hash or any part of it. Log lines carry the request
id only."""

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping

from prometheus_client import Counter
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

KEY = re.compile(r"trb_[A-Za-z0-9_-]{43}")  # query_api.api_keys.KEY: "trb_" + 32 bytes, base64url
MAX_BODY = 1024 * 1024  # an MCP request; verify_figures' text is the largest (20,000 characters)

# JSON-RPC error code for an authentication failure (server-defined range)
UNAUTHORIZED = -32001

NO_KEY = ("Tributary's MCP server needs your API key: send the header "
          "'Authorization: Bearer trb_...'. Make a key in Tributary's Settings, API keys.")
NOT_A_KEY = ("This isn't a Tributary API key: keys start with trb_ and are made in Tributary's "
             "Settings, API keys. A sign-in token or another service's key isn't accepted here.")
REFUSED = ("Tributary refused this API key: it is unknown, expired or revoked. Make a new key in "
           "Tributary's Settings, API keys, and update this connection.")

REFUSALS = Counter("tributary_mcp_auth_refused_total",
                   "MCP requests refused before any tool ran, by reason (no_key, not_a_key, "
                   "refused)", ["reason"])

log = logging.getLogger("tributary_mcp.auth")


def bearer(headers: Mapping[str, str] | None) -> str | None:
    """The Authorization value to forward ("Bearer trb_..."), or None when there is no well-formed
    Tributary key in it. The scheme is case-insensitive (RFC 6750); what goes on is the canonical
    form of the same credential."""
    value = (headers or {}).get("authorization")
    if not value:
        return None
    scheme, _, token = value.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not KEY.fullmatch(token):
        return None
    return f"Bearer {token}"


def _problem(headers: Headers) -> tuple[str, str] | None:
    """(reason, message) when the request carries no usable key."""
    value = headers.get("authorization")
    if not value or not value.strip():
        return "no_key", NO_KEY
    if bearer(headers) is None:
        return "not_a_key", NOT_A_KEY
    return None


async def _refuse(send: Send, status: int, message: str) -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": None,
                       "error": {"code": UNAUTHORIZED, "message": message}}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
        # RFC 6750; no resource metadata: OAuth isn't offered yet (phase 3)
        (b"www-authenticate", b'Bearer realm="Tributary", error="invalid_token"'),
    ]})
    await send({"type": "http.response.body", "body": body})


def _is_initialize(body: bytes) -> bool:
    try:
        message = json.loads(body)
    except ValueError:
        return False
    messages = message if isinstance(message, list) else [message]
    return any(isinstance(m, dict) and m.get("method") == "initialize" for m in messages)


KeyCheck = Callable[[str, str | None], Awaitable[bool | None]]
"""(authorization, request id) -> True: the Query API takes the key; False: it refused it (401);
None: it couldn't say (unreachable, another status). Only False refuses."""


class KeyGate:
    """ASGI middleware: requests to `path` need a well-formed key (else 401 with a JSON-RPC error
    body and a Bearer challenge) and must be POSTs (else 405); an `initialize` is let through only
    if `check` doesn't refuse the key. Every other path (health, metrics) passes untouched."""

    def __init__(self, app: ASGIApp, path: str, check: KeyCheck | None = None) -> None:
        self.app, self.path, self.check = app, path, check

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].rstrip("/") != self.path:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        problem = _problem(headers)
        if problem is not None:
            reason, message = problem
            REFUSALS.labels(reason).inc()
            log.info("mcp request refused: %s", reason)
            await _refuse(send, 401, message)
            return
        if scope["method"] != "POST":
            # no GET stream: the tools send nothing unasked, and in stateless mode the SDK would
            # hold a silent stream open (the spec's alternative: 405); no sessions to DELETE
            await send({"type": "http.response.start", "status": 405,
                        "headers": [(b"allow", b"POST"), (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return
        if self.check is None:
            await self.app(scope, receive, send)
            return
        # read the body once (bounded), look for initialize, then hand the same bytes on
        body, more, trailing = bytearray(), True, None
        while more:
            message = await receive()
            if message["type"] != "http.request":
                trailing = message
                break
            body.extend(message.get("body", b""))
            more = message.get("more_body", False)
            if len(body) > MAX_BODY:
                await send({"type": "http.response.start", "status": 413,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"Request body too large"})
                return
        auth = bearer(headers)
        if auth is not None and _is_initialize(bytes(body)):
            ok = await self.check(auth, headers.get("x-request-id"))
            if ok is False:
                REFUSALS.labels("refused").inc()
                log.info("mcp initialize refused: the Query API refused the key")
                await _refuse(send, 401, REFUSED)
                return
        replay: list[Message] = [{"type": "http.request", "body": bytes(body), "more_body": False}]
        if trailing is not None:
            replay.append(trailing)

        async def replayed() -> Message:
            return replay.pop(0) if replay else await receive()

        await self.app(scope, replayed, send)
