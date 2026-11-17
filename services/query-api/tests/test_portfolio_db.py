"""Portfolio and decision journal against the real app database (migration 021): forced row-level
security on every table, the routes end to end with two users and a demo sandbox (none reads,
changes or corrects another's holdings, lots, sells, prices, links or entries), an API key
refused, the Fidelity import on the synthetic fixture (account numbers never stored or echoed),
the maths through the routes, the 24-hour journal rule (enforced by the database), reviews due on
the bell, and open holdings joining the filing alerts' follows. Skipped unless
APP_TEST_DATABASE_URL is set; every test is rolled back."""

import json
import os
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from query_api.alerts import AppAlerts
from query_api.app import create_app
from query_api.connections import AppConnections, MarketData
from query_api.keybox import Keyring, new_master_key
from query_api.portfolio import AppPortfolio, due_reviews
from query_api.providers import Bar

from .conftest import SECRET
from .test_api import FakeAuthorizer, FakeStore

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

A, B, SANDBOX = "t-pf-alice", "t-pf-bob", "demo-0123456789abcdef"
NVDA, AAPL, MSFT, TSLA, BRK, VOO_FUND = ("0001045810", "0000320193", "0000789019",
                                        "0001318605", "0001067983", "0000036405")
TICKERS = {"NVDA": NVDA, "AAPL": AAPL, "MSFT": MSFT, "TSLA": TSLA, "BRK-B": BRK,
           "VOO": VOO_FUND}  # VOO resolves to a fund CIK nobody reads financials for
NAMES = {NVDA: "NVIDIA", AAPL: "Apple", MSFT: "Microsoft", TSLA: "Tesla", BRK: "Berkshire"}
TABLES = ["portfolio_holdings", "portfolio_lots", "portfolio_sells", "journal_entries"]
FIXTURE = Path(__file__).parent / "fixtures" / "portfolio" / "fidelity_positions_synthetic.csv"
ACCOUNT_NUMBERS = ("Z09876543", "Y55511122", "X31415926", "31415926")
TODAY = date(2026, 11, 16)
Q3 = "0001045810-26-000101"


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


class PortfolioStore(FakeStore):
    """Public identity, the filing index and expected filings, as the Store reads them."""

    def __init__(self, filings=None):
        super().__init__()
        self.filings = filings or []

    def profiles(self, user_id, ids):
        return {c: {"name": NAMES[c], "sic": None, "industry": None, "exchanges": None}
                for c in ids if c in NAMES}

    def tickers(self, user_id, ids):
        return {c: [t] for t, c in TICKERS.items() if c in ids}

    def resolve_tickers(self, user_id, tickers):
        self.calls.append(("resolve_tickers", tuple(tickers)))
        return {t: TICKERS[t] for t in tickers if t in TICKERS}

    def expected_filings(self, user_id, ids, as_of, periods):
        return [{"company_id": c, "form": "10-Q", "period_end": date(2027, 1, 25),
                 "expected_date": date(2027, 2, 25)} for c in ids if c == NVDA]

    def alert_filings(self, user_id, ids, forms, since):
        self.calls.append(("alert_filings", tuple(ids), tuple(forms), since))
        return [f for f in self.filings if f["company_id"] in ids and f["form"] in forms
                and f["filing_date"] >= since]


READ = {"financials", "filings"}
GRANTS = {u: {c: READ for c in NAMES} for u in (A, B, SANDBOX)}


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("INSERT INTO users (user_id, email) VALUES (%s, 'a@example.com'),"
                  " (%s, 'b@example.com'), (%s, 'd@example.com')", (A, B, SANDBOX))
        yield c


@pytest.fixture
def pf(conn):
    return AppPortfolio(OneConnection(conn))


def client(conn, store=None, **kw):
    return TestClient(create_app(
        FakeAuthorizer(GRANTS), store or PortfolioStore(), SECRET,
        portfolio=AppPortfolio(OneConnection(conn)), alerts=AppAlerts(OneConnection(conn)),
        today=lambda: TODAY, clock=lambda: datetime.now(UTC), **kw))


def buy(c, token, user, company_id=NVDA, shares="2", cost="300", day="2026-11-02", **lot):
    r = c.post("/v1/portfolio/holdings", headers=token(user), json={
        "company_id": company_id,
        "lot": {"shares": shares, "cost_total": cost, "bought_on": day, **lot}})
    assert r.status_code == 200, r.text
    return r.json()["holding_id"]


