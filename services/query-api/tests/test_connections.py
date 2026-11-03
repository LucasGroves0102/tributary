"""Connect your data (query_api.connections, providers, valuation): the routes with an in-memory
store keyed by owner (test_connections_db.py runs the real tables and their RLS) and the real
Tiingo adapter over httpx.MockTransport with saved fixtures. Never calls a real provider.

Security cases: the feature is absent without a master key; a key is never returned or logged
(caplog captures every logger); forged and oversized key input is refused before any provider
call; another user can't see my connection or my prices; layer 1 (`require`) runs before any
provider call; disconnect deletes the key and the cached prices."""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.connections import (
    KEY_SAVES_PER_HOUR,
    Limiter,
    MarketData,
    UnknownUser,
    market_data_from_env,
)
from query_api.keybox import Keyring, Sealed, new_master_key
from query_api.providers import BadKey, NotFound, QuotaExceeded, Tiingo, Unavailable
from query_api.valuation import valuation

from .conftest import SECRET
from .test_api import FakeAuthorizer, FakeStore

FIXTURES = Path(__file__).parent / "fixtures"
NVDA, AAPL = "0001045810", "0000320193"
KEY = "test" * 10  # obviously fake: low entropy, never a real key
BOB_KEY = "bobs" * 10
GRANTS = {"alice": {NVDA: {"financials", "filings"}, AAPL: {"financials"}},
          "bob": {NVDA: {"financials"}}}


class FakeConnections:
    """Rows keyed by user, like the forced RLS: every read takes the user and sees only theirs."""

    def __init__(self, users=("alice", "bob")):
        self.users = set(users)
        self.conns: dict[tuple[str, str], dict] = {}
        self.prices_: dict[tuple[str, str, str], dict[date, dict]] = {}
        self.fetches: dict[tuple[str, str, str], dict] = {}

    def mine(self, user_id):
        return [{k: v for k, v in r.items() if k in ("provider", "last4", "created_at",
                                                     "validated_at")}
                for (u, _), r in sorted(self.conns.items()) if u == user_id]

    def sealed(self, user_id, provider):
        r = self.conns.get((user_id, provider))
        return r["sealed"] if r else None

    def put(self, user_id, provider, sealed, last4, validated_at):
        if user_id not in self.users:
            raise UnknownUser(user_id)
        self.conns[(user_id, provider)] = {"provider": provider, "sealed": sealed,
                                           "last4": last4, "created_at": validated_at,
                                           "validated_at": validated_at}
        return self.mine(user_id)[0]

    def delete(self, user_id, provider):
        gone = self.conns.pop((user_id, provider), None) is not None
        for d in (self.prices_, self.fetches):  # ON DELETE CASCADE
            for k in [k for k in d if k[:2] == (user_id, provider)]:
                del d[k]
        return gone

    def fetch_state(self, user_id, provider, company_id):
        return self.fetches.get((user_id, provider, company_id))

    def save_prices(self, user_id, provider, company_id, ticker, status, bars, fetched_at):
        if (user_id, provider) not in self.conns:
            return
        rows = self.prices_.setdefault((user_id, provider, company_id), {})
        for b in bars:
            rows[b.day] = {"day": b.day, "close": b.close, "adj_close": b.adj_close}
        self.fetches[(user_id, provider, company_id)] = {"ticker": ticker, "status": status,
                                                         "fetched_at": fetched_at}

    def prices(self, user_id, provider, company_id, since, until):
        rows = self.prices_.get((user_id, provider, company_id), {})
        return [rows[d] for d in sorted(rows)
                if (since is None or d >= since) and (until is None or d <= until)]

    def latest_day(self, user_id, provider, company_id):
        rows = self.prices_.get((user_id, provider, company_id), {})
        return max(rows) if rows else None


