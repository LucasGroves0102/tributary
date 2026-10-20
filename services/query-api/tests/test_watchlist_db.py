"""AppWatchlists against the real app database. Skipped unless APP_TEST_DATABASE_URL is set;
every test is rolled back."""

import os
from contextlib import contextmanager

import pytest

from query_api.watchlist import AppWatchlists, UnknownUser

psycopg = pytest.importorskip("psycopg")
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
def lists():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        conn.execute("INSERT INTO users (user_id, email) VALUES ('t-w1', 't-w1@example.com'),"
                     " ('t-w2', 't-w2@example.com')")
        yield AppWatchlists(OneConnection(conn))


def test_add_list_remove(lists):
    assert lists.add("t-w1", TSLA) is True
    assert lists.add("t-w1", TSLA) is False
    assert lists.add("t-w1", NVDA) is True
    assert [e["company_id"] for e in lists.list("t-w1")] == [TSLA, NVDA]
    assert lists.count("t-w1") == 2
    assert lists.list("t-w2") == []  # per user
    assert lists.remove("t-w1", TSLA) is True
    assert lists.remove("t-w1", TSLA) is False
    assert [e["company_id"] for e in lists.list("t-w1")] == [NVDA]


def test_unknown_user(lists):
    with pytest.raises(UnknownUser):
        lists.add("t-nobody", TSLA)