def holdings(c, token, user):
    r = c.get("/v1/portfolio", headers=token(user))
    assert r.status_code == 200, r.text
    return r.json()


# --- the tables -------------------------------------------------------------------------------

@pytest.mark.parametrize("table", TABLES)
def test_every_portfolio_table_forces_rls_and_isnt_streamed(conn, table):
    rls, forced, identity = conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity, relreplident FROM pg_class"
        " WHERE oid = %s::regclass", (table,)).fetchone()
    assert rls and forced and identity == "d"
    connector = Path(__file__).parents[3] / "streaming" / "debezium" / "app-connector.json"
    assert table not in json.loads(connector.read_text())["table.include.list"]


def test_rls_binds_the_owner(conn, pf, token):
    c = client(conn)
    hid = buy(c, token, A)
    assert c.post(f"/v1/portfolio/holdings/{hid}/sells", headers=token(A),
                  json={"shares": "1", "price": "160", "sold_on": "2026-11-10"}).status_code == 200
    assert c.post("/v1/portfolio/journal", headers=token(A),
                  json={"company_id": NVDA, "kind": "bought", "why": "AI capex"}).status_code == 200
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                         " AND tablename = 'portfolio_holdings'").fetchone()[0]
    if conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                    (owner,)).fetchone()[0]:
        pytest.skip(f"table owner {owner} bypasses row-level security")
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
    conn.execute("SELECT set_config('app.user_id', %s, true)", (B,))
    for table in TABLES:
        assert conn.execute(sql.SQL("SELECT count(*) FROM {}").format(
            sql.Identifier(table))).fetchone()[0] == 0, table
    assert conn.execute("UPDATE portfolio_holdings SET price = 1, price_on = now(),"
                        " price_source = 'typed'").rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("INSERT INTO portfolio_lots (holding_id, user_id, shares, cost_total, source)"
                     " VALUES (%s, %s, 1, 1, 'typed')", (hid, A))
    # bob's own row can't point at alice's holding either
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute("INSERT INTO portfolio_lots (holding_id, user_id, shares, cost_total, source)"
                     " VALUES (%s, %s, 1, 1, 'typed')", (hid, B))
    conn.execute("SELECT set_config('app.user_id', %s, true)", (A,))
    assert conn.execute("SELECT count(*) FROM portfolio_lots").fetchone()[0] == 1


def test_checks_hold_the_shapes(conn):
    def bad(statement, *args):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(statement, args)

    bad("INSERT INTO portfolio_holdings (user_id) VALUES (%s)", A)  # neither company nor symbol
    bad("INSERT INTO portfolio_holdings (user_id, symbol, price, price_on, price_source)"
        " VALUES (%s, 'VOO', 1, now(), 'typed')", A)                # a not-covered price
    bad("INSERT INTO portfolio_holdings (user_id, company_id, price) VALUES (%s, %s, 1)", A, NVDA)
    hid = conn.execute("INSERT INTO portfolio_holdings (user_id, company_id) VALUES (%s, %s)"
                       " RETURNING holding_id", (A, NVDA)).fetchone()[0]
    for label in ("Z09876543", "acct 123-456789", "12 34 56"):
        bad("INSERT INTO portfolio_lots (holding_id, user_id, shares, cost_total, source,"
            " account_label) VALUES (%s, %s, 1, 1, 'typed', %s)", hid, A, label)
    bad("INSERT INTO portfolio_lots (holding_id, user_id, shares, cost_total, source)"
        " VALUES (%s, %s, 0, 1, 'typed')", hid, A)
    bad("INSERT INTO journal_entries (user_id, company_id, kind, decided_on, why)"
        " VALUES (%s, %s, 'yolo', now(), 'x')", A, NVDA)
    bad("INSERT INTO journal_entries (user_id, company_id, kind, decided_on, why, confidence)"
        " VALUES (%s, %s, 'note', now(), 'x', 6)", A, NVDA)
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        conn.execute("INSERT INTO portfolio_holdings (user_id, company_id) VALUES (%s, %s)",
                     (A, NVDA))


# --- two users, a sandbox and a key -----------------------------------------------------------