class Tiingo404Unless:
    """A MockTransport handler answering from the fixtures; records every request."""

    def __init__(self, keys=(KEY, BOB_KEY)):
        self.keys = set(keys)
        self.requests: list[httpx.Request] = []
        self.status: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status:
            return httpx.Response(self.status, json={"detail": "nope"})
        token = request.headers.get("Authorization", "").removeprefix("Token ")
        if token not in self.keys:
            return httpx.Response(401, json={"detail": "Invalid token."})
        path = request.url.path
        if path == "/tiingo/daily/SPY":
            return httpx.Response(200, content=(FIXTURES / "tiingo_daily_meta_spy.json")
                                  .read_bytes())
        if path == "/tiingo/daily/nvda/prices":
            return httpx.Response(200, content=(FIXTURES / "tiingo_daily_prices_nvda.json")
                                  .read_bytes())
        return httpx.Response(404, json={"detail": "Ticker 'X' not found"})


class Store(FakeStore):
    def tickers(self, user_id, company_ids):
        return {c: {NVDA: ["NVDA"], AAPL: ["AAPL"]}[c] for c in company_ids
                if c in (NVDA, AAPL)}

    def financials(self, user_id, company_id, metrics, quarters, view, basis):
        self.calls.append(("financials", user_id, company_id, tuple(metrics), view))
        row = {"period_start": None, "accession_no": "0001045810-26-000075", "form": "10-Q",
               "filed": date(2026, 8, 26), "source_tag": "us-gaap:X",
               "filer_cik": "0001045810", "unit": "USD"}
        return [
            {**row, "metric": "shares_outstanding", "value": Decimal("24300000000"),
             "unit": "shares", "period_end": date(2026, 8, 20)},
            {**row, "metric": "net_income", "value": Decimal("99200000000"),
             "period_end": date(2026, 7, 26)},
            {**row, "metric": "revenue", "value": Decimal("187100000000"),
             "period_end": date(2026, 7, 26)},
            {**row, "metric": "operating_income", "value": Decimal("115000000000"),
             "period_end": date(2026, 7, 26)},
            {**row, "metric": "net_debt", "value": Decimal("-50000000000"),
             "period_end": date(2026, 7, 26)},
        ]


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)  # 10:00 ET

    def __call__(self):
        return self.now


@pytest.fixture
def world():
    conns, handler, clock, store = FakeConnections(), Tiingo404Unless(), Clock(), Store()
    keyring = Keyring.parse(new_master_key())
    tiingo = Tiingo(httpx.Client(transport=httpx.MockTransport(handler)))
    market = MarketData(keyring, conns, {"tiingo": tiingo}, clock=clock)
    authz = FakeAuthorizer(GRANTS)
    app = create_app(authz, store, SECRET, market_data=market)
    return type("World", (), {"client": TestClient(app), "conns": conns, "handler": handler,
                              "clock": clock, "store": store, "market": market,
                              "authz": authz, "keyring": keyring})


def connect(w, token, user="alice", key=KEY):
    return w.client.put("/v1/connections/tiingo", headers=token(user),
                        json={"api_key": key})


# --- off without the master key ---------------------------------------------------------------

def test_every_route_is_absent_without_a_master_key(token):
    app = create_app(FakeAuthorizer(GRANTS), Store(), SECRET)
    c = TestClient(app)
    for method, path in [("GET", "/v1/connections"), ("PUT", "/v1/connections/tiingo"),
                         ("DELETE", "/v1/connections/tiingo"),
                         ("GET", f"/v1/companies/{NVDA}/prices"),
                         ("GET", f"/v1/companies/{NVDA}/valuation")]:
        assert c.request(method, path, headers=token("alice"),
                         json={"api_key": KEY}).status_code == 404, path


def test_from_env_off_without_key_and_needs_the_app_db():
    assert market_data_from_env({}, FakeConnections(), {}) is None
    with pytest.raises(RuntimeError):
        market_data_from_env({"DATA_CONNECTIONS_KEY": new_master_key()}, None, {})
    assert market_data_from_env({"DATA_CONNECTIONS_KEY": new_master_key()},
                                FakeConnections(), {}) is not None


