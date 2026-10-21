"""One SEC rate limit shared by every process on the host.

The SEC allows 10 requests/second per IP across everything we run. Each process asks a token
bucket in Postgres (`ops.sec_take_token`, db/migrations/019_sec_rate_limit.sql) before every
request; the bucket decides priorities (caller classes with reserves) and shared cooldowns.
If Postgres can't be reached the process falls back to a conservative local limit and retries
the database periodically: slower, never unlimited.
"""

import logging
import os
import random
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from prometheus_client import Counter, Gauge

from tributary_sec.ratelimit import RateLimiter

log = logging.getLogger(__name__)

# live: the poller and doc fetches for new filings; batch: nightly jobs; bulk: crawls.
CALLER_CLASSES = ("live", "batch", "bulk")
DEFAULT_CLASS = "batch"
# Per process when the shared bucket is unavailable: a few processes at once stay under 10/s.
FALLBACK_PER_SECOND = 2.0

GRANTED = Counter("tributary_sec_limiter_granted_total",
                  "SEC request slots granted, by caller class and mode (shared or local fallback)",
                  ["caller_class", "mode"])
WAITS = Counter("tributary_sec_limiter_waits_total",
                "Times the shared bucket told this process to wait", ["caller_class"])
WAIT_SECONDS = Counter("tributary_sec_limiter_wait_seconds_total",
                       "Seconds spent waiting for the shared bucket", ["caller_class"])
THROTTLES = Counter("tributary_sec_limiter_throttles_total",
                    "SEC throttle responses (429 / Request Rate Threshold Exceeded) seen",
                    ["caller_class"])
FALLBACKS = Counter("tributary_sec_limiter_fallbacks_total",
                    "Switches to the local fallback limit because the shared bucket failed",
                    ["caller_class"])
IN_FALLBACK = Gauge("tributary_sec_limiter_fallback",
                    "1 while this process uses the local fallback limit", ["caller_class"])
SHARED_TOKENS = Gauge("tributary_sec_limiter_shared_tokens",
                      "Tokens in the shared bucket at this process's last request")
SHARED_GRANTED = Gauge("tributary_sec_limiter_shared_granted",
                       "Requests granted by the shared bucket to all processes (a counter "
                       "read from Postgres; rate() it for the shared request rate)")
COOLING = Gauge("tributary_sec_limiter_cooling",
                "1 while the shared bucket is in a throttle cooldown")


@dataclass(frozen=True)
class Grant:
    """Answer from the bucket: wait_s == 0 means a token was taken."""
    wait_s: float
    tokens: float
    granted: int
    cooling: bool = False


class TokenStore(Protocol):
    def take(self, caller_class: str) -> Grant: ...
    def report_throttle(self) -> float: ...


class PostgresStore:
    """The shared bucket in Postgres. Connects lazily; a failed call drops the connection so the
    next call reconnects."""

    def __init__(self, url: str, bucket: str = "sec", connect_timeout: int = 3) -> None:
        self._url = url
        self._bucket = bucket
        self._connect_timeout = connect_timeout
        self._conn: Any = None

    def _call(self, sql: str, params: tuple) -> Any:
        import psycopg  # only processes that use the shared bucket need it loaded

        try:
            if self._conn is None or self._conn.closed:
                self._conn = psycopg.connect(
                    self._url, autocommit=True, connect_timeout=self._connect_timeout,
                    options="-c statement_timeout=2000", application_name="tributary-sec-limit")
            return self._conn.execute(sql, params).fetchone()
        except Exception:
            self.close()
            raise

    def take(self, caller_class: str) -> Grant:
        wait_s, tokens, granted, cooling = self._call(
            "SELECT wait_s, tokens, granted, cooling FROM ops.sec_take_token(%s, %s)",
            (caller_class, self._bucket))
        return Grant(float(wait_s), float(tokens), int(granted), bool(cooling))

    def report_throttle(self) -> float:
        (secs,) = self._call("SELECT ops.sec_report_throttle(%s)", (self._bucket,))
        return float(secs)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - closing a broken connection
                pass
            self._conn = None


