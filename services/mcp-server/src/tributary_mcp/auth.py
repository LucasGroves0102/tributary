"""The credential on an MCP request: what a well-formed one looks like, and the gate in front of
/mcp.

A client sends one of two Tributary credentials as `Authorization: Bearer ...` on every HTTP
request to /mcp: an app's OAuth access token (`tro_...`, from connecting Tributary in claude.ai,
ChatGPT or another MCP client: the Query API is the authorization server, query_api/oauth.py)
or the user's personal API key (`trb_...`, made in Settings). This server never checks either
against anything itself and keeps nothing: each tool forwards it, unchanged, to the Query API,
which finds its user and applies OpenFGA and row-level security as for that user. The Query API
issued the token for this server (its resource, RFC 8707) and is this server's own backend, so
forwarding it is not passing a token on to a third party.

The gate refuses, before any MCP handling, a request with no credential or one that isn't a
Tributary credential (a sign-in token, another service's key): 401 with a JSON-RPC error and an
RFC 6750 challenge. With OAuth configured (`challenge`) the challenge names the protected
resource metadata (RFC 9728: `resource_metadata`, `scope`), which is how a client discovers it
can sign the user in. On `initialize` (the first request of a session in the handshake protocol
versions) it asks the Query API once whether it takes the credential, so an unknown, expired or
revoked one fails at connect. An app's token the Query API refuses during a tool call (expired,
disconnected) turns that request's answer into the same 401 (`refused_mid_call`), so the client
refreshes its token or signs in again instead of reading a tool error; a key refused mid-call
stays a tool error, as before.

Never logged, echoed or stored: the credential, its hash or any part of it. Log lines carry the
request id only."""

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from prometheus_client import Counter
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

KEY = re.compile(r"trb_[A-Za-z0-9_-]{43}")  # query_api.api_keys.KEY: "trb_" + 32 bytes, base64url
APP_TOKEN = re.compile(r"tro_[A-Za-z0-9_-]{43}")  # query_api.oauth: an access token, 32 bytes
MAX_BODY = 1024 * 1024  # an MCP request; verify_figures' text is the largest (20,000 characters)
SCOPE = "read"
# set on the request's ASGI scope by a tool whose call the Query API refused an app's token for
REFUSED_MID_CALL = "tributary.credential_refused"

# JSON-RPC error code for an authentication failure (server-defined range)
UNAUTHORIZED = -32001

NO_KEY = ("Tributary's MCP server needs you to sign in: connect Tributary from your app (it opens "
          "Tributary's sign-in), or send an API key as 'Authorization: Bearer trb_...', made in "
          "Tributary's Settings, API keys.")
NOT_A_KEY = ("This isn't a Tributary credential: connect Tributary from your app to sign in, or "
             "use an API key (it starts with trb_), made in Tributary's Settings, API keys. A "
             "sign-in token or another service's key isn't accepted here.")
REFUSED = ("Tributary refused this API key: it is unknown, expired or revoked. Make a new key in "
           "Tributary's Settings, API keys, and update this connection.")
APP_REFUSED = ("Tributary refused this connection: it expired or was disconnected. Connect "
               "Tributary again from your app.")

REFUSALS = Counter("tributary_mcp_auth_refused_total",
                   "MCP requests refused before any tool ran, by reason (no_key, not_a_key, "
                   "refused, refused_mid_call)", ["reason"])

log = logging.getLogger("tributary_mcp.auth")


@dataclass(frozen=True)
class Challenge:
    """What a 401 tells a client about signing in (RFC 9728 5.1): the protected resource
    metadata's URL and the scope to ask for."""
    resource_metadata: str
    scope: str = SCOPE

    def header(self, invalid: bool) -> bytes:
        parts = ['realm="Tributary"']
        if invalid:  # RFC 6750 3.1: no error code when no credential was sent
            parts.append('error="invalid_token"')
        parts += [f'resource_metadata="{self.resource_metadata}"', f'scope="{self.scope}"']
        return ("Bearer " + ", ".join(parts)).encode()


