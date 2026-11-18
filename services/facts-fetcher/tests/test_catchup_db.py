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
    assert {c: v[0] for c, v in stamps(loaded, "gold").items()} == dict.fromkeys(ALL, 155)
    assert {c: v[0] for c, v in stamps(loaded, "check_financials").items()} == \
        dict.fromkeys(ALL, 74)


def test_first_deploy_counts_every_company_current(loaded):
    loaded.execute("DELETE FROM ops.company_builds")  # as on the day 071 lands
    bump(loaded, "gold.rules_version", 64)  # 071's rules (077, 080 and 155 bumped them since)
    results = catchup.run(loaded, 60)
    # current at 071's baseline, except the checks 072 changed: every company re-checked
    assert [(r.kind, r.done, r.behind, r.failed) for r in results] == [
        ("check_segments", 5, 0, 0), ("check_financials", 5, 0, 0), ("gold", 0, 0, 0),
        # 086: no company has a history at the deploy; every one is replayed
        ("known", 5, 0, 0)]


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
    assert {v[0] for v in stamps(loaded, "check_financials").values()} == {74}
    # idempotent: nothing left, nothing touched
    at = stamps(loaded, "gold")
    again = catchup.catch_up(loaded, "gold", FOREVER)
    assert (again.done, again.behind) == (0, 0) and stamps(loaded, "gold") == at
    row = loaded.execute("SELECT version, behind, done_last_day FROM ops.catchup_status"
                         " WHERE kind = 'gold'").fetchone()
    assert row == ("999", 0, 5)


def test_a_company_whose_ciks_changed_is_rebuilt_first(loaded):
    """076: the company-loader linked a CIK to XOM (ops.rebuild_soon): under a rules bump, with
    every other company never stamped, XOM's batch comes first."""
    loaded.execute("DELETE FROM ops.company_builds WHERE kind = 'gold'")  # hermes since 071
    assert loaded.execute("SELECT ops.rebuild_soon(ARRAY[%s])", (XOM,)).fetchone() == (1,)
    assert stamps(loaded, "gold")[XOM][:1] == (0,)
    built = []

    def build(conn, kind, ids):
        built.extend(ids)
        catchup.build(conn, kind, ids)

    catchup.catch_up(loaded, "gold", FOREVER, batch=1, build=build)
    assert built[0] == XOM and sorted(built) == ALL
    assert stamps(loaded, "gold")[XOM][0] == 155


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


def known(conn) -> list[tuple]:
    return conn.execute(
        "SELECT company_id, span, metric, period_end, known_from, known_to, value"
        " FROM gold.financials_known ORDER BY 1, 2, 3, 4, 5").fetchall()


def test_the_history_is_replayed_after_the_deploy_and_kept_by_every_build(loaded):
    """086: the 'known' kind replays every company once (its catch-up starts on deploy); a gold
    build then keeps a replayed company's history and leaves the others to the catch-up."""
    assert stamps(loaded, "known") == {} and known(loaded) == []
    r = catchup.catch_up(loaded, "known", FOREVER, batch=2)
    assert (r.done, r.behind, r.failed) == (5, 0, 0)
    assert {c: v[0] for c, v in stamps(loaded, "known").items()} == dict.fromkeys(ALL, 155)
    history = known(loaded)
    assert {h[0] for h in history} == set(ALL)
    # the open versions are gold today
    assert loaded.execute(
        "SELECT count(*) FROM gold.financials_known WHERE known_to IS NULL").fetchone()[0] == \
        loaded.execute("SELECT (SELECT count(*) FROM gold.quarterly_financials)"
                       " + (SELECT count(*) FROM gold.derived_financials)").fetchone()[0]
    # a rebuild with nothing new changes nothing; the catch-up has nothing left
    loaded.execute("SELECT gold.refresh_financials(%s)", ([XOM],))
    assert known(loaded) == history
    assert catchup.catch_up(loaded, "known", FOREVER).done == 0
    # ops.rebuild_soon (a CIK linked): gold first, then the history again
    loaded.execute("SELECT ops.rebuild_soon(ARRAY[%s])", (XOM,))
    assert loaded.execute("SELECT count(*) FROM ops.company_backlog('known')").fetchone() == (0,)
    catchup.catch_up(loaded, "gold", FOREVER)
    assert catchup.catch_up(loaded, "known", FOREVER).done == 1
    assert known(loaded) == history


def test_the_history_waits_while_its_gold_is_rebuilt(loaded):
    """A rules bump: the history is due only once the company's gold is current (as stamped)."""
    backlog = "SELECT count(*) FROM ops.company_backlog('known')"
    bump(loaded, "gold.rules_version", 999)
    assert loaded.execute(backlog).fetchone() == (0,)  # every company's gold is due first
    loaded.execute("SELECT ops.stamp_builds('gold', %s, 999)", ([NVDA, XOM],))
    assert loaded.execute(backlog).fetchone() == (2,)
    assert loaded.execute("SELECT count(*) FROM ops.company_backlog('known') b"
                          " WHERE b.company_id = ANY(%s)", ([NVDA, XOM],)).fetchone() == (2,)


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
    assert stamps(loaded, "gold")[NVDA][::2] == (155, 999)  # (version, failed_version)
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


def test_a_new_versions_backlog_is_recorded_before_the_run_works(loaded):
    """The status read during the first run after a bump says how far behind, not "current"."""
    seen = []

    def build(conn, kind, companies):
        seen.append(conn.execute("SELECT version, state, behind FROM ops.catchup_status"
                                 " WHERE kind = 'gold'").fetchone())
        catchup.build(conn, kind, companies)

    catchup.catch_up(loaded, "gold", FOREVER)  # 155: nothing to do, recorded
    bump(loaded, "gold.rules_version", 999)
    catchup.catch_up(loaded, "gold", FOREVER, build=build, batch=5)
    assert seen == [("999", "catching_up", 5)]
    # the version unchanged: no start row
    runs = loaded.execute("SELECT count(*) FROM ops.catchup_runs WHERE kind = 'gold'").fetchone()
    catchup.catch_up(loaded, "gold", FOREVER)
    assert loaded.execute("SELECT count(*) FROM ops.catchup_runs WHERE kind = 'gold'"
                          ).fetchone()[0] == runs[0] + 1


def test_the_gold_build_and_the_catch_up_keep_the_screeners_figures(loaded):
    """078: every refresh writes them; the catch-up fills companies that have none, first."""
    def rows():
        return loaded.execute("SELECT span, metric, company_id, period_end, value, unit"
                              " FROM gold.screen_figures ORDER BY 1, 2, 3").fetchall()

    built = rows()
    assert {r[2] for r in built} == set(ALL)
    live = loaded.execute(
        "SELECT k.metric, k.span, c, x.period_end, x.value, x.unit FROM unnest(%s::text[]) c"
        " CROSS JOIN ops.extreme_columns() k"
        " CROSS JOIN LATERAL gold.latest_row(c, k.span, k.metric) x ORDER BY 2, 1, 3",
        (ALL,)).fetchall()
    assert [(s, m, c, p, v, u) for m, s, c, p, v, u in live] == \
        [r for r in built if (r[0], r[1]) in {(s, m) for m, s, *_ in live}]
    loaded.execute("TRUNCATE gold.screen_companies, gold.screen_figures")
    catchup.run(loaded, 60)
    assert rows() == built
    assert loaded.execute("SELECT count(*) FROM gold.screen_backlog()").fetchone() == (0,)
