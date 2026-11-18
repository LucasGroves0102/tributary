"""The cross-user access suite's world (tests/access/README.md): the real Query API (every
feature on, every store real) over the two test databases, one user (ANN) who owns one of
everything, and the people who try to reach it: another user (BEN) signed in, with a read key,
a write key and a connected app's token, and a demo sandbox (SANDBOX). No OpenFGA:
`CatalogAuthorizer` answers as authz/model.fga does (with the catalog open every signed-in user
reads every catalog company, which is exactly why private data can't lean on layer 1), and the
RLS tables say the same.

Fidelity, so a pass means what it says:
- The app database is used as the Query API's own login, the tables' owner (`SET ROLE`), whom
  FORCE ROW LEVEL SECURITY binds when the owner isn't a superuser (CI migrates as `migrator`,
  like hermes). The tributary database is used as the stores use it (`SET LOCAL ROLE
  query_api`). Each store transaction starts with no `app.*` session settings, as a fresh
  production transaction does (`OneConnection`), so a store that forgot to set the user reads
  nothing here too.
- Everything happens in one transaction per database, rolled back at the end; each test runs in
  savepoints rolled back after it (`World.rollback`), so one test's writes never reach another.
- `World.fingerprint()` is every row of ANN's in every user-keyed table, read as the session's
  superuser (around RLS): the proof that a request changed nothing of hers.

conftest.py's `world` fixture is module-scoped and parametrized over the catalog (open,
closed): one world per module per state. It needs TEST_DATABASE_URL and APP_TEST_DATABASE_URL."""

import io
import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from query_api.alerts import AppAlerts
from query_api.api_keys import AppApiKeys, Limits
from query_api.app import create_app
from query_api.auth import mint
from query_api.authorizer import Access
from query_api.connections import AppConnections, MarketData
from query_api.datasets import AppUserData
from query_api.github import AppAccounts, GitHubConfig
from query_api.keybox import Keyring, new_master_key
from query_api.oauth import (
    ACCESS_PREFIX,
    REFRESH_PREFIX,
    AppOAuth,
    OAuthConfig,
    TokenPair,
    digest,
    new_token,
)
from query_api.password_accounts import AppPasswordAccounts
from query_api.portfolio import AppPortfolio
from query_api.providers import Bar
from query_api.sandbox import AppSandboxes, SandboxConfig
from query_api.store import Store
from query_api.thesis_checks import AppThesisChecks
from query_api.views import DEFAULTS, AppViews
from query_api.watches import AppWatches
from query_api.watchlist import AppWatchlists
from query_api.webhooks import Sent
from query_api.workspaces import AppWorkspaces

DATA_DSN = os.environ.get("TEST_DATABASE_URL")
APP_DSN = os.environ.get("APP_TEST_DATABASE_URL")

SECRET = "test-secret-" + "x" * 32
ANN = "t-acc-ann"  # owns one of everything
BEN = "t-acc-ben"  # another user: tries to reach it
SANDBOX = "demo-t-acc-sandbox"  # a "Try the demo" sandbox (is_sandbox_user)
USERS = (ANN, BEN, SANDBOX)
CO, CO2, NOBODYS = "9000000101", "9000000102", "9000000103"  # tickers TACA, TACB, TACC
TICKERS = {CO: "TACA", CO2: "TACB", NOBODYS: "TACC"}
# a company only Ann follows (watchlist, alert setting): its id in anyone else's answer is hers
ANNS_ONLY = "9000000104"
ACC = f"{CO}-26-000001"  # a 10-K of CO's
TODAY = date(2026, 11, 18)
ISSUER = "https://tributary.example"
RESOURCE = ISSUER + "/mcp"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
# no limit gets in the way of a matrix this size (the limits have their own tests)
LIMITS = Limits(per_minute=10**6, per_day=10**8)
MARK = "zq7Ann"  # in every name and text Ann writes: never in anyone else's answer

# Ann's Home: every card, in reverse, the first hidden (anyone else's is the default, null)
ANNS_HOME = {"cards": [{"id": c["id"], "hidden": i == 0}
                       for i, c in enumerate(reversed(DEFAULTS["home"]["cards"]))]}