def test_routes_need_a_token(world):
    assert world.client.get("/v1/connections").status_code == 401
    assert world.client.put("/v1/connections/tiingo", json={"api_key": KEY}).status_code == 401


# --- keys -------------------------------------------------------------------------------------

def test_connect_validates_once_stores_sealed_and_shows_only_last4(world, token, caplog):
    caplog.set_level(logging.DEBUG)
    r = connect(world, token)
    assert r.status_code == 200, r.text
    assert r.json()["connection"]["key_hint"] == "…test"
    assert r.json()["connection"]["provider"] == "tiingo"
    # one validation call, key only in the Authorization header, never in the URL
    assert len(world.handler.requests) == 1
    req = world.handler.requests[0]
    assert req.url.path == "/tiingo/daily/SPY" and KEY not in str(req.url)
    assert req.headers["Authorization"] == f"Token {KEY}"
    stored = world.conns.conns[("alice", "tiingo")]
    assert isinstance(stored["sealed"], Sealed)
    assert KEY.encode() not in stored["sealed"].ciphertext
    listing = world.client.get("/v1/connections", headers=token("alice"))
    assert listing.json()["connections"][0]["key_hint"] == "…test"
    assert listing.json()["providers"][0]["id"] == "tiingo"
    for text in (r.text, listing.text, caplog.text):
        assert KEY not in text and KEY[:-4] not in text


def test_a_refused_key_is_not_stored_and_not_echoed(world, token, caplog):
    caplog.set_level(logging.DEBUG)
    wrong = "a" * 40
    r = connect(world, token, key=wrong)
    assert r.status_code == 422 and r.json()["code"] == "bad_key"
    assert wrong not in r.text and wrong not in caplog.text
    assert world.conns.conns == {}


@pytest.mark.parametrize("body", [
    {"api_key": "short"},
    {"api_key": "x" * 65},
    {"api_key": "test test test test test"},
    {"api_key": "testtesttesttesttest'; DROP TABLE x;--"},
    {"api_key": "<script>alert(1)</script>testtesttest"},
    {"api_key": 12345678901234567890},
    {"api_key": KEY, "user_id": "bob"},
    {"key": KEY},
    [KEY],
])
def test_forged_key_input_is_refused_before_any_provider_call(world, token, body, caplog):
    caplog.set_level(logging.DEBUG)
    r = world.client.put("/v1/connections/tiingo", headers=token("alice"), json=body)
    assert r.status_code == 422
    assert world.handler.requests == [] and world.conns.conns == {}
    for v in (body.values() if isinstance(body, dict) else body):
        assert str(v) not in r.text and (len(str(v)) < 8 or str(v) not in caplog.text)


def test_oversized_and_unparseable_bodies_are_refused(world, token):
    h = {**token("alice"), "Content-Type": "application/json"}
    big = json.dumps({"api_key": "a" * 5000})
    r = world.client.put("/v1/connections/tiingo", headers=h, content=big)
    assert r.status_code == 413 and "a" * 50 not in r.text
    r = world.client.put("/v1/connections/tiingo", headers=h, content=b"{not json")
    assert r.status_code == 422
    r = world.client.put("/v1/connections/tiingo", headers=h, content=b"\xff\xfe")
    assert r.status_code == 422
    assert world.handler.requests == []


def test_unknown_provider_is_404_or_422(world, token):
    r = world.client.put("/v1/connections/yahoo", headers=token("alice"), json={"api_key": KEY})
    assert r.status_code == 422 and KEY not in r.text


def test_key_saves_are_rate_limited_per_user(world, token):
    for _ in range(KEY_SAVES_PER_HOUR):
        assert connect(world, token).status_code == 200
    r = connect(world, token)
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    assert connect(world, token, user="bob", key=BOB_KEY).status_code == 200


