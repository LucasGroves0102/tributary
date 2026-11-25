"""API keys: personal access tokens for using Tributary from a user's own scripts (and next from
their own AI models, through the MCP server). App migration 019; README "API keys".

A key is "trb_" + 32 random bytes in base64url (47 characters). It is shown once, in the answer
that creates it; the app database keeps only its SHA-256 and its first 8 characters (`prefix`, so
the owner can tell keys apart). Nothing logs a key, its hash, or more of it than the prefix.

Authentication. `Authorization: Bearer trb_...` is accepted beside the JWT (app.py
`current_user`). `KeyAuth.user` hashes the key and, in ONE app-DB transaction, finds its row
(the `key_holder` policy: the transaction names the hash), checks it is neither revoked nor
expired (and, for a password account, that the account isn't disabled and the key was made after
its last password change: the rule its tokens follow), then counts the request as the key's user
(`owner` policy) and refuses it over a limit. The user comes from that row and only from it,
never from a parameter, another header or the agent. From there the request is the web app's:
`require()` (OpenFGA) on every data route and Store's RLS as that user. An unknown, malformed,
expired or revoked key gets the same 401 body as a bad JWT ("invalid token"). Nothing is cached:
a revocation takes effect on the key's next request, on every replica.

Scopes. A key can reach only the routes in KEY_ROUTES (default deny: a route added later refuses
keys until it is listed here). `read`: the data routes (GET, plus the POSTs that store nothing:
formula validate/evaluate, the model, the spreadsheets' batch read) and the user's own watchlist and
saved work.
`write` (always with read): creating and editing the user's own saved work and watchlist. Deleting
a workspace or an item stays sign-in only (not restorable). Everything else refuses every key with
403: the account (these key routes, passwords, alert settings, webhooks and notifications,
preferences, market-data connections), the demo and sign-in routes, reports and thesis checks
(agent-api's), and the assistant (agent-api refuses keys itself).

Limits. Per key, PER_MINUTE (60) requests in each clock minute and PER_DAY (10,000) in each UTC
day, both configurable (`API_KEY_PER_MINUTE`, `API_KEY_PER_DAY`). Counted in the app database by
one atomic upsert on the key's row for the day (`api_key_usage`), so the bound holds across every
Query API replica. Over a limit: 429 with Retry-After (seconds to the next minute, or to UTC
midnight) and the request isn't counted, only noted in `limited`. Every key answer carries
X-RateLimit-* headers. Usage per key per day is what Settings shows.

Who. Any signed-in account but a demo sandbox (GitHub and password accounts in production) may
hold MAX_ACTIVE (5) active keys; managing them (GET, POST /v1/api-keys, DELETE
/v1/api-keys/{id}) needs a sign-in, never a key. Expiry is required: 30, 90 or 365 days."""

import base64
import hashlib
import hmac
import logging
import math
import re
import secrets
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from prometheus_client import Counter
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from query_api.api_key_docs import ROUTE_DOCS, SCOPE_WORDS
from query_api.sandbox import is_sandbox_user

PREFIX = "trb_"
KEY = re.compile(r"^trb_[A-Za-z0-9_-]{43}$")
SHOWN = 8  # characters kept for recognition: "trb_" + 4
MAX_ACTIVE = 5
EXPIRY_DAYS = (30, 90, 365)
DEFAULT_EXPIRY_DAYS = 90
PER_MINUTE = 60
PER_DAY = 10_000
# keys revoked or expired longer ago than this are deleted when their owner makes a new one,
# with usage rows older than USAGE_DAYS
KEEP_INACTIVE_DAYS = 30
USAGE_DAYS = 90
LAST_USED_EVERY = timedelta(minutes=1)  # last_used_at is written at most once a minute
SCOPES = ("read", "write")
NAME_MAX = 60
BAD_NAME = f"name the key in 1 to {NAME_MAX} characters (no control or format characters)"
# a 401 for any key the API won't take: the same body as a bad JWT (app.py)
INVALID = "invalid token"

