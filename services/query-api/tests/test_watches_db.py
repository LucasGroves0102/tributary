"""Saved screens that alert against the real app database (migration 024): forced row-level
security, two users (neither reads, changes or stops the other's watch, nor sees their alerts),
watches going with their screen, the job's due list and its record (state, alert and webhook
deliveries in one transaction, unless the watch changed meanwhile), and the alerts' way out
through AppAlerts (the bell's count, mark read, the delivery queue, prune). Skipped unless
APP_TEST_DATABASE_URL is set; every test is rolled back."""

import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from query_api.alert_job import deliver
from query_api.alerts import AppAlerts
from query_api.watches import AppWatches
from query_api.webhooks import Sent

from .conftest import SECRET

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

A, B = "t-watch-a", "t-watch-b"
NVDA, TSLA = "0001045810", "0001318605"
NOW = datetime(2026, 11, 18, 11, 0, tzinfo=UTC)
HOOK = "https://hooks.example.com/tributary/" + "w" * 32
DETAIL = {"entered": {"companies": [NVDA, TSLA], "more": 0}, "figures_at": None}


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("INSERT INTO users (user_id, email) VALUES (%s, 'a@example.com'),"
                  " (%s, 'b@example.com')", (A, B))
        yield c


def screen(conn, user, name="Cheap growers", kind="screen", state=None):
    """A saved screen of the user's (as the table's owner, past RLS: the test's setup)."""
    conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
    ws = conn.execute("INSERT INTO workspaces (user_id, name) VALUES (%s, 'Screens')"
                      " RETURNING workspace_id::text", (user,)).fetchone()[0]
    item = conn.execute("INSERT INTO workspace_items (workspace_id, user_id, kind, name, state)"
                        " VALUES (%s, %s, %s, %s, %s) RETURNING item_id::text",
                        (ws, user, kind, name, json.dumps(state or {"growth": 10}))).fetchone()[0]
    conn.execute("SELECT set_config('app.user_id', '', true)")
    return item


@pytest.fixture
def ws(conn):
    return AppWatches(OneConnection(conn))


@pytest.fixture
def alerts(conn):
    return AppAlerts(OneConnection(conn))


def due_one(ws, user, landed=None, at=NOW):
    rows = ws.due(user, landed, at - timedelta(hours=26))
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize("table", ["watches", "screen_alerts"])
def test_watch_tables_force_rls_and_arent_streamed(conn, table):
    rls, forced, identity = conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity, relreplident FROM pg_class"
        " WHERE oid = %s::regclass", (table,)).fetchone()
    assert rls and forced and identity == "d"
    connector = Path(__file__).parents[3] / "streaming" / "debezium" / "app-connector.json"
    assert table not in json.loads(connector.read_text())["table.include.list"]


def test_one_user_cant_watch_read_or_stop_anothers_screen(conn, ws):
    a_item, b_item = screen(conn, A), screen(conn, B)
    assert ws.put(A, a_item, True, True, [], [])["item_id"] == a_item
    assert ws.put(B, a_item, True, True, [], []) is None       # A's screen: not B's to watch
    assert ws.screen(B, a_item) is None
    assert ws.delete(B, a_item) is False
    assert ws.watches(B) == []
    assert [w["item_id"] for w in ws.watches(A)] == [a_item]
    assert ws.put(B, b_item, True, False, [], [])["leaving"] is False
    # the tables themselves bind their owner (the Query API's login): with B's id in the
    # session, A's rows don't exist and can't be written
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                         " AND tablename = 'watches'").fetchone()[0]
    if conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                    (owner,)).fetchone()[0]:
        pytest.skip(f"table owner {owner} bypasses row-level security")
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
    conn.execute("SELECT set_config('app.user_id', %s, true)", (B,))
    assert conn.execute("SELECT count(*) FROM watches WHERE user_id = %s", (A,)
                        ).fetchone()[0] == 0
    assert conn.execute("UPDATE watches SET entering = false WHERE item_id = %s",
                        (a_item,)).rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("INSERT INTO watches (user_id, item_id) VALUES (%s, %s)", (A, a_item))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("INSERT INTO screen_alerts (user_id, watch_id, item_id, name, detail)"
                     " SELECT %s, gen_random_uuid(), %s, 'x', '{}'", (A, a_item))