class MemoryBucket:
    """In-process twin of ops.sec_take_token / ops.sec_report_throttle (migration 019), for
    tests and simulations. Keep the two algorithms identical."""

    def __init__(self, rate: float = 7, capacity: float = 2,
                 reserves: Mapping[str, float] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.rate = rate
        self.capacity = capacity
        self.reserves = dict(reserves or {"live": 0, "batch": 0.5, "bulk": 1})
        self.clock = clock
        self.tokens = capacity
        self.updated_at = clock()
        self.cooldown_until = float("-inf")
        self.strikes = 0
        self.last_throttle_at: float | None = None
        self.granted = 0
        self.throttles = 0

    def take(self, caller_class: str) -> Grant:
        now = self.clock()
        reserve = self.reserves.get(caller_class, max(self.reserves.values(), default=0))
        need = 1 + min(reserve, self.capacity - 1)
        if now < self.updated_at:
            return Grant(self.updated_at - now + max(0, need - self.tokens) / self.rate,
                         self.tokens, self.granted, True)
        level = min(self.capacity, self.tokens + (now - self.updated_at) * self.rate)
        if level >= need - 1e-9:
            self.tokens, self.updated_at = level - 1, now
            self.granted += 1
            return Grant(0.0, self.tokens, self.granted)
        return Grant((need - level) / self.rate, level, self.granted)

    def report_throttle(self) -> float:
        now = self.clock()
        if now < self.cooldown_until:
            return self.cooldown_until - now
        recent = self.last_throttle_at is not None and now - self.last_throttle_at < 1800
        self.strikes = self.strikes + 1 if recent else 0
        secs = min(600.0, 10.0 * 2 ** min(self.strikes, 6))
        self.tokens = 0
        self.updated_at = self.cooldown_until = now + secs
        self.last_throttle_at = now
        self.throttles += 1
        return secs


class SharedRateLimiter:
    """wait() blocks until the shared bucket grants this process a request.

    Waits are re-asked at least every `max_sleep` seconds (a cooldown can be lifted or the
    bucket retuned meanwhile) with a little jitter, so callers of one class waiting for the
    same token don't wake in lockstep. When the store fails, wait() uses `fallback` and tries
    the store again after `retry_after` seconds.
    """

    def __init__(
        self,
        store: TokenStore,
        caller_class: str = DEFAULT_CLASS,
        fallback: RateLimiter | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
        max_sleep: float = 1.0,
        retry_after: float = 30.0,
    ) -> None:
        if caller_class not in CALLER_CLASSES:
            raise ValueError(f"caller class must be one of {CALLER_CLASSES}, not {caller_class!r}")
        if retry_after <= 0:
            raise ValueError("retry_after must be positive")
        self.caller_class = caller_class
        self._store = store
        self._fallback = fallback or RateLimiter(FALLBACK_PER_SECOND, clock=clock, sleep=sleep)
        self._clock = clock
        self._sleep = sleep
        self._rand = rand
        self._max_sleep = max_sleep
        self._retry_after = retry_after
        self._fallback_until: float | None = None

    @property
    def in_fallback(self) -> bool:
        return self._fallback_until is not None

    def wait(self) -> None:
        cls = self.caller_class
        while True:
            if self._fallback_until is not None and self._clock() < self._fallback_until:
                self._fallback.wait()
                GRANTED.labels(cls, "local").inc()
                return
            try:
                grant = self._store.take(cls)
            except Exception as e:  # noqa: BLE001 - any store failure means fall back
                self._enter_fallback(e)
                continue
            self._leave_fallback()
            SHARED_TOKENS.set(grant.tokens)
            SHARED_GRANTED.set(grant.granted)
            COOLING.set(1 if grant.cooling else 0)
            if grant.wait_s <= 0:
                GRANTED.labels(cls, "shared").inc()
                return
            pause = min(grant.wait_s * (1 + 0.1 * self._rand()), self._max_sleep)
            WAITS.labels(cls).inc()
            WAIT_SECONDS.labels(cls).inc(pause)
            self._sleep(pause)

    def throttled(self) -> None:
        """The SEC answered 429 / rate threshold exceeded: start a cooldown for everyone."""
        THROTTLES.labels(self.caller_class).inc()
        if self._fallback_until is None:
            try:
                secs = self._store.report_throttle()
                log.warning("SEC throttled us; shared cooldown %.0fs", secs,
                            extra={"cooldown_s": secs, "caller_class": self.caller_class})
                return
            except Exception as e:  # noqa: BLE001
                self._enter_fallback(e)
        self._fallback.throttled()

    def _enter_fallback(self, error: Exception) -> None:
        if self._fallback_until is None:
            log.warning("shared SEC rate limit unavailable (%s); local limit %.1f req/s for "
                        "%.0fs", error, FALLBACK_PER_SECOND, self._retry_after,
                        extra={"caller_class": self.caller_class})
            FALLBACKS.labels(self.caller_class).inc()
            IN_FALLBACK.labels(self.caller_class).set(1)
        self._fallback_until = self._clock() + self._retry_after

    def _leave_fallback(self) -> None:
        if self._fallback_until is not None:
            log.info("shared SEC rate limit back", extra={"caller_class": self.caller_class})
            IN_FALLBACK.labels(self.caller_class).set(0)
            self._fallback_until = None


def limiter_from_env(environ: Mapping[str, str] = os.environ) -> SharedRateLimiter | RateLimiter:
    """The limiter sec_client uses by default.

    SEC_LIMITER_DATABASE_URL (else DATABASE_URL): Postgres holding ops.sec_rate_limit.
    SEC_CALLER_CLASS: live | batch | bulk (default batch).
    Without a database URL: the local fallback limit only.
    """
    caller_class = environ.get("SEC_CALLER_CLASS") or DEFAULT_CLASS
    if caller_class not in CALLER_CLASSES:
        raise ValueError(f"SEC_CALLER_CLASS must be one of {CALLER_CLASSES}, not {caller_class!r}")
    url = environ.get("SEC_LIMITER_DATABASE_URL") or environ.get("DATABASE_URL")
    if not url:
        log.warning("no SEC_LIMITER_DATABASE_URL or DATABASE_URL: local SEC limit %.1f req/s "
                    "(%s)", FALLBACK_PER_SECOND, os.path.basename(sys.argv[0]))
        return RateLimiter(FALLBACK_PER_SECOND)
    return SharedRateLimiter(PostgresStore(url), caller_class)