Scope = Literal["read", "write"]

# Every route a key can reach, by (method, path template), and the scope it needs. Anything not
# here refuses every key with 403 (test_api_keys checks each listed route exists and that every
# other signed-in route refuses keys).
READ_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/v1/me"),
    ("GET", "/v1/companies/search"),
    ("GET", "/v1/companies/{company_id}/financials"),
    ("GET", "/v1/companies/{company_id}/annual-per-share"),
    ("GET", "/v1/companies/{company_id}/per-share"),
    ("GET", "/v1/companies/{company_id}/restatements"),
    ("GET", "/v1/companies/{company_id}/segments"),
    ("GET", "/v1/companies/{company_id}/components"),
    ("GET", "/v1/companies/{company_id}/filings"),
    ("GET", "/v1/companies/{company_id}/filings/{accession_no}/figures"),
    ("GET", "/v1/companies/{company_id}/filings/{accession_no}/changes"),
    ("GET", "/v1/companies/{company_id}/sections/{section_key}"),
    ("GET", "/v1/companies/{company_id}/filing-index"),
    ("GET", "/v1/companies/{company_id}/expected-filings"),
    ("GET", "/v1/companies/{company_id}/peers"),
    ("GET", "/v1/companies/{company_id}/relations"),
    ("GET", "/v1/companies/{company_id}/peers/check/{candidate_id}"),
    ("GET", "/v1/companies/{company_id}/data-notes"),
    ("GET", "/v1/companies/{company_id}/events"),
    ("GET", "/v1/companies/{company_id}/earnings"),
    ("GET", "/v1/companies/{company_id}/insiders"),
    ("GET", "/v1/companies/{company_id}/text-kpis"),
    ("POST", "/v1/companies/{company_id}/model"),  # a calculation: stores nothing
    ("GET", "/v1/filings/latest"),
    ("GET", "/v1/filings/{accession_no}/recap"),
    ("GET", "/v1/filing-index"),
    ("GET", "/v1/calendar/expected-filings"),
    ("GET", "/v1/data-notes"),
    ("GET", "/v1/search"),
    ("GET", "/v1/screener"),
    ("GET", "/v1/sectors"),
    ("POST", "/v1/formulas/validate"),  # calculations: store nothing
    ("POST", "/v1/formulas/evaluate"),
    ("POST", "/v1/sheets/values"),  # spreadsheets: many figures, stores nothing (sheets.py)
    ("GET", "/v1/watchlist"),
    ("GET", "/v1/workspaces"),
    ("GET", "/v1/workspaces/{workspace_id}"),
    ("GET", "/v1/workspace-items/{item_id}"),
    ("GET", "/v1/workspace-items/{item_id}/versions"),
    ("GET", "/v1/workspace-items/{item_id}/versions/{version_id}"),
    # B2c: the user's own datasets (owner-only)
    ("GET", "/v1/datasets"),
    ("GET", "/v1/datasets/{dataset_id}"),
    ("GET", "/v1/datasets/{dataset_id}/rows"),
    ("GET", "/v1/companies/{company_id}/datasets"),
})
WRITE_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("PUT", "/v1/watchlist/{company_id}"),
    ("DELETE", "/v1/watchlist/{company_id}"),
    ("POST", "/v1/workspaces"),
    ("PATCH", "/v1/workspaces/{workspace_id}"),
    ("POST", "/v1/workspaces/{workspace_id}/duplicate"),
    ("POST", "/v1/workspaces/{workspace_id}/items"),
    ("PUT", "/v1/workspace-items/{item_id}"),
    ("POST", "/v1/workspace-items/{item_id}/duplicate"),
    # B2c: uploading and changing the user's own datasets (deleting one stays sign-in only)
    ("POST", "/v1/datasets"),
    ("POST", "/v1/datasets/{dataset_id}/upload"),
    ("PATCH", "/v1/datasets/{dataset_id}"),
})
KEY_ROUTES: dict[tuple[str, str], Scope] = {
    **{r: "read" for r in READ_ROUTES}, **{r: "write" for r in WRITE_ROUTES}}
