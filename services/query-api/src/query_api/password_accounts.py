"""Owner-created username/password accounts (app migration 013). There is no public signup:
the owner creates each account inside the Query API pod with `query-api-account` (cli_main),
which prompts for the password and never takes it as an argument or prints it.

`create` writes, in ONE app-DB transaction: users (user_id "pw-<username>", email
"<username>@password.invalid"), password_accounts, and exactly what a GitHub account gets
(sandbox.grant_demo_access): plan pro, the demo's watchlist and starter workspace and, only
while the public catalog is closed, grants on the 20 DEMO_TICKERS. Plain app-DB rows: CDC →
authz-sync turns them into access; nothing here writes OpenFGA tuples.

POST /v1/auth/password {username, password} → {token, expires_in} (the app's ordinary JWT,
7 days, like GitHub sign-in). Every refusal (unknown user, wrong password, disabled, locked)
is the same 401 body, and an unknown username is still checked against a dummy scrypt hash so
it takes as long as a known one: the route is no account oracle. 10 consecutive failures lock
the account for 15 minutes (a success resets the count). Each client address (X-Client-IP,
set by nginx) gets 20 attempts per 15 minutes, in process. The body is parsed by hand, so no
validation error ever echoes the password (FastAPI's 422 would), and nothing logs a password,
a token or a submitted username that matched no account.

A token is refused (401 on every route) when its account is disabled or was issued before the
account's sessions_valid_after (set by set-password and disable): session_ok, cached
SESSION_CACHE_SECONDS per user.

Assistant questions: capped per account by its own questions_per_day (AppSandboxes
.take_question, demo.py agent-quota), never by the demo's shared daily cap."""

import argparse
import getpass
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter
from starlette.concurrency import run_in_threadpool

from query_api.auth import mint
from query_api.passwords import (
    PasswordRejected,
    check_policy,
    dummy_hash,
    hash_password,
    is_password_user,
    needs_rehash,
    normalize_username,
    user_id_for,
    verify,
)
from query_api.sandbox import (
    DEMO_PLAN,
    DEMO_TICKERS,
    DEMO_WATCHLIST,
    Pool,
    grant_demo_access,
    sandbox_workspaces,
)

TOKEN_TTL = 7 * 24 * 3600
MAX_FAILURES = 10
LOCK_FOR = timedelta(minutes=15)
IP_ATTEMPTS = 20
IP_WINDOW_SECONDS = 15 * 60
# scrypt takes ~32 MiB per call: at most this many at once per replica (the pod has 384 MiB)
SCRYPT_SLOTS = 2
SCRYPT_WAIT_SECONDS = 10.0
SESSION_CACHE_SECONDS = 10.0
MAX_BODY = 4096
MAX_QUESTIONS_PER_DAY = 1000

REFUSED = "Wrong username or password, or the account is locked."
BAD_BODY = 'send {"username": "...", "password": "..."}'

SIGNINS = Counter("tributary_password_signins_total",
                  "Password sign-ins by result (ok, refused, ip_limit, busy, invalid)",
                  ["result"])

log = logging.getLogger("query_api.password_accounts")


@dataclass(frozen=True)
class Account:
    user_id: str
    username: str
    password_hash: str
    questions_per_day: int
    disabled: bool
    failed_attempts: int
    locked_until: datetime | None


class AccountExists(Exception):
    pass


class NoSuchAccount(Exception):
    pass


class PasswordAccounts(Protocol):
    def find(self, username: str) -> Account | None: ...
    def failed(self, user_id: str, now: datetime) -> None: ...
    def succeeded(self, user_id: str, now: datetime, new_hash: str | None) -> None: ...
    def session(self, user_id: str) -> tuple[bool, datetime] | None: ...  # (disabled, after)
    def info(self, user_id: str) -> dict | None: ...


_COLUMNS = ("user_id, username, password_hash, questions_per_day, disabled, failed_attempts,"
            " locked_until")