def test_one_user_cant_read_change_or_correct_anothers_portfolio(conn, token):
    c = client(conn)
    hid = buy(c, token, A, account_label="Brokerage")
    lot_id = holdings(c, token, A)["holdings"][0]["lots"][0]["id"]
    sale = c.post(f"/v1/portfolio/holdings/{hid}/sells", headers=token(A),
                  json={"shares": "0.5", "price": "180", "sold_on": "2026-11-10"}).json()
    entry = c.post("/v1/portfolio/journal", headers=token(A), json={
        "company_id": NVDA, "kind": "bought", "why": "Data-centre demand",
        "sell_rules": "Gross margin under 60%", "confidence": 4}).json()["entry"]

    for user in (B, SANDBOX):
        mine = holdings(c, token, user)
        assert mine["holdings"] == [] and mine["totals"]["open"] == 0
        assert c.get(f"/v1/portfolio/position/{NVDA}", headers=token(user)).json() == {
            "position": None}
        assert c.get("/v1/portfolio/journal", headers=token(user)).json()["entries"] == []
        for method, path, body in [
            ("POST", f"/v1/portfolio/holdings/{hid}/lots", {"shares": "1", "cost_total": "1"}),
            ("POST", f"/v1/portfolio/holdings/{hid}/sells",
             {"shares": "1", "price": "1", "sold_on": "2026-11-10"}),
            ("PUT", f"/v1/portfolio/holdings/{hid}/price", {"price": "1", "on": "2026-11-10"}),
            ("DELETE", f"/v1/portfolio/holdings/{hid}/price", None),
            ("PUT", f"/v1/portfolio/holdings/{hid}/link", {}),
            ("DELETE", f"/v1/portfolio/lots/{lot_id}", None),
            ("DELETE", f"/v1/portfolio/sells/{sale['sell']['id']}", None),
            ("PATCH", f"/v1/portfolio/journal/{entry['id']}", {"why": "mine now"}),
            ("DELETE", f"/v1/portfolio/journal/{entry['id']}", None),
            ("DELETE", f"/v1/portfolio/holdings/{hid}", None),
        ]:
            r = c.request(method, path, headers=token(user), json=body)
            assert r.status_code == 404, (user, method, path, r.text)
        # a correction can't hang off another user's entry
        r = c.post("/v1/portfolio/journal", headers=token(user), json={
            "company_id": NVDA, "kind": "note", "why": "x", "corrects": entry["id"]})
        assert r.status_code == 404
        # buying the same company makes the user's own holding, not a lot on alice's
        own = buy(c, token, user, shares="1", cost="150")
        assert own != hid

    a = holdings(c, token, A)["holdings"][0]
    assert (a["shares"], a["lots"][0]["account_label"]) == ("1.5", "Brokerage")
    assert c.get("/v1/portfolio/journal", headers=token(A),
                 params={"company_id": NVDA}).json()["entries"][0]["why"] == "Data-centre demand"


def test_a_sandbox_keeps_its_own_throwaway_rows_and_they_go_with_it(conn, token):
    c = client(conn)
    buy(c, token, SANDBOX)
    old = conn.execute("INSERT INTO journal_entries (user_id, company_id, kind, decided_on, why,"
                       " created_at) VALUES (%s, %s, 'bought', '2026-10-01', 'kept',"
                       " now() - interval '30 days') RETURNING entry_id", (SANDBOX,
                                                                           NVDA)).fetchone()[0]
    assert old
    # demo-cleanup deletes the user: a cascade is no edit, even of an entry kept as written
    conn.execute("DELETE FROM users WHERE user_id = %s", (SANDBOX,))
    for table in TABLES:
        assert conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE user_id = %s").format(
            sql.Identifier(table)), (SANDBOX,)).fetchone()[0] == 0


def test_an_api_key_reaches_no_portfolio_route(conn, token):
    from .test_api_keys import FakeKeys, bearer, make_key
    c = client(conn, api_keys=FakeKeys(users=(A, B)))
    buy(c, token, A)
    key = make_key(c, token, user=A, scopes=("read", "write"))["key"]
    for method, path in [("GET", "/v1/portfolio"), ("GET", f"/v1/portfolio/position/{NVDA}"),
                         ("GET", "/v1/portfolio/journal"), ("POST", "/v1/portfolio/holdings"),
                         ("POST", "/v1/portfolio/import/preview")]:
        r = c.request(method, path, headers=bearer(key))
        assert r.status_code == 403, (method, path)
        assert "Brokerage" not in r.text and NVDA not in r.text


