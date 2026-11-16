"""The extremes report (migration 072, facts_fetcher/extremes.py) on the five loaded companies;
skipped unless TEST_DATABASE_URL is set. Rolled back."""

import os
from decimal import Decimal

import psycopg
import pytest

from facts_fetcher import extremes

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="TEST_DATABASE_URL not set")


def report(conn, column):
    return conn.execute(
        "SELECT side, rank, company_id, value, status, findings, review FROM ops.extremes"
        " WHERE column_key = %s ORDER BY side DESC, rank", (column,)).fetchall()


def test_each_column_ranks_as_the_screener_sorts(loaded):
    flagged = extremes.refresh(loaded, n=2)
    rows = report(loaded, "revenue_ttm")
    top = [r for r in rows if r[0] == "top"]
    bottom = [r for r in rows if r[0] == "bottom"]
    assert [r[1] for r in top] == [1, 2] and [r[1] for r in bottom] == [1, 2]
    assert top[0][3] >= top[1][3] and bottom[0][3] <= bottom[1][3]
    # the screener's value: gold.latest_reading, readings applied
    want = loaded.execute(
        "SELECT x.value FROM silver.companies c, gold.latest_period(c.company_id) p,"
        " gold.latest_reading(c.company_id, 'ttm', 'revenue', c.sic, p.latest) x"
        " WHERE x.status IS NULL OR x.status = 'behind'"
        " ORDER BY x.value DESC NULLS LAST LIMIT 1").fetchone()
    assert top[0][3] == want[0]
    assert flagged == loaded.execute("SELECT count(*) FROM ops.extremes WHERE review").fetchone()[0]
    # every row either names what explains it or asks for review, never both
    assert all(r[6] == (r[5] == []) for r in rows)


def test_a_finding_on_the_metric_or_its_inputs_explains_an_extreme(loaded):
    extremes.refresh(loaded, n=1)
    side, _, company, _, _, findings, review = report(loaded, "gross_margin_ttm")[0]
    period = loaded.execute("SELECT period_end FROM ops.extremes WHERE column_key ="
                            " 'gross_margin_ttm' AND side = %s AND rank = 1", (side,)).fetchone()[0]
    # a finding on revenue (an input of the margin) in the year to its period
    loaded.execute("DELETE FROM ops.quality_findings WHERE company_id = %s", (company,))
    run = loaded.execute("INSERT INTO ops.quality_runs (companies) VALUES (1) RETURNING run_id"
                         ).fetchone()[0]
    loaded.execute("INSERT INTO ops.quality_findings (company_id, check_name, metric, period_end,"
                   " severity, run_id) VALUES (%s, 'outlier', 'revenue', %s, 'warning', %s)",
                   (company, period, run))
    extremes.refresh(loaded, n=1)
    row = [r for r in report(loaded, "gross_margin_ttm") if r[0] == side][0]
    assert "outlier" in row[5] and row[6] is False


def test_a_figure_in_line_with_its_previous_one_is_explained(loaded):
    """Amazon's revenue is large, not wrong: same sign and within 3x of the company's own figure a
    period earlier (a year for TTM) reads in_line; a jump past that is left to explain."""
    extremes.refresh(loaded, n=2)
    rows = loaded.execute("SELECT value, prev_value, findings FROM ops.extremes"
                          " WHERE prev_value IS NOT NULL").fetchall()
    assert rows
    for value, prev, findings in rows:
        close = (value > 0) == (prev > 0) and (value < 0) == (prev < 0) and (
            value == 0 or max(abs(value), abs(prev)) <= 3 * min(abs(value), abs(prev)))
        assert ("in_line" in findings) == close
    assert any("in_line" in f for _, _, f in rows)


def test_ops_reads_the_report_and_the_cli_prints_it(loaded, capsys, monkeypatch):
    extremes.refresh(loaded, n=1)
    loaded.execute("SET LOCAL ROLE ops_reader")
    names = loaded.execute("SELECT DISTINCT company FROM ops.extremes_report").fetchall()
    assert names
    with pytest.raises(psycopg.errors.InsufficientPrivilege), loaded.transaction():
        loaded.execute("SELECT 1 FROM ops.extremes LIMIT 1")
    loaded.execute("RESET ROLE")
    found = extremes.rows(loaded, everything=True, column="revenue_ttm")
    text = extremes.render(found)
    assert "revenue_ttm" in text and len(text.splitlines()) == 2


def test_figures_read_compactly():
    assert extremes.figure(Decimal("130497000000"), "USD") == "130B USD"
    assert extremes.figure(Decimal("0.7512"), "ratio") == "0.7512"
    assert extremes.figure(Decimal("-2500000"), "USD") == "-2.5M USD"
