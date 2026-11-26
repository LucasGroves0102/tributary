"""Gold reads a results release's figures (migration 165): silver.release_figures, written by
parser-index's release reader, enter gold.usable_facts after every tag. Shaped like TSMC: a 20-F
filer in New Taiwan dollars whose quarters are only in its 6-K releases. Skipped unless
TEST_DATABASE_URL is set; every test rolls back (conftest's `conn`)."""

import os
from datetime import date
from decimal import Decimal

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="TEST_DATABASE_URL not set")

CO = "9000000165"
B = Decimal(1_000_000_000)
TWENTY_F = "0009000165-26-000001"


def company(conn) -> None:
    conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, tracked, sic,"
                 " fiscal_year_end) VALUES (%s, 'Release Test Co', %s, true, '3674', '1231')",
                 (CO, CO))
    conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)", (CO, CO))
    # the 20-F's years and balance sheets (ifrs-full, TWD)
    for metric, tag, start, end, value in (
            ("revenue", "Revenue", "2025-01-01", "2025-12-31", 3810 * B),
            ("revenue", "Revenue", "2024-01-01", "2024-12-31", 2894 * B),
            ("net_income", "ProfitLossAttributableToOwnersOfParent", "2025-01-01", "2025-12-31",
             1717 * B),
            ("total_assets", "Assets", None, "2025-12-31", 7000 * B),
            ("total_assets", "Assets", None, "2024-12-31", 6690 * B)):
        conn.execute(
            "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
            " value, accession_no, form, filed, taxonomy) VALUES (%s, %s, %s, 'TWD', %s, %s, %s,"
            " %s, '20-F', '2026-04-16', 'ifrs-full')",
            (CO, metric, tag, start, end, value, TWENTY_F))


def release(conn, acc: str, filed: str, figures: list[tuple]) -> None:
    """A 6-K read: figures (metric, start, end, months, value, currency)."""
    conn.execute("INSERT INTO silver.release_reads (accession_no, cik, form, period, filed,"
                 " reader, figures) VALUES (%s, %s, '6-K', %s, %s, 'releases-1', %s)",
                 (acc, CO, max(f[2] for f in figures), filed, len(figures)))
    for i, (metric, start, end, months, value, currency) in enumerate(figures):
        conn.execute(
            "INSERT INTO silver.release_figures (accession_no, metric, period_start, period_end,"
            " months, value, currency, scale, scale_named, label, section_key, table_no, row_no,"
            " col_no, titled, attributable) VALUES (%s, %s, %s, %s, %s, %s, %s, 1000000, true,"
            " %s, 'EX-99.1', 0, %s, 1, false, false)",
            (acc, metric, start, end, months, value, currency, metric, i + 1))


def quarters(conn, metric: str) -> dict[date, tuple]:
    return {r[0]: r[1:] for r in conn.execute(
        "SELECT period_end, period_start, value, value_source, source_tag, accession_no"
        " FROM gold.quarterly_financials WHERE company_id = %s AND metric = %s",
        (CO, metric))}


@pytest.fixture
def tsmc_like(conn):
    company(conn)
    # 3Q25: nine months, for the fiscal Q4 the 20-F's year leaves
    release(conn, "0009000165-25-000300", "2025-10-16", [
        ("revenue", "2025-01-01", "2025-09-30", 9, 2763 * B, "TWD"),
        ("revenue", "2025-07-01", "2025-09-30", 3, 990 * B, "TWD")])
    # 4Q25: its quarter and year end where the 20-F's year ends: never read
    release(conn, "0009000165-26-000008", "2026-01-15", [
        ("revenue", "2025-10-01", "2025-12-31", 3, 1046 * B, "TWD"),
        ("revenue", "2025-01-01", "2025-12-31", 12, 3809 * B, "TWD"),
        ("net_income", "2025-10-01", "2025-12-31", 3, 506 * B, "TWD")])
    release(conn, "0009000165-26-000200", "2026-04-16", [
        ("revenue", "2026-01-01", "2026-03-31", 3, 1134 * B, "TWD"),
        ("net_income", "2026-01-01", "2026-03-31", 3, 572 * B, "TWD"),
        ("total_assets", None, "2026-03-31", None, 7300 * B, None)])
    release(conn, "0009000165-26-000726", "2026-10-15", [
        ("revenue", "2026-07-01", "2026-09-30", 3, Decimal("1494243000000"), "$"),
        ("net_income", "2026-07-01", "2026-09-30", 3, Decimal("763064000000"), "$"),
        ("operating_income", "2026-07-01", "2026-09-30", 3, Decimal("876445000000"), "$")])
    return conn


