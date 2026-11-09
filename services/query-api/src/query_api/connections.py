"""Connect your data (Phase 9 C): each user's own market-data key, and prices fetched with it.

Off unless DATA_CONNECTIONS_KEY holds a master key (query_api.keybox): without it
`add_connection_routes` adds only `GET /v1/connections`, which answers no providers and no
connections (the web app asks on every load, and a 404 would be an error line in every visitor's
console); every other path below answers 404 and the web app hides the feature. On, for the
caller only (the user is always the JWT `sub`):

  GET    /v1/connections                     providers offered + the caller's connections
                                             (provider, "…last4", dates; never the key)
  PUT    /v1/connections/{provider}          {"api_key": "..."}: checked against the
                                             provider's key pattern, validated with ONE cheap
                                             provider call, sealed (envelope AES-GCM), stored
  DELETE /v1/connections/{provider}          deletes the key AND the caller's cached prices
  GET    /v1/companies/{id}/prices           the caller's daily closes for the company
  GET    /v1/companies/{id}/valuation        market cap, P/E, P/S, EV/EBIT (query_api.valuation)

Two layers, like every private thing here: layer 1 = `require(financials)` on the company
(OpenFGA) + the route reads only the JWT user's own connection; layer 2 = app migration 011's
FORCEd owner-only row-level security on app.user_id (every statement also filters on it).
Prices are stored per user (market_prices): there is no cache shared across users, so a
user only ever sees data fetched with their own key.

Fetch on demand: opening a company's price view refreshes that (user, company) at most once
per US Eastern day (`FRESH_FOR`: the fetch row's ET date must be today), first 10 years then
from 10 days before the newest stored day (catches the provider's corrections). Per-user rate
limits (in-process, per replica, injected clock): PROVIDER_CALLS_PER_HOUR provider calls and
KEY_SAVES_PER_HOUR key saves; over the limit the cached prices are served with `stale: true`
(or 429 if there are none).

The key: read only by `MarketData._key`, opened in-process into a `keybox.Secret` that goes
straight to the provider adapter's Authorization header. It is never logged, never returned
(responses carry provider + last4), never put in an exception or a URL. The PUT body is parsed
here by hand, not by a pydantic model, because FastAPI's 422 echoes the submitted input."""

import json
import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal, Protocol
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter
from psycopg.rows import dict_row
from starlette.concurrency import run_in_threadpool

from query_api.keybox import Keyring, Sealed, SealedKeyError, Secret, last4
from query_api.providers import (
    PROVIDER_INFO,
    BadKey,
    NotFound,
    PriceProvider,
    ProviderError,
)
from query_api.valuation import METRICS as VALUATION_METRICS
from query_api.valuation import valuation

ET = ZoneInfo("America/New_York")
HISTORY_YEARS = 10
OVERLAP_DAYS = 10
PROVIDER_CALLS_PER_HOUR = 30
KEY_SAVES_PER_HOUR = 5
MAX_PUT_BODY = 1024
Provider = Literal["tiingo"]
CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]

PROVIDER_CALLS = Counter("tributary_market_data_calls_total",
                         "Calls to market-data providers by provider, kind (validate, daily) "
                         "and result (ok, bad_key, not_found, quota, unavailable)",
                         ["provider", "kind", "result"])

log = logging.getLogger("query_api.connections")


# --- storage --------------------------------------------------------------------------------

class UnknownUser(Exception):
    """No app.users row for the caller."""


class Connections(Protocol):
    def mine(self, user_id: str) -> list[dict]: ...
    def sealed(self, user_id: str, provider: str) -> Sealed | None: ...
    def put(self, user_id: str, provider: str, sealed: Sealed, last4: str,
            validated_at: datetime) -> dict: ...
    def delete(self, user_id: str, provider: str) -> bool: ...
    def fetch_state(self, user_id: str, provider: str, company_id: str) -> dict | None: ...
    def save_prices(self, user_id: str, provider: str, company_id: str, ticker: str,
                    status: str, bars: list, fetched_at: datetime) -> None: ...
    def prices(self, user_id: str, provider: str, company_id: str, since: date | None,
               until: date | None) -> list[dict]: ...
    def latest_day(self, user_id: str, provider: str, company_id: str) -> date | None: ...