# no token needed at all; listed in the key routes' OpenAPI so a script knows them
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/v1/metrics"),
    ("GET", "/v1/screener/columns"),
})

KEY_REQUESTS = Counter("tributary_api_key_requests_total",
                       "Requests made with an API key, by result (ok, invalid, limited_minute, "
                       "limited_day, refused_route, refused_scope, unavailable)", ["result"])

log = logging.getLogger("query_api.api_keys")


def new_key() -> str:
    """A fresh key: "trb_" + 32 random bytes, base64url without padding (43 characters)."""
    return PREFIX + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def key_hash(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


def is_api_key(credentials: str) -> bool:
    """A bearer credential meant as an API key (a JWT never starts with "trb_")."""
    return credentials.startswith(PREFIX)


def keys_allowed(user_id: str) -> bool:
    """Any signed-in account but a demo sandbox (a day-long anonymous user)."""
    return not is_sandbox_user(user_id)


@dataclass(frozen=True)
class Limits:
    per_minute: int = PER_MINUTE
    per_day: int = PER_DAY

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Limits":
        def positive(name: str, default: int) -> int:
            value = int(env.get(name) or default)
            if value < 1:
                raise ValueError(f"{name} must be at least 1")
            return value
        return cls(positive("API_KEY_PER_MINUTE", PER_MINUTE),
                   positive("API_KEY_PER_DAY", PER_DAY))


@dataclass(frozen=True)
class KeyUse:
    """A request made with a key: whose, what it may do, and what the limits said."""
    key_id: str
    user_id: str
    name: str
    prefix: str
    scopes: tuple[str, ...]
    expires_at: datetime
    outcome: Literal["ok", "minute", "day"]
    retry_after: int  # seconds; 0 when outcome is "ok"
    minute_left: int
    day_left: int


class TooManyKeys(Exception):
    pass


class UnknownUser(Exception):
    """The caller has no row in app.users."""


class ApiKeys(Protocol):
    def use(self, key_hash: bytes, now: datetime, limits: Limits) -> KeyUse | None: ...
    def create(self, user_id: str, name: str, scopes: tuple[str, ...], days: int,
               now: datetime) -> tuple[dict, str]: ...
    def list(self, user_id: str, now: datetime) -> list[dict]: ...
    def revoke(self, user_id: str, key_id: str, now: datetime) -> bool: ...


class Pool(Protocol):
    def connection(self) -> Any: ...


KEY_COLUMNS = ("key_id, name, prefix, scopes, created_at, expires_at, last_used_at,"
               " revoked_at")

# A key the API takes: not revoked, not expired, and for a password account (013) one whose
# account isn't disabled and was made after its last password change or disable, the rule its
# tokens follow (password_accounts.session_ok).
_FIND = (
    "SELECT k.key_id, k.user_id, k.name, k.prefix, k.key_hash, k.scopes, k.expires_at"
    " FROM api_keys k"
    " WHERE k.key_hash = %(hash)s AND k.revoked_at IS NULL AND k.expires_at > %(now)s"
    "   AND (k.user_id NOT LIKE 'pw-%%' OR EXISTS ("
    "        SELECT 1 FROM password_accounts p WHERE p.user_id = k.user_id"
    "          AND NOT p.disabled AND k.created_at >= p.sessions_valid_after))")

# One row per key per UTC day holds the day's count and the current minute's: counted only when
# under both limits (no row back: refused). A replica whose clock is behind never resets the
# minute (GREATEST, and a reset only for a later minute).
_COUNT = (
    "INSERT INTO api_key_usage AS u (key_id, user_id, day, requests, minute, minute_requests)"
    " VALUES (%(key)s, %(user)s, %(day)s, 1, %(minute)s, 1)"
    " ON CONFLICT (key_id, day) DO UPDATE SET"
    "   requests = u.requests + 1,"
    "   minute = GREATEST(u.minute, EXCLUDED.minute),"
    "   minute_requests = CASE WHEN EXCLUDED.minute > u.minute THEN 1"
    "                          ELSE u.minute_requests + 1 END"
    " WHERE u.requests < %(per_day)s"
    "   AND (EXCLUDED.minute > u.minute OR u.minute_requests < %(per_minute)s)"
    " RETURNING requests, minute_requests")


class AppApiKeys:
    """Keys and their usage in the app database. Every statement but the lookup by hash runs as
    the key's user (app.user_id, forced RLS) and filters on it as well."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @contextmanager
    def _tx(self, user_id: str) -> Iterator[psycopg.Cursor]:
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            yield cur

    def use(self, key_hash: bytes, now: datetime, limits: Limits) -> KeyUse | None:
        """The key's user and what the limits say, counting the request when under both; None
        for a key the API doesn't take (unknown, revoked, expired, its account disabled)."""
        now = now.astimezone(UTC)
        minute = now.replace(second=0, microsecond=0)
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.api_key_hash', %s, true)", (key_hash.hex(),))
            cur.execute(_FIND, {"hash": key_hash, "now": now})
            row = cur.fetchone()
            if row is None or not hmac.compare_digest(bytes(row["key_hash"]), key_hash):
                return None
            user_id, key_id = row["user_id"], row["key_id"]
            # from here as the key's user only
            cur.execute("SELECT set_config('app.api_key_hash', '', true),"
                        " set_config('app.user_id', %s, true)", (user_id,))
            cur.execute(_COUNT, {"key": key_id, "user": user_id, "day": now.date(),
                                 "minute": minute, "per_day": limits.per_day,
                                 "per_minute": limits.per_minute})
            counted = cur.fetchone()
            outcome: Literal["ok", "minute", "day"] = "ok"
            retry = 0
            if counted is None:
                cur.execute("UPDATE api_key_usage SET limited = limited + 1"
                            " WHERE key_id = %s AND user_id = %s AND day = %s"
                            " RETURNING requests, minute, minute_requests",
                            (key_id, user_id, now.date()))
                counted = cur.fetchone() or {"requests": limits.per_day, "minute": minute,
                                             "minute_requests": limits.per_minute}
                if counted["requests"] >= limits.per_day:
                    outcome = "day"
                    midnight = datetime.combine(now.date() + timedelta(days=1),
                                                datetime.min.time(), UTC)
                    retry = math.ceil((midnight - now).total_seconds())
                else:
                    outcome = "minute"
                    retry = math.ceil((counted["minute"] + timedelta(minutes=1)
                                       - now).total_seconds())
                retry = max(1, retry)
            else:
                cur.execute("UPDATE api_keys SET last_used_at = %s"
                            " WHERE key_id = %s AND user_id = %s"
                            "   AND (last_used_at IS NULL OR last_used_at < %s)",
                            (now, key_id, user_id, now - LAST_USED_EVERY))
            return KeyUse(
                key_id=str(key_id), user_id=user_id, name=row["name"], prefix=row["prefix"],
                scopes=tuple(row["scopes"]), expires_at=row["expires_at"], outcome=outcome,
                retry_after=retry,
                minute_left=max(0, limits.per_minute - counted["minute_requests"]),
                day_left=max(0, limits.per_day - counted["requests"]))

    def create(self, user_id: str, name: str, scopes: tuple[str, ...], days: int,
               now: datetime) -> tuple[dict, str]:
        """(the new key's row, the key itself). TooManyKeys with MAX_ACTIVE active already."""
        key = new_key()
        try:
            with self._tx(user_id) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            ("api_keys:" + user_id,))
                # keys long revoked or expired, and old usage, go when a new key comes
                cur.execute("DELETE FROM api_keys WHERE user_id = %s"
                            " AND least(coalesce(revoked_at, 'infinity'), expires_at) < %s",
                            (user_id, now - timedelta(days=KEEP_INACTIVE_DAYS)))
                cur.execute("DELETE FROM api_key_usage WHERE user_id = %s AND day < %s",
                            (user_id, now.date() - timedelta(days=USAGE_DAYS)))
                cur.execute("SELECT count(*) AS n FROM api_keys WHERE user_id = %s"
                            " AND revoked_at IS NULL AND expires_at > %s", (user_id, now))
                if (cur.fetchone() or {"n": 0})["n"] >= MAX_ACTIVE:
                    raise TooManyKeys
                cur.execute("INSERT INTO api_keys (user_id, name, prefix, key_hash, scopes,"
                            " created_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                            f" RETURNING {KEY_COLUMNS}",
                            (user_id, name, key[:SHOWN], key_hash(key), list(scopes), now,
                             now + timedelta(days=days)))
                row = cur.fetchone()
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None
        assert row is not None
        return {**row, "requests_today": 0, "limited_today": 0}, key

    def list(self, user_id: str, now: datetime) -> list[dict]:
        """Active keys, and those revoked or expired in the last KEEP_INACTIVE_DAYS, newest
        first, each with today's (UTC) usage."""
        cols = ", ".join(f"k.{c.strip()}" for c in KEY_COLUMNS.split(","))
        with self._tx(user_id) as cur:
            cur.execute(
                f"SELECT {cols}, coalesce(u.requests, 0) AS requests_today,"
                "  coalesce(u.limited, 0) AS limited_today"
                " FROM api_keys k LEFT JOIN api_key_usage u"
                "   ON u.key_id = k.key_id AND u.user_id = k.user_id AND u.day = %(day)s"
                " WHERE k.user_id = %(user)s"
                "   AND least(coalesce(k.revoked_at, 'infinity'), k.expires_at) > %(since)s"
                " ORDER BY k.created_at DESC, k.key_id LIMIT 50",
                {"user": user_id, "day": now.astimezone(UTC).date(),
                 "since": now - timedelta(days=KEEP_INACTIVE_DAYS)})
            return cur.fetchall()

    def revoke(self, user_id: str, key_id: str, now: datetime) -> bool:
        """False: not the caller's key, or revoked already (one 404 either way)."""
        with self._tx(user_id) as cur:
            cur.execute("UPDATE api_keys SET revoked_at = greatest(%s, created_at)"
                        " WHERE key_id = %s AND user_id = %s AND revoked_at IS NULL",
                        (now, key_id, user_id))
            return cur.rowcount == 1