class AppPasswordAccounts:
    """Password accounts in the app database. Sign-in reads and updates only this table."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    def find(self, username: str) -> Account | None:
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM password_accounts WHERE username = %s",
                        (username,))
            row = cur.fetchone()
            return Account(*row) if row else None

    def failed(self, user_id: str, now: datetime) -> None:
        """One more consecutive failure; the MAX_FAILURES-th locks the account for LOCK_FOR and
        starts the count again (one atomic statement, so concurrent attempts can't skip it)."""
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("UPDATE password_accounts SET"
                        " locked_until = CASE WHEN failed_attempts + 1 >= %s THEN %s"
                        "                     ELSE locked_until END,"
                        " failed_attempts = CASE WHEN failed_attempts + 1 >= %s THEN 0"
                        "                        ELSE failed_attempts + 1 END"
                        " WHERE user_id = %s",
                        (MAX_FAILURES, now + LOCK_FOR, MAX_FAILURES, user_id))

    def succeeded(self, user_id: str, now: datetime, new_hash: str | None) -> None:
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("UPDATE password_accounts SET failed_attempts = 0, locked_until = NULL,"
                        " last_sign_in_at = %s,"
                        " password_hash = coalesce(%s, password_hash) WHERE user_id = %s",
                        (now, new_hash, user_id))

    def session(self, user_id: str) -> tuple[bool, datetime] | None:
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT disabled, sessions_valid_after FROM password_accounts"
                        " WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return (row[0], row[1]) if row else None

    def info(self, user_id: str) -> dict | None:
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT username, questions_per_day FROM password_accounts"
                        " WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return {"username": row[0], "questions_per_day": row[1]} if row else None

    # --- the owner's CLI ----------------------------------------------------------------

    def create(self, username: str, password_hash: str, questions_per_day: int,
               at: datetime, company_ids: Sequence[str], watchlist: Sequence[str],
               workspaces: Sequence[dict]) -> str:
        """The user, its password row and a GitHub account's access, in one transaction
        (authz-sync never sees half an account). AccountExists if the user id is taken."""
        user_id = user_id_for(username)
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("INSERT INTO users (user_id, email, created_at) VALUES (%s, %s, %s)"
                        " ON CONFLICT DO NOTHING RETURNING user_id",
                        (user_id, f"{username}@password.invalid", at))
            if cur.fetchone() is None:
                raise AccountExists(username)
            cur.execute("INSERT INTO password_accounts (user_id, username, password_hash,"
                        " questions_per_day, sessions_valid_after, created_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s)",
                        (user_id, username, password_hash, questions_per_day,
                         at.replace(microsecond=0), at))
            grant_demo_access(cur, user_id, at, company_ids, watchlist, workspaces)
        return user_id

    def _update(self, username: str, sql: str, params: tuple) -> None:
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(f"UPDATE password_accounts SET {sql} WHERE username = %s",
                        (*params, username))
            if cur.rowcount != 1:
                raise NoSuchAccount(username)

    def set_password(self, username: str, password_hash: str, at: datetime) -> None:
        """New password; unlocks; tokens issued before now stop working."""
        self._update(username, "password_hash = %s, failed_attempts = 0, locked_until = NULL,"
                     " sessions_valid_after = %s", (password_hash, at.replace(microsecond=0)))

    def set_limit(self, username: str, questions_per_day: int) -> None:
        self._update(username, "questions_per_day = %s", (questions_per_day,))

    def set_disabled(self, username: str, disabled: bool, at: datetime) -> None:
        """disable also ends every session; enable unlocks."""
        if disabled:
            self._update(username, "disabled = true, sessions_valid_after = %s",
                         (at.replace(microsecond=0),))
        else:
            self._update(username, "disabled = false, failed_attempts = 0, locked_until = NULL",
                         ())

    def list(self, now: datetime) -> list[dict]:
        """Every account, with today's assistant questions (never the hash)."""
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT a.username, a.user_id, a.questions_per_day,"
                        "       coalesce(u.questions, 0), a.disabled,"
                        "       a.locked_until > %s, a.failed_attempts, a.created_at,"
                        "       a.last_sign_in_at"
                        " FROM password_accounts a LEFT JOIN demo_agent_usage u"
                        "   ON u.user_id = a.user_id AND u.day = %s"
                        " ORDER BY a.username", (now, now.astimezone(UTC).date()))
            keys = ("username", "user_id", "questions_per_day", "questions_today", "disabled",
                    "locked", "failed_attempts", "created_at", "last_sign_in_at")
            return [dict(zip(keys, r, strict=True)) for r in cur.fetchall()]


