"""Market-data providers behind one small interface ("Connect your data", app migration 011).

A provider adapter knows three things: what a key looks like (`key_pattern`, checked before
anything is sent), how to validate a key with one cheap call (`validate`), and how to fetch
end-of-day daily prices for one ticker (`daily`). A second provider is a second adapter
registered in PROVIDERS (plus the app DB's provider CHECK and the web app's provider list),
not a rewrite.

Every adapter takes an injected `httpx.Client` (tests pass `httpx.MockTransport`; nothing in
the test suite calls a real provider) and receives the key as a `keybox.Secret`, revealed only
into the request's Authorization header: never into a URL (httpx logs request URLs at INFO),
never into an exception message, never into a log line. Provider response bodies are never
echoed to the caller either; errors are our own short messages.

Tiingo (https://www.tiingo.com/documentation/end-of-day):
  validate: GET https://api.tiingo.com/tiingo/daily/SPY (the ticker metadata endpoint)
  daily:    GET https://api.tiingo.com/tiingo/daily/<ticker>/prices?startDate=YYYY-MM-DD
            [&endDate=YYYY-MM-DD] -> [{date, close, adjClose, volume, ...}], oldest first
  auth:     header `Authorization: Token <key>`
  401/403 -> bad key; 404 -> unknown ticker; 429 -> the user's Tiingo quota is used up.
Tiingo's developer guide allows software that "requires users to supply their own Tiingo API
Token", provided Tiingo is clearly attributed as the data source and the software does not
redistribute the data (the README lists what to re-check before launch)."""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from query_api.keybox import Secret

log = logging.getLogger("query_api.providers")

TICKER = re.compile(r"^[A-Z0-9.-]{1,12}$")
MAX_BARS = 20_000  # ~80 years of trading days: anything longer is not a daily series


@dataclass(frozen=True)
class Bar:
    day: date
    close: Decimal
    adj_close: Decimal | None
    volume: int | None


class ProviderError(Exception):
    """A provider call failed; `code` and the message are safe to return to the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BadKey(ProviderError):
    def __init__(self) -> None:
        super().__init__("bad_key", "the provider refused this API key: check it and try again")


class NotFound(ProviderError):
    def __init__(self) -> None:
        super().__init__("not_found", "the provider has no prices for this ticker")


class QuotaExceeded(ProviderError):
    def __init__(self) -> None:
        super().__init__("quota", "your provider plan's request limit is used up; "
                                  "try again later")


class Unavailable(ProviderError):
    def __init__(self) -> None:
        super().__init__("unavailable", "the provider didn't answer; try again later")


class PriceProvider(Protocol):
    id: str
    name: str
    #: what a key may look like; anything else is refused before any request
    key_pattern: re.Pattern[str]

    def validate(self, key: Secret) -> None:
        """One cheap call; raises BadKey, QuotaExceeded or Unavailable."""
        ...

    def daily(self, key: Secret, ticker: str, start: date, end: date | None = None) -> list[Bar]:
        """End-of-day prices, oldest first; raises a ProviderError."""
        ...


def _dec(v: object) -> Decimal | None:
    if isinstance(v, bool) or not isinstance(v, int | float | str):
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    return d if d.is_finite() and d > 0 else None


class Tiingo:
    id = "tiingo"
    name = "Tiingo"
    # Tiingo tokens are 40 hex characters; allow some slack in length, never other characters
    key_pattern = re.compile(r"^[A-Za-z0-9]{20,64}$")
    BASE = "https://api.tiingo.com"

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def _get(self, key: Secret, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        try:
            r = self._client.get(f"{self.BASE}{path}", params=params,
                                 headers={"Authorization": f"Token {key.reveal()}",
                                          "Content-Type": "application/json"})
        except httpx.HTTPError as e:
            # the exception text can carry the URL (no key in it), not the headers
            log.warning("tiingo request failed: %s", type(e).__name__)
            raise Unavailable() from None
        if r.status_code in (401, 403):
            raise BadKey()
        if r.status_code == 429:
            raise QuotaExceeded()
        if r.status_code == 404:
            raise NotFound()
        if r.status_code != 200:
            log.warning("tiingo answered %d", r.status_code)
            raise Unavailable()
        return r

    def validate(self, key: Secret) -> None:
        r = self._get(key, "/tiingo/daily/SPY")
        try:
            body = r.json()
        except ValueError:
            raise Unavailable() from None
        # an invalid token can also come back as 200 {"detail": "Invalid token."}
        if not isinstance(body, dict) or "ticker" not in body:
            raise BadKey()

    def daily(self, key: Secret, ticker: str, start: date, end: date | None = None) -> list[Bar]:
        if not TICKER.fullmatch(ticker):
            raise NotFound()
        params = {"startDate": start.isoformat(), "format": "json"}
        if end:
            params["endDate"] = end.isoformat()
        r = self._get(key, f"/tiingo/daily/{ticker.lower()}/prices", params)
        try:
            body = r.json()
        except ValueError:
            raise Unavailable() from None
        if isinstance(body, dict) and "detail" in body:
            raise BadKey() if "token" in str(body["detail"]).lower() else NotFound()
        if not isinstance(body, list) or len(body) > MAX_BARS:
            raise Unavailable()
        bars: dict[date, Bar] = {}
        for row in body:
            if not isinstance(row, dict) or not isinstance(row.get("date"), str):
                continue
            try:
                day = date.fromisoformat(row["date"][:10])
            except ValueError:
                continue
            close = _dec(row.get("close"))
            if close is None:
                continue
            vol = row.get("volume")
            bars[day] = Bar(day, close, _dec(row.get("adjClose")),
                            int(vol) if isinstance(vol, int | float) and not isinstance(vol, bool)
                            and vol >= 0 else None)
        return [bars[d] for d in sorted(bars)]


ProviderFactory = Callable[[httpx.Client], PriceProvider]
PROVIDERS: dict[str, ProviderFactory] = {"tiingo": Tiingo}
#: shown in the Connections page: where to get a key, the terms, the attribution line
PROVIDER_INFO: dict[str, dict[str, str]] = {
    "tiingo": {
        "name": "Tiingo",
        "signup_url": "https://www.tiingo.com/account/api/token",
        "terms_url": "https://www.tiingo.com/about/pricing",
        "attribution": "End-of-day prices from Tiingo, fetched with your own API key.",
        "data": "End-of-day daily prices for US equities",
    },
}