def status_of(row: Mapping[str, Any], now: datetime) -> str:
    if row.get("revoked_at") is not None:
        return "revoked"
    return "active" if row["expires_at"] > now else "expired"


def shape(row: Mapping[str, Any], now: datetime) -> dict:
    """A key as the API shows it: never the key or its hash."""
    return {"key_id": str(row["key_id"]), "name": row["name"], "prefix": row["prefix"],
            "scopes": list(row["scopes"]), "status": status_of(row, now),
            "created_at": row["created_at"], "expires_at": row["expires_at"],
            "last_used_at": row["last_used_at"], "revoked_at": row["revoked_at"],
            "today": {"requests": row.get("requests_today", 0),
                      "limited": row.get("limited_today", 0)}}


# --- authentication -------------------------------------------------------------------------

class KeyAuth:
    """`user(request, key)`: the key's user for this request, or an HTTPException (401 for a key
    the API doesn't take, 403 for a route or scope it can't use, 429 over a limit, 503 when the
    app database can't answer). Sets request.state.api_key (a KeyUse) for /v1/me and the
    X-RateLimit-* headers."""

    def __init__(self, keys: ApiKeys | None, limits: Limits,
                 clock: Callable[[], datetime]) -> None:
        self.keys, self.limits, self.clock = keys, limits, clock

    def user(self, request: Request, key: str) -> str:
        if self.keys is None or not KEY.fullmatch(key):
            KEY_REQUESTS.labels("invalid").inc()
            raise HTTPException(401, INVALID)
        try:
            use = self.keys.use(key_hash(key), self.clock(), self.limits)
        except psycopg.Error as e:
            # the type only: a psycopg message can quote a statement's values
            KEY_REQUESTS.labels("unavailable").inc()
            log.warning("api key check unavailable: %s", type(e).__name__)
            raise HTTPException(503, "sign-in check unavailable") from None
        if use is None:
            KEY_REQUESTS.labels("invalid").inc()
            raise HTTPException(401, INVALID)
        request.state.api_key = use
        fields = {"user_id": use.user_id, "key_id": use.key_id}
        if use.outcome != "ok":
            KEY_REQUESTS.labels(f"limited_{use.outcome}").inc()
            log.info("api key over its %s limit", use.outcome, extra=fields)
            per = self.limits.per_minute if use.outcome == "minute" else self.limits.per_day
            raise HTTPException(429, f"this API key made {per:,} requests this {use.outcome}"
                                     f"{' (UTC)' if use.outcome == 'day' else ''}; try again in"
                                     f" {use.retry_after} seconds",
                                headers={"Retry-After": str(use.retry_after)})
        route = request.scope.get("route")
        need = KEY_ROUTES.get((request.method, getattr(route, "path", "")))
        if need is None:
            KEY_REQUESTS.labels("refused_route").inc()
            raise HTTPException(403, "API keys can't use this route: sign in to Tributary")
        if need not in use.scopes:
            KEY_REQUESTS.labels("refused_scope").inc()
            raise HTTPException(403, f"this API key can't {need}: it has the "
                                     f"{' and '.join(use.scopes)} scope")
        KEY_REQUESTS.labels("ok").inc()
        return use.user_id


