"""Sign in with GitHub: a permanent account with the same access as a demo sandbox.

Flow (all here, routes exist only when GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET are set):

1. GET /v1/auth/github/start: 302 to GitHub's authorize page with no scopes (public profile
   only) and a `state` = nonce.expiry.HMAC (key derived from the JWT secret). The nonce is also
   set in a short-lived HttpOnly, Secure, SameSite=Lax cookie scoped to the auth routes, so the
   callback only accepts a state started in the same browser (no login CSRF, no replay from
   another browser).
2. GET /v1/auth/github/callback: checks the state's signature, expiry and cookie nonce, trades
   the code for an access token server-side, reads GET /user ONCE with it and drops it (never
   stored or logged), then finds or creates the user and mints the app's JWT (`sub` =
   "gh-<GitHub id>", 7 days). It redirects to `/#/signed-in?token=...`: the token travels in
   the URL fragment, which browsers never send to a server, so it isn't in any access log.
   Every failure redirects to `/#/signed-in?error=<code>` with no detail.

A first sign-in writes, in ONE app-DB transaction: users (email = GitHub's noreply address),
github_accounts (app migration 007), and what sandbox.grant_demo_access gives a sandbox: plan
pro, grants on the 20 DEMO_TICKERS, the watchlist and the starter workspace. authz-sync turns
those rows into access through CDC, as for any user; nothing here writes tuples. A returning
sign-in changes no access and keeps the user's saved work: only the login and last sign-in
time are updated. GitHub users share the sandboxes' daily assistant caps (take_question) but
have no demo_sandboxes row, so demo-cleanup never deletes them."""

import base64
import hashlib
import hmac
import logging
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol
from urllib.parse import urlencode

import httpx
from fastapi import Cookie, FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from prometheus_client import Counter

from query_api.auth import mint
from query_api.sandbox import DEMO_TICKERS, DEMO_WATCHLIST, Pool, grant_demo_access

USER_PREFIX = "gh-"
TOKEN_TTL = 7 * 24 * 3600
STATE_TTL = 600
NONCE_COOKIE = "tributary_gh_nonce"
# The path the browser sees: nginx (and the dev proxy) serve the Query API under /api/query.
COOKIE_PATH = "/api/query/v1/auth/"
AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
SIGNED_IN = "/#/signed-in"
LOGIN = re.compile(r"^[A-Za-z0-9-]{1,39}$")

SIGNINS = Counter("tributary_github_signins_total",
                  "GitHub sign-ins by result (created, returning, denied, state, github, "
                  "unavailable)", ["result"])

log = logging.getLogger("query_api.github")


def is_github_user(user_id: str) -> bool:
    return user_id.startswith(USER_PREFIX)


@dataclass(frozen=True)
class GitHubConfig:
    client_id: str
    client_secret: str
    # the callback URL registered on the OAuth app; None: GitHub uses the registered one
    redirect_uri: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "GitHubConfig | None":
        """None (sign-in off) unless both GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET are set."""
        client_id = env.get("GITHUB_CLIENT_ID", "").strip()
        secret = env.get("GITHUB_CLIENT_SECRET", "").strip()
        if not client_id or not secret:
            if client_id or secret:
                log.warning("GitHub sign-in off: set both GITHUB_CLIENT_ID and "
                            "GITHUB_CLIENT_SECRET")
            return None
        return cls(client_id, secret, env.get("GITHUB_REDIRECT_URI", "").strip() or None)


class BadState(Exception):
    pass


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def state_key(jwt_secret: str) -> bytes:
    """A key for OAuth states only, derived from the JWT secret (a state is never a token)."""
    return hmac.new(jwt_secret.encode(), b"tributary github oauth state", hashlib.sha256).digest()


def sign_state(key: bytes, nonce: str, expires: int) -> str:
    payload = f"{nonce}.{expires}"
    return f"{payload}.{_b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())}"