STORES_CSV = (b"ticker,date,stores,sales\n"
              b"TACA,2026-03-31,10,1000\nTACA,2026-06-30,12,1500\nTACB,2026-06-30,7,700\n")

# Tables a user's rows live in, and the columns that are bookkeeping (counted on every request
# made with a key or an app's token, by the owner too): left out of the fingerprint.
APP_TABLES = (
    "users", "subscriptions", "watchlists", "workspaces", "workspace_items", "recent_filings",
    "item_versions", "user_views", "thesis_checks", "data_connections", "market_prices",
    "market_price_fetches", "alert_settings", "notifications", "red_lines", "webhooks",
    "webhook_deliveries", "alert_checks", "api_keys", "portfolio_holdings", "portfolio_lots",
    "portfolio_sells", "journal_entries", "watches", "screen_alerts", "oauth_grants",
    "oauth_codes", "oauth_tokens", "company_grants", "demo_agent_usage")
DATA_TABLES = ("userdata.datasets", "userdata.uploads", "userdata.rows", "authz.user_access",
               "authz.user_tiers")
BOOKKEEPING = {"api_keys": {"last_used_at"}, "oauth_grants": {"last_used_at"},
               "alert_checks": {"checked_at"}}


class OneConnection:
    """A pool of one connection inside the test's transaction. Every checkout starts like a new
    production transaction: none of the `app.*` settings a previous request set (set_config(...,
    true) lasts to the end of the outer transaction here, not of the request)."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    @contextmanager
    def connection(self) -> Iterator[Any]:
        self.conn.execute(
            "SELECT set_config('app.user_id', '', true), set_config('app.api_key_hash', '', true),"
            " set_config('app.oauth_code_hash', '', true),"
            " set_config('app.oauth_token_hash', '', true),"
            " set_config('tributary.today', '', true)")
        yield self.conn


class Unused:
    """A pool for an app that is only enumerated, never called."""

    def connection(self) -> Any:
        raise AssertionError("no database in this test")


class CatalogAuthorizer:
    """OpenFGA as authz/model.fga answers it: a private grant, or (catalog open) any catalog
    company within the user's plan tiers."""

    def __init__(self, open_: bool, grants: dict[str, dict[str, set[str]]],
                 tiers: dict[str, set[str]]) -> None:
        self.open, self.grants, self.tiers = open_, grants, tiers

    def can(self, user_id: str, tier: str, company_id: str) -> bool:
        if tier in self.grants.get(user_id, {}).get(company_id, set()):
            return True
        return self.open and tier in self.tiers.get(user_id, set()) and company_id in TICKERS

    def access(self, user_id: str) -> Access:
        public = frozenset(self.tiers.get(user_id, set())) if self.open else frozenset()
        return Access(public, {c: set(t) for c, t in self.grants.get(user_id, {}).items()})


class FakeProvider:
    """A market-data provider that takes any well-formed key (no network)."""
    id = "tiingo"
    name = "Tiingo"
    key_pattern = re.compile(r"^[A-Za-z0-9]{20,64}$")

    def validate(self, key: Any) -> None:
        return None

    def daily(self, key: Any, ticker: str, start: date, end: date | None = None) -> list[Bar]:
        return [Bar(TODAY - timedelta(days=1), Decimal("101.5"), Decimal("101.5"), 1000)]


class NoSend:
    """Webhook sends never leave the test: every one "succeeds"."""

    def send(self, url: str, body: bytes, headers: dict[str, str]) -> Sent:
        return Sent(True, 200, None)


def public_address(*_a: Any, **_k: Any) -> list:
    return [(2, 1, 6, "", ("151.101.1.1", 443))]


class NoGitHub:
    """GitHub sign-in is enumerated, never used here."""