def test_release_quarters_reach_gold_with_their_table_and_cell(tsmc_like):
    conn = tsmc_like
    conn.execute("SELECT gold.refresh_financials(ARRAY[%s])", (CO,))
    rev = quarters(conn, "revenue")
    assert rev[date(2026, 9, 30)] == (date(2026, 7, 1), Decimal("1494243000000"), "reported",
                                      "release:EX-99.1/0/1/1", "0009000165-26-000726")
    assert rev[date(2026, 3, 31)][1] == 1134 * B
    # a bare "$" at a New Taiwan dollar company reads as its currency; net income and operating
    # income beside the release's revenue
    assert quarters(conn, "net_income")[date(2026, 9, 30)][1] == Decimal("763064000000")
    assert quarters(conn, "operating_income")[date(2026, 9, 30)][1] == Decimal("876445000000")
    assert {r[0] for r in conn.execute(
        "SELECT DISTINCT unit FROM gold.quarterly_financials WHERE company_id = %s", (CO,))
    } == {"TWD"}


def test_the_20f_year_is_never_replaced_by_a_release(tsmc_like):
    """The release's own Q4 and year end where the 20-F's year does: the fiscal Q4 is the year
    less the releases' nine months, and the year is the 20-F's."""
    conn = tsmc_like
    conn.execute("SELECT gold.refresh_financials(ARRAY[%s])", (CO,))
    q4 = quarters(conn, "revenue")[date(2025, 12, 31)]
    assert q4[:3] == (date(2025, 10, 1), 3810 * B - 2763 * B, "derived")
    assert "0009000165-26-000008" not in {r[0] for r in conn.execute(
        "SELECT accession_no FROM gold.quarterly_financials WHERE company_id = %s"
        " UNION SELECT unnest(source_accessions) FROM gold.quarterly_financials"
        " WHERE company_id = %s", (CO, CO))}
    ttm = conn.execute(
        "SELECT value FROM gold.derived_financials WHERE company_id = %s AND metric = 'revenue'"
        " AND span = 'ttm' AND period_end = '2025-12-31'", (CO,)).fetchone()
    assert ttm is None or ttm[0] == 3810 * B


def test_a_figure_gold_cant_bear_out_is_left_out(tsmc_like):
    conn = tsmc_like
    release(conn, "0009000165-26-000450", "2026-07-16", [
        # a thousandth of the 20-F's size: a scale misread
        ("revenue", "2026-04-01", "2026-06-30", 3, Decimal("933000000"), "TWD"),
        # another currency
        ("total_assets", None, "2026-06-30", None, 230 * B, "USD")])
    release(conn, "0009000165-26-000451", "2026-07-17", [
        # net income with no revenue beside it in its release
        ("net_income", "2026-04-01", "2026-06-30", 3, 398 * B, "TWD")])
    conn.execute("SELECT gold.refresh_financials(ARRAY[%s])", (CO,))
    assert date(2026, 6, 30) not in quarters(conn, "revenue")
    assert date(2026, 6, 30) not in quarters(conn, "net_income")
    assert date(2026, 6, 30) not in quarters(conn, "total_assets")
    assert quarters(conn, "total_assets")[date(2026, 3, 31)][1] == 7300 * B  # none named: TWD


def test_a_release_figure_never_competes_with_an_xbrl_one(tsmc_like):
    conn = tsmc_like
    conn.execute(
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed, taxonomy) VALUES (%s, 'revenue', 'Revenue', 'TWD',"
        " '2026-07-01', '2026-09-30', %s, '0009000165-26-000900', '6-K', '2026-11-13',"
        " 'ifrs-full')", (CO, Decimal("1494243284000")))
    conn.execute("SELECT gold.refresh_financials(ARRAY[%s])", (CO,))
    q = quarters(conn, "revenue")[date(2026, 9, 30)]
    assert q[1:4] == (Decimal("1494243284000"), "reported", "ifrs-full:Revenue")


def test_as_of_a_day_a_release_counts_from_its_filing(tsmc_like):
    conn = tsmc_like
    rows = conn.execute(
        "SELECT period_end FROM gold.usable_facts(ARRAY[%s], '2026-10-14') WHERE taxonomy ="
        " 'release'", (CO,)).fetchall()
    assert date(2026, 9, 30) not in {r[0] for r in rows}
    assert date(2026, 3, 31) in {r[0] for r in rows}
    days = {r[0] for r in conn.execute("SELECT filed FROM gold.knowledge_facts(%s)", (CO,))}
    assert {date(2026, 4, 16), date(2026, 10, 15), date(2025, 10, 16)} <= days


def test_a_release_read_makes_the_companys_gold_due(tsmc_like):
    conn = tsmc_like
    conn.execute("SELECT gold.refresh_financials(ARRAY[%s])", (CO,))
    due = "SELECT count(*) FROM ops.company_backlog('gold') WHERE company_id = %s"
    assert conn.execute(due, (CO,)).fetchone()[0] == 0
    release(conn, "0009000165-26-000999", "2026-11-20", [
        ("revenue", "2026-07-01", "2026-09-30", 3, Decimal("1494243000000"), "TWD")])
    conn.execute("UPDATE silver.release_reads SET read_at = now() + interval '1 second'"
                 " WHERE accession_no = '0009000165-26-000999'")  # read after the build
    assert conn.execute(due, (CO,)).fetchone()[0] == 1
