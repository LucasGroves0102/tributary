"""Demo sandboxes: "Try the demo" creates a temporary REAL user in the app database (db/app
migration 003), so a visitor exercises the whole access path: app DB → Debezium → Kafka →
authz-sync → OpenFGA tuples + authz.user_access (RLS). Nothing here writes tuples: the rows
below are ordinary users, subscriptions and company grants, and authz-sync is still their only
reader-and-writer of authorization state.

What a visitor gets is fixed here, never taken from the request: plan DEMO_PLAN, grants on
DEMO_TICKERS (resolved through silver.resolve_company, never CIK = company), a watchlist of
DEMO_WATCHLIST, a starter workspace (DEMO_WORKSPACE), for `ttl_hours`. The session is a
normal JWT whose `sub` is the new user and whose `exp` is the sandbox's expiry; nothing can
extend it. The hourly `query-api-demo-cleanup`
job deletes expired sandbox users, which revokes their access through CDC like any user
deletion; watchlists, workspaces and usage go with them (ON DELETE CASCADE).

Real sign-in (OAuth) is a second way to get the same kind of JWT: find or create the user, mint
the token; nothing downstream knows how the user signed in."""

import hashlib
import hmac
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import psycopg

from query_api.workspaces import seed, starter

# The demo's companies, by ticker. The one list: the API resolves it, the web app shows what
# the API returns, and tests import it.
DEMO_TICKERS: tuple[str, ...] = (
    "PEP", "KO", "KDP", "MNST", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
    "COST", "WMT", "JPM", "V", "XOM", "JNJ", "UNH", "HD", "MCD", "NFLX",
)
# Seeded on the new user's watchlist (a subset of DEMO_TICKERS; watchlists never grant access).
DEMO_WATCHLIST: tuple[str, ...] = ("PEP", "KO", "MSFT", "NVDA", "COST", "JPM")
DEMO_PLAN = "pro"
# The starter workspace every sandbox gets (sandbox_workspaces); it goes with the user.
DEMO_WORKSPACE = "PepsiCo vs Coca-Cola"
# Sandbox user ids start with this; nothing else in the app database does (named demo users
# are alice/bob/carol/dave, real sign-in will use its own ids).
USER_PREFIX = "demo-"
# Serializes sandbox creation, so the per-IP and live-sandbox caps can't be raced past.
LOCK_KEY = 0x7472_6962_6465_6D6F  # "tribdemo"

log = logging.getLogger("query_api.sandbox")


def is_sandbox_user(user_id: str) -> bool:
    return user_id.startswith(USER_PREFIX)


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be true or false, not {raw!r}")


def _positive(env: Mapping[str, str], name: str, default: int) -> int:
    value = int(env.get(name) or default)
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


@dataclass(frozen=True)
class SandboxConfig:
    """Limits for public demo sandboxes. Defaults are deliberately low (model spend); the Helm
    chart sets them from values.yaml `demo.sandbox`."""

    enabled: bool = False
    ttl_hours: int = 24
    creations_per_ip_per_hour: int = 3
    max_live: int = 200
    agent_enabled: bool = True
    agent_questions_per_user_per_day: int = 10
    agent_questions_per_day: int = 200

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "SandboxConfig":
        d = cls()
        config = cls(
            enabled=_flag(env, "DEMO_SANDBOX_ENABLED", d.enabled),
            ttl_hours=_positive(env, "DEMO_SANDBOX_TTL_HOURS", d.ttl_hours),
            creations_per_ip_per_hour=_positive(env, "DEMO_SANDBOX_PER_IP_PER_HOUR",
                                                d.creations_per_ip_per_hour),
            max_live=_positive(env, "DEMO_SANDBOX_MAX_LIVE", d.max_live),
            agent_enabled=_flag(env, "DEMO_AGENT_ENABLED", d.agent_enabled),
            agent_questions_per_user_per_day=_positive(
                env, "DEMO_AGENT_QUESTIONS_PER_USER_PER_DAY", d.agent_questions_per_user_per_day),
            agent_questions_per_day=_positive(env, "DEMO_AGENT_QUESTIONS_PER_DAY",
                                              d.agent_questions_per_day),
        )
        if config.enabled and not 1 <= config.ttl_hours <= 24 * 7:
            raise ValueError("DEMO_SANDBOX_TTL_HOURS must be between 1 and 168")
        return config