def full_app(app_pool: Any, data_pool: Any, authorizer: Any) -> Any:
    """The Query API as main.build_app makes it, with every feature on: every route exists.
    test_classification checks every create_app parameter is passed here, so a feature added
    later can't hide its routes from the suite."""
    market = MarketData(Keyring.parse(new_master_key()), AppConnections(app_pool),
                        {"tiingo": FakeProvider()})
    return create_app(
        authorizer=authorizer, store=Store(data_pool), jwt_secret=SECRET,
        demo_users=frozenset({ANN, BEN}), today=lambda: TODAY,
        watchlists=AppWatchlists(app_pool), sandboxes=AppSandboxes(app_pool),
        sandbox_config=SandboxConfig(enabled=True), clock=lambda: datetime.now(UTC),
        workspaces=AppWorkspaces(app_pool), views=AppViews(app_pool),
        github_config=GitHubConfig("test-client", "test-secret"), github=NoGitHub(),
        github_accounts=AppAccounts(app_pool), thesis_checks=AppThesisChecks(app_pool),
        market_data=market, password_accounts=AppPasswordAccounts(app_pool),
        alerts=AppAlerts(app_pool), webhook_sender=NoSend(), webhook_resolver=public_address,
        api_keys=AppApiKeys(app_pool), api_key_limits=LIMITS, portfolio=AppPortfolio(app_pool),
        userdata=AppUserData(data_pool), watches=AppWatches(app_pool), oauth=AppOAuth(app_pool),
        oauth_config=OAuthConfig(ISSUER, RESOURCE), client_documents=NoGitHub(),
        monotonic=__import__("time").monotonic)


FULL_APP_PARAMS = ("authorizer", "store", "jwt_secret", "demo_users", "today", "watchlists",
                   "sandboxes", "sandbox_config", "clock", "workspaces", "views",
                   "github_config", "github", "github_accounts", "thesis_checks", "market_data",
                   "password_accounts", "alerts", "webhook_sender", "webhook_resolver",
                   "monotonic", "api_keys", "api_key_limits", "portfolio", "userdata",
                   "watches", "oauth", "oauth_config", "client_documents")


def enumerated_app() -> Any:
    """The full app for enumeration only (no database)."""
    return full_app(Unused(), Unused(), CatalogAuthorizer(True, {}, {}))


# --- the world --------------------------------------------------------------------------------

@dataclass
class World:
    client: Any
    app: Any
    data: Any  # tributary database connection
    appdb: Any  # app database connection
    owner: str  # the app tables' owner (the Query API's login)
    rls_binds: bool  # FORCE ROW LEVEL SECURITY binds the owner (it isn't a superuser)
    catalog_open: bool
    ids: dict[str, str] = field(default_factory=dict)  # Ann's (and Ben's) objects by name
    missing: dict[str, str] = field(default_factory=dict)  # each of Ann's names -> nobody's
    markers: set[str] = field(default_factory=set)  # never in an answer to anyone but Ann
    creds: dict[str, dict[str, str]] = field(default_factory=dict)  # name -> headers
    baseline: dict = field(default_factory=dict)  # Ann's fingerprint after setup
    keys: dict[str, str] = field(default_factory=dict)  # credential name -> raw secret

    def call(self, cred: str | None, method: str, url: str, **kw: Any) -> Any:
        headers = {**(self.creds[cred] if cred else {}), **kw.pop("headers", {})}
        return self.client.request(method, url, headers=headers, **kw)

    @contextmanager
    def rollback(self) -> Iterator[None]:
        """Savepoints on both databases, rolled back: a test's writes never outlive it."""
        with self.appdb.transaction(force_rollback=True), \
                self.data.transaction(force_rollback=True):
            yield

    def fingerprint(self) -> dict:
        """Every row of Ann's in every user-keyed table, read around RLS (the session user)."""
        out: dict[str, list] = {}
        self.appdb.execute("RESET ROLE")
        try:
            for t in APP_TABLES:
                out[t] = _rows(self.appdb, t, BOOKKEEPING.get(t, set()))
        finally:
            self.appdb.execute(f'SET ROLE "{self.owner}"')
        for t in DATA_TABLES:
            out[t] = _rows(self.data, t, set())
        return out


def _rows(conn: Any, table: str, skip: set[str]) -> list:
    cols = [r[0] for r in conn.execute(
        "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0"
        " AND NOT attisdropped ORDER BY attnum", (table,)).fetchall()]
    keep = ", ".join(f'"{c}"' for c in cols if c not in skip)
    return [tuple(r) for r in conn.execute(
        f"SELECT {keep} FROM {table} WHERE user_id = %s ORDER BY 1, 2", (ANN,)).fetchall()]