def test_provider_down_on_save_is_502_not_stored(world, token):
    world.handler.status = 500
    r = connect(world, token)
    assert r.status_code == 502 and r.json()["code"] == "unavailable"
    assert world.conns.conns == {}


def test_unknown_user_is_404(world, token):
    r = connect(world, token, user="mallory")
    assert r.status_code == 404


def test_another_user_cant_see_my_connection(world, token):
    connect(world, token)
    assert world.client.get("/v1/connections", headers=token("bob")).json()["connections"] == []
    # bob's delete can't touch alice's connection
    assert world.client.delete("/v1/connections/tiingo", headers=token("bob")).json() == \
        {"deleted": False}
    assert ("alice", "tiingo") in world.conns.conns


# --- prices -----------------------------------------------------------------------------------

def test_prices_need_a_connection(world, token):
    r = world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("alice"))
    assert r.status_code == 404 and "connect" in r.json()["detail"]
    assert world.handler.requests == []


def test_prices_are_fetched_once_per_eastern_day_and_stored_for_the_user(world, token):
    connect(world, token)
    world.handler.requests.clear()
    r = world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["day"] for x in body["rows"]][:2] == ["2026-10-26", "2026-10-27"]
    assert body["rows"][2] == {"day": "2026-10-28", "close": "191.49", "adj_close": "191.4"}
    assert body["provider"] == "tiingo" and "Tiingo" in body["attribution"]
    assert body["ticker"] == "NVDA" and body["stale"] is False
    req = world.handler.requests[0]
    assert req.url.path == "/tiingo/daily/nvda/prices"
    assert req.url.params["startDate"] == "2016-11-02"  # 10 years of history first
    assert KEY not in str(req.url) and KEY not in r.text
    # the same ET day: no second call
    world.clock.now += timedelta(hours=8)  # 18:00 ET
    world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("alice"))
    assert len(world.handler.requests) == 1
    # next ET day: from 10 days before the newest stored day
    world.clock.now += timedelta(hours=7)  # 01:00 ET next day
    world.client.get(f"/v1/companies/{NVDA}/prices?from=2026-10-29", headers=token("alice"))
    assert len(world.handler.requests) == 2
    assert world.handler.requests[1].url.params["startDate"] == "2026-10-20"


def test_first_fetch_on_a_leap_day(world, token):
    connect(world, token)
    world.handler.requests.clear()
    world.clock.now = datetime(2028, 2, 29, 15, tzinfo=UTC)
    assert world.client.get(f"/v1/companies/{NVDA}/prices",
                            headers=token("alice")).status_code == 200
    assert world.handler.requests[0].url.params["startDate"] == "2018-02-28"


def test_another_user_never_sees_my_prices(world, token):
    connect(world, token)
    world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("alice"))
    # bob has no connection: no prices, even though alice's are stored for the same company
    r = world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("bob"))
    assert r.status_code == 404
    # with his own connection he gets his own fetch (with his key), never alice's rows
    connect(world, token, user="bob", key=BOB_KEY)
    world.handler.requests.clear()
    r = world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("bob"))
    assert r.status_code == 200
    assert world.handler.requests[0].headers["Authorization"] == f"Token {BOB_KEY}"
    assert KEY not in r.text
    assert ("bob", "tiingo", NVDA) in world.conns.prices_


def test_layer_one_runs_before_any_provider_call(world, token):
    connect(world, token, user="bob", key=BOB_KEY)
    world.handler.requests.clear()
    r = world.client.get(f"/v1/companies/{AAPL}/prices", headers=token("bob"))
    assert r.status_code == 403
    assert world.handler.requests == []
    assert ("bob", "financials", AAPL) in world.authz.checks


