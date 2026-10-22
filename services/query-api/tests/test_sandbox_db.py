"""AppSandboxes against the real app database (db/app migration 003). Skipped unless
APP_TEST_DATABASE_URL is set; every test is rolled back."""

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from query_api.sandbox import (
    DEMO_PLAN,
    NewSandbox,
    QuotaExceeded,
    Refused,
    SandboxGone,
    sandbox_workspaces,
)

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)
PEP, KO, NVDA = "0000077476", "0000021344", "0001045810"


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def db():
    from query_api.sandbox import AppSandboxes

    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        # only this test's rows count towards the limits
        conn.execute("DELETE FROM demo_creations")
        conn.execute("DELETE FROM users WHERE user_id IN (SELECT user_id FROM demo_sandboxes)")
        yield AppSandboxes(OneConnection(conn)), conn


def sandbox(user="demo-t1", at=NOW, hours=24, companies=(PEP, KO, NVDA), watch=(KO, PEP)):
    return NewSandbox(user, list(companies), list(watch), at, at + timedelta(hours=hours))


def rows(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def test_create_writes_an_ordinary_user_plan_grants_and_watchlist(db):
    s, conn = db
    s.create(sandbox(), "ip1", 3, 10)
    assert rows(conn, "SELECT plan_id FROM subscriptions WHERE user_id = 'demo-t1'") == [
        (DEMO_PLAN,)]
    assert sorted(rows(conn, "SELECT company_id FROM company_grants WHERE user_id = 'demo-t1'"
                       " AND org_id IS NULL")) == sorted([(PEP,), (KO,), (NVDA,)])
    # watchlist keeps the configured order
    assert rows(conn, "SELECT company_id FROM watchlists WHERE user_id = 'demo-t1'"
                      " ORDER BY added_at") == [(KO,), (PEP,)]
    assert s.expires_at("demo-t1") == NOW + timedelta(hours=24)
    assert s.expires_at("alice-not-a-sandbox") is None


def test_per_ip_limit_and_live_cap(db):
    s, _ = db
    s.create(sandbox("demo-a"), "ip1", 2, 3)
    s.create(sandbox("demo-b", at=NOW + timedelta(minutes=1)), "ip1", 2, 3)
    with pytest.raises(Refused) as e:
        s.create(sandbox("demo-c", at=NOW + timedelta(minutes=2)), "ip1", 2, 3)
    assert e.value.reason == "ip"
    s.create(sandbox("demo-c", at=NOW + timedelta(minutes=2)), "ip2", 2, 3)
    with pytest.raises(Refused) as e:
        s.create(sandbox("demo-d", at=NOW + timedelta(minutes=3)), "ip3", 2, 3)
    assert e.value.reason == "full"
    # an hour later ip1 may create again; expired sandboxes don't count as live
    later = NOW + timedelta(hours=25)
    s.create(sandbox("demo-e", at=later), "ip1", 2, 3)


def test_refused_creation_writes_nothing(db):
    s, conn = db
    with pytest.raises(Refused):
        s.create(sandbox("demo-x"), "ip1", 0, 3)
    assert rows(conn, "SELECT 1 FROM users WHERE user_id = 'demo-x'") == []


def test_take_question_caps(db):
    s, _ = db
    s.create(sandbox("demo-a"), "ip1", 5, 5)
    s.create(sandbox("demo-b"), "ip2", 5, 5)
    assert s.take_question("demo-a", NOW, 2, 3) == 1
    assert s.take_question("demo-a", NOW, 2, 3) == 0
    with pytest.raises(QuotaExceeded) as e:
        s.take_question("demo-a", NOW, 2, 3)
    assert e.value.scope == "user"
    assert s.take_question("demo-b", NOW, 2, 3) == 0
    with pytest.raises(QuotaExceeded) as e:
        s.take_question("demo-b", NOW, 2, 3)
    assert e.value.scope == "day"
    # a new UTC day starts again
    assert s.take_question("demo-a", NOW + timedelta(hours=10), 2, 3) == 1


def test_take_question_for_expired_or_unknown_sandbox(db):
    s, _ = db
    s.create(sandbox("demo-a", hours=1), "ip1", 5, 5)
    with pytest.raises(SandboxGone):
        s.take_question("demo-a", NOW + timedelta(hours=1), 2, 3)
    with pytest.raises(SandboxGone):
        s.take_question("demo-nobody", NOW, 2, 3)


def test_delete_expired_removes_only_expired_sandbox_users_and_their_rows(db):
    s, conn = db
    conn.execute("INSERT INTO users (user_id, email) VALUES ('t-regular', 't-r@example.com')")
    conn.execute("INSERT INTO company_grants (company_id, user_id) VALUES (%s, 't-regular')",
                 (PEP,))
    s.create(sandbox("demo-old", hours=1), "ip1", 5, 5)
    s.create(sandbox("demo-new", hours=24), "ip1", 5, 5)
    s.take_question("demo-old", NOW, 5, 5)
    counts = s.delete_expired(NOW + timedelta(hours=2))
    assert counts["users"] == 1
    assert {r[0] for r in rows(conn, "SELECT user_id FROM users WHERE user_id IN"
                                     " ('t-regular', 'demo-old', 'demo-new')")} == {
        "t-regular", "demo-new"}
    for table in ("subscriptions", "company_grants", "watchlists", "demo_sandboxes",
                  "demo_agent_usage"):
        assert rows(conn, f"SELECT 1 FROM {table} WHERE user_id = 'demo-old'") == []  # noqa: S608
    assert rows(conn, "SELECT 1 FROM company_grants WHERE user_id = 'demo-new'")
    # the day's global count is kept, so deleting sandboxes doesn't refill the day's budget
    assert rows(conn, "SELECT questions FROM demo_agent_daily WHERE day = %s",
                NOW.date()) == [(1,)]
    # creation rate-limit rows are pruned after a day
    s.delete_expired(NOW + timedelta(days=2))
    assert rows(conn, "SELECT 1 FROM demo_creations") == []



def test_starter_workspace_is_created_and_deleted_with_the_sandbox(db):
    s, conn = db
    ids = {"PEP": PEP, "KO": KO, "KDP": "0001418135", "MNST": "0000865752"}
    starter = sandbox("demo-ws", hours=1)
    s.create(NewSandbox(**{**starter.__dict__, "workspaces": tuple(sandbox_workspaces(ids))}),
             "ip1", 5, 5)
    assert rows(conn, "SELECT name FROM workspaces WHERE user_id = 'demo-ws'") == [
        ("PepsiCo vs Coca-Cola",)]
    assert rows(conn, "SELECT kind, name FROM workspace_items WHERE user_id = 'demo-ws'"
                      " ORDER BY created_at") == [("chart", "Margins: PEP vs KO"),
                                                 ("comps", "PEP vs KO, KDP, MNST")]
    s.delete_expired(NOW + timedelta(hours=2))
    for table in ("workspaces", "workspace_items"):
        assert rows(conn, f"SELECT 1 FROM {table} WHERE user_id = 'demo-ws'") == []  # noqa: S608