def ip_key(secret: str, ip: str) -> str:
    """What the creation limit stores instead of the client address."""
    return hmac.new(secret.encode(), b"demo-ip:" + ip.encode(), hashlib.sha256).hexdigest()[:32]


def sandbox_workspaces(ids: Mapping[str, str]) -> list[dict]:
    """DEMO_WORKSPACE from ticker → company_id: a margins chart for PEP vs KO and comps for PEP
    vs KO, KDP, MNST (whichever resolve). Validated like any saved item. None without PEP+KO."""
    pep, ko = ids.get("PEP"), ids.get("KO")
    if not pep or not ko:
        return []
    layers = [{"kind": "metric", "companyId": c, "metric": m, "transform": "ttm",
               "type": "line", "axis": "L"}
              for m in ("gross_margin", "operating_margin") for c in (pep, ko)]
    peers = [t for t in ("KO", "KDP", "MNST") if ids.get(t)]
    return [starter(DEMO_WORKSPACE, [
        ("chart", "Margins: PEP vs KO",
         {"title": "Gross and operating margin, TTM: PepsiCo vs Coca-Cola", "range": 5,
          "align": "calendar", "layers": layers}),
        ("comps", f"PEP vs {', '.join(peers)}",
         {"target": pep, "peers": [ids[t] for t in peers]}),
    ])]


class Refused(Exception):
    """Sandbox not created: `reason` is "ip" (too many from this client) or "full"."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class QuotaExceeded(Exception):
    """No assistant question left: `scope` is "user" or "day" (all sandboxes)."""

    def __init__(self, scope: str) -> None:
        super().__init__(scope)
        self.scope = scope


class SandboxGone(Exception):
    """The caller's sandbox expired or was deleted."""


@dataclass(frozen=True)
class NewSandbox:
    user_id: str
    company_ids: list[str]
    watchlist: list[str]
    created_at: datetime
    expires_at: datetime
    # starter workspaces (sandbox_workspaces); deleted with the user (ON DELETE CASCADE)
    workspaces: tuple[dict, ...] = ()


class Sandboxes(Protocol):
    def create(self, sandbox: NewSandbox, ip_key: str, per_ip_per_hour: int,
               max_live: int) -> None: ...  # raises Refused
    def expires_at(self, user_id: str) -> datetime | None: ...
    def take_question(self, user_id: str, now: datetime, per_user: int,
                      per_day: int) -> int: ...  # questions left today; raises QuotaExceeded
    def delete_expired(self, now: datetime) -> dict[str, int]: ...


class Pool(Protocol):
    def connection(self) -> Any: ...


