"""Owner-created password accounts against the real app database (db/app migration 013): what
`query-api-account create` writes (in ONE transaction: the same rows as a GitHub account plus
the password row, never the password), the other CLI commands, sign-in with its lockout as
stored rows, and the per-account assistant cap in take_question. Skipped unless
APP_TEST_DATABASE_URL is set; every test is rolled back."""

import io
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.password_accounts import (
    LOCK_FOR,
    MAX_FAILURES,
    AppPasswordAccounts,
    run_cli,
)
from query_api.passwords import verify
from query_api.sandbox import (
    DEMO_PLAN,
    DEMO_TICKERS,
    DEMO_WATCHLIST,
    AppSandboxes,
    QuotaExceeded,
    SandboxConfig,
    SandboxGone,
    sandbox_workspaces,
)

from .conftest import SECRET
from .test_passwords import GOOD, WRONG, NoAuthz, Pipe
from .test_sandbox import IDS, FakeStore

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

NOW = datetime.now(UTC).replace(microsecond=0)  # tokens are checked against the real clock
USER = "pw-tester"
NEW_PASSWORD = "another-horse-test-only"
COMPANIES = [IDS[t] for t in DEMO_TICKERS]


def access():
    return COMPANIES, [IDS[t] for t in DEMO_WATCHLIST], sandbox_workspaces(IDS)


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def db():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        conn.execute("DELETE FROM users WHERE user_id LIKE 'pw-%'")
        conn.execute("UPDATE public_catalogs SET public = false WHERE catalog_id = 'sec'")
        yield conn, AppPasswordAccounts(OneConnection(conn))


def cli(store, *argv, password=GOOD, clock=lambda: NOW):
    out, err = io.StringIO(), io.StringIO()
    code = run_cli(list(argv), store, access=access, out=out, err=err,
                   stdin=Pipe(password + "\n"), clock=clock)
    return code, out.getvalue(), err.getvalue()


