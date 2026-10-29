"""GitHub sign-in against the real app database (db/app migration 007), end to end through the
callback with a fake GitHub: what a first sign-in writes (the same rows as a sandbox, which CDC
turns into access), that a second one changes no access and keeps saved work, that the access
token is stored nowhere, that demo-cleanup never deletes a GitHub user and that the assistant
caps apply. Skipped unless APP_TEST_DATABASE_URL is set; every test is rolled back."""

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.github import NONCE_COOKIE, AppAccounts, GitHubUser
from query_api.sandbox import (
    DEMO_PLAN,
    DEMO_TICKERS,
    DEMO_WATCHLIST,
    AppSandboxes,
    NewSandbox,
    QuotaExceeded,
    SandboxConfig,
    SandboxGone,
)

from .conftest import SECRET
from .test_github import ACCESS_TOKEN, CONFIG, FakeGitHub
from .test_sandbox import IDS, FakeStore

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)  # tokens use the real clock
USER = "gh-583231"


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


class NoAuthz:
    def readable(self, user_id):
        return {}

    def can(self, *args):
        return False


@pytest.fixture
def world():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        conn.execute("DELETE FROM users WHERE user_id LIKE 'gh-%'")
        pool = OneConnection(conn)
        clock, gh = Clock(), FakeGitHub(GitHubUser(583231, "octocat"))
        app = create_app(NoAuthz(), FakeStore(), SECRET, clock=clock,
                         sandboxes=AppSandboxes(pool), sandbox_config=SandboxConfig(),
                         github_config=CONFIG, github=gh, github_accounts=AppAccounts(pool))
        yield TestClient(app, follow_redirects=False), conn, clock, gh


def sign_in(client) -> str:
    r = client.get("/v1/auth/github/start")
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    r = client.get("/v1/auth/github/callback", params={"code": "good-code", "state": state},
                   headers={"Cookie": f"{NONCE_COOKIE}={r.cookies[NONCE_COOKIE]}"})
    fragment = urlsplit(r.headers["location"]).fragment
    return parse_qs(fragment.split("?", 1)[1])["token"][0]


def rows(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def test_first_sign_in_writes_an_ordinary_user_with_the_demo_access(world):
    client, conn, _, _ = world
    token = sign_in(client)
    assert jwt.decode(token, SECRET, algorithms=["HS256"])["sub"] == USER
    assert rows(conn, "SELECT email FROM users WHERE user_id = %s", USER) == [
        ("583231+octocat@users.noreply.github.com",)]
    assert rows(conn, "SELECT github_id, login FROM github_accounts WHERE user_id = %s",
                USER) == [(583231, "octocat")]
    assert rows(conn, "SELECT plan_id FROM subscriptions WHERE user_id = %s", USER) == [
        (DEMO_PLAN,)]
    grants = rows(conn, "SELECT company_id FROM company_grants WHERE user_id = %s"
                        " AND org_id IS NULL", USER)
    assert sorted(g[0] for g in grants) == sorted(IDS[t] for t in DEMO_TICKERS)
    assert len(grants) == 20
    assert rows(conn, "SELECT company_id FROM watchlists WHERE user_id = %s ORDER BY added_at",
                USER) == [(IDS[t],) for t in DEMO_WATCHLIST]
    assert rows(conn, "SELECT name FROM workspaces WHERE user_id = %s", USER) == [
        ("PepsiCo vs Coca-Cola",)]
    # a GitHub user is not a sandbox
    assert rows(conn, "SELECT 1 FROM demo_sandboxes WHERE user_id = %s", USER) == []


def test_the_access_token_is_stored_nowhere(world):
    client, conn, _, _ = world
    sign_in(client)
    tables = [r[0] for r in rows(conn, "SELECT tablename FROM pg_tables"
                                       " WHERE schemaname = 'public'")]
    for table in tables:
        hits = conn.execute(f"SELECT count(*) FROM {table} t"  # noqa: S608
                            " WHERE t::text LIKE %s OR t::text LIKE '%%good-code%%'",
                            (f"%{ACCESS_TOKEN}%",)).fetchone()
        assert hits == (0,), table


def test_second_sign_in_changes_no_access_and_keeps_saved_work(world):
    client, conn, clock, gh = world
    sign_in(client)
    # the user reshapes their work and watchlist between sign-ins
    conn.execute("UPDATE workspaces SET name = 'Mine now' WHERE user_id = %s", (USER,))
    conn.execute("DELETE FROM watchlists WHERE user_id = %s AND company_id = %s",
                 (USER, IDS["PEP"]))
    snapshot = {t: sorted(rows(conn, f"SELECT * FROM {t} WHERE user_id = %s", USER))  # noqa: S608
                for t in ("users", "subscriptions", "company_grants", "watchlists",
                          "workspaces", "workspace_items")}
    clock.now = NOW + timedelta(days=3)
    gh.user_ = GitHubUser(583231, "octocat-renamed")
    token = sign_in(client)
    assert jwt.decode(token, SECRET, algorithms=["HS256"],
                      options={"verify_iat": False})["sub"] == USER  # minted 3 days ahead
    for t, before in snapshot.items():
        assert sorted(rows(conn, f"SELECT * FROM {t} WHERE user_id = %s", USER)) == before, t  # noqa: S608
    assert rows(conn, "SELECT login, created_at, last_sign_in_at FROM github_accounts"
                      " WHERE user_id = %s", USER) == [
        ("octocat-renamed", NOW, NOW + timedelta(days=3))]


def test_demo_cleanup_never_deletes_a_github_user(world):
    client, conn, _, _ = world
    sign_in(client)
    sandboxes = AppSandboxes(OneConnection(conn))
    sandboxes.create(NewSandbox("demo-gh-test", [IDS["PEP"]], [], NOW - timedelta(days=2),
                                NOW - timedelta(days=1)), "ip-gh-test", 99, 10_000)
    sandboxes.take_question(USER, NOW - timedelta(days=200), 10, 1000)
    counts = sandboxes.delete_expired(NOW + timedelta(days=400))
    assert counts["users"] >= 1
    assert rows(conn, "SELECT 1 FROM users WHERE user_id = 'demo-gh-test'") == []
    assert rows(conn, "SELECT 1 FROM users WHERE user_id = %s", USER) == [(1,)]
    assert len(rows(conn, "SELECT 1 FROM company_grants WHERE user_id = %s", USER)) == 20
    # its old usage rows are pruned like the daily totals
    assert rows(conn, "SELECT 1 FROM demo_agent_usage WHERE user_id = %s", USER) == []


def test_assistant_caps_apply_to_a_github_user(world):
    client, conn, _, _ = world
    sign_in(client)
    s = AppSandboxes(OneConnection(conn))
    day = NOW + timedelta(days=500)  # a day no other test has used
    assert s.take_question(USER, day, 2, 1000) == 1
    assert s.take_question(USER, day, 2, 1000) == 0
    with pytest.raises(QuotaExceeded) as e:
        s.take_question(USER, day, 2, 1000)
    assert e.value.scope == "user"
    with pytest.raises(SandboxGone):
        s.take_question("gh-1", day, 2, 1000)
