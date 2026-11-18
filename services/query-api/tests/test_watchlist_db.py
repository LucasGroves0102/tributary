"""AppWatchlists against the real app database, as the tables' owner (the Query API's login),
whom the forced row-level security (app 045) binds. Skipped unless APP_TEST_DATABASE_URL is set;
every test is rolled back."""

import os
from contextlib import contextmanager

import pytest

from query_api.watchlist import AppWatchlists, UnknownUser

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")
TSLA, NVDA = "0001318605", "0001045810"


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        owner = c.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                          " AND tablename = 'watchlists'").fetchone()[0]
        c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
        c.execute("INSERT INTO users (user_id, email) VALUES ('t-w1', 't-w1@example.com'),"
                  " ('t-w2', 't-w2@example.com')")
        yield c


@pytest.fixture
def lists(conn):
    return AppWatchlists(OneConnection(conn))


def test_add_list_remove(lists):
    assert lists.add("t-w1", TSLA) is True
    assert lists.add("t-w1", TSLA) is False
    assert lists.add("t-w1", NVDA) is True
    # one transaction: equal added_at, so the tie falls back to company_id
    assert [e["company_id"] for e in lists.list("t-w1")] == sorted([TSLA, NVDA])
    assert lists.count("t-w1") == 2
    assert lists.list("t-w2") == []  # per user
    assert lists.remove("t-w1", TSLA) is True
    assert lists.remove("t-w1", TSLA) is False
    assert [e["company_id"] for e in lists.list("t-w1")] == [NVDA]


def test_unknown_user(lists):
    with pytest.raises(UnknownUser):
        lists.add("t-nobody", TSLA)


def test_owner_only_even_without_the_filter(conn, lists):
    """app 045: another user's session (or none) can't read, change or delete a watchlist row,
    even with no `user_id =` filter in the statement."""
    forced = conn.execute("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class"
                          " WHERE oid = 'watchlists'::regclass").fetchone()[0]
    assert forced
    owner = conn.execute("SELECT current_user").fetchone()[0]
    if conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                    (owner,)).fetchone()[0]:
        pytest.skip(f"table owner {owner} bypasses row-level security")
    lists.add("t-w1", TSLA)
    for user in ("t-w2", ""):
        conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        assert conn.execute("SELECT count(*) FROM watchlists").fetchone()[0] == 0
        assert conn.execute("UPDATE watchlists SET added_at = now()").rowcount == 0
        assert conn.execute("DELETE FROM watchlists").rowcount == 0
    conn.execute("SELECT set_config('app.user_id', 't-w2', true)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("INSERT INTO watchlists (user_id, company_id) VALUES ('t-w1', %s)", (NVDA,))
    assert [e["company_id"] for e in lists.list("t-w1")] == [TSLA]
