"""Migration 074: the statement reconciliation (ops.check_financials' statement_mismatch, over
ops.statement_comparisons) and the confidence mark (gold.company_confidence), on conftest's five
real companies. Statement lines are written as parser-index writes them (silver.statement_reads /
statement_lines); NVIDIA's FY2026 10-K prints revenue 215,938 and total assets 206,803 (millions).

Skipped unless TEST_DATABASE_URL is set; every test is rolled back."""

import os
from datetime import date

import pytest

pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="TEST_DATABASE_URL not set")

NVDA, HD, JPM = "0001045810", "0000354950", "0000019617"
TEN_K = "0001045810-26-000021"
M = 1_000_000


def lines(conn, accession=TEN_K, rows=(), form="10-K", period=date(2026, 1, 25),
          filed=date(2026, 2, 25), cik=NVDA):
    """A filing read: (line, printed value, scale, titled, section, row) per candidate."""
    conn.execute("INSERT INTO silver.statement_reads (accession_no, cik, form, period, filed,"
                 " reader, lines) VALUES (%s, %s, %s, %s, %s, 'statements-1', %s)",
                 (accession, cik, form, period, filed, len(rows)))
    for n, (line, printed, scale, titled, section, row) in enumerate(rows):
        conn.execute(
            "INSERT INTO silver.statement_lines (accession_no, line, n, label, value, scale,"
            " period_end, months, section_key, table_no, row_no, titled, loss_label)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, 12, %s, 1, %s, %s, false)",
            (accession, line, n, line, printed * (scale or 1), scale, period, section, row,
             titled))


def recheck(conn, company=NVDA):
    conn.execute("SELECT ops.recheck('check_financials', %s)", ([company],))


def findings(conn, company=NVDA):
    return conn.execute(
        "SELECT metric, period_end, detail->>'rule', detail->>'line', (detail->>'factor')::float8"
        " FROM ops.quality_findings WHERE company_id = %s AND check_name = 'statement_mismatch'"
        " ORDER BY 1", (company,)).fetchall()


def counts(conn, company=NVDA):
    return conn.execute(
        "SELECT statements_checked, statements_agreed, statements_differ_at"
        " FROM ops.quality_companies WHERE company_id = %s", (company,)).fetchone()


def mark(conn, company=NVDA):
    return conn.execute(
        "SELECT m.level, m.reasons FROM silver.companies c"
        " CROSS JOIN LATERAL gold.company_confidence(c.company_id, c.sic,"
        "   (SELECT p.latest FROM gold.latest_period(c.company_id) p)) m"
        " WHERE c.company_id = %s", (company,)).fetchone()


def test_a_statement_that_agrees_with_gold(loaded):
    lines(loaded, rows=[("revenue", 215938, M, True, "8", 5),
                        ("total_assets", 206803, M, True, "8", 20),
                        ("liabilities_and_equity", 206803, M, True, "8", 40)])
    recheck(loaded)
    assert findings(loaded) == []
    assert counts(loaded) == (3, 3, None)


def test_a_scale_slip_against_the_print_is_a_finding(loaded):
    # gold holds 215,938M, the filing's own fact; the statement prints thousands, say
    lines(loaded, rows=[("revenue", 215938, 1000, True, "8", 5)])
    recheck(loaded)
    assert findings(loaded) == [("revenue", date(2026, 1, 25), "scale", "revenue", 1000.0)]
    assert counts(loaded) == (1, 0, date(2026, 1, 25))


def test_a_different_total_assets_says_differs(loaded):
    lines(loaded, rows=[("total_assets", 199000, M, True, "8", 20)])
    recheck(loaded)
    (metric, _, rule, line, factor), = findings(loaded)
    assert (metric, rule, line) == ("total_assets", "differs", "total_assets")
    assert factor == pytest.approx(206803 / 199000, rel=1e-6)


def test_no_finding_from_an_unclear_print(loaded):
    # two current figures in one row (a column the reader couldn't tell apart), an untitled table,
    # a table outside the statements' home: all compared for agreement, never a finding
    lines(loaded, rows=[("revenue", 1, M, True, "8", 5), ("revenue", 2, M, True, "8", 5)])
    lines(loaded, accession="x-2", rows=[("total_assets", 1, M, False, "8", 1)])
    lines(loaded, accession="x-3", rows=[("total_assets", 1, M, True, "7", 1)])
    recheck(loaded)
    assert findings(loaded) == []


