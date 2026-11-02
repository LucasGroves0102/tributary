"""Reconciler against real Postgres (rolled back) and a fake OpenFGA."""

import os

import pytest

from authz_sync.main import load_catalog
from authz_sync.reconcile import Reconciler
from authz_sync.state import Desired
from tributary_authz import FgaError, Tuple

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


class FakeFga:
    def __init__(self, tuples=(), fail=False):
        self.tuples = set(tuples)
        self.calls = []
        self.reads = 0
        self.fail = fail

    def read_all(self):
        self.reads += 1
        return set(self.tuples)

    def write(self, writes=frozenset(), deletes=frozenset()):
        self.calls.append((set(writes), set(deletes)))
        if self.fail:
            raise FgaError("write failed (500)")
        self.tuples = (self.tuples - deletes) | writes


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        for table in ("user_access", "user_tiers", "public_access"):
            c.execute(f"DELETE FROM authz.{table}")  # noqa: S608
        yield c


A = Tuple("user:alice", "viewer", "company:nvda")
B = Tuple("user:bob", "viewer", "company:nvda")
READER = Tuple("user:*", "reader", "catalog:sec")


def desired(tuples=frozenset(), access=frozenset(), tiers=frozenset(), public=frozenset()):
    return Desired(set(tuples), set(access), set(tiers), set(public))


def rows(conn, table):
    return sorted(conn.execute(f"SELECT * FROM authz.{table}").fetchall())  # noqa: S608


def test_applies_only_the_difference(conn):
    fga = FakeFga([A])
    r = Reconciler(fga, conn)
    counts = r.apply(desired({B}, {("bob", "nvda", "financials")}))
    assert fga.calls == [({B}, {A})]
    assert counts == {"tuples_added": 1, "tuples_deleted": 1,
                      "access_added": 1, "access_deleted": 0,
                      "tiers_added": 0, "tiers_deleted": 0,
                      "public_added": 0, "public_deleted": 0}
    assert conn.execute("SELECT user_id FROM authz.user_access").fetchall() == [("bob",)]


def test_no_changes_no_writes(conn):
    fga = FakeFga([A])
    r = Reconciler(fga, conn)
    r.apply(desired({A}, {("alice", "nvda", "financials")}))
    fga.calls.clear()
    counts = r.apply(desired({A}, {("alice", "nvda", "financials")}))
    assert fga.calls == []
    assert set(counts.values()) == {0}


def test_revocation_removes_access_rows(conn):
    r = Reconciler(FakeFga(), conn)
    r.apply(desired(access={("alice", "nvda", "financials"), ("alice", "aapl", "financials")}))
    r.apply(desired(access={("alice", "aapl", "financials")}))
    assert conn.execute("SELECT company_id FROM authz.user_access").fetchall() == [("aapl",)]


def test_opening_and_closing_the_catalog(conn):
    fga = FakeFga()
    r = Reconciler(fga, conn)
    public = {(c, t) for c in ("nvda", "tsla") for t in ("financials", "filings")}
    tiers = {("carol", "financials"), ("carol", "filings")}
    counts = r.apply(desired({READER}, tiers=tiers, public=public))
    assert counts["public_added"] == 4 and counts["tiers_added"] == 2
    assert [row[:2] for row in rows(conn, "public_access")] == sorted(public)
    assert [row[:2] for row in rows(conn, "user_tiers")] == sorted(tiers)
    counts = r.apply(desired(tiers=tiers))
    assert counts["public_deleted"] == 4 and counts["tuples_deleted"] == 1
    assert rows(conn, "public_access") == [] and READER not in fga.tuples


def test_tracks_tuples_without_rereading_after_a_write(conn):
    fga = FakeFga([A])
    r = Reconciler(fga, conn)
    r.apply(desired({A, B}))
    r.apply(desired({B}))
    assert fga.reads == 1  # only at start
    assert fga.calls == [({B}, set()), (set(), {A})]


def test_rereads_after_a_failed_write(conn):
    fga = FakeFga([A], fail=True)
    r = Reconciler(fga, conn)
    with pytest.raises(FgaError):
        r.apply(desired({B}))
    assert fga.reads == 2


def test_large_diffs_in_one_statement_each(conn):
    r = Reconciler(FakeFga(), conn)
    public = {(f"{i:010d}", t) for i in range(6000) for t in ("financials", "filings")}
    assert r.apply(desired(public=public))["public_added"] == 12000
    assert conn.execute("SELECT count(*) FROM authz.public_access").fetchone() == (12000,)
    assert r.apply(desired())["public_deleted"] == 12000


def test_catalog_companies_have_gold_rows_or_documents(conn):
    for cid in ("9100000001", "9100000002", "9100000003"):
        conn.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                     " VALUES (%s, 'T', %s)", (cid, cid))
        conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                     (cid, cid))
    conn.execute(
        "INSERT INTO gold.quarterly_financials (company_id, metric, period_end, value, unit,"
        " value_source, source_tag, accession_no, filed, source_accessions, ingested_at)"
        " VALUES ('9100000001', 'revenue', '2026-06-30', 1, 'USD', 'reported', 't', 'a',"
        " '2026-08-01', '{}', now())")
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name,"
        " url, content_gzip, content_sha256, size_bytes)"
        " VALUES ('9100000002-26-000001', '9100000002', '8-K', '2026-02-01', 'd', 'u',"
        " '\\x00', 'x', 1)")
    got = {c for c in load_catalog(conn) if c.startswith("91000000")}
    assert got == {"9100000001", "9100000002"}  # 3 has neither
