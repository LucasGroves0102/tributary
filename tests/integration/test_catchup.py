"""The catch-up's state and status (migration 071). The workers have their own tests
(services/parser/tests/test_catchup_db.py, services/facts-fetcher/tests/test_catchup_db.py).
Skipped unless TEST_DATABASE_URL is set; every test is rolled back."""

import os
from datetime import timedelta
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE ops.catchup_runs")
        c.execute("UPDATE ops.catchup SET paused = false, version = NULL, started_at = NULL")
        yield c


def run(conn, kind, hours_ago, behind, done, version="9", in_flight=0, failed=0):
    conn.execute("INSERT INTO ops.catchup_runs (kind, run_at, version, behind, in_flight, failed,"
                 " done) VALUES (%s, now() - %s * interval '1 hour', %s, %s, %s, %s, %s)",
                 (kind, hours_ago, version, behind, in_flight, failed, done))


def status(conn, kind):
    return conn.execute(
        "SELECT behind, in_flight, done_last_day, eta, progress_at, measured_at"
        " FROM ops.catchup_status WHERE kind = %s", (kind,)).fetchone()


def test_versions_live_with_their_rules():
    with psycopg.connect(DSN) as c:
        # 072 bumped both company checks (the catch-up re-checks every company) and added
        # the parser-output checks, per document; 074 the statement reconciliation; 077 gold's
        # net income rules, 080 its revenue industry tags, 155 its debt, citations and gross
        # profit (every company rebuilt); 095 the parser-output checks' keys for 20-F, 40-F and
        # 6-K (every document re-checked)
        assert c.execute("SELECT gold.rules_version(), ops.check_financials_version(),"
                         " ops.check_segments_version(), ops.check_sections_version()"
                         ).fetchone() == (155, 74, 72, 95)
        assert c.execute("SELECT kind, baseline FROM ops.catchup WHERE baseline IS NOT NULL"
                         " ORDER BY 1").fetchall() == [
            ("check_financials", 57), ("check_segments", 70), ("gold", 64),
            ("known", 0)]  # 086: no company has a history at the deploy
        # 071 ships the parser kind paused (the owner's call), every other kind running
        assert c.execute("SELECT kind FROM ops.catchup WHERE paused").fetchall() == [("parser",)]
        assert c.execute("SELECT ops.parser_version_number('rust-7'),"
                         " ops.parser_version_number('python-12'),"
                         " ops.parser_version_number('odd')").fetchone() == (7, 12, 0)


def test_status_reads_the_latest_run_the_last_days_pace_and_eta(conn):
    conn.execute("SELECT * FROM ops.catchup_begin('gold', '9')")
    conn.execute("UPDATE ops.catchup SET started_at = now() - interval '2 days'"
                 " WHERE kind = 'gold'")
    run(conn, "gold", 30, behind=500, done=100)  # over a day ago: not in the pace
    run(conn, "gold", 20, behind=400, done=100)
    run(conn, "gold", 2, behind=300, done=100)
    behind, in_flight, done, eta, progress, measured = status(conn, "gold")
    assert (behind, in_flight, done) == (300, 0, 200)
    assert eta == timedelta(days=1.5)  # 300 left at 200 a day
    assert progress == measured  # the latest run did something


def test_a_kind_that_stops_moving_keeps_its_last_progress(conn):
    conn.execute("SELECT * FROM ops.catchup_begin('parser', '7')")
    conn.execute("UPDATE ops.catchup SET started_at = now() - interval '3 days'"
                 " WHERE kind = 'parser'")
    run(conn, "parser", 50, behind=1000, done=3000, version="7")
    run(conn, "parser", 1, behind=1000, done=0, version="7", in_flight=2000)
    behind, in_flight, done, eta, progress, _ = status(conn, "parser")
    assert (behind, in_flight, done, eta) == (1000, 2000, 0, None)
    assert conn.execute("SELECT now() - interval '50 hours' = %s", (progress,)).fetchone()[0]


def test_a_new_version_restarts_the_clock(conn):
    first = conn.execute("SELECT started_at FROM ops.catchup_begin('passages', 'a')").fetchone()
    same = conn.execute("SELECT started_at FROM ops.catchup_begin('passages', 'a')").fetchone()
    new = conn.execute("SELECT started_at FROM ops.catchup_begin('passages', 'b')").fetchone()
    assert first == same and new[0] > first[0]


def test_nothing_behind_reads_done(conn):
    run(conn, "diffs", 1, behind=0, done=0)
    assert status(conn, "diffs")[3] == timedelta(0)


def test_state_tells_paused_from_stalled(conn):
    for kind in ("parser", "gold"):
        conn.execute("SELECT * FROM ops.catchup_begin(%s, '9')", (kind,))
    conn.execute("UPDATE ops.catchup SET started_at = now() - interval '3 days'"
                 " WHERE kind IN ('parser', 'gold')")
    run(conn, "parser", 1, behind=500, done=0)  # no progress for days...
    run(conn, "gold", 1, behind=500, done=0)
    conn.execute("UPDATE ops.catchup SET paused = (kind = 'parser')")
    states = dict(conn.execute("SELECT kind, state FROM ops.catchup_status"))
    assert states["parser"] == "paused"  # ... but paused by hand: not stalled
    assert states["gold"] == "stalled"
    assert states["diffs"] == "current"
    run(conn, "gold", 0, behind=499, done=1)
    assert dict(conn.execute("SELECT kind, state FROM ops.catchup_status"))["gold"] == \
        "catching_up"


def test_ops_reader_sees_the_status_only(conn):
    run(conn, "gold", 1, behind=3, done=1)
    conn.execute("SET LOCAL ROLE ops_reader")
    assert conn.execute("SELECT count(*) FROM ops.catchup_status").fetchone() == (9,)
    for table in ("ops.catchup", "ops.catchup_runs", "ops.company_builds"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1")


def test_the_exporter_query_runs_as_ops_reader(conn):
    yaml = pytest.importorskip("yaml")
    path = (Path(__file__).parents[2] / "deploy" / "helm" / "monitoring" / "files"
            / "ops-exporter-queries.yaml")
    query = yaml.safe_load(path.read_text())["tributary_catchup"]["query"]
    run(conn, "gold", 1, behind=3, done=1)
    conn.execute("SET LOCAL ROLE ops_reader")
    rows = {r[0]: r[1:] for r in conn.execute(query)}
    assert set(rows) == {"parser", "passages", "diffs", "gold", "check_financials",
                         "check_segments", "check_sections", "statements", "known"}
    assert rows["gold"][:5] == (3, 0, 0, 1, 0)
    assert rows["parser"][4] == 0  # the fixture unpaused it; paused = 1 keeps the alert quiet
    assert all(v is not None for r in rows.values() for v in r)