def _seed_data(conn: Any, catalog_open: bool) -> None:
    for cid, ticker in TICKERS.items():
        conn.execute("INSERT INTO silver.companies (company_id, name, current_cik,"
                     " fiscal_year_end) VALUES (%s, %s, %s, '1231')",
                     (cid, f"Access Test {ticker}", cid))
        conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                     (cid, cid))
        conn.execute("INSERT INTO silver.company_aliases (company_id, cik, alias_type, alias,"
                     " source) VALUES (%s, %s, 'ticker', %s, 'curated')", (cid, cid, ticker))
        for end, value, filed in (("2026-03-31", 100, "2026-05-01"),
                                  ("2026-06-30", 150, "2026-08-01")):
            acc = f"{cid}-26-{end[5:7]}0000"
            conn.execute(
                "INSERT INTO gold.quarterly_financials (company_id, metric, period_start,"
                " period_end, value, unit, value_source, source_tag, accession_no, filed,"
                " source_accessions, ingested_at) VALUES (%s, 'revenue', %s::date - 90, %s,"
                " %s, 'USD', 'reported', 'us-gaap:Revenues', %s, %s, ARRAY[%s], now())",
                (cid, end, end, value, acc, filed, acc))
    conn.execute("SELECT gold.refresh_screen(%s)", (list(TICKERS),))
    conn.execute("INSERT INTO silver.companies (company_id, name, current_cik) VALUES"
                 " (%s, 'Quiet Lane Corp', %s)", (ANNS_ONLY, ANNS_ONLY))
    conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                 (ANNS_ONLY, ANNS_ONLY))
    # authz-sync's tables: plans' tiers for everyone, Ann's private grant, the catalog
    conn.execute("DELETE FROM authz.public_access")
    conn.execute("DELETE FROM authz.user_access WHERE user_id = ANY(%s)", (list(USERS),))
    conn.execute("DELETE FROM authz.user_tiers WHERE user_id = ANY(%s)", (list(USERS),))
    conn.execute("INSERT INTO authz.user_tiers (user_id, tier) SELECT u, t"
                 " FROM unnest(%s::text[]) u, unnest(ARRAY['financials', 'filings']) t",
                 (list(USERS),))
    conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier) VALUES"
                 " (%s, %s, 'financials'), (%s, %s, 'filings')", (ANN, CO, ANN, CO))
    if catalog_open:
        conn.execute("INSERT INTO authz.public_access (company_id, tier) SELECT c, t"
                     " FROM unnest(%s::text[]) c, unnest(ARRAY['financials', 'filings']) t",
                     (list(TICKERS),))


def _seed_app(conn: Any, catalog_open: bool) -> None:
    conn.execute("INSERT INTO users (user_id, email) SELECT u, u || '@example.test'"
                 " FROM unnest(%s::text[]) u", (list(USERS),))
    conn.execute("INSERT INTO subscriptions (user_id, plan_id) SELECT u, 'pro'"
                 " FROM unnest(%s::text[]) u", (list(USERS),))
    conn.execute("INSERT INTO demo_sandboxes (user_id, created_at, expires_at)"
                 " VALUES (%s, now(), now() + interval '1 day')", (SANDBOX,))
    conn.execute("INSERT INTO company_grants (company_id, user_id) VALUES (%s, %s)", (CO, ANN))
    conn.execute("UPDATE public_catalogs SET public = %s WHERE catalog_id = 'sec'",
                 (catalog_open,))


def _ok(r: Any, what: str) -> dict:
    assert r.status_code in (200, 201), f"{what}: {r.status_code} {r.text}"
    return r.json()