def rows(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def test_create_writes_a_github_accounts_access_and_the_password_row(db):
    conn, store = db
    code, out, err = cli(store, "create", "tester", "--questions-per-day", "100")
    assert (code, err) == (0, "") and "created tester (pw-tester)" in out
    assert rows(conn, "SELECT email FROM users WHERE user_id = %s", USER) == [
        ("tester@password.invalid",)]
    assert rows(conn, "SELECT plan_id FROM subscriptions WHERE user_id = %s", USER) == [
        (DEMO_PLAN,)]
    grants = rows(conn, "SELECT company_id FROM company_grants WHERE user_id = %s", USER)
    assert sorted(g[0] for g in grants) == sorted(COMPANIES)
    assert rows(conn, "SELECT company_id FROM watchlists WHERE user_id = %s ORDER BY added_at",
                USER) == [(IDS[t],) for t in DEMO_WATCHLIST]
    assert rows(conn, "SELECT name FROM workspaces WHERE user_id = %s", USER) == [
        ("PepsiCo vs Coca-Cola",)]
    (username, password_hash, cap, disabled, failed, locked, after), = rows(
        conn, "SELECT username, password_hash, questions_per_day, disabled, failed_attempts,"
              " locked_until, sessions_valid_after FROM password_accounts WHERE user_id = %s",
        USER)
    assert (username, cap, disabled, failed, locked, after) == (
        "tester", 100, False, 0, None, NOW)
    assert password_hash.startswith("scrypt$n=32768,r=8,p=1$") and verify(GOOD, password_hash)
    # not a sandbox, not a GitHub account, and the password is stored nowhere
    assert rows(conn, "SELECT 1 FROM demo_sandboxes WHERE user_id = %s", USER) == []
    for (table,) in rows(conn, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"):
        hits = conn.execute(f"SELECT count(*) FROM {table} t WHERE t::text LIKE %s",  # noqa: S608
                            (f"%{GOOD}%",)).fetchone()
        assert hits == (0,), table


def test_with_the_catalog_public_create_writes_no_company_grants(db):
    conn, store = db
    conn.execute("UPDATE public_catalogs SET public = true WHERE catalog_id = 'sec'")
    assert cli(store, "create", "tester", "--questions-per-day", "5")[0] == 0
    assert rows(conn, "SELECT 1 FROM company_grants WHERE user_id = %s", USER) == []
    assert rows(conn, "SELECT plan_id FROM subscriptions WHERE user_id = %s", USER) == [
        (DEMO_PLAN,)]


def test_create_is_one_transaction(db):
    conn, store = db
    # the watchlist insert (after users, password_accounts, subscription and grants) fails
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.create("tester", "scrypt$n=32768,r=8,p=1$AAAA$AAAA", 5, NOW, COMPANIES,
                     [COMPANIES[0], COMPANIES[0]], [])
    for table in ("users", "password_accounts", "subscriptions", "company_grants",
                  "watchlists"):
        assert rows(conn, f"SELECT 1 FROM {table} WHERE user_id = %s", USER) == [], table


def test_create_refuses_a_taken_username(db):
    conn, store = db
    assert cli(store, "create", "tester", "--questions-per-day", "5")[0] == 0
    code, _, err = cli(store, "create", "tester", "--questions-per-day", "9",
                       password=NEW_PASSWORD)
    assert code == 1 and "already exists" in err
    assert rows(conn, "SELECT questions_per_day FROM password_accounts") == [(5,)]


def test_constraints(db):
    conn, store = db
    for username, cap in (("Bad Name", 5), ("tester", 1001), ("tester", -1)):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            store.create(username, "scrypt$n=32768,r=8,p=1$AAAA$AAAA", cap, NOW, [], [], [])
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        store.create("tester", "plaintext-password", 5, NOW, [], [], [])


def test_owner_commands(db):
    conn, store = db
    cli(store, "create", "tester", "--questions-per-day", "5")
    later = NOW + timedelta(minutes=3)
    assert cli(store, "set-limit", "tester", "--questions-per-day", "50")[0] == 0
    assert cli(store, "set-password", "tester", password=NEW_PASSWORD,
               clock=lambda: later)[0] == 0
    (password_hash, cap, after), = rows(
        conn, "SELECT password_hash, questions_per_day, sessions_valid_after"
              " FROM password_accounts")
    assert verify(NEW_PASSWORD, password_hash) and not verify(GOOD, password_hash)
    assert (cap, after) == (50, later)
    assert cli(store, "disable", "tester", clock=lambda: later + timedelta(minutes=1))[0] == 0
    assert rows(conn, "SELECT disabled, sessions_valid_after FROM password_accounts") == [
        (True, later + timedelta(minutes=1))]
    assert cli(store, "enable", "tester")[0] == 0
    assert rows(conn, "SELECT disabled FROM password_accounts") == [(False,)]
    code, out, _ = cli(store, "list")
    assert code == 0 and "tester  pw-tester  50  0  False  False  0" in out
    assert "scrypt" not in out and NEW_PASSWORD not in out
    code, _, err = cli(store, "disable", "nobody")
    assert code == 1 and "doesn't exist" in err


@pytest.fixture
def world(db):
    conn, store = db
    cli(store, "create", "tester", "--questions-per-day", "3")
    clock = Clock()
    sandboxes = AppSandboxes(OneConnection(conn))
    app = create_app(NoAuthz(), FakeStore(), SECRET, clock=clock, password_accounts=store,
                     sandboxes=sandboxes,
                     sandbox_config=SandboxConfig(agent_questions_per_user_per_day=1,
                                                  agent_questions_per_day=1))
    return TestClient(app), conn, clock, sandboxes


def sign_in(client, password=GOOD):
    return client.post("/v1/auth/password", json={"username": "tester", "password": password})


def test_sign_in_lockout_and_reset_in_the_database(world):
    client, conn, clock, _ = world
    assert sign_in(client, WRONG).status_code == 401
    assert rows(conn, "SELECT failed_attempts FROM password_accounts") == [(1,)]
    r = sign_in(client)
    assert r.status_code == 200
    assert rows(conn, "SELECT failed_attempts, last_sign_in_at FROM password_accounts") == [
        (0, NOW)]
    me = client.get("/v1/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.json()["account"]["agent_questions_per_day"] == 3
    for _ in range(MAX_FAILURES):
        assert sign_in(client, WRONG).status_code == 401
    assert rows(conn, "SELECT failed_attempts, locked_until FROM password_accounts") == [
        (0, NOW + LOCK_FOR)]
    assert sign_in(client).status_code == 401
    clock.now = NOW + LOCK_FOR + timedelta(seconds=1)
    assert sign_in(client).status_code == 200
    assert rows(conn, "SELECT locked_until FROM password_accounts") == [(None,)]


def test_questions_use_the_accounts_own_cap_not_the_demo_day(world):
    _, conn, _, sandboxes = world
    day = NOW.date()
    # the demo's shared day is already used up: the account isn't held to it
    conn.execute("INSERT INTO demo_agent_daily (day, questions) VALUES (%s, 1000)"
                 " ON CONFLICT (day) DO UPDATE SET questions = 1000", (day,))
    assert sandboxes.take_question(USER, NOW, 1, 1) == 2
    assert sandboxes.take_question(USER, NOW, 1, 1, questions=2) == 0
    with pytest.raises(QuotaExceeded) as e:
        sandboxes.take_question(USER, NOW, 1, 1)
    assert (e.value.scope, e.value.left, e.value.limit) == ("user", 0, 3)
    assert rows(conn, "SELECT questions FROM demo_agent_usage WHERE user_id = %s", USER) == [
        (3,)]
    assert rows(conn, "SELECT questions FROM demo_agent_daily WHERE day = %s", day) == [(1000,)]
    # a new day, a new allowance; a disabled account has none
    assert sandboxes.take_question(USER, NOW + timedelta(days=1), 1, 1) == 2
    conn.execute("UPDATE password_accounts SET disabled = true")
    with pytest.raises(SandboxGone):
        sandboxes.take_question(USER, NOW + timedelta(days=1), 1, 1)


def test_questions_left_is_the_accounts_own_cap_and_takes_nothing(world):
    _, conn, _, sandboxes = world
    assert sandboxes.questions_left(USER, NOW, 1, 1) == (3, 3)
    sandboxes.take_question(USER, NOW, 1, 1)
    assert sandboxes.questions_left(USER, NOW, 1, 1) == (3, 2)
    assert rows(conn, "SELECT questions FROM demo_agent_usage WHERE user_id = %s", USER) == [
        (1,)]
    conn.execute("UPDATE password_accounts SET disabled = true")
    with pytest.raises(SandboxGone):
        sandboxes.questions_left(USER, NOW, 1, 1)


def test_agent_quota_message_names_the_accounts_cap(world):
    client, *_ = world
    h = {"Authorization": f"Bearer {sign_in(client).json()['token']}"}
    for left in (2, 1, 0):
        assert client.post("/v1/demo/agent-quota", headers=h).json()["remaining_today"] == left
    r = client.post("/v1/demo/agent-quota", headers=h)
    assert r.status_code == 429
    assert r.json()["detail"].startswith("Your account has used its 3 assistant questions")