class IpLimiter:
    """At most `limit` attempts per client address in any `window` seconds (in process, per
    replica). Idle addresses are dropped, so the map can't grow without bound."""

    def __init__(self, limit: int = IP_ATTEMPTS, window: float = IP_WINDOW_SECONDS,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.limit, self.window, self._now = limit, window, monotonic
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._swept = monotonic()

    def take(self, key: str) -> bool:
        now = self._now()
        with self._lock:
            if now - self._swept >= self.window:
                self._swept = now
                for k in [k for k, q in self._events.items() if q[-1] <= now - self.window]:
                    del self._events[k]
            q = self._events.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


def _refused() -> JSONResponse:
    return JSONResponse({"detail": REFUSED}, status_code=401,
                        headers={"Cache-Control": "no-store"})


def _bad(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def read_credentials(raw: bytes) -> tuple[str, str] | None:
    """(username, password) from a JSON body of exactly those two strings, else None."""
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if (not isinstance(body, dict) or set(body) != {"username", "password"}
            or not isinstance(body["username"], str) or not isinstance(body["password"], str)):
        return None
    return body["username"], body["password"]


def add_password_routes(app: FastAPI, *, accounts: PasswordAccounts | None, jwt_secret: str,
                        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                        monotonic: Callable[[], float] = time.monotonic,
                        client_ip_header: str = "X-Client-IP",
                        limiter: IpLimiter | None = None,
                        ) -> tuple[Callable[[str, int | None], bool], Callable[[str], dict | None]]:
    """Adds POST /v1/auth/password (404 when accounts is None: no app database). Returns
    session_ok(user_id, iat) for the token check and info(user_id) for /v1/me."""
    limiter = limiter or IpLimiter(monotonic=monotonic)
    slots = threading.BoundedSemaphore(SCRYPT_SLOTS)
    cache: dict[str, tuple[float, bool, int]] = {}
    cache_lock = threading.Lock()

    def client_ip(request: Request) -> str:
        ip = request.headers.get(client_ip_header, "").strip()
        if ip:
            return ip
        return request.client.host if request.client else "unknown"

    def attempt(username: str, password: str) -> str | None:
        """The user id if the credentials are good, else None. Exactly one scrypt per call."""
        name = normalize_username(username)
        account = accounts.find(name) if accounts is not None and name else None
        now = clock()
        if account is None:
            verify(password, dummy_hash())
            log.info("password sign-in refused", extra={"reason": "unknown"})
            return None
        locked = account.locked_until is not None and account.locked_until > now
        if account.disabled or locked:
            verify(password, dummy_hash())
            log.info("password sign-in refused", extra={
                "user_id": account.user_id, "reason": "disabled" if account.disabled
                else "locked"})
            return None
        if not verify(password, account.password_hash):
            accounts.failed(account.user_id, now)  # type: ignore[union-attr]
            log.info("password sign-in refused", extra={"user_id": account.user_id,
                                                        "reason": "password"})
            return None
        new_hash = None
        if needs_rehash(account.password_hash):
            new_hash = hash_password(password)
        accounts.succeeded(account.user_id, now, new_hash)  # type: ignore[union-attr]
        log.info("password sign-in", extra={"user_id": account.user_id})
        return account.user_id

    def guarded(username: str, password: str) -> str | None | bool:
        """attempt() in one of SCRYPT_SLOTS; False when none frees up in time."""
        if not slots.acquire(timeout=SCRYPT_WAIT_SECONDS):
            return False
        try:
            return attempt(username, password)
        finally:
            slots.release()

    @app.post("/v1/auth/password")
    async def sign_in(request: Request):
        """{username, password} → {token, expires_in}. 401 (always the same body) for any
        refusal; 429 when this address tried too often; 422 for a malformed body (never
        echoed)."""
        if accounts is None:
            return _bad(404, "Not Found")
        size = request.headers.get("content-length")
        if size is not None and (not size.isdigit() or int(size) > MAX_BODY):
            SIGNINS.labels("invalid").inc()
            return _bad(413, f"send a JSON body of at most {MAX_BODY} bytes")
        raw = await request.body()
        if len(raw) > MAX_BODY:
            SIGNINS.labels("invalid").inc()
            return _bad(413, f"send a JSON body of at most {MAX_BODY} bytes")
        if not limiter.take(client_ip(request)):
            SIGNINS.labels("ip_limit").inc()
            return _bad(429, "Too many sign-in attempts from your network. Try again in 15 "
                             "minutes.")
        creds = read_credentials(raw)
        if creds is None:
            SIGNINS.labels("invalid").inc()
            return _bad(422, BAD_BODY)
        try:
            user_id = await run_in_threadpool(guarded, *creds)
        except psycopg.Error:
            log.exception("password sign-in: app database unavailable")
            SIGNINS.labels("busy").inc()
            return _bad(503, "Sign-in isn't available right now. Try again later.")
        if user_id is False:
            SIGNINS.labels("busy").inc()
            return _bad(503, "Sign-in is busy. Try again in a moment.")
        if not user_id:
            SIGNINS.labels("refused").inc()
            return _refused()
        SIGNINS.labels("ok").inc()
        token = mint(str(user_id), jwt_secret, TOKEN_TTL, now=int(clock().timestamp()))
        return JSONResponse({"token": token, "expires_in": TOKEN_TTL},
                            headers={"Cache-Control": "no-store"})

    def session_ok(user_id: str, iat: int | None) -> bool:
        """False for a password account's token that is disabled or older than its last
        password change / disable (or with no iat); True for everyone else."""
        if not is_password_user(user_id):
            return True
        if accounts is None or iat is None:
            return False
        now = monotonic()
        with cache_lock:
            hit = cache.get(user_id)
        if hit is None or hit[0] <= now:
            state = accounts.session(user_id)
            disabled, after = (True, 0) if state is None else (
                bool(state[0]), int(state[1].timestamp()))
            hit = (now + SESSION_CACHE_SECONDS, disabled, after)
            with cache_lock:
                if len(cache) > 10_000:
                    cache.clear()
                cache[user_id] = hit
        return not hit[1] and int(iat) >= hit[2]

    def info(user_id: str) -> dict | None:
        if not is_password_user(user_id) or accounts is None:
            return None
        return accounts.info(user_id)

    return session_ok, info


# --- the owner's CLI: query-api-account ------------------------------------------------------

def read_password(stdin: Any = None, prompt: Callable[[str], str] = getpass.getpass) -> str:
    """Twice from the terminal (getpass: never echoed), or one line from stdin when it isn't a
    terminal. Raises PasswordRejected (no password in the message)."""
    stdin = stdin or sys.stdin
    if stdin.isatty():
        first = prompt("Password: ")
        if prompt("Password again: ") != first:
            raise PasswordRejected("the passwords don't match")
    else:
        first = stdin.readline().rstrip("\r\n")
    check_policy(first)
    return first


def _username(raw: str) -> str:
    name = normalize_username(raw)
    if name is None or name != raw:
        raise argparse.ArgumentTypeError(
            "usernames are 3-32 characters: lower-case letters, digits, '_', '.', '-'")
    return name


def _questions(raw: str) -> int:
    try:
        n = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("a whole number") from None
    if not 0 <= n <= MAX_QUESTIONS_PER_DAY:
        raise argparse.ArgumentTypeError(f"between 0 and {MAX_QUESTIONS_PER_DAY}")
    return n


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="query-api-account",
        description="Owner-created username/password accounts. Passwords are prompted for "
                    "(or read from stdin when it isn't a terminal), never taken as arguments.")
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create", help="a new account (plan pro, the GitHub account's access)")
    c.add_argument("username", type=_username)
    c.add_argument("--questions-per-day", type=_questions, required=True,
                   help=f"assistant questions per UTC day, 0-{MAX_QUESTIONS_PER_DAY}")
    s = sub.add_parser("set-password", help="new password; unlocks; signs out every session")
    s.add_argument("username", type=_username)
    limit = sub.add_parser("set-limit", help="change the daily assistant questions")
    limit.add_argument("username", type=_username)
    limit.add_argument("--questions-per-day", type=_questions, required=True)
    d = sub.add_parser("disable", help="refuse sign-in and end every session")
    d.add_argument("username", type=_username)
    e = sub.add_parser("enable", help="allow sign-in again (also unlocks)")
    e.add_argument("username", type=_username)
    sub.add_parser("list", help="every account (no password hashes)")
    return ap


def demo_access(database_url: str) -> tuple[list[str], list[str], list[dict]]:
    """(company ids, watchlist, starter workspaces) a GitHub account gets, resolved in the
    tributary database like the API does."""
    from psycopg_pool import ConnectionPool

    from query_api.store import Store

    with ConnectionPool(database_url, min_size=1, max_size=1, open=True) as pool:
        ids = Store(pool).resolve_tickers("", list(DEMO_TICKERS))
    companies = list(dict.fromkeys(ids[t] for t in DEMO_TICKERS if t in ids))
    watch = list(dict.fromkeys(ids[t] for t in DEMO_WATCHLIST if t in ids))
    return companies, watch, sandbox_workspaces(ids)


class _OneConnection:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def connection(self) -> "_OneConnection":
        return self

    def __enter__(self) -> psycopg.Connection:
        return self.conn

    def __exit__(self, *exc: object) -> None:
        return None


def run_cli(argv: Sequence[str], store: AppPasswordAccounts, *,
            access: Callable[[], tuple[list[str], list[str], list[dict]]],
            out: Any = None, err: Any = None, stdin: Any = None,
            prompt: Callable[[str], str] = getpass.getpass,
            clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    """The CLI against a store (tests pass their own); returns the exit code."""
    out, err = out or sys.stdout, err or sys.stderr
    args = _parser().parse_args(list(argv))
    now = clock()
    try:
        if args.command == "create":
            password_hash = hash_password(read_password(stdin, prompt))
            companies, watch, workspaces = access()
            if not companies:
                print("error: no demo company resolves in the tributary database", file=err)
                return 1
            user_id = store.create(args.username, password_hash, args.questions_per_day, now,
                                   companies, watch, workspaces)
            print(f"created {args.username} ({user_id}): plan {DEMO_PLAN}, "
                  f"{args.questions_per_day} assistant questions a day", file=out)
        elif args.command == "set-password":
            password_hash = hash_password(read_password(stdin, prompt))
            store.set_password(args.username, password_hash, now)
            print(f"password changed for {args.username}; its sessions are signed out",
                  file=out)
        elif args.command == "set-limit":
            store.set_limit(args.username, args.questions_per_day)
            print(f"{args.username}: {args.questions_per_day} assistant questions a day",
                  file=out)
        elif args.command in ("disable", "enable"):
            store.set_disabled(args.username, args.command == "disable", now)
            print(f"{args.username} {args.command}d", file=out)
        else:
            rows = store.list(now)
            print("username  user_id  questions/day  today  disabled  locked  failed  "
                  "created  last_sign_in", file=out)
            for r in rows:
                print(f"{r['username']}  {r['user_id']}  {r['questions_per_day']}  "
                      f"{r['questions_today']}  {r['disabled']}  {bool(r['locked'])}  "
                      f"{r['failed_attempts']}  {r['created_at']:%Y-%m-%d}  "
                      f"{r['last_sign_in_at'] or '-'}", file=out)
    except PasswordRejected as e:
        print(f"error: {e}", file=err)
        return 1
    except (AccountExists, NoSuchAccount) as e:
        what = "already exists" if isinstance(e, AccountExists) else "doesn't exist"
        print(f"error: account {e} {what}", file=err)
        return 1
    except psycopg.errors.UniqueViolation:
        print(f"error: account {args.username} already exists", file=err)
        return 1
    return 0


def cli_main(argv: Sequence[str] | None = None, env: Mapping[str, str] = os.environ) -> None:
    """query-api-account: needs APP_DATABASE_URL (and DATABASE_URL for create)."""
    argv = sys.argv[1:] if argv is None else argv
    with psycopg.connect(env["APP_DATABASE_URL"], autocommit=True) as conn:
        code = run_cli(argv, AppPasswordAccounts(_OneConnection(conn)),
                       access=lambda: demo_access(env["DATABASE_URL"]))
    sys.exit(code)