def test_unknown_ticker_is_remembered_for_the_day(world, token):
    connect(world, token)
    world.handler.requests.clear()
    r = world.client.get(f"/v1/companies/{AAPL}/prices", headers=token("alice"))
    assert r.status_code == 200 and r.json()["status"] == "not_found" and r.json()["rows"] == []
    world.client.get(f"/v1/companies/{AAPL}/prices", headers=token("alice"))
    assert len(world.handler.requests) == 1


def test_provider_calls_are_rate_limited_per_user_serving_the_cache(world, token):
    connect(world, token)
    world.market.calls = Limiter(1)
    assert world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("alice")).json()[
        "stale"] is False
    world.clock.now += timedelta(days=1)
    body = world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("alice")).json()
    assert body["stale"] is True and "limit" in body["warning"] and len(body["rows"]) == 5


def test_quota_and_outage_serve_the_cache_with_a_warning(world, token):
    connect(world, token)
    world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("alice"))
    world.clock.now += timedelta(days=1)
    world.handler.status = 429
    body = world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("alice")).json()
    assert body["stale"] is True and "limit" in body["warning"] and len(body["rows"]) == 5


def test_a_revoked_key_asks_to_reconnect(world, token):
    connect(world, token)
    world.handler.keys = set()
    r = world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("alice"))
    assert r.status_code == 409 and "reconnect" in r.json()["detail"]
    assert KEY not in r.text


def test_a_ciphertext_moved_to_another_user_asks_to_reconnect(world, token):
    connect(world, token)
    connect(world, token, user="bob", key=BOB_KEY)
    # an attacker with database write access swaps alice's sealed key into bob's row
    world.conns.conns[("bob", "tiingo")]["sealed"] = world.conns.conns[("alice", "tiingo")][
        "sealed"]
    world.handler.requests.clear()
    r = world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("bob"))
    assert r.status_code == 409 and world.handler.requests == []


def test_disconnect_deletes_the_key_and_the_cached_prices(world, token):
    connect(world, token)
    world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("alice"))
    assert world.conns.prices_
    r = world.client.delete("/v1/connections/tiingo", headers=token("alice"))
    assert r.json() == {"deleted": True}
    assert world.conns.conns == {} and world.conns.prices_ == {} and world.conns.fetches == {}
    assert world.client.get(f"/v1/companies/{NVDA}/prices",
                            headers=token("alice")).status_code == 404


def test_nothing_logged_carries_the_key(world, token, caplog):
    caplog.set_level(logging.DEBUG)
    connect(world, token)
    world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("alice"))
    world.client.get(f"/v1/companies/{NVDA}/valuation", headers=token("alice"))
    world.handler.keys = set()
    world.clock.now += timedelta(days=1)
    world.client.get(f"/v1/companies/{NVDA}/prices", headers=token("alice"))
    world.client.delete("/v1/connections/tiingo", headers=token("alice"))
    assert caplog.records  # the access log and httpx's request lines are captured
    assert KEY not in caplog.text


# --- valuation --------------------------------------------------------------------------------

def test_valuation_formulas_and_dates(world, token):
    connect(world, token)
    r = world.client.get(f"/v1/companies/{NVDA}/valuation", headers=token("alice"))
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["price"] == {"close": "202.89", "day": "2026-10-30", "ticker": "NVDA"}
    # 202.89 × 24.3e9
    assert v["market_cap"] == {"value": "4930227000000", "price_day": "2026-10-30",
                               "shares_period_end": "2026-08-20"}
    assert v["enterprise_value"]["value"] == "4880227000000"
    m = v["multiples"]
    assert m["pe"]["value"] == "49.70"       # 4,930.227 / 99.2
    assert m["ps"]["value"] == "26.35"       # 4,930.227 / 187.1
    assert m["ev_ebit"]["value"] == "42.44"  # 4,880.227 / 115
    assert v["inputs"]["net_income"]["citation"]["accession_no"] == "0001045810-26-000075"
    assert v["formulas"]["pe"] == "market cap / TTM net income"
    assert ("financials", "alice", NVDA, ("shares_outstanding", "net_income", "revenue",
                                          "operating_income", "net_debt"), "ttm") \
        in world.store.calls