def test_a_watch_cant_point_at_another_users_screen_or_a_chart(conn, ws):
    a_item, chart = screen(conn, A), screen(conn, A, kind="chart", state={"layers": []})
    conn.execute("SELECT set_config('app.user_id', %s, true)", (B,))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute("INSERT INTO watches (user_id, item_id) VALUES (%s, %s)", (B, a_item))
    conn.execute("SELECT set_config('app.user_id', '', true)")
    assert ws.put(A, chart, True, True, [], []) is None


def test_the_job_records_alerts_once_with_a_delivery_per_enabled_webhook(conn, ws, alerts):
    item = screen(conn, A)
    hook = alerts.add_webhook(A, HOOK, "json", b"s" * 16)
    off = alerts.add_webhook(A, HOOK + "x", "json", b"t" * 16)
    alerts.set_webhook(A, off["webhook_id"], False)
    ws.put(A, item, True, True, [], [])
    w = due_one(ws, A)
    assert w["checked_at"] is None and w["name"] == "Cheap growers"
    assert ws.record(A, w, {"outcome": "baseline", "basis": "b" * 64, "members": [NVDA],
                            "near": {}, "companies": 1, "detail": None}, NOW) is None
    assert ws.due(A, None, NOW - timedelta(hours=26)) == []      # nothing landed since
    landed = NOW + timedelta(hours=20)
    w = due_one(ws, A, landed, NOW + timedelta(hours=21))
    assert (w["members"], w["basis"]) == ([NVDA], "b" * 64)
    alert_id = ws.record(A, w, {"outcome": "ok", "basis": "b" * 64, "members": [NVDA, TSLA],
                                "near": {}, "companies": 2, "detail": DETAIL},
                         NOW + timedelta(hours=21))
    assert alert_id
    # the bell counts it; the queue has it for the enabled webhook only
    assert alerts.unread(A)["unread"] == 1 and alerts.unread(B)["unread"] == 0
    due = alerts.due(A, NOW + timedelta(hours=22))
    assert [(d["webhook_id"], d["screen_alert_id"], d["name"]) for d in due] == [
        (hook["webhook_id"], alert_id, "Cheap growers")]
    assert due[0]["screen_detail"] == DETAIL and due[0]["notification_id"] is None
    assert alerts.due(B, NOW + timedelta(hours=22)) == []

    sent = []

    class Sender:
        def send(self, url, body, headers):
            sent.append((url, json.loads(body), headers["X-Tributary-Event"]))
            return Sent(True, 204)

    class Names:
        def profiles(self, user_id, ids):
            return {i: {"name": "Co"} for i in ids}

        def tickers(self, user_id, ids):
            return {}

    ok, failed, _ = deliver(alerts, Names(), Sender(), A, jwt_secret=SECRET,
                            app_url="https://app.example", at=NOW + timedelta(hours=22),
                            can=lambda u, t, c: c == NVDA)
    assert (ok, failed) == (1, 0)
    assert sent[0][2] == "screen" and sent[0][1]["hidden"] == 1
    log = alerts.deliveries(A, None, 10)
    assert log[0]["status"] == "delivered" and log[0]["screen_alert_id"] == alert_id
    assert ws.alerts(B, None, 10) == []
    assert [a["alert_id"] for a in ws.alerts(A, item, 10)] == [alert_id]


def test_a_watch_changed_during_its_check_stores_nothing(conn, ws):
    item = screen(conn, A)
    ws.put(A, item, True, True, [], [])
    w = due_one(ws, A)
    conn.execute("SELECT set_config('app.user_id', %s, true)", (A,))
    conn.execute("UPDATE watches SET updated_at = updated_at + interval '1 second'"
                 " WHERE user_id = %s", (A,))
    conn.execute("SELECT set_config('app.user_id', '', true)")
    assert ws.record(A, w, {"outcome": "ok", "basis": "b" * 64, "members": [NVDA], "near": {},
                            "companies": 1, "detail": DETAIL}, NOW) is None
    assert ws.alerts(A, None, 10) == []
    assert due_one(ws, A)["checked_at"] is None


def test_a_saved_over_screen_or_watch_is_due_again(conn, ws):
    item = screen(conn, A)
    ws.put(A, item, True, True, [], [])
    ws.record(A, due_one(ws, A), {"outcome": "baseline", "basis": "b" * 64, "members": [],
                                  "near": {}, "companies": 0}, NOW + timedelta(minutes=1))
    assert ws.due(A, None, NOW - timedelta(hours=26)) == []
    conn.execute("SELECT set_config('app.user_id', %s, true)", (A,))
    conn.execute("UPDATE workspace_items SET updated_at = %s WHERE user_id = %s",
                 (NOW + timedelta(minutes=5), A))
    conn.execute("SELECT set_config('app.user_id', '', true)")
    assert len(ws.due(A, None, NOW - timedelta(hours=26))) == 1