def _keys_and_tokens(w: World) -> None:
    """Ben's read and write keys and connected app; Ann's own (to show a key or an app never
    reaches what it may not, even its owner's)."""
    now = datetime.now(UTC)
    keys = AppApiKeys(OneConnection(w.appdb))
    for user, name, scopes in ((BEN, "ben_read", ("read",)),
                               (BEN, "ben_write", ("read", "write")),
                               (ANN, "ann_read", ("read",)),
                               (ANN, "ann_write", ("read", "write"))):
        row, key = keys.create(user, f"{MARK} key" if user == ANN else "key", scopes, 90, now)
        w.creds[name] = {"Authorization": f"Bearer {key}"}
        w.keys[name] = key
        if user == ANN:
            w.ids[f"api_key_{name}"] = str(row["key_id"])
            w.markers.update({str(row["key_id"]), row["prefix"]})
    oauth = AppOAuth(OneConnection(w.appdb))
    ann_only = oauth.register("trc_" + uuid.uuid4().hex, f"{MARK} app", None, (CALLBACK,),
                              "none", None, now)
    shared = oauth.register("trc_" + uuid.uuid4().hex, "Shared app", None, (CALLBACK,), "none",
                            None, now)
    w.ids["client_ann"] = str(ann_only.client_key)
    w.ids["ben_client"] = str(shared.client_key)
    w.markers.add(str(ann_only.client_key))
    for user, client, name in ((ANN, ann_only, "ann_app"), (ANN, shared, None),
                               (BEN, shared, "ben_app")):
        code = new_token("")
        oauth.approve(user, client.client_key, RESOURCE, CALLBACK, CHALLENGE, digest(code),
                      digest(uuid.uuid4().hex), now)
        pair = TokenPair(new_token(ACCESS_PREFIX), new_token(REFRESH_PREFIX))
        issued = oauth.exchange_code(digest(code), now, lambda _p: True, pair)
        assert issued.outcome == "ok", issued
        if name:
            w.creds[name] = {"Authorization": f"Bearer {pair.access}"}
            w.keys[name] = pair.access


