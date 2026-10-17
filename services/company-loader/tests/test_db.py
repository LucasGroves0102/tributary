"""Runs against a real Postgres with migrations applied; skipped unless TEST_DATABASE_URL is set.
Every test runs inside a transaction that is rolled back, so real data is never touched."""

import os
from dataclasses import replace
from datetime import date

import pytest

from company_loader.build import build
from company_loader.db import load

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

DAY1, DAY2 = date(2026, 10, 16), date(2026, 10, 17)


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        # start from empty tables inside the rolled-back transaction
        c.execute("DELETE FROM silver.company_aliases; DELETE FROM silver.company_ciks;"
                  " DELETE FROM silver.companies")
        yield c


def resolve(conn, q, as_of=None):
    sql = "SELECT company_id, matched_on, is_current FROM silver.resolve_company(%s"
    sql += ", %s)" if as_of else ")"
    return conn.execute(sql, (q, as_of) if as_of else (q,)).fetchall()


def test_resolve(conn, inputs):
    load(conn, build(**inputs), DAY1)
    assert resolve(conn, "META")[0] == ("0001326801", "ticker", True)
    assert resolve(conn, "FB")[0] == ("0001326801", "ticker", False)
    assert resolve(conn, "fb", as_of=date(2020, 1, 1))[0] == ("0001326801", "ticker", True)
    assert resolve(conn, "Facebook")[0] == ("0001326801", "name", False)
    assert resolve(conn, "google")[0][0] == "0001288776"
    assert resolve(conn, "GOOGL")[0][0] == "0001288776"
    assert resolve(conn, "XOM")[0][0] == "0000034088"
    assert resolve(conn, "Exxon Mobil")[0][0] == "0000034088"
    assert resolve(conn, "34088")[0] == ("0000034088", "cik", True)
    assert resolve(conn, "2115436")[0] == ("0000034088", "cik", True)
    assert resolve(conn, "Nvidia")[0][0] == "0001045810"
    assert resolve(conn, "no such company") == []


def test_reload_is_idempotent(conn, inputs):
    load(conn, build(**inputs), DAY1)
    counts = load(conn, build(**inputs), DAY2)
    assert counts["tickers_opened"] == counts["tickers_closed"] == 0


def test_ticker_change_is_tracked_over_time(conn, inputs):
    load(conn, build(**inputs), DAY1)
    # day 2: NFLX renames its ticker to NFLY
    renamed = [replace(t, ticker="NFLY") if t.ticker == "NFLX" else t for t in inputs["tickers"]]
    tracked = [t for t in inputs["tracked"] if t != "NFLX"] + ["NFLY"]
    counts = load(conn, build(**{**inputs, "tickers": renamed, "tracked": tracked}), DAY2)
    assert counts["tickers_closed"] == 1 and counts["tickers_opened"] == 1
    rows = conn.execute(
        "SELECT alias, valid_from, valid_to FROM silver.company_aliases"
        " WHERE company_id = '0001065280' AND alias_type = 'ticker' ORDER BY alias"
    ).fetchall()
    assert rows == [("NFLX", None, DAY2), ("NFLY", DAY2, None)]
    assert resolve(conn, "NFLX")[0] == ("0001065280", "ticker", False)


def test_new_succession_merges_companies(conn, inputs):
    # day 1: nobody has told us ExxonMobil Holdings succeeds Exxon Mobil Corp
    no_exxon = [s for s in inputs["successions"] if s.predecessor_cik != "0000034088"]
    load(conn, build(**{**inputs, "successions": no_exxon}), DAY1)
    assert resolve(conn, "XOM")[0][0] == "0002115436"

    # day 2: the succession is added; the holdings company folds into the original
    counts = load(conn, build(**inputs), DAY2)
    assert counts["companies_merged"] == 1
    assert resolve(conn, "XOM")[0][0] == "0000034088"
    assert conn.execute(
        "SELECT count(*) FROM silver.companies WHERE company_id = '0002115436'"
    ).fetchone()[0] == 0