class Remaining(Protocol):
    """What the X-RateLimit-* headers say: a key's use (KeyUse) or a connected app's
    (oauth.TokenUse)."""
    @property
    def minute_left(self) -> int: ...
    @property
    def day_left(self) -> int: ...


def rate_headers(use: Remaining, limits: Limits) -> dict[str, str]:
    return {"X-RateLimit-Limit-Minute": str(limits.per_minute),
            "X-RateLimit-Remaining-Minute": str(use.minute_left),
            "X-RateLimit-Limit-Day": str(limits.per_day),
            "X-RateLimit-Remaining-Day": str(use.day_left)}


def key_info(request: Request) -> dict | None:
    """/v1/me's `api_key` when the request was made with one (absent for a sign-in)."""
    use = getattr(request.state, "api_key", None)
    if not isinstance(use, KeyUse):
        return None
    return {"key_id": use.key_id, "name": use.name, "prefix": use.prefix,
            "scopes": list(use.scopes), "expires_at": use.expires_at}


# --- routes -----------------------------------------------------------------------------------

class NewKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    scopes: list[Scope] = Field(default=["read"], min_length=1, max_length=2)
    expires_in_days: int = DEFAULT_EXPIRY_DAYS


def add_api_key_routes(app: FastAPI, *, keys: ApiKeys | None, current_user: Callable[..., str],
                       limits: Limits, clock: Callable[[], datetime]) -> None:
    """GET/POST /v1/api-keys, DELETE /v1/api-keys/{key_id} (sign-in only: not in KEY_ROUTES),
    the public OpenAPI of the key routes, and the X-RateLimit-* headers on key answers."""
    User = Annotated[str, Depends(current_user)]

    @app.middleware("http")
    async def key_rate_headers(request: Request, call_next):
        response = await call_next(request)
        use = getattr(request.state, "api_key", None)
        if isinstance(use, KeyUse):
            response.headers.update(rate_headers(use, limits))
        return response

    def store() -> ApiKeys:
        if keys is None:
            raise HTTPException(503, "API keys unavailable")
        return keys

    def settings(user_id: str) -> dict:
        return {"allowed": keys_allowed(user_id), "max_active": MAX_ACTIVE,
                "expiry_days": list(EXPIRY_DAYS), "default_expiry_days": DEFAULT_EXPIRY_DAYS,
                "scopes": list(SCOPES),
                "limits": {"per_minute": limits.per_minute, "per_day": limits.per_day}}

    @app.get("/v1/api-keys", response_model=dict)
    def list_keys(user_id: User) -> dict:
        """The caller's keys (never a key or its hash): active ones and those revoked or expired
        in the last 30 days, each with today's (UTC) usage; and what can be chosen. A demo
        sandbox can't have keys (`allowed` false)."""
        if not keys_allowed(user_id):
            return {**settings(user_id), "keys": []}
        now = clock()
        return {**settings(user_id),
                "keys": [shape(r, now) for r in store().list(user_id, now)]}

    @app.post("/v1/api-keys", status_code=201, response_model=dict)
    def create_key(user_id: User, body: NewKey, response: Response) -> dict:
        """{"name", "scopes": ["read"] or ["read", "write"], "expires_in_days": 30, 90 or 365}:
        a new key, in this answer only (`key`); store it now, it can't be shown again."""
        if not keys_allowed(user_id):
            raise HTTPException(403, "API keys need a permanent account: sign in with GitHub "
                                     "or with an account")
        name = body.name.strip()
        if not name or len(name) > NAME_MAX or any(unicodedata.category(ch) in ("Cc", "Cf")
                                                   for ch in name):
            raise HTTPException(422, BAD_NAME)
        if "read" not in body.scopes or len(set(body.scopes)) != len(body.scopes):
            raise HTTPException(422, 'scopes: ["read"] or ["read", "write"]')
        if body.expires_in_days not in EXPIRY_DAYS:
            raise HTTPException(422, "expires_in_days: one of "
                                     + ", ".join(str(d) for d in EXPIRY_DAYS))
        scopes = tuple(s for s in SCOPES if s in body.scopes)
        now = clock()
        try:
            row, key = store().create(user_id, name, scopes, body.expires_in_days, now)
        except TooManyKeys:
            raise HTTPException(409, f"at most {MAX_ACTIVE} active keys: revoke one first") \
                from None
        except UnknownUser:
            raise HTTPException(409, "no account to make a key for") from None
        except psycopg.errors.CheckViolation:
            # a name the database's CHECK refuses that the rule above let through
            raise HTTPException(422, BAD_NAME) from None
        except psycopg.Error as e:
            # the type only: a constraint's message quotes the row, the key's hash with it
            log.warning("api key not created: %s", type(e).__name__, extra={"user_id": user_id})
            raise HTTPException(503, "API keys unavailable") from None
        log.info("api key created", extra={"user_id": user_id, "key_id": str(row["key_id"])})
        response.headers["Cache-Control"] = "no-store"
        return {"key": key, "api_key": shape(row, now)}

    @app.delete("/v1/api-keys/{key_id}", response_model=dict)
    def revoke_key(user_id: User, key_id: UUID) -> dict:
        """Revokes the caller's key at once: its next request is refused (401). Another user's
        key, or one revoked already, is the same 404 as none."""
        if not store().revoke(user_id, str(key_id), clock()):
            raise HTTPException(404, "no such key")
        log.info("api key revoked", extra={"user_id": user_id, "key_id": str(key_id)})
        return {"key_id": str(key_id), "revoked": True}

    cached: dict[str, dict] = {}

    @app.get("/v1/openapi-keys.json", include_in_schema=False)
    def key_openapi() -> dict:
        """The routes an API key can use, from this app's own OpenAPI (public, no token)."""
        if "doc" not in cached:
            cached["doc"] = key_routes_openapi(app.openapi(), limits)
        return cached["doc"]