def _anns_things(w: World) -> None:
    """One of everything, made through the routes as Ann (what only a job or agent-api writes:
    straight into the tables, as her)."""
    a = "ann"
    ids, m = w.ids, w.markers

    def post(url: str, **kw: Any) -> dict:
        return _ok(w.call(a, "POST", url, **kw), url)

    ws = post("/v1/workspaces", json={"name": f"{MARK} folder"})
    project = post("/v1/workspaces", json={"name": f"{MARK} project", "project": {
        "companyId": CO, "notes": f"{MARK} how it makes money"}})
    ids.update(ws=ws["workspace_id"], project=project["workspace_id"])
    states = {
        "chart": {"title": f"{MARK} chart", "range": 3, "align": "calendar", "layers": [
            {"kind": "metric", "companyId": CO, "metric": "revenue", "transform": "ttm",
             "type": "line", "axis": "L"}]},
        "thesis": {"companyId": CO, "markdown": f"# {MARK} thesis\n\nStores grow."},
        "screen": {"industry": "software", "margin": 20, "sort": "-revenue_ttm"},
        "metric": {"formula": "mine.stores.sales / revenue", "view": "quarter"},
        "model": {"companyId": CO, "historyYears": 8,
                  "assumptions": [{"growth": 25, "margin": 55.5, "note": f"{MARK} ramp"}]},
        "comps": {"target": CO, "peers": [CO2], "cols": ["revenue_ttm"]},
    }
    for kind, state in states.items():
        item = post(f"/v1/workspaces/{ids['ws']}/items",
                    json={"kind": kind, "name": f"{MARK} {kind}", "state": state})
        ids[kind] = item["item_id"]
    # a version: save over the thesis once
    _ok(w.call(a, "PUT", f"/v1/workspace-items/{ids['thesis']}", json={"state": {
        "companyId": CO, "markdown": f"# {MARK} thesis v2\n\nStores grow faster."}}), "save")
    versions = _ok(w.call(a, "GET", f"/v1/workspace-items/{ids['thesis']}/versions"), "versions")
    ids["version"] = versions["versions"][0]["version_id"]
    ids["report"] = post(f"/v1/workspaces/{ids['ws']}/reports", json={
        "name": f"{MARK} report", "state": report_state(ids["ws"], ids["chart"])})["item_id"]
    ids["check"] = post(f"/v1/thesis-checks/{ids['thesis']}",
                        json={"result": check_result(ids["thesis"])})["check_id"]
    _ok(w.call(a, "POST", f"/v1/workspace-items/{ids['chart']}/open"), "open")
    _ok(w.call(a, "PUT", "/v1/recent/filings", json={"company_id": CO, "accession_no": ACC}),
        "recent filing")
    _ok(w.call(a, "PUT", f"/v1/watchlist/{CO}"), "watchlist")
    _ok(w.call(a, "PUT", f"/v1/watchlist/{ANNS_ONLY}"), "watchlist")
    m.add(ANNS_ONLY)
    _ok(w.call(a, "PUT", "/v1/views/home", json={"state": ANNS_HOME}), "view")
    # Your data
    r = w.call(a, "POST", "/v1/datasets", files={"file": ("x.csv", io.BytesIO(STORES_CSV),
                                                          "text/csv")},
               data={"name": "stores", "title": f"{MARK} store counts"})
    ids["dataset"] = _ok(r, "dataset")["dataset"]["dataset_id"]
    # alerts
    _ok(w.call(a, "PUT", f"/v1/alerts/settings/{CO}", json={"forms": ["10-K", "8-K"]}),
        "setting")
    _ok(w.call(a, "PUT", f"/v1/alerts/settings/{ANNS_ONLY}", json={"forms": ["10-Q"]}),
        "setting")
    ids["red_line"] = post("/v1/alerts/red-lines", json={
        "company_id": CO, "label": f"{MARK} red line", "formula": "revenue", "op": "<",
        "threshold": "50"})["red_line"]["rule_id"]
    hook = post("/v1/alerts/webhooks", json={"url": f"https://hooks.example.com/{MARK}"})
    ids["webhook"] = str(hook["webhook"]["id"])
    m.add(hook["secret"])
    _ok(w.call(a, "PUT", f"/v1/watches/screen/{ids['screen']}", json={"entering": True}),
        "watch")
    # portfolio and journal
    holding = post("/v1/portfolio/holdings", json={
        "company_id": CO, "lot": {"shares": "10", "cost_total": "1000",
                                  "bought_on": "2026-01-05", "account_label": f"{MARK} ira"}})
    ids["holding"] = holding["holding_id"]
    ids["sell"] = post(f"/v1/portfolio/holdings/{ids['holding']}/sells", json={
        "shares": "1", "price": "120", "sold_on": "2026-02-02"})["sell"]["id"]
    _ok(w.call(a, "PUT", f"/v1/portfolio/holdings/{ids['holding']}/price",
               json={"price": "110", "on": "2026-11-17"}), "price")
    _ok(w.call(a, "PUT", f"/v1/portfolio/holdings/{ids['holding']}/link",
               json={"thesis_item_id": ids["thesis"]}), "link")
    ids["entry"] = post("/v1/portfolio/journal", json={
        "company_id": CO, "kind": "bought", "why": f"{MARK} because stores grow",
        "red_line_ids": [ids["red_line"]]})["entry"]["id"]
    # Connect your data: a key and a day of prices (fetched with the fake provider)
    _ok(w.call(a, "PUT", "/v1/connections/tiingo", json={"api_key": "abc123" * 5 + "Q7zA"}),
        "connection")
    m.add("\u2026Q7zA")  # the key's hint ("…" and its last four)
    _ok(w.call(a, "GET", f"/v1/companies/{CO}/prices"), "prices")
    # what the alerts job writes, as Ann
    ids["lot"] = post(f"/v1/portfolio/holdings/{ids['holding']}/lots", json={
        "shares": "2", "cost_total": "210", "bought_on": "2026-03-02"})["lot"]["id"]
    w.appdb.execute("SELECT set_config('app.user_id', %s, true)", (ANN,))
    ids["notification"] = str(w.appdb.execute(
        "INSERT INTO notifications (user_id, company_id, accession_no, form, filed, sources,"
        " status, detail) VALUES (%s, %s, %s, '10-K', '2026-11-17', ARRAY['watchlist'],"
        " 'ready', jsonb_build_object('note', %s::text)) RETURNING notification_id",
        (ANN, CO, ACC, MARK)).fetchone()[0])
    watch_id = w.appdb.execute("SELECT watch_id FROM watches WHERE user_id = %s",
                               (ANN,)).fetchone()[0]
    ids["screen_alert"] = str(w.appdb.execute(
        "INSERT INTO screen_alerts (user_id, watch_id, item_id, name, detail)"
        " VALUES (%s, %s, %s, %s, '{}') RETURNING alert_id",
        (ANN, watch_id, ids["screen"], f"{MARK} screen alert")).fetchone()[0])
    ids["delivery"] = str(w.appdb.execute(
        "INSERT INTO webhook_deliveries (user_id, webhook_id, notification_id)"
        " VALUES (%s, %s, %s) RETURNING delivery_id",
        (ANN, ids["webhook"], ids["notification"])).fetchone()[0])
    w.appdb.execute("INSERT INTO alert_checks (user_id, checked_at) VALUES (%s, now())", (ANN,))
    w.appdb.execute("SELECT set_config('app.user_id', '', true)")
    # her key and her app used once each (usage rows)
    for cred in ("ann_read", "ann_app"):
        _ok(w.call(cred, "GET", "/v1/me"), cred)
    m.update(v for k, v in ids.items() if not k.startswith("ben_"))


