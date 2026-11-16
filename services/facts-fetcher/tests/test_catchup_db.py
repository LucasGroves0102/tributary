"""Gold and check catch-up (migration 071, facts_fetcher/catchup.py) on the five companies of
conftest.py; skipped unless TEST_DATABASE_URL is set. Every test is rolled back."""

import itertools
import os

import pytest

from facts_fetcher import catchup

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

NVDA, COST, JPM, XOM, HD = "0001045810", "0000909832", "0000019617", "0000034088", "0000354950"
ALL = sorted([NVDA, COST, JPM, XOM, HD])  # conftest.py's companies
FOREVER = float("inf")


def bump(conn, function: str, version: int) -> None:
    """What a migration that changes a rule does."""
    conn.execute(f"CREATE OR REPLACE FUNCTION {function}() RETURNS integer"
                 f" LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT {version}'")


def gold(conn) -> list[tuple]:
    return conn.execute(
        "SELECT company_id, metric, period_start, period_end, value, unit, accession_no,"
        " source_tag FROM gold.quarterly_financials ORDER BY 1, 2, 4").fetchall() + \
        conn.execute("SELECT company_id, span, metric, period_end, value, unit, inputs::text"
                     " FROM gold.derived_financials ORDER BY 1, 2, 3, 4").fetchall()


def stamps(conn, kind: str) -> dict[str, tuple]:
    return {r[0]: r[1:] for r in conn.execute(
        "SELECT company_id, version, built_at, failed_version FROM ops.company_builds"
        " WHERE kind = %s", (kind,))}


def refreshed(conn) -> list[tuple]:
    return conn.execute("SELECT company_id, max(refreshed_at) FROM gold.quarterly_financials"
                        " GROUP BY 1 ORDER BY 1").fetchall()


def test_every_refresh_stamps_its_companies(loaded):
    assert {c: v[0] for c, v in stamps(loaded, "gold").items()} == dict.fromkeys(ALL, 64)
    assert {c: v[0] for c, v in stamps(loaded, "check_financials").items()} == \
        dict.fromkeys(ALL, 57)


def test_first_deploy_counts_every_company_current(loaded):
    loaded.execute("DELETE FROM ops.company_builds")  # as on the day 071 lands
    results = catchup.run(loaded, 60)
    assert [(r.kind, r.done, r.behind, r.failed) for r in results] == [
        ("check_segments", 0, 0, 0), ("check_financials", 0, 0, 0), ("gold", 0, 0, 0)]


def test_a_rules_bump_rebuilds_every_company_in_budgeted_batches(loaded):
    before = gold(loaded)
    bump(loaded, "gold.rules_version", 999)
    ticks = itertools.chain([0, 0], itertools.repeat(100))  # time for one batch only
    first = catchup.catch_up(loaded, "gold", 1, clock=lambda: next(ticks), batch=2)
    assert (first.done, first.behind, first.failed) == (2, 3, 0)
    assert {c for c, v in stamps(loaded, "gold").items() if v[0] == 999} == set(ALL[:2])
    rest = catchup.catch_up(loaded, "gold", FOREVER, batch=2)
    assert (rest.done, rest.behind) == (3, 0)
    assert gold(loaded) == before  # same rules, same gold
    assert {v[0] for v in stamps(loaded, "check_financials").values()} == {57}
    # idempotent: nothing left, nothing touched
    at = stamps(loaded, "gold")
    again = catchup.catch_up(loaded, "gold", FOREVER)
    assert (again.done, again.behind) == (0, 0) and stamps(loaded, "gold") == at
    row = loaded.execute("SELECT version, behind, done_last_day FROM ops.catchup_status"
                         " WHERE kind = 'gold'").fetchone()
    assert row == ("999", 0, 5)


def test_a_check_bump_reruns_the_check_without_rebuilding(loaded):
    at = refreshed(loaded)
    bump(loaded, "ops.check_financials_version", 999)
    bump(loaded, "ops.check_segments_version", 998)
    results = {r.kind: r for r in catchup.run(loaded, 60)}
    assert results["check_financials"].done == 5 and results["check_segments"].done == 5
    assert results["gold"].done == 0
    assert refreshed(loaded) == at  # nothing re-derived
    assert {v[0] for v in stamps(loaded, "check_financials").values()} == {999}
    assert {v[0] for v in stamps(loaded, "check_segments").values()} == {998}


def test_a_check_waits_while_its_gold_is_rebuilt(loaded):
    bump(loaded, "gold.rules_version", 999)
    bump(loaded, "ops.check_financials_version", 999)
    assert loaded.execute("SELECT count(*) FROM ops.company_backlog('check_financials')"
                          ).fetchone() == (0,)
    results = {r.kind: r for r in catchup.run(loaded, 60)}
    assert results["check_financials"].done == 0 and results["gold"].done == 5
    assert {v[0] for v in stamps(loaded, "check_financials").values()} == {999}


def test_a_company_that_raises_is_recorded_and_left_until_the_next_version(loaded):
    def build(conn, kind, companies):
        if NVDA in companies:
            raise psycopg.errors.DataError("numeric field overflow")
        catchup.build(conn, kind, companies)

    bump(loaded, "gold.rules_version", 999)
    r = catchup.catch_up(loaded, "gold", FOREVER, build=build)
    assert (r.done, r.behind, r.failed) == (4, 0, 1)
    assert stamps(loaded, "gold")[NVDA][::2] == (64, 999)  # (version, failed_version)
    assert catchup.catch_up(loaded, "gold", FOREVER, build=build).done == 0
    bump(loaded, "gold.rules_version", 1000)
    assert catchup.catch_up(loaded, "gold", FOREVER).done == 5


def test_stopping_mid_batch_leaves_whole_companies(loaded):
    before, at = gold(loaded), stamps(loaded, "gold")

    def killed(conn, kind, companies):
        with conn.transaction():
            catchup.build(conn, kind, companies)
            raise KeyboardInterrupt  # the pod is stopped before the batch commits

    bump(loaded, "gold.rules_version", 999)
    with pytest.raises(KeyboardInterrupt):
        catchup.catch_up(loaded, "gold", FOREVER, build=killed)
    assert gold(loaded) == before and stamps(loaded, "gold") == at
    assert catchup.catch_up(loaded, "gold", FOREVER).done == 5


def test_paused_measures_only(loaded):
    loaded.execute("UPDATE ops.catchup SET paused = true WHERE kind = 'gold'")
    bump(loaded, "gold.rules_version", 999)
    r = catchup.catch_up(loaded, "gold", FOREVER)
    assert (r.paused, r.done, r.behind) == (True, 0, 5)


def test_gold_writers_of_one_company_take_turns():
    with psycopg.connect(DSN) as a, \
            psycopg.connect(DSN, options="-c lock_timeout=200ms") as b:
        a.execute("SELECT gold.lock_companies(%s)", ([NVDA, COST],))
        with pytest.raises(psycopg.errors.LockNotAvailable):
            b.execute("SELECT gold.lock_companies(%s)", ([COST],))
        b.rollback()
        b.execute("SELECT gold.lock_companies(%s)", ([JPM],))  # another company: no wait
        b.rollback()
        with pytest.raises(psycopg.errors.LockNotAvailable):  # over 1,000: one lock for all
            b.execute("SELECT gold.lock_companies(%s)", ([f"x{i}" for i in range(1001)],))
        b.rollback()
        a.rollback()
        b.execute("SELECT gold.lock_companies(%s)", ([COST],))
        b.rollback()