# --- the key routes' OpenAPI ------------------------------------------------------------------

# a response's required description, in words for an API user (never the app's own)
RESPONSE_WORDS = {"200": "OK", "201": "Created",
                  "422": "A parameter or body the route doesn't take; `detail` says which"}


def key_routes_openapi(full: dict, limits: Limits) -> dict:
    """`full` (the app's OpenAPI) narrowed to KEY_ROUTES and PUBLIC_ROUTES, and only the schemas
    those reference: the parameters, bodies and types are the app's own (generated, never written
    by hand). The words are not: each operation gets its user-facing summary and description from
    api_key_docs.ROUTE_DOCS plus its scope (`x-tributary-scope`: read, write or public), and every
    other description the app carries (developer docstrings, parameter and model notes) is
    dropped, so the public spec shows nothing written for developers."""
    paths: dict[str, dict] = {}
    for path, ops in full.get("paths", {}).items():
        for method, op in ops.items():
            key = (method.upper(), path)
            scope = KEY_ROUTES.get(key) or ("public" if key in PUBLIC_ROUTES else None)
            if scope is None:
                continue
            summary, description = ROUTE_DOCS[key]
            out = {k: _undescribed(v) for k, v in op.items()
                   if k in ("operationId", "parameters", "requestBody")}
            out["responses"] = {
                code: {**_undescribed(r), "description": RESPONSE_WORDS.get(code, "Error")}
                for code, r in op.get("responses", {}).items()}
            out.update({"summary": summary,
                        "description": f"{description}\n\n{SCOPE_WORDS[scope]}",
                        "x-tributary-scope": scope})
            if scope != "public":
                out["security"] = [{"apiKey": []}]
            paths.setdefault(path, {})[method] = out
    schemas = full.get("components", {}).get("schemas", {})
    wanted: dict[str, Any] = {}
    todo = list(_refs(paths))
    while todo:
        name = todo.pop()
        if name in wanted or name not in schemas:
            continue
        wanted[name] = _undescribed(schemas[name])
        todo.extend(_refs(schemas[name]))
    return {
        "openapi": full.get("openapi", "3.1.0"),
        "info": {
            "title": "Tributary API (API keys)",
            "version": full.get("info", {}).get("version", "1"),
            "description": (
                "The Query API routes a personal API key can use. Send the key as "
                "`Authorization: Bearer trb_...`. A `read` key reaches every route marked read; "
                "a `write` key also those marked write. Limits per key: "
                f"{limits.per_minute} requests a minute and {limits.per_day:,} a day; over "
                "either, 429 with Retry-After. An unknown, expired or revoked key: 401. A route "
                "or scope the key can't use: 403. Routes marked public need no key."),
            "x-tributary-limits": {"per_minute": limits.per_minute, "per_day": limits.per_day},
        },
        "servers": [{"url": "/api/query"}],
        "paths": dict(sorted(paths.items())),
        "components": {
            "schemas": dict(sorted(wanted.items())),
            "securitySchemes": {"apiKey": {
                "type": "http", "scheme": "bearer",
                "description": "A personal API key (trb_...), made in Tributary's Settings"}},
        },
    }


def _undescribed(node: Any) -> Any:
    """`node` without any free text: every string `description` (a docstring, a parameter's or
    a field's note) is dropped, at any depth. A property NAMED description is a schema (a dict),
    so it stays."""
    if isinstance(node, dict):
        return {k: _undescribed(v) for k, v in node.items()
                if not (k in ("description", "summary") and isinstance(v, str))}
    if isinstance(node, list):
        return [_undescribed(v) for v in node]
    return node


def _refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            yield ref.rsplit("/", 1)[1]
        for v in node.values():
            yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)