def check_state(key: bytes, state: str | None, cookie_nonce: str | None, now: int) -> None:
    """Raises BadState unless `state` is ours, unexpired and started in this browser."""
    if not state or not cookie_nonce or len(state) > 256:
        raise BadState("missing")
    parts = state.split(".")
    if len(parts) != 3:
        raise BadState("malformed")
    nonce, expires, sig = parts
    good = _b64(hmac.new(key, f"{nonce}.{expires}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, good):
        raise BadState("signature")
    if not expires.isdigit() or int(expires) <= now:
        raise BadState("expired")
    if not hmac.compare_digest(nonce, cookie_nonce):
        raise BadState("browser")


@dataclass(frozen=True)
class GitHubUser:
    id: int
    login: str


class GitHubError(Exception):
    """GitHub refused the code or didn't answer as documented."""


class GitHub(Protocol):
    def exchange(self, code: str) -> str: ...  # access token; raises GitHubError
    def user(self, access_token: str) -> GitHubUser: ...  # raises GitHubError


class HttpGitHub:
    """GitHub's OAuth web flow over an injected httpx client (tests use a fake GitHub)."""

    def __init__(self, config: GitHubConfig, http: httpx.Client) -> None:
        self._config = config
        self._http = http

    def exchange(self, code: str) -> str:
        data = {"client_id": self._config.client_id,
                "client_secret": self._config.client_secret, "code": code}
        if self._config.redirect_uri:
            data["redirect_uri"] = self._config.redirect_uri
        try:
            r = self._http.post(TOKEN_URL, data=data, headers={"Accept": "application/json"})
            body = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            raise GitHubError("token exchange failed") from None
        # GitHub answers 200 with {"error": "bad_verification_code", ...} for a bad code
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise GitHubError("no access token")
        return token

    def user(self, access_token: str) -> GitHubUser:
        try:
            r = self._http.get(USER_URL, headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"})
            body = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            raise GitHubError("user lookup failed") from None
        gid, login = (body.get("id"), body.get("login")) if isinstance(body, dict) else (0, "")
        if (not isinstance(gid, int) or isinstance(gid, bool) or gid <= 0
                or not isinstance(login, str) or not LOGIN.match(login)):
            raise GitHubError("unexpected user")
        return GitHubUser(gid, login)


def user_id_for(github_id: int) -> str:
    return f"{USER_PREFIX}{github_id}"


def noreply_email(user: GitHubUser) -> str:
    """GitHub's own no-reply form: no real address is requested or stored."""
    return f"{user.id}+{user.login}@users.noreply.github.com"


@dataclass(frozen=True)
class NewAccount:
    user: GitHubUser
    at: datetime
    company_ids: Sequence[str]
    watchlist: Sequence[str]
    workspaces: Sequence[dict] = ()


class Accounts(Protocol):
    def sign_in(self, account: NewAccount) -> bool: ...  # True: created
    def login(self, user_id: str) -> str | None: ...


class AppAccounts:
    """GitHub accounts in the app database."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    def sign_in(self, account: NewAccount) -> bool:
        """First time: the user, its github_accounts row and the demo's access, in one
        transaction (authz-sync never sees half an account). Afterwards: only the login and
        last sign-in time change. Concurrent first sign-ins of one GitHub id are serialized by
        the users primary key: the second finds the user."""
        a, user_id = account, user_id_for(account.user.id)
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("INSERT INTO users (user_id, email, created_at) VALUES (%s, %s, %s)"
                        " ON CONFLICT (user_id) DO NOTHING RETURNING user_id",
                        (user_id, noreply_email(a.user), a.at))
            created = cur.fetchone() is not None
            cur.execute("INSERT INTO github_accounts"
                        " (github_id, user_id, login, created_at, last_sign_in_at)"
                        " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (github_id) DO UPDATE"
                        " SET login = EXCLUDED.login, last_sign_in_at = EXCLUDED.last_sign_in_at",
                        (a.user.id, user_id, a.user.login, a.at, a.at))
            if created:
                grant_demo_access(cur, user_id, a.at, a.company_ids, a.watchlist, a.workspaces)
            return created

    def login(self, user_id: str) -> str | None:
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT login FROM github_accounts WHERE user_id = %s", (user_id,))
            row: Any = cur.fetchone()
            return row[0] if row else None


def add_github_routes(app: FastAPI, *, config: GitHubConfig | None, github: GitHub | None,
                      accounts: Accounts | None, jwt_secret: str,
                      demo_companies: Callable[[], dict[str, str]],
                      workspaces: Callable[[dict[str, str]], list[dict]],
                      clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                      ) -> Callable[[str], str | None]:
    """Adds the routes; returns login(user_id) for /v1/me (None: not a GitHub user)."""
    enabled = config is not None and github is not None and accounts is not None
    key = state_key(jwt_secret)

    def cookie(response: RedirectResponse, value: str, max_age: int) -> None:
        response.set_cookie(NONCE_COOKIE, value, max_age=max_age, path=COOKIE_PATH,
                            httponly=True, secure=True, samesite="lax")

    def landing(fragment: dict[str, str]) -> RedirectResponse:
        r = RedirectResponse(f"{SIGNED_IN}?{urlencode(fragment)}", status_code=302,
                             headers={"Cache-Control": "no-store"})
        cookie(r, "", 0)  # the nonce is single-use
        return r

    def failed(code: str) -> RedirectResponse:
        SIGNINS.labels(code).inc()
        return landing({"error": code})

    @app.get("/v1/auth/providers")
    def providers() -> dict:
        """Which sign-in buttons the web app shows (public)."""
        return {"github": enabled}

    @app.get("/v1/auth/github/start")
    def start() -> RedirectResponse:
        if not enabled or config is None:
            raise HTTPException(404)
        nonce = secrets.token_urlsafe(16)
        state = sign_state(key, nonce, int(clock().timestamp()) + STATE_TTL)
        params = {"client_id": config.client_id, "scope": "", "state": state,
                  "allow_signup": "true"}
        if config.redirect_uri:
            params["redirect_uri"] = config.redirect_uri
        r = RedirectResponse(f"{AUTHORIZE_URL}?{urlencode(params)}", status_code=302,
                             headers={"Cache-Control": "no-store"})
        cookie(r, nonce, STATE_TTL)
        return r

    @app.get("/v1/auth/github/callback")
    def callback(
        code: Annotated[str | None, Query()] = None,
        state: Annotated[str | None, Query()] = None,
        error: Annotated[str | None, Query()] = None,
        nonce: Annotated[str | None, Cookie(alias=NONCE_COOKIE)] = None,
    ) -> RedirectResponse:
        """Every outcome is a redirect to the web app (no 4xx page a visitor would land on)."""
        if not enabled or github is None or accounts is None:
            raise HTTPException(404)
        now = clock()
        try:
            check_state(key, state, nonce, int(now.timestamp()))
        except BadState as e:
            log.info("github sign-in: bad state (%s)", e)
            return failed("state")
        if error == "access_denied":
            return failed("denied")
        if error or not code or len(code) > 512:
            return failed("github")
        try:
            # the access token lives only in this frame: never stored, logged or returned
            user = github.user(github.exchange(code))
        except GitHubError as e:
            log.warning("github sign-in failed: %s", e)
            return failed("github")
        try:
            ids = demo_companies()
        except Exception:
            log.exception("github sign-in: demo companies unavailable")
            return failed("unavailable")
        companies = list(dict.fromkeys(ids[t] for t in DEMO_TICKERS if t in ids))
        if not companies:
            return failed("unavailable")
        account = NewAccount(user=user, at=now.replace(microsecond=0), company_ids=companies,
                             watchlist=list(dict.fromkeys(
                                 ids[t] for t in DEMO_WATCHLIST if t in ids)),
                             workspaces=workspaces(ids))
        try:
            created = accounts.sign_in(account)
        except Exception:
            log.exception("github sign-in: app database write failed")
            return failed("unavailable")
        user_id = user_id_for(user.id)
        SIGNINS.labels("created" if created else "returning").inc()
        log.info("github sign-in", extra={"user_id": user_id, "new_account": created})
        token = mint(user_id, jwt_secret, TOKEN_TTL, now=int(now.timestamp()))
        return landing({"token": token, "expires_in": str(TOKEN_TTL)})

    def login(user_id: str) -> str | None:
        if not is_github_user(user_id) or accounts is None:
            return None
        return accounts.login(user_id)

    return login
