# ruff: noqa: F811  (the tests take the imported fixture `conn`)
"""A watched screen evaluated as its owner over real Postgres (watch_job.py): the screener's SQL
as query_api with the owner's app.user_id (RLS) and the owner's id list, so a company the owner
can't read is never in their screen though another user reads it, with the catalog closed and
open; the members equal what /v1/screener lists for the same saved screen; a line compares the
screener's readings (n/m, old never; behind does) and crosses end to end; and the nightly
figures' landing time (tributary 110). Skipped unless TEST_DATABASE_URL is set; rolled back."""

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from query_api.authorizer import Access, readable_from
from query_api.store import Store
from query_api.watch_job import check, evaluate

from .test_readings_db import (
    ABSURD,
    GENUINE,
    LOCKED,
    NORMAL,
    STALE,
    TIERS,
    OneConnection,
    api,
    conn,  # noqa: F401  (test_readings_db's fixture, by its name)
)

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="TEST_DATABASE_URL not set")

A, B = "t-watch-a", "t-watch-b"
STATE = {"margin": 10}  # operating margin at least 10%: NORMAL 20%, GENUINE 78%, STALE 30%


def watch(lines=(), state=None, **kw):
    return {"watch_id": "w", "item_id": "i", "name": "Margins", "source": "screen",
            "kind": "screen", "state": STATE if state is None else state, "columns": [],
            "lines": list(lines), "entering": True, "leaving": True, "basis": None,
            "members": None, "near": None, **kw}


def closed(conn):
    """The catalog closed: A reads NORMAL alone, B reads NORMAL and GENUINE (authz-sync's rows
    for RLS; the matching Access for layer 1)."""
    conn.execute("DELETE FROM authz.public_access")
    conn.execute("DELETE FROM authz.user_tiers")
    for user, ids in ((A, [NORMAL]), (B, [NORMAL, GENUINE])):
        for cid in ids:
            conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier)"
                         " VALUES (%s, %s, 'financials')", (user, cid))
    return {A: Access(frozenset(), {NORMAL: {"financials"}}),
            B: Access(frozenset(), {NORMAL: {"financials"}, GENUINE: {"financials"}})}


def opened(conn):
    for user in (A, B):
        conn.execute("INSERT INTO authz.user_tiers (user_id, tier) VALUES (%s, 'financials')",
                     (user,))
    return {u: Access(TIERS, {}) for u in (A, B)}


def members(conn, user, access, w=None):
    store = Store(OneConnection(conn))
    readable = readable_from(access, lambda: store.catalog_companies(user))
    return evaluate(store, user, readable, w or watch(), {})


def test_closed_catalog_the_owner_never_sees_what_only_another_reads(conn):
    access = closed(conn)
    a, b = members(conn, A, access[A]), members(conn, B, access[B])
    assert a.members == [NORMAL]
    assert b.members == sorted([NORMAL, GENUINE])
    # A asked with B's list would still get only what RLS lets A read: values, so no margin
    lying = members(conn, A, access[B])
    assert GENUINE not in lying.members


def test_open_catalog_every_reader_sees_the_catalog_and_nothing_outside_it(conn):
    access = opened(conn)
    a, b = members(conn, A, access[A]), members(conn, B, access[B])
    assert a.members == b.members
    assert {NORMAL, GENUINE, STALE} <= set(a.members) and LOCKED not in a.members


@pytest.mark.parametrize("make", [closed, opened])
def test_members_are_what_companies_lists_for_the_same_saved_screen(conn, token, make):
    """The web's params for {margin: 10} (screenParams) with access=financials: /v1/screener."""
    access = make(conn)

    class Auth:
        def can(self, user, tier, company):
            return company in readable_from(access[user], lambda: frozenset())

        def access(self, user):
            return access[user]

    for user in (A, B):
        r = api(conn, Auth()).get("/v1/screener", params={
            "where": ["operating_margin_ttm:gte:0.1"], "access": "financials", "limit": 500,
            "columns": ["revenue_ttm", "revenue_growth_ttm", "gross_margin_ttm",
                        "operating_margin_ttm", "free_cash_flow_ttm", "net_debt_q",
                        "debt_to_equity_q"]}, headers=token(user))
        assert r.status_code == 200, r.text
        listed = sorted(row["company_id"] for row in r.json()["rows"])
        assert members(conn, user, access[user]).members == listed


def test_lines_read_the_screeners_readings(conn):
    """n/m (ABSURD's gross margin) and old (STALE's net debt) read as no value; STALE's
    operating margin is a quarter behind and compares."""
    access = opened(conn)
    lines = [{"column": "gross_margin_ttm", "label": "GM", "direction": "above",
              "threshold": "0.5", "company_id": None},
             {"column": "net_debt_q", "label": "Net debt", "direction": "below",
              "threshold": "0", "company_id": STALE},
             {"column": "operating_margin_ttm", "label": "OM", "direction": "above",
              "threshold": "0.25", "company_id": STALE}]
    snap = members(conn, A, access[A], watch(lines, state={}))
    assert ABSURD in snap.members and snap.values[0][ABSURD][0] is None
    assert snap.values[0][NORMAL][0] == Decimal("0.7")
    assert snap.values[1][STALE][0] is None
    assert snap.values[2][STALE][0] == Decimal("0.3")


def test_a_line_crosses_end_to_end_with_the_first_check_silent(conn):
    access = opened(conn)
    store = Store(OneConnection(conn))
    readable = readable_from(access[A], lambda: store.catalog_companies(A))
    line = {"column": "operating_margin_ttm", "label": "OM", "direction": "above",
            "threshold": "0.25", "company_id": None}
    w = watch([line], entering=False, leaving=False)
    first = check(store, A, access[A], readable, w, item=lambda u, i: None, datasets=None,
                  landed=None)
    assert first["outcome"] == "baseline" and first["detail"] is None
    assert NORMAL in first["near"]["0"]
    conn.execute("UPDATE gold.derived_financials SET value = 0.31 WHERE company_id = %s"
                 " AND metric = 'operating_margin'", (NORMAL,))
    w.update(basis=first["basis"], members=first["members"], near=first["near"])
    second = check(store, A, access[A], readable, w, item=lambda u, i: None, datasets=None,
                   landed=None)
    assert second["outcome"] == "ok"
    assert second["detail"]["crossed"][0]["companies"] == [
        {"company_id": NORMAL, "value": "0.31"}]


def test_the_figures_landing_time_is_the_nightly_coverage_run(conn):
    store = Store(OneConnection(conn))
    conn.execute("DELETE FROM ops.coverage_runs")
    assert store.figures_landed_at() is None
    at = datetime(2026, 11, 18, 10, 31, tzinfo=UTC)
    conn.execute("INSERT INTO ops.coverage_runs (run_at, listed, gaps) VALUES (%s, 1, 0),"
                 " (%s, 1, 0)", (at, datetime(2026, 11, 17, 10, 0, tzinfo=UTC)))
    assert store.figures_landed_at() == at