class Pool(Protocol):
    def connection(self) -> Any: ...


PUBLIC = "provider, last4, created_at, validated_at"


class AppConnections:
    """Each method is one app-DB transaction as `user_id` (app.user_id for the forced RLS),
    and every statement filters on it as well."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @contextmanager
    def _tx(self, user_id: str) -> Iterator[psycopg.Cursor]:
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            yield cur

    def mine(self, user_id: str) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {PUBLIC} FROM data_connections WHERE user_id = %s"
                        " ORDER BY provider", (user_id,))
            return cur.fetchall()

    def sealed(self, user_id: str, provider: str) -> Sealed | None:
        with self._tx(user_id) as cur:
            cur.execute("SELECT key_id, wrapped_key, nonce, ciphertext FROM data_connections"
                        " WHERE user_id = %s AND provider = %s", (user_id, provider))
            r = cur.fetchone()
            return Sealed(r["key_id"], bytes(r["wrapped_key"]), bytes(r["nonce"]),
                          bytes(r["ciphertext"])) if r else None

    def put(self, user_id: str, provider: str, sealed: Sealed, last4: str,
            validated_at: datetime) -> dict:
        try:
            with self._tx(user_id) as cur:
                cur.execute(
                    "INSERT INTO data_connections (user_id, provider, key_id, wrapped_key, nonce,"
                    "  ciphertext, last4, created_at, validated_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (user_id, provider) DO UPDATE SET key_id = EXCLUDED.key_id,"
                    "  wrapped_key = EXCLUDED.wrapped_key, nonce = EXCLUDED.nonce,"
                    "  ciphertext = EXCLUDED.ciphertext, last4 = EXCLUDED.last4,"
                    "  created_at = EXCLUDED.created_at, validated_at = EXCLUDED.validated_at"
                    f" RETURNING {PUBLIC}",
                    (user_id, provider, sealed.key_id, sealed.wrapped_key, sealed.nonce,
                     sealed.ciphertext, last4, validated_at, validated_at))
                return cur.fetchone()
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None

    def delete(self, user_id: str, provider: str) -> bool:
        """The key and (ON DELETE CASCADE) every price and fetch row of this connection."""
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM data_connections WHERE user_id = %s AND provider = %s",
                        (user_id, provider))
            return cur.rowcount == 1

    def fetch_state(self, user_id: str, provider: str, company_id: str) -> dict | None:
        with self._tx(user_id) as cur:
            cur.execute("SELECT ticker, status, fetched_at FROM market_price_fetches"
                        " WHERE user_id = %s AND provider = %s AND company_id = %s",
                        (user_id, provider, company_id))
            return cur.fetchone()

    def save_prices(self, user_id: str, provider: str, company_id: str, ticker: str,
                    status: str, bars: list, fetched_at: datetime) -> None:
        try:
            with self._tx(user_id) as cur:
                if bars:
                    cur.executemany(
                        "INSERT INTO market_prices (user_id, provider, company_id, day, close,"
                        "  adj_close, volume) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT (user_id, provider, company_id, day) DO UPDATE SET"
                        "  close = EXCLUDED.close, adj_close = EXCLUDED.adj_close,"
                        "  volume = EXCLUDED.volume",
                        [(user_id, provider, company_id, b.day, b.close, b.adj_close, b.volume)
                         for b in bars])
                cur.execute(
                    "INSERT INTO market_price_fetches (user_id, provider, company_id, ticker,"
                    "  status, fetched_at) VALUES (%s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (user_id, provider, company_id) DO UPDATE SET"
                    "  ticker = EXCLUDED.ticker, status = EXCLUDED.status,"
                    "  fetched_at = EXCLUDED.fetched_at",
                    (user_id, provider, company_id, ticker, status, fetched_at))
        except psycopg.errors.ForeignKeyViolation:
            pass  # disconnected while the fetch ran: nothing to keep

    def prices(self, user_id: str, provider: str, company_id: str, since: date | None,
               until: date | None) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute("SELECT day, close, adj_close FROM market_prices"
                        " WHERE user_id = %s AND provider = %s AND company_id = %s"
                        "   AND (%s::date IS NULL OR day >= %s::date)"
                        "   AND (%s::date IS NULL OR day <= %s::date)"
                        " ORDER BY day",
                        (user_id, provider, company_id, since, since, until, until))
            return cur.fetchall()

    def latest_day(self, user_id: str, provider: str, company_id: str) -> date | None:
        with self._tx(user_id) as cur:
            cur.execute("SELECT max(day) AS day FROM market_prices"
                        " WHERE user_id = %s AND provider = %s AND company_id = %s",
                        (user_id, provider, company_id))
            r = cur.fetchone()
            return r["day"] if r else None


# --- rate limits ----------------------------------------------------------------------------

class Limiter:
    """At most `limit` events per user in any `window` seconds (in-process, per replica)."""

    def __init__(self, limit: int, window: float = 3600.0,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.limit, self.window, self._now = limit, window, monotonic
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def take(self, user_id: str) -> bool:
        now = self._now()
        with self._lock:
            q = self._events[user_id]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


# --- the service ----------------------------------------------------------------------------

@dataclass
class Fetched:
    provider: str
    ticker: str | None
    status: str          # ok | not_found | no_ticker
    fetched_at: datetime | None
    stale: bool          # the refresh was skipped (rate limit) or failed: cached data shown
    warning: str | None


class Reconnect(Exception):
    """The stored key no longer works (provider refused it, or it can't be decrypted)."""


class MarketData:
    def __init__(self, keyring: Keyring, connections: Connections,
                 providers: Mapping[str, PriceProvider],
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 calls: Limiter | None = None, saves: Limiter | None = None) -> None:
        self.keyring = keyring
        self.connections = connections
        self.providers = dict(providers)
        self.clock = clock
        self.calls = calls or Limiter(PROVIDER_CALLS_PER_HOUR)
        self.saves = saves or Limiter(KEY_SAVES_PER_HOUR)

    def provider_of(self, user_id: str) -> str | None:
        """The caller's connected provider (the first in PROVIDERS order), or None."""
        mine = {c["provider"] for c in self.connections.mine(user_id)}
        return next((p for p in self.providers if p in mine), None)

    def _key(self, user_id: str, provider: str) -> Secret:
        sealed = self.connections.sealed(user_id, provider)
        if sealed is None:
            raise Reconnect()
        try:
            return self.keyring.open_(user_id, provider, sealed)
        except SealedKeyError:
            log.warning("stored market-data key can't be opened",
                        extra={"user_id": user_id, "provider": provider})
            raise Reconnect() from None

    def connect(self, user_id: str, provider: str, api_key: str) -> dict:
        """Validate with one provider call, then seal and store. Raises ProviderError."""
        adapter = self.providers[provider]
        secret = Secret(api_key)
        try:
            adapter.validate(secret)
        except ProviderError as e:
            PROVIDER_CALLS.labels(provider, "validate", e.code).inc()
            raise
        PROVIDER_CALLS.labels(provider, "validate", "ok").inc()
        sealed = self.keyring.seal(user_id, provider, secret.reveal())
        return self.connections.put(user_id, provider, sealed, last4(api_key), self.clock())

    def fresh(self, user_id: str, provider: str, company_id: str,
              ticker: str | None) -> Fetched:
        """Refresh the caller's prices for the company if the last fetch isn't from today (ET);
        returns what the stored data is."""
        if ticker is None:
            return Fetched(provider, None, "no_ticker", None, False, None)
        state = self.connections.fetch_state(user_id, provider, company_id)
        now = self.clock()
        if (state and state["ticker"] == ticker
                and state["fetched_at"].astimezone(ET).date() == now.astimezone(ET).date()):
            return Fetched(provider, ticker, state["status"], state["fetched_at"], False, None)
        cached = Fetched(provider, ticker, state["status"] if state else "ok",
                         state["fetched_at"] if state else None, True, None)
        if not self.calls.take(user_id):
            cached.warning = "price refresh skipped: hourly limit for your connection reached"
            return cached
        latest = self.connections.latest_day(user_id, provider, company_id)
        today = now.astimezone(ET).date()
        start = (latest - timedelta(days=OVERLAP_DAYS) if latest and state
                 and state["ticker"] == ticker
                 # day capped at 28: Feb 29 has no counterpart ten years back
                 else today.replace(year=today.year - HISTORY_YEARS, day=min(today.day, 28)))
        key = self._key(user_id, provider)
        try:
            bars = self.providers[provider].daily(key, ticker, start)
        except NotFound:
            PROVIDER_CALLS.labels(provider, "daily", "not_found").inc()
            self.connections.save_prices(user_id, provider, company_id, ticker, "not_found",
                                         [], now)
            return Fetched(provider, ticker, "not_found", now, False, None)
        except BadKey:
            PROVIDER_CALLS.labels(provider, "daily", "bad_key").inc()
            raise Reconnect() from None
        except ProviderError as e:
            PROVIDER_CALLS.labels(provider, "daily", e.code).inc()
            cached.warning = f"price refresh failed: {e}"
            return cached
        PROVIDER_CALLS.labels(provider, "daily", "ok").inc()
        self.connections.save_prices(user_id, provider, company_id, ticker, "ok", bars, now)
        return Fetched(provider, ticker, "ok", now, False, None)


def market_data_from_env(env: Mapping[str, str], connections: Connections | None,
                         providers: Mapping[str, PriceProvider]) -> MarketData | None:
    """None (feature off) without DATA_CONNECTIONS_KEY; with it, the app DB is required."""
    keyring = Keyring.from_env(env)
    if keyring is None:
        return None
    if connections is None:
        raise RuntimeError("DATA_CONNECTIONS_KEY needs APP_DATABASE_URL")
    return MarketData(keyring, connections, providers)


# --- routes ---------------------------------------------------------------------------------

def _connection(row: dict) -> dict:
    return {"provider": row["provider"], "key_hint": f"…{row['last4']}",
            "created_at": row["created_at"], "validated_at": row["validated_at"]}


def _bad(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"detail": message, "code": code}, status_code=status)