def test_valuation_without_a_connection_is_404(world, token):
    assert world.client.get(f"/v1/companies/{NVDA}/valuation",
                            headers=token("alice")).status_code == 404


def _row(metric, value, end=date(2026, 7, 26)):
    return {"metric": metric, "value": Decimal(value), "unit": "USD", "period_end": end,
            "accession_no": "a", "form": "10-Q", "filed": end, "source_tag": None}


def test_valuation_edge_cases():
    price = {"day": date(2026, 10, 30), "close": Decimal("10"), "ticker": "X"}
    v = valuation(price, [_row("shares_outstanding", "100"), _row("net_income", "-5"),
                          _row("revenue", "0")], ["X"])
    assert v["market_cap"]["value"] == "1000"
    assert v["multiples"]["pe"] == {"value": None,
                                    "note": "not meaningful: TTM net income is zero or negative"}
    assert v["multiples"]["ps"]["value"] is None
    assert v["multiples"]["ev_ebit"]["note"] == "no net debt in the filings"
    assert v["share_classes"] is None
    v = valuation(None, [_row("shares_outstanding", "100")], ["GOOG", "GOOGL"])
    assert v["market_cap"] is None and v["note"] == "no close in the last 14 days"
    assert v["share_classes"] == ["GOOG", "GOOGL"]
    v = valuation(price, [], ["X"])
    assert v["market_cap"] is None and "shares outstanding" in v["note"]


# --- the adapter alone ------------------------------------------------------------------------

def _tiingo(handler):
    return Tiingo(httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("status, error", [(401, BadKey), (403, BadKey), (404, NotFound),
                                           (429, QuotaExceeded), (500, Unavailable)])
def test_tiingo_errors(status, error):
    from query_api.keybox import Secret
    t = _tiingo(lambda r: httpx.Response(status, json={"detail": KEY}))
    with pytest.raises(error) as e:
        t.daily(Secret(KEY), "NVDA", date(2026, 1, 1))
    assert KEY not in str(e.value)


def test_tiingo_invalid_token_body_and_bad_rows():
    from query_api.keybox import Secret
    t = _tiingo(lambda r: httpx.Response(200, json={"detail": "Invalid token."}))
    with pytest.raises(BadKey):
        t.validate(Secret(KEY))
    with pytest.raises(BadKey):
        t.daily(Secret(KEY), "NVDA", date(2026, 1, 1))
    rows = [{"date": "2026-10-30T00:00:00.000Z", "close": "x"},
            {"date": "garbage", "close": 1},
            {"date": "2026-10-29T00:00:00.000Z", "close": -1},
            {"date": "2026-10-28T00:00:00.000Z", "close": 5, "adjClose": None, "volume": True},
            "not a row"]
    t = _tiingo(lambda r: httpx.Response(200, json=rows))
    bars = t.daily(Secret(KEY), "NVDA", date(2026, 1, 1))
    assert [(b.day, b.close, b.adj_close, b.volume) for b in bars] == \
        [(date(2026, 10, 28), Decimal("5"), None, None)]


def test_tiingo_refuses_odd_tickers_without_a_call():
    from query_api.keybox import Secret
    calls = []
    t = _tiingo(lambda r: calls.append(r) or httpx.Response(200, json=[]))
    with pytest.raises(NotFound):
        t.daily(Secret(KEY), "../../account", date(2026, 1, 1))
    assert calls == []


def test_tiingo_network_error_is_unavailable_without_details():
    from query_api.keybox import Secret

    def boom(request):
        raise httpx.ConnectError("connection refused", request=request)
    with pytest.raises(Unavailable):
        _tiingo(boom).validate(Secret(KEY))