def test_a_failed_check_keeps_what_it_saw(conn, ws):
    item = screen(conn, A)
    ws.put(A, item, True, True, [], [])
    ws.record(A, due_one(ws, A), {"outcome": "baseline", "basis": "b" * 64, "members": [NVDA],
                                  "near": {"0": [NVDA]}, "companies": 1}, NOW)
    w = due_one(ws, A, NOW + timedelta(hours=20), NOW + timedelta(hours=21))
    ws.record(A, w, {"outcome": "failed"}, NOW + timedelta(hours=21))
    row = ws.watches(A)[0]
    assert row["outcome"] == "failed"
    w = due_one(ws, A, NOW + timedelta(hours=44), NOW + timedelta(hours=45))
    assert (w["members"], w["near"]) == ([NVDA], {"0": [NVDA]})


def test_deleting_the_screen_deletes_its_watch_alerts_and_deliveries(conn, ws, alerts):
    item = screen(conn, A)
    alerts.add_webhook(A, HOOK, "json", b"s" * 16)
    ws.put(A, item, True, True, [], [])
    ws.record(A, due_one(ws, A), {"outcome": "ok", "basis": "b" * 64, "members": [NVDA],
                                  "near": {}, "companies": 1, "detail": DETAIL}, NOW)
    conn.execute("SELECT set_config('app.user_id', %s, true)", (A,))
    conn.execute("DELETE FROM workspace_items WHERE item_id = %s", (item,))
    for table in ("watches", "screen_alerts"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM webhook_deliveries WHERE screen_alert_id IS NOT"
                        " NULL").fetchone()[0] == 0
    conn.execute("SELECT set_config('app.user_id', '', true)")


def test_marking_read_and_pruning_know_screen_alerts(conn, ws, alerts):
    item = screen(conn, A)
    alerts.add_webhook(A, HOOK, "json", b"s" * 16)
    ws.put(A, item, True, True, [], [])
    alert_id = ws.record(A, due_one(ws, A), {"outcome": "ok", "basis": "b" * 64,
                                             "members": [NVDA], "near": {}, "companies": 1,
                                             "detail": DETAIL}, NOW)
    assert alerts.mark_read(A, None, NVDA) == 0          # one company's: filing alerts only
    assert alerts.mark_read(B, [alert_id], None) == 0    # another user's: nothing
    assert alerts.mark_read(A, [alert_id], None) == 1
    assert alerts.unread(A)["unread"] == 0
    # the test-send prune leaves a screen alert's delivery alone; the alert's own prune takes it
    alerts.prune(A, NOW + timedelta(days=400))
    assert len(alerts.deliveries(A, None, 10)) == 1
    assert ws.prune(A, NOW + timedelta(days=1)) == 1
    assert alerts.deliveries(A, None, 10) == []


def test_a_delivery_carries_one_alert_of_its_own_user(conn, ws, alerts):
    item = screen(conn, A)
    hook = alerts.add_webhook(B, HOOK, "json", b"s" * 16)
    ws.put(A, item, True, True, [], [])
    alert_id = ws.record(A, due_one(ws, A), {"outcome": "ok", "basis": "b" * 64,
                                             "members": [NVDA], "near": {}, "companies": 1,
                                             "detail": DETAIL}, NOW)
    conn.execute("SELECT set_config('app.user_id', %s, true)", (B,))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute("INSERT INTO webhook_deliveries (user_id, webhook_id, screen_alert_id)"
                     " VALUES (%s, %s, %s)", (B, hook["webhook_id"], alert_id))
    conn.execute("SELECT set_config('app.user_id', '', true)")


@pytest.mark.parametrize("changes", [
    {"source": "query"}, {"columns": ["c"] * 13}, {"members": ["0000000001"] * 501},
    {"basis": "nothex"}, {"outcome": "great"}, {"lines": '{"not": "a list"}'},
    {"lines": json.dumps([{}] * 6)}])
def test_watch_rows_are_checked(conn, changes):
    item = screen(conn, A)
    conn.execute("SELECT set_config('app.user_id', %s, true)", (A,))
    cols = ", ".join(["user_id", "item_id", *changes])
    marks = ", ".join(["%s"] * (2 + len(changes)))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(f"INSERT INTO watches ({cols}) VALUES ({marks})",
                     (A, item, *changes.values()))
    conn.execute("SELECT set_config('app.user_id', '', true)")