def _bens_things(w: World) -> None:
    """Ben's own folder, chart, report, holding and journal entry: the other half of a request
    that names one of his things and one of Ann's."""
    b, ids = "ben", w.ids
    ids["ben_ws"] = _ok(w.call(b, "POST", "/v1/workspaces", json={"name": "Ben's"}),
                        "ws")["workspace_id"]
    ids["ben_chart"] = _ok(w.call(b, "POST", f"/v1/workspaces/{ids['ben_ws']}/items", json={
        "kind": "chart", "name": "Ben's chart", "state": {"title": "x", "layers": [
            {"kind": "metric", "companyId": CO, "metric": "revenue"}]}}), "item")["item_id"]
    ids["ben_report"] = _ok(w.call(b, "POST", f"/v1/workspaces/{ids['ben_ws']}/reports", json={
        "name": "Ben's report", "state": report_state(ids["ben_ws"], ids["ben_chart"])}),
        "report")["item_id"]
    holding = _ok(w.call(b, "POST", "/v1/portfolio/holdings", json={
        "company_id": CO2, "lot": {"shares": "1", "cost_total": "100"}}), "holding")
    ids["ben_holding"] = holding["holding_id"]
    ids["ben_entry"] = _ok(w.call(b, "POST", "/v1/portfolio/journal", json={
        "company_id": CO, "kind": "bought", "why": "Ben's reasons"}), "entry")["entry"]["id"]


def report_state(workspace_id: str, item_id: str) -> dict:
    """A report as agent-api's report writer saves it (query_api.reports)."""
    return {
        "workspace_id": workspace_id,
        "markdown": f"# {MARK} report\n\n## Summary\n\nRevenue was $150 (10-K filed 2026-08-01,"
                    f" {ACC}).\n\n[[embed:item-1]]\n\nAs of 2026-11-17.",
        "items": [{"ref": "item-1", "item_id": item_id, "kind": "chart", "name": "Rev",
                   "state": {"title": "x", "layers": [{"kind": "metric", "companyId": CO,
                                                       "metric": "revenue"}]}}],
        "citations": [{"accession_no": ACC, "form": "10-K", "filed": "2026-08-01",
                       "company_id": CO, "url": "https://www.sec.gov/Archives/edgar/data/1/x.htm",
                       "sections": []}],
        "verification": {"ok": True, "figures": [{"text": "$150", "status": "verified"}],
                         "unverified_accessions": [], "used_data": True, "freshness": True},
        "as_of": "2026-11-17T08:00:00+00:00", "written_at": "2026-11-17T09:00:00+00:00",
        "model": "claude-haiku-5-5", "questions": 3, "stop": "end_turn",
    }


def check_result(item_id: str) -> dict:
    """A thesis check as agent-api's thesis_check.assemble writes it."""
    cite = {"accession_no": ACC, "form": "10-K", "filed": "2026-08-01", "company_id": CO,
            "url": "https://www.sec.gov/Archives/edgar/data/1/x.htm", "section_key": "1A",
            "figure": "$150", "figure_status": "verified", "as_of": "2026-11-17T08:00:00+00:00",
            "verified": True}
    return {
        "version": 1,
        "thesis": {"item_id": item_id, "title": f"{MARK} thesis", "company_id": CO,
                   "text_hash": "a" * 64, "characters": 40, "characters_checked": 40,
                   "truncated": False},
        "summary": f"{MARK} mostly supported.",
        "claims": [{"quote": "Stores grow.", "quote_found": True, "status": "supported",
                    "explanation": "The 10-K says so.", "citations": [cite],
                    "figures": [{"text": "$150", "status": "verified"}]}],
        "assumptions": [], "not_mentioned": [], "notes": [],
        "citations": [{"accession_no": ACC, "form": "10-K", "filed": "2026-08-01",
                       "company_id": CO, "sections": ["1A"]}],
        "verification": {"figures": [{"text": "$150", "status": "verified"}],
                         "unverified_accessions": [], "used_data": True, "freshness": True},
        "as_of": "2026-11-17T08:00:00+00:00", "checked_at": "2026-11-17T09:00:00+00:00",
        "model": "claude-haiku-5-5", "questions": 4, "stop": "end_turn", "rounds": 5,
        "usage": {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0},
        "cost_usd": 0.0012,
    }