def test_without_the_app_database_the_routes_answer_503(token):
    c = TestClient(create_app(FakeAuthorizer(GRANTS), PortfolioStore(), SECRET))
    assert c.get("/v1/portfolio", headers=token(A)).status_code == 503
    assert c.get("/v1/portfolio/journal", headers=token(A)).status_code == 503


# --- the maths through the routes -------------------------------------------------------------

def test_cost_value_and_gains_come_from_the_users_own_rows_and_price(conn, token):
    c = client(conn)
    hid = buy(c, token, A, shares="10", cost="1000", day="2026-01-05")
    assert c.post(f"/v1/portfolio/holdings/{hid}/lots", headers=token(A), json={
        "shares": "10", "cost_per_share": "200", "bought_on": "2026-03-02"}).status_code == 200
    assert c.post(f"/v1/portfolio/holdings/{hid}/sells", headers=token(A), json={
        "shares": "5", "price": "300", "sold_on": "2026-06-01"}).status_code == 200
    h = holdings(c, token, A)
    (n,) = h["holdings"]
    assert (n["shares"], n["cost_basis"], n["average_cost"], n["realised"]) == (
        "15", "2250.00", "150", "750.00")
    assert n["price"] is None and n["value"] is None and n["weight"] == "1.0000"
    assert h["weight_basis"] == "cost" and h["totals"]["value"] is None
    assert n["next_report"] == {"form": "10-Q", "period_end": "2027-01-25",
                                "expected_date": "2027-02-25"}

    assert c.put(f"/v1/portfolio/holdings/{hid}/price", headers=token(A),
                 json={"price": "160", "on": "2026-11-13"}).status_code == 200
    h = holdings(c, token, A)
    (n,) = h["holdings"]
    assert n["price"] == {"value": "160", "on": "2026-11-13", "source": "typed",
                          "provider": None, "stale": False}
    assert (n["value"], n["unrealised"], n["unrealised_pct"]) == ("2400.00", "150.00", "0.0667")
    assert h["weight_basis"] == "value" and h["totals"]["value"] == "2400.00"

    c.put(f"/v1/portfolio/holdings/{hid}/price", headers=token(A),
          json={"price": "160", "on": "2026-11-01"})
    assert holdings(c, token, A)["holdings"][0]["price"]["stale"] is True
    assert c.put(f"/v1/portfolio/holdings/{hid}/price", headers=token(A),
                 json={"price": "160", "on": "2026-12-25"}).status_code == 422


def test_a_sale_of_more_than_was_held_is_refused_and_nothing_is_saved(conn, token):
    c = client(conn)
    hid = buy(c, token, A, shares="2", day="2026-11-02")
    r = c.post(f"/v1/portfolio/holdings/{hid}/sells", headers=token(A),
               json={"shares": "3", "price": "1", "sold_on": "2026-11-10"})
    assert r.status_code == 409 and "more shares than you held" in r.json()["detail"]
    r = c.post(f"/v1/portfolio/holdings/{hid}/sells", headers=token(A),
               json={"shares": "2", "price": "160", "sold_on": "2026-11-10"})
    assert r.status_code == 200
    lot_id = holdings(c, token, A)["holdings"][0]["lots"][0]["id"]
    assert c.delete(f"/v1/portfolio/lots/{lot_id}", headers=token(A)).status_code == 409
    h = holdings(c, token, A)["holdings"][0]
    assert h["open"] is False and h["realised"] == "20.00" and len(h["lots"]) == 1
    # a closed holding isn't followed by the alerts any more
    assert AppAlerts(OneConnection(conn)).follows(A) == []


def test_a_typed_account_label_never_holds_an_account_number(conn, token):
    c = client(conn)
    r = c.post("/v1/portfolio/holdings", headers=token(A), json={
        "company_id": NVDA, "lot": {"shares": "1", "cost_total": "1",
                                    "account_label": "Z09876543"}})
    assert r.status_code == 422 and "Z09876543" not in r.text


