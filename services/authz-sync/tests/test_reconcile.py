"""Reconciler against real Postgres (rolled back) and a fake OpenFGA."""

import os

import pytest

from authz_sync.reconcile import Reconciler
from tributary_authz import Tuple

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


class FakeFga:
    def __init__(self, tuples=()):
        self.tuples = set(tuples)
        self.calls = []

    def read_all(self):
        return set(self.tuples)

    def write(self, writes=frozenset(), deletes=frozenset()):
        self.calls.append((set(writes), set(deletes)))
        self.tuples = (self.tuples - deletes) | writes


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("DELETE FROM authz.user_access")
        yield c


A = Tuple("user:alice", "viewer", "company:nvda")
B = Tuple("user:bob", "viewer", "company:nvda")


def test_applies_only_the_difference(conn):
    fga = FakeFga([A])
    r = Reconciler(fga, conn)
    counts = r.apply({B}, {("bob", "nvda", "financials")})
    assert fga.calls == [({B}, {A})]
    assert counts == {"tuples_added": 1, "tuples_deleted": 1,
                      "access_added": 1, "access_deleted": 0}
    assert conn.execute("SELECT user_id FROM authz.user_access").fetchall() == [("bob",)]


def test_no_changes_no_writes(conn):
    fga = FakeFga([A])
    r = Reconciler(fga, conn)
    r.apply({A}, {("alice", "nvda", "financials")})
    fga.calls.clear()
    counts = r.apply({A}, {("alice", "nvda", "financials")})
    assert fga.calls == []
    assert set(counts.values()) == {0}


def test_revocation_removes_access_rows(conn):
    r = Reconciler(FakeFga(), conn)
    r.apply(set(), {("alice", "nvda", "financials"), ("alice", "aapl", "financials")})
    r.apply(set(), {("alice", "aapl", "financials")})
    rows = conn.execute("SELECT company_id FROM authz.user_access").fetchall()
    assert rows == [("aapl",)]
