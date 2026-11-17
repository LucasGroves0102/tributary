"""Connected apps: Tributary's OAuth 2.1 authorization server, so a user adds Tributary to
claude.ai or ChatGPT by URL, signs in, approves and the app reads Tributary as them through the
MCP server, with no key to copy (MCP phase 3). App migration 025; README "Connected apps
(OAuth)". On only with OAUTH_ISSUER (and the app database); otherwise every route here is 404
and an access token is refused like a bad token.

The pieces (MCP authorization spec 2025-11-25; RFCs 6749, 7591, 7636, 8414, 8707, 9207):

- Metadata (RFC 8414) at /.well-known/oauth-authorization-server: issuer = OAUTH_ISSUER (the
  site, https://tributary.lucasgroves.dev), PKCE S256 only, both client registrations below,
  `authorization_response_iss_parameter_supported` (every authorization answer carries `iss`).
  The MCP server's protected-resource metadata (RFC 9728) names this issuer.
- Clients. (a) Client ID metadata documents (the spec's preferred way; claude.ai and ChatGPT use
  it when the metadata says `client_id_metadata_document_supported` with `none` among the token
  endpoint's auth methods): the client_id is an https URL, fetched through client_metadata.py
  (public addresses only, no redirects, small, quick), cached in `oauth_clients` for its
  Cache-Control max-age clamped to DOC_MIN_TTL..DOC_MAX_TTL (a failed refresh keeps a copy up to
  DOC_STALE_OK old), at most FETCHES_PER_IP per client address and FETCHES_TOTAL per replica in
  each FETCH_WINDOW. (b) Dynamic registration (RFC 7591) at POST /oauth/register, the fallback:
  JSON parsed by hand, at most REGISTRATIONS_PER_IP per address and REGISTRATIONS_TOTAL per
  replica an hour; a client asking for `client_secret_basic`/`_post` (the default when it names
  none, RFC 7591) gets a secret, shown once, kept as its SHA-256. Unused registrations go after
  a day, used ones 60 days after their last use (no refresh token lives that long).
- Redirect URIs (`redirect_ok`): https, or http on a loopback address (localhost, 127.0.0.1,
  [::1]) for apps on the user's own computer; never javascript:, data:, another scheme, a
  fragment or user info. Matched exactly, except that a loopback URI's port may differ (RFC 8252
  7.3; Claude Code picks a port per session).
- GET /oauth/authorize: checks the client and redirect URI first; a problem with either is
  shown on Tributary's own page (`/#/connect?error=...`), never sent to the URI. Then
  response_type=code, PKCE (code_challenge, method S256 only: plain is refused), `resource`
  (RFC 8707: absent or the MCP server's own URL, compared canonically; another is
  invalid_target), state at most STATE_MAX characters; a problem there goes back to the
  redirect URI with `error`, `state` and `iss`. Scopes: only `read` exists; anything else asked
  for is ignored (the answer's `scope` says what was granted). Nothing is stored yet: the
  request is sealed (HMAC with a key derived from the JWT secret, its own label, REQUEST_TTL)
  and the browser is sent to the web app's consent page, `/#/connect?request=<sealed>`, with a
  random nonce in an HttpOnly, Secure, SameSite=Lax cookie scoped to the consent routes (its
  hash is in the sealed request): consent can only be given in the browser that started the
  request, so a link to someone else's request can't be approved by a victim (CSRF), and the
  consent routes take the user only from the signed-in bearer token, never a cookie.
- Consent (POST /v1/oauth/consent, .../approve, .../deny; sign-in only: never an API key or an
  app's token): the web app shows the client's name, who published it (a document's host), the
  redirect URI's host (a loopback one is flagged), what it may do, and Approve / Deny. Approve
  re-checks the client and the cookie, refuses a demo sandbox (403) and more than MAX_GRANTS live
  connections, then in ONE app-DB transaction as the user stores the grant and a code (60 s,
  single-use, bound to the grant, so client and user, the redirect URI and the PKCE challenge)
  and answers the redirect (`code`, `state`, `iss`); Deny answers it with access_denied. Both
  clear the nonce: one decision per request.
- POST /oauth/token (form-encoded, parsed by hand): client authentication (none for public
  clients; the secret by Basic or in the body for confidential ones); `authorization_code`:
  the code is spent on first presentation whatever follows (a second presentation revokes the
  grant and everything it issued), then the client, the exact redirect URI, the PKCE verifier
  and `resource` must match; `refresh_token`: rotated every time (the new pair in the same
  answer), a used one presented again revokes the grant (reuse detection), another client's is
  refused and spent. Access tokens 'tro_' + 32 random bytes, 1 hour; refresh 'trr_' + 32, 30
  days; both opaque, stored only as SHA-256 + first 8 characters. Errors are RFC 6749's
  (invalid_grant for any bad code or refresh token; invalid_client 401).
- POST /oauth/revoke (RFC 7009): a client's access or refresh token revokes its grant; unknown
  tokens and another client's answer 200 and change nothing.
- Using a token (`OAuthAuth.user`, app.py's current_user): `Bearer tro_...` is taken beside the
  JWT and API keys: the user is the grant's row (found by the token's hash; never a parameter,
  another header or the agent), then require() and RLS as that user, like a read-scope API key:
  only api_keys.READ_ROUTES (default deny: account routes never), the key limits per grant (60 a
  minute, 10,000 a day, 429 with Retry-After), one 401 for unknown, expired or revoked tokens,
  a token whose resource isn't this MCP server's, and a password account disabled or whose
  password changed after the grant. agent-api refuses these tokens itself.
- Settings, Connected apps (GET /v1/oauth/connections, DELETE /v1/oauth/connections/{id}, sign
  in only): each client with a live grant (name, publisher, redirect host, connected on, last
  used); Disconnect revokes every grant of that client for the user at once.

Never logged, returned after issue or stored in clear: codes, tokens, client secrets, PKCE
verifiers, `state`. Logs carry user, client key and grant ids and short outcome codes."""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit
from uuid import UUID

import psycopg
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from prometheus_client import Counter
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from query_api.api_keys import INVALID, READ_ROUTES, Limits, rate_headers
from query_api.client_metadata import (
    DocumentFetcher,
    DocumentRefused,
    clean_name,
    document_url,
    parse_document,
)
from query_api.password_accounts import IpLimiter
from query_api.sandbox import is_sandbox_user

ACCESS_PREFIX = "tro_"
REFRESH_PREFIX = "trr_"
CLIENT_PREFIX = "trc_"
SECRET_PREFIX = "trs_"
TOKEN = re.compile(r"^tr[or]_[A-Za-z0-9_-]{43}$")
CODE = re.compile(r"^[A-Za-z0-9_-]{43}$")
CLIENT_ID = re.compile(r"^trc_[A-Za-z0-9_-]{32}$")
SECRET = re.compile(r"^trs_[A-Za-z0-9_-]{43}$")
CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")  # base64url(SHA-256), unpadded
VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")  # RFC 7636 4.1
SHOWN = 8
SCOPES = ("read",)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(seconds=60)
REQUEST_TTL = 600  # seconds from /oauth/authorize to the decision (sign-in included)
STATE_MAX = 1024
MAX_GRANTS = 25  # live connections per user
LAST_USED_EVERY = timedelta(minutes=1)
DOC_MIN_TTL = timedelta(minutes=5)
DOC_MAX_TTL = timedelta(hours=24)
DOC_STALE_OK = timedelta(days=7)
FETCH_WINDOW = 600.0
FETCHES_PER_IP = 20
FETCHES_TOTAL = 200
REGISTRATIONS_PER_IP = 30
REGISTRATIONS_TOTAL = 300
REGISTRATION_WINDOW = 3600.0
UNUSED_CLIENT_TTL = timedelta(days=1)
IDLE_CLIENT_TTL = timedelta(days=60)
MAX_BODY = 16 * 1024
MAX_REDIRECT_URIS = 10
URL_MAX = 2048
NAME_MAX = 100
AUTH_METHODS = ("none", "client_secret_basic", "client_secret_post")
NONCE_COOKIE = "tributary_oauth_nonce"
# the consent routes as the browser sees them (nginx and the dev proxy: /api/query)
COOKIE_PATH = "/api/query/v1/oauth/"
CONNECT_PAGE = "/#/connect"
LOOPBACK = ("localhost", "127.0.0.1", "::1")
REQUEST_LABEL = b"tributary oauth request v1"
# a 403 for an app's token on a route it can't use (default deny)
NOT_FOR_APPS = "connected apps can't use this route: sign in to Tributary"
NO_SANDBOX = ("Demo sessions can't connect apps: sign in with GitHub or with an account to "
              "connect this app")