class AppSandboxes:
    """Sandboxes in the app database. Creation writes the user, subscription, grants,
    watchlist, starter workspaces and sandbox row in ONE transaction, so authz-sync never sees
    half a sandbox."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    def create(self, sandbox: NewSandbox, ip_key: str, per_ip_per_hour: int,
               max_live: int) -> None:
        s = sandbox
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
            cur.execute("SELECT count(*) FROM demo_creations"
                        " WHERE ip_key = %s AND created_at > %s",
                        (ip_key, s.created_at - timedelta(hours=1)))
            if _count(cur) >= per_ip_per_hour:
                raise Refused("ip")
            cur.execute("SELECT count(*) FROM demo_sandboxes WHERE expires_at > %s",
                        (s.created_at,))
            if _count(cur) >= max_live:
                raise Refused("full")
            cur.execute("INSERT INTO users (user_id, email, created_at) VALUES (%s, %s, %s)",
                        (s.user_id, f"{s.user_id}@sandbox.invalid", s.created_at))
            cur.execute("INSERT INTO demo_sandboxes (user_id, created_at, expires_at)"
                        " VALUES (%s, %s, %s)", (s.user_id, s.created_at, s.expires_at))
            cur.execute("INSERT INTO subscriptions (user_id, plan_id, started_at)"
                        " VALUES (%s, %s, %s)", (s.user_id, DEMO_PLAN, s.created_at))
            cur.execute("INSERT INTO company_grants (company_id, user_id, granted_at)"
                        " SELECT c, %s, %s FROM unnest(%s::text[]) c",
                        (s.user_id, s.created_at, s.company_ids))
            cur.execute("INSERT INTO watchlists (user_id, company_id, added_at)"
                        " SELECT %s, c, %s + make_interval(secs => o::float8 / 1000)"
                        " FROM unnest(%s::text[]) WITH ORDINALITY w(c, o)",
                        (s.user_id, s.created_at, s.watchlist))
            seed(cur, s.user_id, list(s.workspaces))
            cur.execute("INSERT INTO demo_creations (ip_key, created_at) VALUES (%s, %s)",
                        (ip_key, s.created_at))

    def expires_at(self, user_id: str) -> datetime | None:
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT expires_at FROM demo_sandboxes WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return row[0] if row else None

    def take_question(self, user_id: str, now: datetime, per_user: int, per_day: int) -> int:
        day = now.astimezone(UTC).date()
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT expires_at FROM demo_sandboxes WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if row is None or row[0] <= now:
                raise SandboxGone(user_id)
            cur.execute("INSERT INTO demo_agent_daily (day, questions) VALUES (%s, 0)"
                        " ON CONFLICT DO NOTHING", (day,))
            # the day's row lock serializes every sandbox's question count for that day
            cur.execute("SELECT questions FROM demo_agent_daily WHERE day = %s FOR UPDATE",
                        (day,))
            total = _count(cur)
            cur.execute("SELECT questions FROM demo_agent_usage WHERE user_id = %s AND day = %s",
                        (user_id, day))
            mine = _count(cur)
            # the user's own cap first, so one sandbox can't spend more than its share of the day
            if mine >= per_user:
                raise QuotaExceeded("user")
            if total >= per_day:
                raise QuotaExceeded("day")
            cur.execute("INSERT INTO demo_agent_usage (user_id, day, questions) VALUES (%s, %s, 1)"
                        " ON CONFLICT (user_id, day)"
                        " DO UPDATE SET questions = demo_agent_usage.questions + 1",
                        (user_id, day))
            cur.execute("UPDATE demo_agent_daily SET questions = questions + 1 WHERE day = %s",
                        (day,))
            return min(per_user - mine, per_day - total) - 1

    def delete_expired(self, now: datetime) -> dict[str, int]:
        """Deletes expired sandbox users (only users with a sandbox row: never anyone else) and
        everything that cascades from them; prunes old rate-limit and usage rows."""
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id IN"
                        " (SELECT user_id FROM demo_sandboxes WHERE expires_at <= %s)", (now,))
            users = cur.rowcount
            cur.execute("DELETE FROM demo_creations WHERE created_at < %s",
                        (now - timedelta(days=1),))
            creations = cur.rowcount
            cur.execute("DELETE FROM demo_agent_daily WHERE day < %s",
                        (_day(now) - timedelta(days=90),))
            return {"users": users, "creations": creations, "days": cur.rowcount}


def _count(cur: Any) -> int:
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _day(now: datetime) -> date:
    return now.astimezone(UTC).date()


class _OneConnection:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def connection(self) -> "_OneConnection":
        return self

    def __enter__(self) -> psycopg.Connection:
        return self.conn

    def __exit__(self, *exc: object) -> None:
        return None


def cleanup_main() -> None:
    """Delete expired demo sandbox users (CronJob demo-cleanup, hourly). Their access is
    revoked through CDC → authz-sync like any user deletion."""
    from tributary_telemetry import configure_logging

    configure_logging()
    with psycopg.connect(os.environ["APP_DATABASE_URL"]) as conn:
        counts = AppSandboxes(_OneConnection(conn)).delete_expired(datetime.now(UTC))
    log.info("deleted %d expired demo sandboxes", counts["users"], extra=counts)