@contextmanager
def make_world(catalog_open: bool) -> Iterator[World]:
    import psycopg
    from fastapi.testclient import TestClient

    assert DATA_DSN and APP_DSN
    with psycopg.connect(DATA_DSN) as data, data.transaction(force_rollback=True), \
            psycopg.connect(APP_DSN) as appdb, appdb.transaction(force_rollback=True):
        owner = appdb.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                              " AND tablename = 'workspace_items'").fetchone()[0]
        exempt = appdb.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles"
                               " WHERE rolname = %s", (owner,)).fetchone()[0]
        _seed_data(data, catalog_open)
        appdb.execute(f'SET ROLE "{owner}"')
        _seed_app(appdb, catalog_open)
        authorizer = CatalogAuthorizer(
            catalog_open, grants={ANN: {CO: {"financials", "filings"}}},
            tiers={u: {"financials", "filings"} for u in USERS})
        app = full_app(OneConnection(appdb), OneConnection(data), authorizer)
        client = TestClient(app, raise_server_exceptions=False)
        w = World(client, app, data, appdb, owner, not exempt, catalog_open)
        w.creds.update({u: {"Authorization": f"Bearer {mint(t, SECRET)}"}
                        for u, t in (("ann", ANN), ("ben", BEN), ("sandbox", SANDBOX))})
        _keys_and_tokens(w)
        _anns_things(w)
        _bens_things(w)
        w.markers.add(MARK)
        w.ids.update(co=CO, dataset_name="stores")
        w.missing = {k: str(uuid.uuid4()) for k in w.ids if not k.startswith("ben_")}
        # a name of the same length: an error's position and length don't differ by it
        w.missing.update(co=NOBODYS, dataset_name="nosuch")
        w.baseline = w.fingerprint()
        yield w


# --- naming things in a request -----------------------------------------------------------------

# Ben's things stand in for Ann's own in the owner's control (a request naming his and hers)
BEN_TWIN = {"ben_ws": "ws", "ben_chart": "chart", "ben_report": "report",
            "ben_holding": "holding", "ben_entry": "entry", "ben_client": "client_ann"}


def refs(world: World, mode: str) -> dict[str, str]:
    """"@name" -> value. mode "ann": Ann's things; "missing": nobody's; "owner": Ann's, with
    each of Ben's things in a request replaced by her twin of it."""
    out = {}
    for k, v in world.ids.items():
        if k.startswith("ben_"):
            out[k] = world.ids[BEN_TWIN[k]] if mode == "owner" else v
        else:
            out[k] = world.missing[k] if mode == "missing" else v
    return out


def fill(value: Any, names: dict[str, str]) -> Any:
    """`value` with every "@name" replaced; {"$report": [ws, item]} and {"$check": item} are a
    report's and a thesis check's whole state, as agent-api writes them."""
    if isinstance(value, str):
        for k in sorted(names, key=len, reverse=True):
            value = value.replace("@" + k, names[k])
        return value
    if isinstance(value, dict):
        if set(value) == {"$report"}:
            ws, item = (fill(x, names) for x in value["$report"])
            return report_state(ws, item)
        if set(value) == {"$check"}:
            return check_result(fill(value["$check"], names))
        return {k: fill(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, names) for v in value]
    return value


def normalized(text: str, *names: dict[str, str]) -> str:
    """`text` with each named value written as its name: an answer about Ann's thing and one
    about nobody's compare equal when they say the same."""
    for ref in names:
        for k in sorted(ref, key=lambda k: len(ref[k]), reverse=True):
            text = text.replace(ref[k], f"<{k}>")
    return text