EXPIRED = ("This request to connect an app has expired or was started in another browser: "
           "start again from the app")
CORS = {"Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type, MCP-Protocol-Version",
        "Access-Control-Max-Age": "600"}
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

EVENTS = Counter("tributary_oauth_events_total",
                 "Connected-app (OAuth) events by kind (register, document, authorize, approve, "
                 "deny, token, refresh, revoke, use) and result", ["event", "result"])

log = logging.getLogger("query_api.oauth")


# --- configuration -----------------------------------------------------------------------------

@dataclass(frozen=True)
class OAuthConfig:
    """issuer: the site (https, or http on localhost for development), no path; resource: the MCP
    server's URL (RFC 8707), issuer + /mcp unless OAUTH_RESOURCE says otherwise."""
    issuer: str
    resource: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "OAuthConfig | None":
        issuer = env.get("OAUTH_ISSUER", "").strip().rstrip("/")
        if not issuer:
            return None
        parts = urlsplit(issuer)
        local = parts.scheme == "http" and parts.hostname in LOOPBACK
        if (parts.scheme != "https" and not local) or parts.path or parts.query \
                or parts.fragment or not parts.hostname:
            raise ValueError("OAUTH_ISSUER must be the site's https:// origin, without a path")
        resource = env.get("OAUTH_RESOURCE", "").strip() or f"{issuer}/mcp"
        if canonical_resource(resource) is None:
            raise ValueError("OAUTH_RESOURCE must be an absolute https:// URL")
        return cls(issuer, resource)

    def endpoint(self, name: str) -> str:
        return f"{self.issuer}/oauth/{name}"

    def metadata(self) -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.endpoint("authorize"),
            "token_endpoint": self.endpoint("token"),
            "registration_endpoint": self.endpoint("register"),
            "revocation_endpoint": self.endpoint("revoke"),
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": list(AUTH_METHODS),
            "revocation_endpoint_auth_methods_supported": list(AUTH_METHODS),
            "scopes_supported": list(SCOPES),
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
            "service_documentation": f"{self.issuer}/#/api",
        }


# --- pure rules ----------------------------------------------------------------------------------

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def new_token(prefix: str) -> str:
    return prefix + _b64(secrets.token_bytes(32))


def canonical_resource(value: str | None) -> str | None:
    """A resource URI compared as RFC 8707 / the MCP spec say: scheme and host in lower case,
    the default port dropped, no trailing slash, no fragment; None for anything else."""
    if not isinstance(value, str) or not value or len(value) > URL_MAX:
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http") or not parts.hostname or parts.fragment \
            or "@" in parts.netloc:
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default = 443 if scheme == "https" else 80
    netloc = host if port in (None, default) else f"{host}:{port}"
    path = parts.path.rstrip("/")
    return f"{scheme}://{netloc}{path}" + (f"?{parts.query}" if parts.query else "")


def redirect_ok(uri: str) -> bool:
    """The redirect rule: https, or http on a loopback address; no fragment or user info, no
    spaces or control characters, at most URL_MAX characters."""
    if not isinstance(uri, str) or not uri or len(uri) > URL_MAX:
        return False
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in uri) or "\\" in uri:
        return False
    try:
        parts = urlsplit(uri)
        parts.port  # noqa: B018 (a bad port raises)
    except ValueError:
        return False
    if parts.fragment or "@" in parts.netloc or not parts.hostname or "%" in parts.netloc:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in LOOPBACK


def is_loopback(uri: str) -> bool:
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    return parts.scheme == "http" and parts.hostname in LOOPBACK


def redirect_matches(registered: tuple[str, ...] | list[str], given: str) -> str | None:
    """The registered URI `given` matches, or None: exact string equality, or for a loopback
    URI the same host, path and query on any port (RFC 8252 7.3)."""
    if given in registered:
        return given
    if not is_loopback(given) or not redirect_ok(given):
        return None
    g = urlsplit(given)
    for r in registered:
        if not is_loopback(r):
            continue
        p = urlsplit(r)
        if (p.hostname, p.path or "/", p.query) == (g.hostname, g.path or "/", g.query):
            return given
    return None


def pkce_ok(verifier: str | None, challenge: str) -> bool:
    """RFC 7636 S256: base64url(SHA-256(verifier)) is the challenge."""
    if not isinstance(verifier, str) or not VERIFIER.fullmatch(verifier):
        return False
    return hmac.compare_digest(_b64(hashlib.sha256(verifier.encode("ascii")).digest()),
                               challenge)


def with_params(uri: str, params: Mapping[str, str | None]) -> str:
    """`uri` (no fragment: redirect_ok) with params added to its query."""
    extra = urlencode({k: v for k, v in params.items() if v is not None})
    return f"{uri}{'&' if '?' in uri else '?'}{extra}"


def request_key(jwt_secret: str) -> bytes:
    """A key for sealed authorization requests only, derived from the JWT secret."""
    return hmac.new(jwt_secret.encode(), REQUEST_LABEL, hashlib.sha256).digest()


def seal(key: bytes, payload: dict) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_b64(hmac.new(key, body.encode(), hashlib.sha256).digest())}"


# the longest request (two 2,048-character URLs, a 1,024-character state) seals to ~7,200
SEALED = re.compile(r"^[A-Za-z0-9_-]{1,8000}\.[A-Za-z0-9_-]{43}$")


def unseal(key: bytes, sealed: str, now: int) -> dict | None:
    """The request, or None when it isn't ours, was changed or has expired."""
    if not isinstance(sealed, str) or not SEALED.fullmatch(sealed):
        return None
    body, sig = sealed.split(".")
    good = _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, good):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("exp"), int) \
            or payload["exp"] <= now:
        return None
    return payload


def host_of(uri: str | None) -> str | None:
    try:
        return urlsplit(uri).hostname if uri else None
    except ValueError:
        return None


# --- the store -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Client:
    client_key: str
    client_id: str
    kind: Literal["registered", "metadata_document"]
    name: str
    client_uri: str | None
    redirect_uris: tuple[str, ...]
    auth_method: str
    secret_hash: bytes | None
    created_at: datetime
    refreshed_at: datetime
    fresh_until: datetime | None


@dataclass(frozen=True)
class TokenPair:
    access: str
    refresh: str


@dataclass(frozen=True)
class Issued:
    """What a code or refresh exchange came to: `ok` with the grant's tokens, else why not
    (`invalid`: unknown, expired, spent on a mismatch, revoked; `reused`: a second presentation,
    and the grant is now revoked)."""
    outcome: Literal["ok", "invalid", "reused"]
    grant_id: str | None = None
    user_id: str | None = None


@dataclass(frozen=True)
class Presented:
    """A code or refresh token as the store found it, for the route's checks."""
    client_key: str
    resource: str
    scopes: tuple[str, ...]
    expires_at: datetime
    redirect_uri: str | None = None  # codes
    code_challenge: str | None = None  # codes