def test_a_ticker_resolves_to_its_company_and_a_fund_is_not_covered(conn, token):
    c = client(conn)
    r = c.post("/v1/portfolio/holdings", headers=token(A), json={
        "symbol": "brk.b", "lot": {"shares": "1", "cost_total": "500"}})
    assert r.json()["company_id"] == BRK and r.json()["covered"]
    r = c.post("/v1/portfolio/holdings", headers=token(A), json={
        "symbol": "VOO", "name": "Vanguard S&P 500 ETF",
        "lot": {"shares": "1", "cost_total": "500"}})
    assert r.json()["company_id"] is None and not r.json()["covered"]
    voo = next(h for h in holdings(c, token, A)["holdings"] if h["symbol"] == "VOO")
    assert voo["name"] == "Vanguard S&P 500 ETF" and voo["price"] is None
    r = c.put(f"/v1/portfolio/holdings/{voo['id']}/price", headers=token(A),
              json={"price": "560", "on": "2026-11-13"})
    assert r.status_code == 409


# --- the Fidelity import ----------------------------------------------------------------------

def test_the_import_previews_matches_and_saves_without_account_numbers(conn, token):
    c = client(conn)
    raw = FIXTURE.read_bytes()
    r = c.post("/v1/portfolio/import/preview", headers={**token(A), "Content-Type": "text/csv"},
               content=raw)
    assert r.status_code == 200, r.text
    for n in ACCOUNT_NUMBERS:
        assert n not in r.text
    preview = r.json()
    assert preview["downloaded"] == "2026-11-16"
    rows = {(p["symbol"], p["account_label"]): p for p in preview["positions"]}
    assert rows[("NVDA", "Individual")]["company_id"] == NVDA
    assert rows[("BRK.B", "Individual")]["company_id"] == BRK
    assert not rows[("VOO", "Individual")]["covered"]       # a fund: not a company here
    assert not rows[("FXAIX", "ROTH IRA")]["covered"]
    assert rows[("MSFT", "ROTH IRA")]["cost_total"] is None  # "--": typed in the preview
    assert [s["reason"] for s in preview["skipped"]] == ["cash", "pending", "option", "cash"]

    chosen = [{k: p[k] for k in ("symbol", "description", "shares", "cost_total", "last_price",
                                 "account_label")}
              for p in preview["positions"] if p["cost_total"] is not None]
    r = c.post("/v1/portfolio/import", headers=token(A),
               json={"positions": chosen, "price_on": preview["downloaded"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"holdings_added": 6, "lots_added": 7, "prices_saved": 5}
    stored = " ".join(str(v) for row in conn.execute(
        "SELECT h.*, l.* FROM portfolio_holdings h JOIN portfolio_lots l USING (holding_id)"
        " WHERE h.user_id = %s", (A,)).fetchall() for v in row)
    for n in ACCOUNT_NUMBERS:
        assert n not in stored
    h = {x["symbol"]: x for x in holdings(c, token, A)["holdings"]}
    assert h["NVDA"]["shares"] == "1.75" and len(h["NVDA"]["lots"]) == 2
    assert h["NVDA"]["price"] == {"value": "186.26", "on": "2026-11-16", "source": "fidelity",
                                  "provider": None, "stale": False}
    assert h["VOO"]["covered"] is False and h["VOO"]["price"] is None
    assert h["TSLA"]["lots"][0]["account_label"] is None
    assert h["NVDA"]["lots"][0]["bought_on"] is None  # the export has no buy dates

    # previewed again: what's already held says so
    again = c.post("/v1/portfolio/import/preview", headers=token(A), content=raw).json()
    nv = next(p for p in again["positions"] if p["symbol"] == "NVDA")
    assert nv["held"]["shares"] == "1.75"


def test_an_import_preview_that_isnt_an_export_is_refused_without_echo(conn, token):
    c = client(conn)
    r = c.post("/v1/portfolio/import/preview", headers=token(A),
               content=b"Account Number,Z09876543\nsecret stuff")
    assert r.status_code == 422 and r.json()["code"] == "not_an_export"
    assert "Z09876543" not in r.text and "secret" not in r.text
    big = b"Symbol,Quantity\n" + b"A,1\n" * 200_000
    assert c.post("/v1/portfolio/import/preview", headers=token(A),
                  content=big).status_code == 413


# --- the journal ------------------------------------------------------------------------------

def test_an_entry_can_be_fixed_for_24_hours_then_is_kept_and_corrected(conn, token):
    c = client(conn)
    conn.execute("INSERT INTO red_lines (user_id, company_id, label, formula, view, op,"
                 " threshold) VALUES (%s, %s, 'Margin', 'gross_margin', 'ttm', '<', 0.6)",
                 (A, NVDA))
    rule = conn.execute("SELECT rule_id::text FROM red_lines WHERE user_id = %s",
                        (A,)).fetchone()[0]
    r = c.post("/v1/portfolio/journal", headers=token(A), json={
        "company_id": NVDA, "kind": "bought", "why": "first", "red_line_ids": [rule],
        "sell_rules": "Margins crack", "confidence": 3})
    assert r.status_code == 200, r.text
    e = r.json()["entry"]
    assert not e["kept"] and e["red_lines"][0]["formula"] == "gross_margin"
    assert e["red_lines"][0]["display"] == "percent"
    # the red line changing later doesn't rewrite what the entry said
    conn.execute("UPDATE red_lines SET threshold = 0.5 WHERE user_id = %s", (A,))
    r = c.patch(f"/v1/portfolio/journal/{e['id']}", headers=token(A),
                json={"why": "first, fixed", "confidence": None})
    assert r.status_code == 200
    fixed = r.json()["entry"]
    assert (fixed["why"], fixed["confidence"], fixed["edited_at"] is not None) == (
        "first, fixed", None, True)
    assert fixed["red_lines"][0]["threshold"] == "0.6"

    old = conn.execute("INSERT INTO journal_entries (user_id, company_id, kind, decided_on, why,"
                       " created_at) VALUES (%s, %s, 'bought', '2026-10-01', 'as written',"
                       " now() - interval '25 hours') RETURNING entry_id::text",
                       (A, NVDA)).fetchone()[0]
    r = c.patch(f"/v1/portfolio/journal/{old}", headers=token(A), json={"why": "rewritten"})
    assert r.status_code == 409 and "correction" in r.json()["detail"]
    assert c.delete(f"/v1/portfolio/journal/{old}", headers=token(A)).status_code == 409
    with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE journal_entries SET why = 'quietly' WHERE entry_id = %s", (old,))
    with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
        conn.execute("DELETE FROM journal_entries WHERE entry_id = %s", (old,))
    listed = c.get("/v1/portfolio/journal", headers=token(A),
                   params={"company_id": NVDA}).json()["entries"]
    assert next(x for x in listed if x["id"] == old)["kept"] is True

    r = c.post("/v1/portfolio/journal", headers=token(A), json={
        "company_id": NVDA, "kind": "bought", "why": "as written, corrected", "corrects": old})
    assert r.status_code == 200 and r.json()["entry"]["corrects"] == old
    # a correction is about the same company
    r = c.post("/v1/portfolio/journal", headers=token(A), json={
        "company_id": AAPL, "kind": "note", "why": "x", "corrects": old})
    assert r.status_code == 404
    # within the day an entry can be deleted
    assert c.delete(f"/v1/portfolio/journal/{e['id']}", headers=token(A)).status_code == 200


def test_entries_are_checked(conn, token):
    c = client(conn)
    for body in [{"company_id": NVDA, "kind": "note", "why": "   "},
                 {"company_id": NVDA, "symbol": "VOO", "kind": "note", "why": "x"},
                 {"symbol": "VOO", "kind": "note", "why": "x",
                  "red_line_ids": ["0b6f8a52-63f4-4d6e-9d0e-2f4d3b8a1c11"]},
                 {"company_id": NVDA, "kind": "note", "why": "bell\x07"},
                 {"company_id": NVDA, "kind": "note", "why": "x", "decided_on": "2027-01-01"},
                 {"company_id": NVDA, "kind": "note", "why": "x",
                  "red_line_ids": ["0b6f8a52-63f4-4d6e-9d0e-2f4d3b8a1c11"]}]:
        r = c.post("/v1/portfolio/journal", headers=token(A), json=body)
        assert r.status_code == 422, (body, r.text)


# --- reviews and alerts -----------------------------------------------------------------------

def test_a_new_report_makes_a_review_due_until_a_review_entry(conn, token):
    store = PortfolioStore()
    c = client(conn, store=store)
    buy(c, token, A)
    later = datetime.now(UTC) + timedelta(minutes=5)
    store.filings = [{"company_id": NVDA, "accession_no": Q3, "form": "10-Q", "cik": NVDA,
                      "filing_date": later.date(), "accepted_at": later,
                      "period_end": date(2026, 10, 26)}]
    h = holdings(c, token, A)
    assert h["reviews_due"] == 1
    assert h["holdings"][0]["review_due"]["accession_no"] == Q3
    assert c.get("/v1/alerts/unread", headers=token(A)).json()["reviews_due"] == 1
    assert c.get("/v1/alerts/unread", headers=token(B)).json()["reviews_due"] == 0
    # a note isn't a review
    c.post("/v1/portfolio/journal", headers=token(A),
           json={"company_id": NVDA, "kind": "note", "why": "read the headline"})
    assert holdings(c, token, A)["reviews_due"] == 1
    # the report lands just after the holding was added; a review written after it clears it
    added = datetime.fromisoformat(h["holdings"][0]["added_at"])
    store.filings[0]["accepted_at"] = added + timedelta(microseconds=1)
    assert holdings(c, token, A)["reviews_due"] == 1
    c.post("/v1/portfolio/journal", headers=token(A),
           json={"company_id": NVDA, "kind": "review", "why": "Thesis intact"})
    assert holdings(c, token, A)["reviews_due"] == 0
    assert c.get("/v1/alerts/unread", headers=token(A)).json()["reviews_due"] == 0


def test_due_reviews_rules():
    added = datetime(2026, 11, 1, 15, tzinfo=UTC)
    opened = [{"company_id": NVDA, "created_at": added}]
    old = {"company_id": NVDA, "accession_no": "a", "form": "10-K",
           "filing_date": date(2026, 10, 1), "accepted_at": datetime(2026, 10, 1, 20, tzinfo=UTC)}
    new = {"company_id": NVDA, "accession_no": "b", "form": "10-Q",
           "filing_date": date(2026, 11, 20), "accepted_at": None}
    assert due_reviews(opened, [old], {}) == {}
    assert due_reviews(opened, [old, new], {})[NVDA]["accession_no"] == "b"
    assert due_reviews(opened, [new], {NVDA: datetime(2026, 11, 21, tzinfo=UTC)}) == {}
    assert due_reviews([], [new], {}) == {}


def test_open_holdings_join_the_alerts_follows(conn, token):
    c = client(conn)
    conn.execute("INSERT INTO watchlists (user_id, company_id, added_at) VALUES"
                 " (%s, %s, '2026-11-10T12:00:00Z')", (A, NVDA))
    buy(c, token, A)
    buy(c, token, A, company_id=AAPL)
    c.post("/v1/portfolio/holdings", headers=token(A),
           json={"symbol": "VOO", "lot": {"shares": "1", "cost_total": "1"}})
    follows = {f["company_id"]: f["sources"] for f in AppAlerts(OneConnection(conn)).follows(A)}
    assert follows == {NVDA: ["portfolio", "watchlist"], AAPL: ["portfolio"]}
    # the notifications table takes the new source
    AppAlerts(OneConnection(conn)).add_notifications(A, [{
        "company_id": AAPL, "accession_no": "0000320193-26-000101", "form": "10-Q",
        "filed": date(2026, 11, 14), "period_end": None, "items": None,
        "sources": ["portfolio", "watchlist", "analysis"], "cik": AAPL}])
    settings = c.get("/v1/alerts/settings", headers=token(A)).json()
    assert {x["company_id"]: x["sources"] for x in settings["companies"]}[AAPL] == ["portfolio"]


def test_a_holding_links_to_the_users_own_project_or_thesis(conn, token):
    c = client(conn)
    hid = buy(c, token, A)
    ws = conn.execute("INSERT INTO workspaces (user_id, name, project)"
                      " VALUES (%s, 'NVDA deep dive', %s) RETURNING workspace_id::text",
                      (A, json.dumps({"companyId": NVDA}))).fetchone()[0]
    thesis = conn.execute("INSERT INTO workspace_items (workspace_id, user_id, kind, name, state)"
                          " VALUES (%s, %s, 'thesis', 'Why NVDA', '{}') RETURNING item_id::text",
                          (ws, A)).fetchone()[0]
    bobs = conn.execute("INSERT INTO workspaces (user_id, name, project) VALUES (%s, 'Bob',"
                        " %s) RETURNING workspace_id::text",
                        (B, json.dumps({"companyId": NVDA}))).fetchone()[0]
    h = holdings(c, token, A)["holdings"][0]
    assert h["link"] is None
    assert {(x["kind"], x["name"]) for x in h["candidates"]} == {
        ("project", "NVDA deep dive"), ("thesis", "Why NVDA")}
    assert c.put(f"/v1/portfolio/holdings/{hid}/link", headers=token(A),
                 json={"project_id": bobs}).status_code == 404
    assert c.put(f"/v1/portfolio/holdings/{hid}/link", headers=token(A),
                 json={"thesis_item_id": thesis}).status_code == 200
    assert holdings(c, token, A)["holdings"][0]["link"] == {
        "kind": "thesis", "id": thesis, "name": "Why NVDA"}
    # the thesis deleted: the link goes, the holding stays
    conn.execute("DELETE FROM workspace_items WHERE item_id = %s", (thesis,))
    assert holdings(c, token, A)["holdings"][0]["link"] is None
    assert c.put(f"/v1/portfolio/holdings/{hid}/link", headers=token(A),
                 json={"project_id": ws}).status_code == 200
    assert holdings(c, token, A)["holdings"][0]["link"]["kind"] == "project"


def test_deleting_a_holding_keeps_its_journal(conn, token):
    c = client(conn)
    hid = buy(c, token, A)
    c.post("/v1/portfolio/journal", headers=token(A),
           json={"company_id": NVDA, "kind": "bought", "why": "x"})
    assert c.delete(f"/v1/portfolio/holdings/{hid}", headers=token(A)).status_code == 200
    assert holdings(c, token, A)["holdings"] == []
    assert len(c.get("/v1/portfolio/journal", headers=token(A)).json()["entries"]) == 1


def test_the_position_line_for_the_company_page(conn, token):
    c = client(conn)
    buy(c, token, A, day="2026-11-02")
    buy(c, token, A, shares="1", cost="100", day="2026-09-01")
    pos = c.get(f"/v1/portfolio/position/{NVDA}", headers=token(A)).json()["position"]
    assert (pos["shares"], pos["open"], pos["first_bought"]) == ("3", True, "2026-09-01")


class StubProvider:
    """A market-data provider that answers one close without the network."""
    name = "Tiingo"

    def __init__(self):
        self.asked = []

    def validate(self, key):
        return None

    def daily(self, key, ticker, start):
        self.asked.append(ticker)
        return [Bar(date(2026, 11, 14), Decimal("190.5"), None, None)]


def test_the_users_own_provider_close_counts_when_newer_and_is_theirs_alone(conn, token):
    connections, ring, stub = AppConnections(OneConnection(conn)), Keyring.parse(
        new_master_key()), StubProvider()
    market = MarketData(ring, connections, {"tiingo": stub},
                        clock=lambda: datetime(2026, 11, 16, 15, tzinfo=UTC))
    c = client(conn, market_data=market)
    connections.put(A, "tiingo", ring.seal(A, "tiingo", "test" * 10), "test",
                    datetime(2026, 11, 16, 15, tzinfo=UTC))
    hid = buy(c, token, A)
    bob = buy(c, token, B)
    for user, holding in ((A, hid), (B, bob)):
        c.put(f"/v1/portfolio/holdings/{holding}/price", headers=token(user),
              json={"price": "180", "on": "2026-11-13"})
    assert holdings(c, token, A)["refresh"] is True
    r = c.post("/v1/portfolio/prices/refresh", headers=token(A))
    assert r.status_code == 200 and r.json()["asked"] == 1 and stub.asked == ["NVDA"]
    assert c.post("/v1/portfolio/prices/refresh", headers=token(B)).status_code == 404
    mine = holdings(c, token, A)["holdings"][0]
    assert mine["price"] == {"value": "190.5", "on": "2026-11-14", "source": "provider",
                             "provider": "tiingo", "stale": False}
    # bob reads no one's provider prices but his own (he has none)
    assert holdings(c, token, B)["holdings"][0]["price"]["source"] == "typed"
    # a newer typed price wins
    c.put(f"/v1/portfolio/holdings/{hid}/price", headers=token(A),
          json={"price": "181", "on": "2026-11-16"})
    assert holdings(c, token, A)["holdings"][0]["price"]["source"] == "typed"