def bearer(headers: Mapping[str, str] | None) -> str | None:
    """The Authorization value to forward ("Bearer trb_..." or "Bearer tro_..."), or None when
    there is no well-formed Tributary credential in it. The scheme is case-insensitive (RFC 6750);
    what goes on is the canonical form of the same credential."""
    value = (headers or {}).get("authorization")
    if not value:
        return None
    scheme, _, token = value.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not (KEY.fullmatch(token) or APP_TOKEN.fullmatch(token)):
        return None
    return f"Bearer {token}"


def is_app_token(authorization: str | None) -> bool:
    """Whether a forwarded Authorization value is a connected app's token (not an API key)."""
    return authorization is not None and authorization.startswith("Bearer tro_")


def _problem(headers: Headers) -> tuple[str, str] | None:
    """(reason, message) when the request carries no usable credential."""
    value = headers.get("authorization")
    if not value or not value.strip():
        return "no_key", NO_KEY
    if bearer(headers) is None:
        return "not_a_key", NOT_A_KEY
    return None


async def _refuse(send: Send, message: str, challenge: Challenge | None, invalid: bool) -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": None,
                       "error": {"code": UNAUTHORIZED, "message": message}}).encode()
    header = challenge.header(invalid) if challenge else \
        b'Bearer realm="Tributary", error="invalid_token"'
    await send({"type": "http.response.start", "status": 401, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
        (b"www-authenticate", header),
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
"""(authorization, request id) -> True: the Query API takes the credential; False: it refused it
(401); None: it couldn't say (unreachable, another status). Only False refuses."""


class KeyGate:
    """ASGI middleware: requests to `path` need a well-formed credential (else 401 with a
    JSON-RPC error body and a Bearer challenge) and must be POSTs (else 405); an `initialize` is
    let through only if `check` doesn't refuse the credential, and an app's token refused during
    the call becomes a 401 (module docstring). Every other path (health, metrics, the protected
    resource metadata) passes untouched."""

    def __init__(self, app: ASGIApp, path: str, check: KeyCheck | None = None,
                 challenge: Challenge | None = None) -> None:
        self.app, self.path, self.check, self.challenge = app, path, check, challenge

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
            await _refuse(send, message, self.challenge, invalid=reason != "no_key")
            return
        if scope["method"] != "POST":
            # no GET stream: the tools send nothing unasked, and in stateless mode the SDK would
            # hold a silent stream open (the spec's alternative: 405); no sessions to DELETE
            await send({"type": "http.response.start", "status": 405,
                        "headers": [(b"allow", b"POST"), (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return
        auth = bearer(headers)
        app_token = is_app_token(auth)
        if self.check is None:
            await self.app(scope, receive, self._watching(scope, send, app_token))
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
        if auth is not None and _is_initialize(bytes(body)):
            ok = await self.check(auth, headers.get("x-request-id"))
            if ok is False:
                REFUSALS.labels("refused").inc()
                log.info("mcp initialize refused: the Query API refused the credential")
                await _refuse(send, APP_REFUSED if app_token else REFUSED, self.challenge,
                              invalid=True)
                return
        replay: list[Message] = [{"type": "http.request", "body": bytes(body), "more_body": False}]
        if trailing is not None:
            replay.append(trailing)

        async def replayed() -> Message:
            return replay.pop(0) if replay else await receive()

        await self.app(scope, replayed, self._watching(scope, send, app_token))

    def _watching(self, scope: Scope, send: Send, app_token: bool) -> Send:
        """`send`, except that an app's token refused during the call (a tool marked the scope)
        answers 401 with the challenge instead of the tool's error (JSON answers are sent whole,
        after the tools ran)."""
        if not app_token:
            return send
        swallowed = False

        async def guarded(message: Message) -> None:
            nonlocal swallowed
            if message["type"] == "http.response.start" and scope.get(REFUSED_MID_CALL):
                swallowed = True
                REFUSALS.labels("refused_mid_call").inc()
                log.info("mcp request refused: the Query API refused the app's token")
                await _refuse(send, APP_REFUSED, self.challenge, invalid=True)
                return
            if swallowed:
                return
            await send(message)
        return guarded