@dataclass(frozen=True)
class TokenUse:
    """A request made with an app's access token: whose, which app, what the limits said."""
    grant_id: str
    user_id: str
    client_name: str
    scopes: tuple[str, ...]
    resource: str
    expires_at: datetime
    outcome: Literal["ok", "minute", "day"]
    retry_after: int
    minute_left: int
    day_left: int


class TooManyGrants(Exception):
    pass


class AlreadyDecided(Exception):
    """The consent request was approved already (its nonce's hash is on a grant)."""


class OAuthStore(Protocol):
    def client(self, client_id: str) -> Client | None: ...
    def register(self, client_id: str, name: str, client_uri: str | None,
                 redirect_uris: tuple[str, ...], auth_method: str, secret_hash: bytes | None,
                 now: datetime) -> Client: ...
    def save_document(self, url: str, name: str, client_uri: str | None,
                      redirect_uris: tuple[str, ...], now: datetime,
                      fresh_until: datetime) -> Client: ...
    def approve(self, user_id: str, client_key: str, resource: str, redirect_uri: str,
                code_challenge: str, code_hash: bytes, consent_hash: bytes,
                now: datetime) -> str: ...
    def exchange_code(self, code_hash: bytes, now: datetime,
                      check: Callable[[Presented], bool], tokens: TokenPair) -> Issued: ...
    def refresh(self, token_hash: bytes, now: datetime, check: Callable[[Presented], bool],
                tokens: TokenPair) -> Issued: ...
    def revoke(self, token_hash: bytes, client_key: str, now: datetime) -> bool: ...
    def use(self, token_hash: bytes, now: datetime, limits: Limits) -> TokenUse | None: ...
    def connections(self, user_id: str, now: datetime) -> list[dict]: ...
    def disconnect(self, user_id: str, client_key: str, now: datetime) -> int: ...


class Pool(Protocol):
    def connection(self) -> Any: ...


CLIENT_COLUMNS = ("client_key, client_id, kind, client_name, client_uri, redirect_uris,"
                  " auth_method, secret_hash, created_at, refreshed_at, fresh_until")

# a grant whose user may still use it: not revoked and, for a password account (013), the account
# not disabled and the grant made after its last password change (the rule its tokens follow)
_GRANT_LIVE = (
    " g.revoked_at IS NULL"
    " AND (g.user_id NOT LIKE 'pw-%%' OR EXISTS ("
    "      SELECT 1 FROM password_accounts p WHERE p.user_id = g.user_id"
    "        AND NOT p.disabled AND g.created_at >= p.sessions_valid_after))")

# api_keys._COUNT for a grant: one row per grant per UTC day holds the day's and the minute's
_COUNT = (
    "INSERT INTO oauth_usage AS u (grant_id, user_id, day, requests, minute, minute_requests)"
    " VALUES (%(grant)s, %(user)s, %(day)s, 1, %(minute)s, 1)"
    " ON CONFLICT (grant_id, day) DO UPDATE SET"
    "   requests = u.requests + 1,"
    "   minute = GREATEST(u.minute, EXCLUDED.minute),"
    "   minute_requests = CASE WHEN EXCLUDED.minute > u.minute THEN 1"
    "                          ELSE u.minute_requests + 1 END"
    " WHERE u.requests < %(per_day)s"
    "   AND (EXCLUDED.minute > u.minute OR u.minute_requests < %(per_minute)s)"
    " RETURNING requests, minute_requests")


def _client(row: Mapping[str, Any]) -> Client:
    return Client(client_key=str(row["client_key"]), client_id=row["client_id"],
                  kind=row["kind"], name=row["client_name"], client_uri=row["client_uri"],
                  redirect_uris=tuple(row["redirect_uris"]), auth_method=row["auth_method"],
                  secret_hash=bytes(row["secret_hash"]) if row["secret_hash"] else None,
                  created_at=row["created_at"], refreshed_at=row["refreshed_at"],
                  fresh_until=row["fresh_until"])