def test_no_finding_where_gold_holds_a_figure_this_filing_doesnt_state(loaded):
    # a filing whose facts silver doesn't have: gold's figure isn't its own (a later restatement)
    lines(loaded, accession="0001045810-26-999999",
          rows=[("total_assets", 199000, M, True, "8", 2)])
    recheck(loaded)
    assert findings(loaded) == []
    assert counts(loaded)[:2] == (0, 0)


def test_a_print_without_scale_words_agrees_at_any_scale_and_never_finds(loaded):
    lines(loaded, rows=[("revenue", 215938, None, True, "8", 5),
                        ("total_assets", 123, None, True, "8", 20)])
    recheck(loaded)
    assert findings(loaded) == []
    assert counts(loaded)[:2] == (1, 1)


def test_a_new_read_makes_the_check_due(loaded):
    due = "SELECT count(*) FROM ops.company_backlog('check_financials') WHERE company_id = %s"
    loaded.execute("UPDATE ops.company_builds SET built_at = now() - interval '1 hour'"
                   " WHERE kind = 'check_financials'")  # checked an hour ago
    assert loaded.execute(due, (NVDA,)).fetchone() == (0,)
    lines(loaded, rows=[("revenue", 215938, M, True, "8", 5)])
    loaded.execute("UPDATE silver.statement_reads SET read_at = now() - interval '1 minute'")
    assert loaded.execute(due, (NVDA,)).fetchone() == (1,)
    recheck(loaded)
    assert loaded.execute(due, (NVDA,)).fetchone() == (0,)


# the confidence mark ------------------------------------------------------------------------------

def test_nvidia_and_jpmorgan_read_high(loaded):
    lines(loaded, rows=[("revenue", 215938, M, True, "8", 5)])
    recheck(loaded)
    level, reasons = mark(loaded)
    assert level == "high"
    assert any("no error found" in r for r in reasons)
    assert any("statements agrees with the figures here" in r or "agree with" in r
               for r in reasons)
    assert mark(loaded, JPM)[0] == "high"


def test_a_shell_reads_low_with_its_reason(loaded):
    loaded.execute("UPDATE silver.companies SET sic = '6770' WHERE company_id = %s", (HD,))
    recheck(loaded, HD)
    level, reasons = mark(loaded, HD)
    assert level == "low" and reasons[0].startswith("a blank-check company")


def test_impossible_figures_read_low(loaded):
    # Rectitude before 064: revenue a sliver of the real figure, so gross profit over revenue in
    # every period (impossible_value errors, 2 or more per 1,000 figures)
    loaded.execute("UPDATE gold.quarterly_financials g SET value = r.value * 2"
                   " FROM gold.quarterly_financials r WHERE g.company_id = %s"
                   " AND g.metric = 'gross_profit' AND r.company_id = g.company_id"
                   " AND r.metric = 'revenue' AND r.period_end = g.period_end", (NVDA,))
    recheck(loaded)
    level, reasons = mark(loaded)
    assert level == "low", reasons
    assert any("errors a reader could see" in r for r in reasons), reasons


def test_a_statement_mismatch_reads_medium(loaded):
    lines(loaded, rows=[("revenue", 215938, 1000, True, "8", 5)])
    recheck(loaded)
    level, reasons = mark(loaded)
    assert level == "medium"
    assert "a filing's own statement prints another headline figure than the one here" in reasons


def test_long_past_figures_read_low(loaded):
    loaded.execute("SELECT set_config('tributary.today', '2029-06-30', true)")
    level, reasons = mark(loaded)
    assert level == "low" and any(r.startswith("no report since") for r in reasons)


def test_no_mark_for_a_company_the_caller_cant_read(loaded):
    loaded.execute("SET LOCAL ROLE query_api")
    loaded.execute("SELECT set_config('app.user_id', 'nobody', true)")
    assert mark(loaded) == (None, None)
    loaded.execute("RESET ROLE")