def add_connection_routes(app: FastAPI, *, market: MarketData | None, store: Any,
                          current_user: Callable[..., str],
                          require: Callable[[str, str, str], None]) -> None:
    User = Annotated[str, Depends(current_user)]
    if market is None:
        # feature off: nothing offered and nothing connected (the web app hides it); every other
        # route of the feature is absent (404)
        @app.get("/v1/connections")
        def no_connections(user_id: User) -> dict:
            return {"providers": [], "connections": []}

        return

    @app.get("/v1/connections")
    def list_connections(user_id: User) -> dict:
        mine = market.connections.mine(user_id)
        return {"providers": [{"id": p, **PROVIDER_INFO[p]} for p in market.providers],
                "connections": [_connection(r) for r in mine]}

    @app.put("/v1/connections/{provider}")
    async def put_connection(provider: Provider, request: Request, user_id: User):
        size = request.headers.get("content-length")
        if size is None or not size.isdigit() or int(size) > MAX_PUT_BODY:
            return _bad(413, "too_large", f"send a JSON body of at most {MAX_PUT_BODY} bytes")
        raw = await request.body()
        if len(raw) > MAX_PUT_BODY:
            return _bad(413, "too_large", f"send a JSON body of at most {MAX_PUT_BODY} bytes")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return _bad(422, "invalid", 'send {"api_key": "..."}')
        if not isinstance(body, dict) or set(body) != {"api_key"} \
                or not isinstance(body["api_key"], str):
            return _bad(422, "invalid", 'send {"api_key": "..."}')
        api_key = body["api_key"].strip()
        adapter = market.providers[provider]
        if not adapter.key_pattern.fullmatch(api_key):
            # never echo the input: say what a key looks like instead
            return _bad(422, "invalid", f"that doesn't look like a {adapter.name} API key "
                                        "(letters and digits only, 20-64 characters)")
        if not market.saves.take(user_id):
            return _bad(429, "rate_limited", "too many key saves; try again in an hour")
        try:
            row = await run_in_threadpool(market.connect, user_id, provider, api_key)
        except BadKey as e:
            return _bad(422, e.code, str(e))
        except ProviderError as e:
            return _bad(502, e.code, f"couldn't check the key: {e}")
        except UnknownUser:
            return _bad(404, "unknown_user", "unknown user")
        return {"connection": _connection(row)}

    @app.delete("/v1/connections/{provider}")
    def delete_connection(provider: Provider, user_id: User) -> dict:
        return {"deleted": market.connections.delete(user_id, provider)}

    def prepared(user_id: str, company_id: str) -> tuple[str, list[str], Fetched]:
        require(user_id, "financials", company_id)  # layer 1, before anything is read
        provider = market.provider_of(user_id)
        if provider is None:
            raise HTTPException(404, "connect your market data first (Settings → Connections)")
        tickers = store.tickers(user_id, [company_id]).get(company_id) or []
        try:
            fetched = market.fresh(user_id, provider, company_id,
                                   tickers[0] if tickers else None)
        except Reconnect:
            raise HTTPException(409, "your market-data key no longer works: reconnect it in "
                                     "Settings → Connections") from None
        return provider, tickers, fetched

    def meta(f: Fetched) -> dict:
        return {"provider": f.provider, "attribution": PROVIDER_INFO[f.provider]["attribution"],
                "ticker": f.ticker, "status": f.status, "fetched_at": f.fetched_at,
                "stale": f.stale, "warning": f.warning}

    @app.get("/v1/companies/{company_id}/prices", response_model=dict)
    def prices(user_id: User, company_id: CompanyId,
               start: Annotated[date | None, Query(alias="from")] = None,
               end: Annotated[date | None, Query(alias="to")] = None) -> dict:
        """The caller's own end-of-day prices (exact decimal strings), oldest first."""
        provider, _, fetched = prepared(user_id, company_id)
        rows = market.connections.prices(user_id, provider, company_id, start, end)
        return {"company_id": company_id, **meta(fetched), "rows": [
            {"day": r["day"], "close": str(r["close"]),
             "adj_close": str(r["adj_close"]) if r["adj_close"] is not None else None}
            for r in rows]}

    @app.get("/v1/companies/{company_id}/valuation", response_model=dict)
    def company_valuation(user_id: User, company_id: CompanyId) -> dict:
        """Market cap and multiples from the caller's latest close and the latest gold
        figures (formulas and dates in the response)."""
        provider, tickers, fetched = prepared(user_id, company_id)
        since = market.clock().astimezone(ET).date() - timedelta(days=14)
        last = market.connections.prices(user_id, provider, company_id, since, None)
        price = ({"day": last[-1]["day"], "close": last[-1]["close"], "ticker": fetched.ticker}
                 if last else None)
        gold = store.financials(user_id, company_id, VALUATION_METRICS, 1, "ttm", "fiscal")
        return {"company_id": company_id, **meta(fetched), **valuation(price, gold, tickers)}