class AppOAuth:
    """Clients, grants, codes and tokens in the app database. Grants, codes, tokens and usage
    are read and written as their user (app.user_id, forced RLS) with a user filter as well; a
    code or token is found by its hash alone (the holder policies) before the user is known."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @contextmanager
    def _tx(self, user_id: str | None = None) -> Iterator[psycopg.Cursor]:
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            if user_id is not None:
                cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            yield cur

    # clients (no user data: no row-level security)

    def client(self, client_id: str) -> Client | None:
        with self._tx() as cur:
            cur.execute(f"SELECT {CLIENT_COLUMNS} FROM oauth_clients WHERE client_id = %s",
                        (client_id,))
            row = cur.fetchone()
            return _client(row) if row else None

    def register(self, client_id: str, name: str, client_uri: str | None,
                 redirect_uris: tuple[str, ...], auth_method: str, secret_hash: bytes | None,
                 now: datetime) -> Client:
        with self._tx() as cur:
            # registrations never used go after a day, used ones 60 days after their last use
            # (their grants' refresh tokens are long dead); a few at a time
            cur.execute("DELETE FROM oauth_clients WHERE client_key IN ("
                        " SELECT client_key FROM oauth_clients WHERE kind = 'registered'"
                        "  AND ((last_used_at IS NULL AND created_at < %s)"
                        "       OR last_used_at < %s) LIMIT 100)",
                        (now - UNUSED_CLIENT_TTL, now - IDLE_CLIENT_TTL))
            cur.execute("INSERT INTO oauth_clients (client_id, kind, client_name, client_uri,"
                        " redirect_uris, auth_method, secret_hash, created_at, refreshed_at)"
                        " VALUES (%s, 'registered', %s, %s, %s, %s, %s, %s, %s)"
                        f" RETURNING {CLIENT_COLUMNS}",
                        (client_id, name, client_uri, list(redirect_uris), auth_method,
                         secret_hash, now, now))
            row = cur.fetchone()
            assert row is not None
            return _client(row)

    def save_document(self, url: str, name: str, client_uri: str | None,
                      redirect_uris: tuple[str, ...], now: datetime,
                      fresh_until: datetime) -> Client:
        with self._tx() as cur:
            cur.execute("INSERT INTO oauth_clients (client_id, kind, client_name, client_uri,"
                        " redirect_uris, auth_method, created_at, refreshed_at, fresh_until)"
                        " VALUES (%s, 'metadata_document', %s, %s, %s, 'none', %s, %s, %s)"
                        " ON CONFLICT (client_id) DO UPDATE SET"
                        "  client_name = EXCLUDED.client_name, client_uri = EXCLUDED.client_uri,"
                        "  redirect_uris = EXCLUDED.redirect_uris,"
                        "  refreshed_at = EXCLUDED.refreshed_at,"
                        "  fresh_until = EXCLUDED.fresh_until"
                        " WHERE oauth_clients.kind = 'metadata_document'"
                        f" RETURNING {CLIENT_COLUMNS}",
                        (url, name, client_uri, list(redirect_uris), now, now, fresh_until))
            row = cur.fetchone()
            if row is None:  # a registered client can't own an https id (a CHECK); never here
                raise psycopg.errors.UniqueViolation("client_id")
            return _client(row)

    # consent

    def approve(self, user_id: str, client_key: str, resource: str, redirect_uri: str,
                code_challenge: str, code_hash: bytes, consent_hash: bytes,
                now: datetime) -> str:
        try:
            return self._approve(user_id, client_key, resource, redirect_uri, code_challenge,
                                 code_hash, consent_hash, now)
        except psycopg.errors.UniqueViolation:
            raise AlreadyDecided from None

    def _approve(self, user_id: str, client_key: str, resource: str, redirect_uri: str,
                 code_challenge: str, code_hash: bytes, consent_hash: bytes,
                 now: datetime) -> str:
        with self._tx(user_id) as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        ("oauth_grants:" + user_id,))
            # old codes, long-revoked grants, and grants that never got (or outlived) a token
            cur.execute("DELETE FROM oauth_codes WHERE user_id = %s AND expires_at < %s",
                        (user_id, now - timedelta(days=1)))
            cur.execute("DELETE FROM oauth_grants WHERE user_id = %s AND revoked_at < %s",
                        (user_id, now - timedelta(days=30)))
            cur.execute("UPDATE oauth_grants g SET revoked_at = greatest(%s, g.created_at)"
                        " WHERE g.user_id = %s AND g.revoked_at IS NULL AND g.created_at < %s"
                        "   AND NOT EXISTS (SELECT 1 FROM oauth_tokens t"
                        "    WHERE t.grant_id = g.grant_id AND t.user_id = g.user_id"
                        "      AND t.expires_at > %s)",
                        (now, user_id, now - timedelta(hours=1), now))
            cur.execute("SELECT count(*) AS n FROM oauth_grants WHERE user_id = %s"
                        " AND revoked_at IS NULL", (user_id,))
            if (cur.fetchone() or {"n": 0})["n"] >= MAX_GRANTS:
                raise TooManyGrants
            cur.execute("INSERT INTO oauth_grants (user_id, client_key, scopes, resource,"
                        " consent_hash, created_at) VALUES (%s, %s, %s, %s, %s, %s)"
                        " RETURNING grant_id",
                        (user_id, client_key, list(SCOPES), resource, consent_hash, now))
            row = cur.fetchone()
            assert row is not None
            grant_id = row["grant_id"]
            cur.execute("INSERT INTO oauth_codes (code_hash, grant_id, user_id, redirect_uri,"
                        " code_challenge, created_at, expires_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (code_hash, grant_id, user_id, redirect_uri, code_challenge, now,
                         now + CODE_TTL))
            cur.execute("UPDATE oauth_clients SET last_used_at = %s WHERE client_key = %s",
                        (now, client_key))
            return str(grant_id)

    # the token endpoint

    def _issue(self, cur: psycopg.Cursor, user_id: str, grant_id: Any, client_key: Any,
               tokens: TokenPair, now: datetime) -> None:
        cur.execute("INSERT INTO oauth_tokens (token_hash, grant_id, user_id, kind, prefix,"
                    " created_at, expires_at) VALUES"
                    " (%s, %s, %s, 'access', %s, %s, %s), (%s, %s, %s, 'refresh', %s, %s, %s)",
                    (digest(tokens.access), grant_id, user_id, tokens.access[:SHOWN], now,
                     now + ACCESS_TTL,
                     digest(tokens.refresh), grant_id, user_id, tokens.refresh[:SHOWN], now,
                     now + REFRESH_TTL))
        cur.execute("UPDATE oauth_clients SET last_used_at = %s WHERE client_key = %s",
                    (now, client_key))

    def _revoke_grant(self, cur: psycopg.Cursor, user_id: str, grant_id: Any,
                      now: datetime) -> None:
        cur.execute("UPDATE oauth_grants SET revoked_at = greatest(%s, created_at)"
                    " WHERE grant_id = %s AND user_id = %s AND revoked_at IS NULL",
                    (now, grant_id, user_id))

    def _grant(self, cur: psycopg.Cursor, user_id: str, grant_id: Any) -> dict | None:
        cur.execute("SELECT g.grant_id, g.client_key, g.scopes, g.resource FROM oauth_grants g"
                    f" WHERE g.grant_id = %s AND g.user_id = %s AND {_GRANT_LIVE}",
                    (grant_id, user_id))
        return cur.fetchone()

    def exchange_code(self, code_hash: bytes, now: datetime,
                      check: Callable[[Presented], bool], tokens: TokenPair) -> Issued:
        with self._tx() as cur:
            cur.execute("SELECT set_config('app.oauth_code_hash', %s, true)", (code_hash.hex(),))
            cur.execute("SELECT user_id, grant_id FROM oauth_codes WHERE code_hash = %s",
                        (code_hash,))
            found = cur.fetchone()
            if found is None:
                return Issued("invalid")
            user_id, grant_id = found["user_id"], found["grant_id"]
            cur.execute("SELECT set_config('app.oauth_code_hash', '', true),"
                        " set_config('app.user_id', %s, true)", (user_id,))
            # spent now, whatever follows: a second presentation finds it used
            cur.execute("UPDATE oauth_codes SET used_at = %s WHERE code_hash = %s"
                        " AND user_id = %s AND used_at IS NULL"
                        " RETURNING redirect_uri, code_challenge, expires_at",
                        (now, code_hash, user_id))
            code = cur.fetchone()
            if code is None:
                self._revoke_grant(cur, user_id, grant_id, now)
                return Issued("reused", str(grant_id), user_id)
            grant = self._grant(cur, user_id, grant_id)
            if grant is None or code["expires_at"] <= now or not check(Presented(
                    client_key=str(grant["client_key"]), resource=grant["resource"],
                    scopes=tuple(grant["scopes"]), expires_at=code["expires_at"],
                    redirect_uri=code["redirect_uri"],
                    code_challenge=code["code_challenge"])):
                return Issued("invalid")
            self._issue(cur, user_id, grant_id, grant["client_key"], tokens, now)
            return Issued("ok", str(grant_id), user_id)

    def refresh(self, token_hash: bytes, now: datetime, check: Callable[[Presented], bool],
                tokens: TokenPair) -> Issued:
        with self._tx() as cur:
            cur.execute("SELECT set_config('app.oauth_token_hash', %s, true)",
                        (token_hash.hex(),))
            cur.execute("SELECT user_id, grant_id FROM oauth_tokens WHERE token_hash = %s"
                        " AND kind = 'refresh'", (token_hash,))
            found = cur.fetchone()
            if found is None:
                return Issued("invalid")
            user_id, grant_id = found["user_id"], found["grant_id"]
            cur.execute("SELECT set_config('app.oauth_token_hash', '', true),"
                        " set_config('app.user_id', %s, true)", (user_id,))
            cur.execute("UPDATE oauth_tokens SET used_at = %s WHERE token_hash = %s"
                        " AND user_id = %s AND used_at IS NULL RETURNING expires_at",
                        (now, token_hash, user_id))
            token = cur.fetchone()
            if token is None:  # rotated already: someone else holds a copy
                self._revoke_grant(cur, user_id, grant_id, now)
                return Issued("reused", str(grant_id), user_id)
            grant = self._grant(cur, user_id, grant_id)
            if grant is None or token["expires_at"] <= now or not check(Presented(
                    client_key=str(grant["client_key"]), resource=grant["resource"],
                    scopes=tuple(grant["scopes"]), expires_at=token["expires_at"])):
                return Issued("invalid")
            # the grant's dead tokens go (used refresh tokens stay until they expire: reuse
            # detection needs them)
            cur.execute("DELETE FROM oauth_tokens WHERE grant_id = %s AND user_id = %s"
                        " AND expires_at < %s", (grant_id, user_id, now))
            self._issue(cur, user_id, grant_id, grant["client_key"], tokens, now)
            return Issued("ok", str(grant_id), user_id)

    def revoke(self, token_hash: bytes, client_key: str, now: datetime) -> bool:
        with self._tx() as cur:
            cur.execute("SELECT set_config('app.oauth_token_hash', %s, true)",
                        (token_hash.hex(),))
            cur.execute("SELECT user_id, grant_id FROM oauth_tokens WHERE token_hash = %s",
                        (token_hash,))
            found = cur.fetchone()
            if found is None:
                return False
            user_id, grant_id = found["user_id"], found["grant_id"]
            cur.execute("SELECT set_config('app.oauth_token_hash', '', true),"
                        " set_config('app.user_id', %s, true)", (user_id,))
            cur.execute("UPDATE oauth_grants SET revoked_at = greatest(%s, created_at)"
                        " WHERE grant_id = %s AND user_id = %s AND client_key = %s"
                        " AND revoked_at IS NULL", (now, grant_id, user_id, client_key))
            return cur.rowcount == 1

    # resource requests

    def use(self, token_hash: bytes, now: datetime, limits: Limits) -> TokenUse | None:
        now = now.astimezone(UTC)
        minute = now.replace(second=0, microsecond=0)
        with self._tx() as cur:
            cur.execute("SELECT set_config('app.oauth_token_hash', %s, true)",
                        (token_hash.hex(),))
            cur.execute("SELECT user_id, grant_id, token_hash, expires_at FROM oauth_tokens"
                        " WHERE token_hash = %s AND kind = 'access' AND expires_at > %s",
                        (token_hash, now))
            found = cur.fetchone()
            if found is None or not hmac.compare_digest(bytes(found["token_hash"]), token_hash):
                return None
            user_id, grant_id = found["user_id"], found["grant_id"]
            # from here as the grant's user only
            cur.execute("SELECT set_config('app.oauth_token_hash', '', true),"
                        " set_config('app.user_id', %s, true)", (user_id,))
            cur.execute("SELECT g.scopes, g.resource, c.client_name FROM oauth_grants g"
                        " JOIN oauth_clients c ON c.client_key = g.client_key"
                        f" WHERE g.grant_id = %s AND g.user_id = %s AND {_GRANT_LIVE}",
                        (grant_id, user_id))
            grant = cur.fetchone()
            if grant is None:
                return None
            cur.execute(_COUNT, {"grant": grant_id, "user": user_id, "day": now.date(),
                                 "minute": minute, "per_day": limits.per_day,
                                 "per_minute": limits.per_minute})
            counted = cur.fetchone()
            outcome: Literal["ok", "minute", "day"] = "ok"
            retry = 0
            if counted is None:
                cur.execute("UPDATE oauth_usage SET limited = limited + 1"
                            " WHERE grant_id = %s AND user_id = %s AND day = %s"
                            " RETURNING requests, minute, minute_requests",
                            (grant_id, user_id, now.date()))
                counted = cur.fetchone() or {"requests": limits.per_day, "minute": minute,
                                             "minute_requests": limits.per_minute}
                if counted["requests"] >= limits.per_day:
                    outcome = "day"
                    midnight = datetime.combine(now.date() + timedelta(days=1),
                                                datetime.min.time(), UTC)
                    retry = int((midnight - now).total_seconds()) + 1
                else:
                    outcome = "minute"
                    retry = int((counted["minute"] + timedelta(minutes=1) - now)
                                .total_seconds()) + 1
                retry = max(1, retry)
            else:
                cur.execute("UPDATE oauth_grants SET last_used_at = %s"
                            " WHERE grant_id = %s AND user_id = %s"
                            "   AND (last_used_at IS NULL OR last_used_at < %s)",
                            (now, grant_id, user_id, now - LAST_USED_EVERY))
            return TokenUse(
                grant_id=str(grant_id), user_id=user_id, client_name=grant["client_name"],
                scopes=tuple(grant["scopes"]), resource=grant["resource"],
                expires_at=found["expires_at"], outcome=outcome, retry_after=retry,
                minute_left=max(0, limits.per_minute - counted["minute_requests"]),
                day_left=max(0, limits.per_day - counted["requests"]))

    # Settings

    def connections(self, user_id: str, now: datetime) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute(
                "SELECT c.client_key, c.client_id, c.kind, c.client_name, c.redirect_uris,"
                "  min(g.created_at) AS connected_at, max(g.last_used_at) AS last_used_at"
                " FROM oauth_grants g JOIN oauth_clients c ON c.client_key = g.client_key"
                f" WHERE g.user_id = %(user)s AND {_GRANT_LIVE}"
                "   AND EXISTS (SELECT 1 FROM oauth_tokens t WHERE t.grant_id = g.grant_id"
                "     AND t.user_id = g.user_id AND t.kind = 'refresh' AND t.used_at IS NULL"
                "     AND t.expires_at > %(now)s)"
                " GROUP BY c.client_key"
                " ORDER BY max(coalesce(g.last_used_at, g.created_at)) DESC LIMIT 50",
                {"user": user_id, "now": now})
            return cur.fetchall()

    def disconnect(self, user_id: str, client_key: str, now: datetime) -> int:
        with self._tx(user_id) as cur:
            cur.execute("UPDATE oauth_grants SET revoked_at = greatest(%s, created_at)"
                        " WHERE user_id = %s AND client_key = %s AND revoked_at IS NULL",
                        (now, user_id, client_key))
            return cur.rowcount


# --- using a token -------------------------------------------------------------------------------

class OAuthAuth:
    """`user(request, token)`: the token's user for this request, or an HTTPException (401 for a
    token the API doesn't take, 403 for a route an app can't use, 429 over a limit, 503 when the
    app database can't answer). Sets request.state.oauth (a TokenUse) for /v1/me and the
    X-RateLimit-* headers."""

    def __init__(self, store: OAuthStore | None, config: OAuthConfig | None, limits: Limits,
                 clock: Callable[[], datetime]) -> None:
        self.store, self.config, self.limits, self.clock = store, config, limits, clock

    def user(self, request: Request, token: str) -> str:
        if (self.store is None or self.config is None or not TOKEN.fullmatch(token)
                or not token.startswith(ACCESS_PREFIX)):
            EVENTS.labels("use", "invalid").inc()
            raise HTTPException(401, INVALID)
        try:
            use = self.store.use(digest(token), self.clock(), self.limits)
        except psycopg.Error as e:
            EVENTS.labels("use", "unavailable").inc()
            log.warning("oauth token check unavailable: %s", type(e).__name__)
            raise HTTPException(503, "sign-in check unavailable") from None
        if use is None or canonical_resource(use.resource) != canonical_resource(
                self.config.resource):
            EVENTS.labels("use", "invalid").inc()
            raise HTTPException(401, INVALID)
        request.state.oauth = use
        if use.outcome != "ok":
            EVENTS.labels("use", f"limited_{use.outcome}").inc()
            log.info("connected app over its %s limit", use.outcome,
                     extra={"user_id": use.user_id, "grant_id": use.grant_id})
            per = self.limits.per_minute if use.outcome == "minute" else self.limits.per_day
            raise HTTPException(429, f"this connected app made {per:,} requests this "
                                     f"{use.outcome}{' (UTC)' if use.outcome == 'day' else ''};"
                                     f" try again in {use.retry_after} seconds",
                                headers={"Retry-After": str(use.retry_after)})
        route = request.scope.get("route")
        if (request.method, getattr(route, "path", "")) not in READ_ROUTES:
            EVENTS.labels("use", "refused_route").inc()
            raise HTTPException(403, NOT_FOR_APPS)
        EVENTS.labels("use", "ok").inc()
        return use.user_id


def app_info(request: Request) -> dict | None:
    """/v1/me's `connected_app` when the request was made with an app's token."""
    use = getattr(request.state, "oauth", None)
    if not isinstance(use, TokenUse):
        return None
    return {"name": use.client_name, "scopes": list(use.scopes), "expires_at": use.expires_at}


# --- the routes ----------------------------------------------------------------------------------

class ConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1, max_length=8100)


def _oauth_error(status: int, error: str, description: str,
                 headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status,
                        headers={**NO_STORE, **CORS, **(headers or {})})


class _Form(dict):
    """A form body parsed by hand: one value per name (a repeated name is refused)."""


def _read_form(raw: bytes) -> _Form | None:
    try:
        pairs = parse_qsl(raw.decode("ascii"), keep_blank_values=True, strict_parsing=False,
                          max_num_fields=30)
    except (UnicodeDecodeError, ValueError):
        return None
    form = _Form()
    for k, v in pairs:
        if k in form:
            return None
        form[k] = v
    return form


async def _body(request: Request) -> bytes | None:
    """The request body, or None past MAX_BODY (by Content-Length or as read)."""
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_BODY):
        return None
    out = bytearray()
    async for chunk in request.stream():
        out.extend(chunk)
        if len(out) > MAX_BODY:
            return None
    return bytes(out)


def add_oauth_routes(app: FastAPI, *, config: OAuthConfig | None, store: OAuthStore | None,
                     documents: DocumentFetcher | None, jwt_secret: str,
                     current_user: Callable[..., str], limits: Limits,
                     clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                     fetch_limiter: IpLimiter | None = None,
                     registration_limiter: IpLimiter | None = None,
                     client_ip_header: str = "X-Client-IP") -> None:
    """The authorization server's routes (404 unless `config` and `store`), the consent and
    Connected apps routes, and the X-RateLimit-* headers on app-token answers."""
    User = Annotated[str, Depends(current_user)]
    key = request_key(jwt_secret)
    fetches = fetch_limiter or IpLimiter(FETCHES_PER_IP, FETCH_WINDOW)
    fetches_total = IpLimiter(FETCHES_TOTAL, FETCH_WINDOW)
    registrations = registration_limiter or IpLimiter(REGISTRATIONS_PER_IP, REGISTRATION_WINDOW)
    registrations_total = IpLimiter(REGISTRATIONS_TOTAL, REGISTRATION_WINDOW)

    @app.middleware("http")
    async def oauth_rate_headers(request: Request, call_next):
        response = await call_next(request)
        use = getattr(request.state, "oauth", None)
        if isinstance(use, TokenUse):
            response.headers.update(rate_headers(use, limits))
        return response

    def need() -> tuple[OAuthConfig, OAuthStore]:
        if config is None or store is None:
            raise HTTPException(404)
        return config, store

    def client_ip(request: Request) -> str:
        ip = request.headers.get(client_ip_header, "").strip()
        if ip:
            return ip
        return request.client.host if request.client else "unknown"

    def resolve(client_id: str, ip: str, now: datetime) -> Client | None:
        """The client a client_id names: a registered one, or a metadata document (cached, else
        fetched); None for none the server can use."""
        assert store is not None
        if CLIENT_ID.fullmatch(client_id):
            found = store.client(client_id)
            return found if found and found.kind == "registered" else None
        if not document_url(client_id):
            return None
        cached = store.client(client_id)
        if cached is not None and cached.kind != "metadata_document":
            return None
        if cached is not None and cached.fresh_until and cached.fresh_until > now:
            return cached
        stale_ok = cached if cached and cached.refreshed_at > now - DOC_STALE_OK else None
        if documents is None or not fetches.take(ip) or not fetches_total.take("*"):
            EVENTS.labels("document", "limited").inc()
            return stale_ok
        try:
            fetched = documents.fetch(client_id)
            doc = parse_document(client_id, fetched.body, redirect_ok)
        except DocumentRefused as e:
            EVENTS.labels("document", e.code).inc()
            log.info("client metadata document refused: %s", e.code)
            return stale_ok
        ttl = timedelta(seconds=fetched.max_age) if fetched.max_age is not None else DOC_MIN_TTL
        ttl = min(max(ttl, DOC_MIN_TTL), DOC_MAX_TTL)
        EVENTS.labels("document", "ok").inc()
        return store.save_document(client_id, doc.name, doc.client_uri, doc.redirect_uris, now,
                                   now + ttl)

    def page_error(code: str) -> RedirectResponse:
        """A problem with the client or its redirect URI: Tributary's own page, never theirs."""
        EVENTS.labels("authorize", code).inc()
        return RedirectResponse(f"{CONNECT_PAGE}?{urlencode({'error': code})}", status_code=302,
                                headers=NO_STORE)

    def back(redirect_uri: str, params: dict[str, str | None]) -> RedirectResponse:
        assert config is not None
        return RedirectResponse(with_params(redirect_uri, {**params, "iss": config.issuer}),
                                status_code=302, headers=NO_STORE)

    # --- metadata -------------------------------------------------------------------------------

    @app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    def metadata() -> JSONResponse:
        cfg, _ = need()
        return JSONResponse(cfg.metadata(), headers={**CORS, "Cache-Control": "max-age=3600"})

    for path in ("/.well-known/oauth-authorization-server", "/oauth/token", "/oauth/register",
                 "/oauth/revoke"):
        app.add_api_route(path, lambda: Response(status_code=204, headers=CORS),
                          methods=["OPTIONS"], include_in_schema=False)

    # --- authorization ----------------------------------------------------------------------------

    @app.get("/oauth/authorize", include_in_schema=False)
    def authorize(request: Request) -> RedirectResponse:
        cfg, _ = need()
        now = clock()
        q = request.query_params
        repeated = {k for k in q if len(q.getlist(k)) > 1}
        client_id = q.get("client_id") or ""
        if not client_id or len(client_id) > URL_MAX or "client_id" in repeated:
            return page_error("client")
        try:
            client = resolve(client_id, client_ip(request), now)
        except psycopg.Error as e:
            log.warning("oauth authorize unavailable: %s", type(e).__name__)
            return page_error("unavailable")
        if client is None:
            return page_error("client")
        given = q.get("redirect_uri")
        redirect_uri = redirect_matches(client.redirect_uris, given) \
            if given and "redirect_uri" not in repeated else None
        if redirect_uri is None:
            return page_error("redirect_uri")
        # from here a problem goes back to the client, with its state
        state = q.get("state")
        if state is not None and (len(state) > STATE_MAX or "state" in repeated):
            return back(redirect_uri, {"error": "invalid_request",
                                       "error_description": "state is too long"})
        if repeated:
            return back(redirect_uri, {"error": "invalid_request", "state": state,
                                       "error_description": "a parameter is repeated"})
        if q.get("response_type") != "code":
            EVENTS.labels("authorize", "response_type").inc()
            return back(redirect_uri, {"error": "unsupported_response_type", "state": state,
                                       "error_description": "response_type must be code"})
        challenge = q.get("code_challenge") or ""
        if q.get("code_challenge_method") != "S256" or not CHALLENGE.fullmatch(challenge):
            EVENTS.labels("authorize", "pkce").inc()
            return back(redirect_uri, {"error": "invalid_request", "state": state,
                                       "error_description": "PKCE is required: a "
                                       "code_challenge with code_challenge_method S256"})
        resource = q.get("resource")
        if resource is not None and canonical_resource(resource) != canonical_resource(
                cfg.resource):
            EVENTS.labels("authorize", "resource").inc()
            return back(redirect_uri, {"error": "invalid_target", "state": state,
                                       "error_description": "tokens are issued only for "
                                       + cfg.resource})
        nonce = secrets.token_urlsafe(24)
        sealed = seal(key, {
            "c": client.client_id, "r": redirect_uri, "ch": challenge, "st": state,
            "res": cfg.resource, "n": _b64(digest(nonce)),
            "exp": int(now.timestamp()) + REQUEST_TTL})
        EVENTS.labels("authorize", "ok").inc()
        r = RedirectResponse(f"{CONNECT_PAGE}?{urlencode({'request': sealed})}",
                             status_code=302, headers=NO_STORE)
        r.set_cookie(NONCE_COOKIE, nonce, max_age=REQUEST_TTL, path=COOKIE_PATH, httponly=True,
                     secure=True, samesite="lax")
        return r

    def opened(body: ConsentBody, nonce: str | None) -> tuple[dict, Client]:
        """The sealed request, checked again: ours, unexpired, started in this browser, and its
        client and redirect URI still good. 400 otherwise (one message)."""
        assert store is not None
        now = clock()
        req = unseal(key, body.request, int(now.timestamp()))
        if req is None or not nonce or not hmac.compare_digest(
                str(req.get("n", "")), _b64(digest(nonce))):
            raise HTTPException(400, EXPIRED)
        client = store.client(str(req.get("c", "")))
        if client is None or redirect_matches(client.redirect_uris, str(req.get("r"))) is None:
            raise HTTPException(400, EXPIRED)
        return req, client

    def describe_client(client: Client, redirect_uri: str) -> dict:
        return {"name": client.name, "kind": client.kind,
                "publisher": host_of(client.client_id)
                if client.kind == "metadata_document" else None,
                "registered_at": client.created_at if client.kind == "registered" else None,
                "redirect_uri": redirect_uri, "redirect_host": host_of(redirect_uri),
                "loopback": is_loopback(redirect_uri)}

    def decided(response: Response) -> None:
        response.headers.update(NO_STORE)
        response.delete_cookie(NONCE_COOKIE, path=COOKIE_PATH, httponly=True, secure=True,
                               samesite="lax")

    @app.post("/v1/oauth/consent", response_model=dict)
    def consent(user_id: User, body: ConsentBody, response: Response,
                nonce: Annotated[str | None, Cookie(alias=NONCE_COOKIE)] = None) -> dict:
        """What the consent screen shows for a sealed request: the app, where it sends the user
        back, what it may do, and whether this account may connect apps."""
        need()
        req, client = opened(body, nonce)
        response.headers.update(NO_STORE)
        return {"client": describe_client(client, str(req["r"])), "scopes": list(SCOPES),
                "allowed": not is_sandbox_user(user_id),
                "expires_at": datetime.fromtimestamp(req["exp"], UTC)}

    @app.post("/v1/oauth/consent/approve", response_model=dict)
    def approve(user_id: User, body: ConsentBody, response: Response,
                nonce: Annotated[str | None, Cookie(alias=NONCE_COOKIE)] = None) -> dict:
        """Connects the app: a grant and a single-use code (60 s); `redirect` is where the
        browser goes next (the app's redirect URI with the code, state and iss)."""
        cfg, st = need()
        req, client = opened(body, nonce)
        if is_sandbox_user(user_id):
            EVENTS.labels("approve", "sandbox").inc()
            raise HTTPException(403, NO_SANDBOX)
        code = _b64(secrets.token_bytes(32))
        try:
            grant_id = st.approve(user_id, client.client_key, str(req["res"]), str(req["r"]),
                                  str(req["ch"]), digest(code), digest(str(req["n"])), clock())
        except AlreadyDecided:
            raise HTTPException(400, EXPIRED) from None
        except TooManyGrants:
            raise HTTPException(409, f"at most {MAX_GRANTS} connected apps: disconnect one in "
                                     "Settings first") from None
        except psycopg.errors.ForeignKeyViolation:
            raise HTTPException(409, "no account to connect an app to") from None
        except psycopg.Error as e:
            log.warning("oauth approve failed: %s", type(e).__name__, extra={"user_id": user_id})
            raise HTTPException(503, "connected apps unavailable") from None
        EVENTS.labels("approve", "ok").inc()
        log.info("connected app approved", extra={"user_id": user_id, "grant_id": grant_id,
                                                  "client_key": client.client_key})
        decided(response)
        return {"redirect": with_params(str(req["r"]), {"code": code, "state": req.get("st"),
                                                        "iss": cfg.issuer})}

    @app.post("/v1/oauth/consent/deny", response_model=dict)
    def deny(user_id: User, body: ConsentBody, response: Response,
             nonce: Annotated[str | None, Cookie(alias=NONCE_COOKIE)] = None) -> dict:
        """Says no: `redirect` takes the browser back to the app with access_denied."""
        cfg, _ = need()
        req, _client = opened(body, nonce)
        EVENTS.labels("deny", "ok").inc()
        decided(response)
        return {"redirect": with_params(str(req["r"]), {
            "error": "access_denied", "error_description": "the user didn't connect the app",
            "state": req.get("st"), "iss": cfg.issuer})}

    # --- registration ----------------------------------------------------------------------------

    def bad_metadata(description: str) -> JSONResponse:
        EVENTS.labels("register", "refused").inc()
        return _oauth_error(400, "invalid_client_metadata", description)

    @app.post("/oauth/register", include_in_schema=False)
    async def register(request: Request) -> JSONResponse:
        """RFC 7591 dynamic registration: the JSON body is read by hand."""
        _, st = need()
        raw = await _body(request)
        if raw is None:
            return bad_metadata(f"the registration is larger than {MAX_BODY:,} bytes")
        try:
            meta = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            meta = None
        if not isinstance(meta, dict):
            return bad_metadata("send the client's metadata as a JSON object")
        uris = meta.get("redirect_uris")
        if not isinstance(uris, list) or not 1 <= len(uris) <= MAX_REDIRECT_URIS:
            return _oauth_error(400, "invalid_redirect_uri",
                                f"redirect_uris: 1 to {MAX_REDIRECT_URIS} URIs")
        if not all(isinstance(u, str) and redirect_ok(u) for u in uris):
            EVENTS.labels("register", "redirect_uri").inc()
            return _oauth_error(400, "invalid_redirect_uri",
                                "each redirect URI must be https://, or http:// on localhost, "
                                "127.0.0.1 or [::1], without a fragment")
        method = meta.get("token_endpoint_auth_method", "client_secret_basic")
        if method not in AUTH_METHODS:
            return bad_metadata("token_endpoint_auth_method: one of " + ", ".join(AUTH_METHODS))
        grants = meta.get("grant_types", ["authorization_code"])
        if (not isinstance(grants, list) or "authorization_code" not in grants
                or not set(grants) <= {"authorization_code", "refresh_token"}):
            return bad_metadata("grant_types: authorization_code, and refresh_token if wanted")
        responses = meta.get("response_types", ["code"])
        if responses != ["code"]:
            return bad_metadata("response_types: code")
        client_uri = meta.get("client_uri")
        if client_uri is not None and not (isinstance(client_uri, str) and redirect_ok(
                client_uri) and client_uri.startswith("https://")):
            return bad_metadata("client_uri: an https:// URL")
        uris = list(dict.fromkeys(uris))
        name = clean_name(meta.get("client_name"), host_of(uris[0]) or "An app")
        ip = client_ip(request)
        if not registrations.take(ip) or not registrations_total.take("*"):
            EVENTS.labels("register", "limited").inc()
            return _oauth_error(429, "temporarily_unavailable",
                                "too many registrations: try again later",
                                {"Retry-After": "600"})
        client_id = CLIENT_PREFIX + _b64(secrets.token_bytes(24))
        secret = new_token(SECRET_PREFIX) if method != "none" else None
        now = clock()
        try:
            client = st.register(client_id, name, client_uri, tuple(uris), method,
                                 digest(secret) if secret else None, now)
        except psycopg.Error as e:
            log.warning("oauth registration failed: %s", type(e).__name__)
            return _oauth_error(503, "temporarily_unavailable", "registration unavailable")
        EVENTS.labels("register", "ok").inc()
        log.info("oauth client registered", extra={"client_key": client.client_key})
        out: dict[str, Any] = {
            "client_id": client.client_id, "client_id_issued_at": int(now.timestamp()),
            "client_name": client.name, "redirect_uris": list(client.redirect_uris),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": method,
            "scope": " ".join(SCOPES)}
        if client_uri:
            out["client_uri"] = client_uri
        if secret:
            out.update(client_secret=secret, client_secret_expires_at=0)
        return JSONResponse(out, status_code=201, headers={**NO_STORE, **CORS})

    # --- the token endpoint ---------------------------------------------------------------

    def authenticate(request: Request, form: _Form) -> Client | JSONResponse:
        """The client of a token or revocation request (RFC 6749 2.3.1), or the 401."""
        assert store is not None
        basic_id = basic_secret = None
        header = request.headers.get("authorization", "")
        if header[:6].lower() == "basic ":
            try:
                decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
                left, sep, right = decoded.partition(":")
                if not sep:
                    raise ValueError
                basic_id, basic_secret = unquote(left), unquote(right)
            except (ValueError, UnicodeDecodeError):
                return _invalid_client(True)
            if "client_secret" in form:
                return _oauth_error(400, "invalid_request", "use one client authentication")
        client_id = basic_id or form.get("client_id")
        if basic_id and form.get("client_id") not in (None, basic_id):
            return _invalid_client(True)
        if not client_id or len(client_id) > URL_MAX:
            return _invalid_client(basic_id is not None)
        client = store.client(client_id)
        if client is None:
            return _invalid_client(basic_id is not None)
        if client.auth_method != "none":
            given = basic_secret if basic_id else form.get("client_secret")
            if (not given or not SECRET.fullmatch(given) or client.secret_hash is None
                    or not hmac.compare_digest(digest(given), client.secret_hash)):
                return _invalid_client(basic_id is not None)
        return client

    def _invalid_client(basic: bool) -> JSONResponse:
        EVENTS.labels("token", "invalid_client").inc()
        return _oauth_error(401, "invalid_client", "client authentication failed",
                            {"WWW-Authenticate": 'Basic realm="Tributary"'} if basic else None)

    def tokens_answer(pair: TokenPair) -> JSONResponse:
        return JSONResponse({"access_token": pair.access, "token_type": "Bearer",
                             "expires_in": int(ACCESS_TTL.total_seconds()),
                             "refresh_token": pair.refresh, "scope": " ".join(SCOPES)},
                            headers={**NO_STORE, **CORS})

    @app.post("/oauth/token", include_in_schema=False)
    async def token(request: Request) -> JSONResponse:
        cfg, st = need()
        kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
        raw = await _body(request)
        form = _read_form(raw) if raw is not None and kind == \
            "application/x-www-form-urlencoded" else None
        if form is None:
            return _oauth_error(400, "invalid_request",
                                "send a form (application/x-www-form-urlencoded), each "
                                f"parameter once, at most {MAX_BODY:,} bytes")
        client = authenticate(request, form)
        if isinstance(client, JSONResponse):
            return client
        grant_type = form.get("grant_type")
        resource = form.get("resource")
        if resource is not None and canonical_resource(resource) != canonical_resource(
                cfg.resource):
            EVENTS.labels("token", "resource").inc()
            return _oauth_error(400, "invalid_target", "tokens are issued only for "
                                + cfg.resource)
        now = clock()
        pair = TokenPair(new_token(ACCESS_PREFIX), new_token(REFRESH_PREFIX))
        try:
            if grant_type == "authorization_code":
                code, redirect_uri, verifier = (form.get("code"), form.get("redirect_uri"),
                                                form.get("code_verifier"))
                if not code or not redirect_uri or not verifier:
                    EVENTS.labels("token", "invalid_request").inc()
                    return _oauth_error(400, "invalid_request",
                                        "code, redirect_uri and code_verifier are required")
                if not CODE.fullmatch(code):
                    EVENTS.labels("token", "invalid").inc()
                    return _oauth_error(400, "invalid_grant",
                                        "the code is invalid, expired or used")

                def code_ok(p: Presented) -> bool:
                    return (p.client_key == client.client_key and p.redirect_uri == redirect_uri
                            and pkce_ok(verifier, p.code_challenge or "")
                            and canonical_resource(p.resource) == canonical_resource(
                                resource or p.resource))
                issued = st.exchange_code(digest(code), now, code_ok, pair)
                event = "token"
            elif grant_type == "refresh_token":
                presented = form.get("refresh_token") or ""
                if not TOKEN.fullmatch(presented) or not presented.startswith(REFRESH_PREFIX):
                    EVENTS.labels("refresh", "invalid").inc()
                    return _oauth_error(400, "invalid_grant",
                                        "the refresh token is invalid, expired or revoked")

                def refresh_ok(p: Presented) -> bool:
                    return p.client_key == client.client_key
                issued = st.refresh(digest(presented), now, refresh_ok, pair)
                event = "refresh"
            else:
                return _oauth_error(400, "unsupported_grant_type",
                                    "grant_type: authorization_code or refresh_token")
        except psycopg.Error as e:
            log.warning("oauth token request failed: %s", type(e).__name__)
            return _oauth_error(503, "temporarily_unavailable", "try again in a moment")
        EVENTS.labels(event, issued.outcome).inc()
        if issued.outcome == "reused":
            log.warning("oauth %s presented again: grant revoked", event,
                        extra={"user_id": issued.user_id, "grant_id": issued.grant_id})
        if issued.outcome != "ok":
            return _oauth_error(400, "invalid_grant",
                                "the code is invalid, expired or used" if event == "token"
                                else "the refresh token is invalid, expired or revoked")
        log.info("oauth tokens issued (%s)", event,
                 extra={"user_id": issued.user_id, "grant_id": issued.grant_id})
        return tokens_answer(pair)

    @app.post("/oauth/revoke", include_in_schema=False)
    async def revoke(request: Request) -> Response:
        """RFC 7009: the token's grant is revoked (every token it issued); 200 also for a token
        that is unknown or another client's."""
        _, st = need()
        kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
        raw = await _body(request)
        form = _read_form(raw) if raw is not None and kind == \
            "application/x-www-form-urlencoded" else None
        if form is None or not form.get("token"):
            return _oauth_error(400, "invalid_request", "send the token as a form parameter")
        client = authenticate(request, form)
        if isinstance(client, JSONResponse):
            return client
        presented = form["token"]
        try:
            done = TOKEN.fullmatch(presented) is not None and st.revoke(
                digest(presented), client.client_key, clock())
        except psycopg.Error as e:
            log.warning("oauth revocation failed: %s", type(e).__name__)
            return _oauth_error(503, "temporarily_unavailable", "try again in a moment")
        EVENTS.labels("revoke", "ok" if done else "unknown").inc()
        return Response(status_code=200, headers={**NO_STORE, **CORS})

    # --- Settings: Connected apps ---------------------------------------------------------

    @app.get("/v1/oauth/connections", response_model=dict)
    def connections(user_id: User) -> dict:
        """The apps connected to the caller's account (each with a live grant): name, who
        published it, where it sends the user back, connected on, last used."""
        _, st = need()
        if is_sandbox_user(user_id):
            return {"allowed": False, "connections": []}
        rows = st.connections(user_id, clock())
        return {"allowed": True, "connections": [{
            "connection_id": str(r["client_key"]), "name": r["client_name"],
            "kind": r["kind"],
            "publisher": host_of(r["client_id"]) if r["kind"] == "metadata_document" else None,
            "redirect_hosts": sorted({h for u in r["redirect_uris"] if (h := host_of(u))}),
            "connected_at": r["connected_at"], "last_used_at": r["last_used_at"],
        } for r in rows]}

    @app.delete("/v1/oauth/connections/{connection_id}", response_model=dict)
    def disconnect(user_id: User, connection_id: UUID) -> dict:
        """Disconnects an app: every grant the caller gave it is revoked at once (its tokens
        are refused from their next request). Another user's app, or one not connected, is the
        same 404."""
        _, st = need()
        n = st.disconnect(user_id, str(connection_id), clock())
        if not n:
            raise HTTPException(404, "no such connected app")
        log.info("connected app disconnected",
                 extra={"user_id": user_id, "client_key": str(connection_id)})
        return {"connection_id": str(connection_id), "disconnected": True}
