# ruff: noqa: F811  (the tests take the imported fixture `conn`)
"""The screener's stored figures (migration 078) read exactly as gold.latest_reading does, over
test_readings_db's companies plus a few more shapes: every column of every company, as query_api
under RLS, at several dates (stale is read time), and whole screens through the API with nothing
stored and then everything stored. Skipped unless TEST_DATABASE_URL is set; rolled back."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from .test_readings_db import (
    LATEST,
    LOCKED,
    NORMAL,
    USER,
    conn,  # noqa: F401  (test_readings_db's fixture, by its name)
    derived,
    gross_sales,
    reported,
    screen,
)

psycopg = pytest.importorskip("psycopg")

YEN, FOREIGN, RETAIL, FUTURE, QUIET = (f"92000001{i:02d}" for i in range(1, 6))
TODAYS = ["2026-11-15", str(LATEST + timedelta(days=271)), str(LATEST + timedelta(days=400)),
          str(LATEST + timedelta(days=501))]
SCREENS = [
    {},
    {"sort": "-operating_margin_ttm"},
    {"sort": "gross_margin_ttm", "where": ["gross_margin_ttm:gte:0.1"]},
    {"sort": "-revenue_growth_ttm", "revenue_band": "1b-10b"},
    {"sort": "debt_to_equity_q", "columns": ["revenue_ttm", "debt_to_equity_q", "net_debt_q",
                                            "total_assets_q", "stockholders_equity_q"]},
    {"sort": "name", "columns": ["gross_margin_ttm", "revenue_q", "cash_ttm"]},
    {"sort": "-net_debt_q", "access": "financials", "limit": 3, "offset": 1},
    {"q": "co", "sort": "-revenue_ttm"},
]


def more_shapes(conn):
    """A yen filer, an annual 20-F filer, a warehouse club (no gross profit computed), a filer
    with a future-dated period (read live from that day), one whose figures stopped, and
    test_readings_db's gross-sales venue, miner and maker (083)."""
    for cid, name, sic in ((YEN, "Yen Co", "3711"), (FOREIGN, "Foreign Co", "2834"),
                           (RETAIL, "Club Co", "5331"), (FUTURE, "Future Co", "7372"),
                           (QUIET, "Quiet Co", "3674")):
        conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, sic)"
                     " VALUES (%s, %s, %s, %s)", (cid, name, cid, sic))
        conn.execute("INSERT INTO authz.public_access (company_id, tier)"
                     " VALUES (%s, 'financials')", (cid,))
    reported(conn, YEN, "revenue", LATEST, 9_000_000_000_000, unit="JPY")
    derived(conn, YEN, "gross_margin", LATEST, Decimal("0.3"), {"revenue": 9e12})
    derived(conn, YEN, "revenue", LATEST, 36_000_000_000_000, {"quarters": []}, unit="JPY")
    year_end = date(2025, 12, 31)
    reported(conn, FOREIGN, "total_assets", year_end, 50_000_000_000)
    for metric, value in (("revenue", 9_000_000_000), ("net_income", 2_000_000_000)):
        conn.execute(
            "INSERT INTO gold.derived_financials (company_id, span, metric, period_start,"
            " period_end, value, unit, value_source, inputs, accession_no, form, filed,"
            " source_accessions, ingested_at) VALUES (%s, 'ttm', %s, %s, %s, %s, 'USD', 'ttm',"
            " '{}', '0000000000-26-000020', '20-F', '2026-04-01', '{0000000000-26-000020}',"
            " now())", (FOREIGN, metric, year_end - timedelta(days=364), year_end, value))
    reported(conn, RETAIL, "revenue", LATEST, 70_000_000_000)
    reported(conn, RETAIL, "cost_of_revenue", LATEST, 60_000_000_000)
    reported(conn, FUTURE, "revenue", LATEST, 3_000_000)
    reported(conn, FUTURE, "stockholders_equity", LATEST, -2_000_000)
    reported(conn, FUTURE, "stockholders_equity", date.today() + timedelta(days=30), 5_000_000)
    derived(conn, FUTURE, "debt_to_equity", LATEST, Decimal("4"), {}, span="quarter")
    for end in (date(2025, 3, 31), date(2025, 6, 30)):
        reported(conn, QUIET, "revenue", end, 400_000_000)
        reported(conn, QUIET, "net_income", end, 40_000_000)
    derived(conn, QUIET, "net_margin", date(2025, 6, 30), Decimal("0.1"), {"revenue": 1.6e9})
    gross_sales(conn)  # 083: a venue's revenue reads the stored cost of revenue beside it


def companies(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT company_id FROM silver.companies"
                                       " WHERE company_id LIKE '92%' ORDER BY 1")]


def as_reader(conn, sql, params=()):
    with conn.transaction(force_rollback=True), conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute("SELECT set_config('app.user_id', %s, true)", (USER,))
        cur.execute(sql, params)
        return cur.fetchall()


EVERY_READING = """
    SELECT c.company_id, k.column_key, x.*
    FROM silver.companies c
    CROSS JOIN ops.extreme_columns() k
    LEFT JOIN LATERAL gold.latest_period(c.company_id) p ON true
    CROSS JOIN LATERAL gold.latest_reading(c.company_id, k.span, k.metric, c.sic, p.latest) x
    WHERE c.company_id = ANY(%s)
    ORDER BY 1, 2"""
EVERY_STORED = """
    SELECT c.company_id, k.column_key, x.*
    FROM silver.companies c
    CROSS JOIN ops.extreme_columns() k
    CROSS JOIN LATERAL gold.screen_company(c.company_id) s
    CROSS JOIN LATERAL gold.screen_reading(c.company_id, k.span, k.metric, c.sic, s.latest,
                                           s.cached, s.equity, s.currency, s.recent) x
    WHERE c.company_id = ANY(%s)
    ORDER BY 1, 2"""


def test_every_stored_reading_equals_the_live_one_at_every_date(conn):
    more_shapes(conn)
    ids = companies(conn)
    conn.execute("SELECT gold.refresh_screen(%s)", (ids,))
    assert as_reader(conn, "SELECT count(*) FILTER (WHERE cached) FROM silver.companies c,"
                     " gold.screen_company(c.company_id) s WHERE c.company_id = ANY(%s)",
                     (ids,)) == [(len(ids) - 1,)]  # LOCKED: no read grant, so read live (nothing)
    for today in TODAYS:
        conn.execute("SELECT set_config('tributary.today', %s, false)", (today,))
        live = as_reader(conn, EVERY_READING, (ids,))
        stored = as_reader(conn, EVERY_STORED, (ids,))
        assert len(live) == len(ids) * len(stored) // len(ids) and stored == live, today
        statuses = {r[7] for r in live}
        if today == TODAYS[0]:
            assert {"not_applicable", "other_currency", "not_computed", "not_meaningful", "old",
                    "behind"} <= statuses, statuses
        else:
            assert "stale" in statuses, today


def screens(conn, token) -> list[dict]:
    return [{k: v for k, v in screen(conn, token, **params).items() if k != "as_of"}
            for params in SCREENS]


def test_whole_screens_read_the_same_stored_or_live(conn, token):
    more_shapes(conn)
    live = screens(conn, token)
    assert sum(len(s["rows"]) for s in live) > 40
    conn.execute("SELECT gold.refresh_screen(%s)", (companies(conn),))
    assert screens(conn, token) == live
    # and with the figures past their stale dates
    conn.execute("SELECT set_config('tributary.today', %s, false)", (TODAYS[-1],))
    stored = screens(conn, token)
    conn.execute("TRUNCATE gold.screen_companies, gold.screen_figures")
    assert screens(conn, token) == stored


def test_a_company_past_until_and_one_never_stored_are_read_live(conn):
    more_shapes(conn)
    conn.execute("SELECT gold.refresh_screen(%s)", ([FUTURE, NORMAL],))
    until = conn.execute("SELECT until FROM gold.screen_companies WHERE company_id = %s",
                         (FUTURE,)).fetchone()[0]
    assert until == date.today() + timedelta(days=30)
    assert conn.execute("SELECT until FROM gold.screen_companies WHERE company_id = %s",
                        (NORMAL,)).fetchone() == (None,)
    # the day its future period begins (as if today had reached it): read live
    conn.execute("UPDATE gold.screen_companies SET until = current_date WHERE company_id = %s",
                 (FUTURE,))
    cached = dict(as_reader(conn, "SELECT c, s.cached FROM unnest(%s::text[]) c,"
                            " gold.screen_company(c) s", ([FUTURE, NORMAL, YEN],)))
    assert cached == {FUTURE: False, NORMAL: True, YEN: False}
    ids = [FUTURE, NORMAL, YEN]
    assert as_reader(conn, EVERY_STORED, (ids,)) == as_reader(conn, EVERY_READING, (ids,))


def test_the_gold_build_keeps_them_and_unchanged_rows_stay_put(conn):
    more_shapes(conn)
    conn.execute("SELECT gold.refresh_screen(%s)", ([NORMAL],))
    before = conn.execute("SELECT ctid, span, metric FROM gold.screen_figures"
                          " WHERE company_id = %s ORDER BY 2, 3", (NORMAL,)).fetchall()
    conn.execute("SELECT gold.refresh_screen(%s)", ([NORMAL],))
    assert conn.execute("SELECT ctid, span, metric FROM gold.screen_figures"
                        " WHERE company_id = %s ORDER BY 2, 3", (NORMAL,)).fetchall() == before
    # a figure gone from gold goes from the screen; a new one arrives
    conn.execute("DELETE FROM gold.derived_financials WHERE company_id = %s"
                 " AND metric = 'free_cash_flow'", (NORMAL,))
    reported(conn, NORMAL, "cash", LATEST, 7)
    conn.execute("SELECT gold.refresh_screen(%s)", ([NORMAL],))
    keys = {(s, m) for s, m in conn.execute("SELECT span, metric FROM gold.screen_figures"
                                             " WHERE company_id = %s", (NORMAL,))}
    assert ("ttm", "free_cash_flow") not in keys
    assert {("quarter", "cash"), ("ttm", "cash")} <= keys  # a balance's quarter is its TTM too
    assert conn.execute("SELECT reported_metrics FROM gold.screen_companies"
                        " WHERE company_id = %s", (NORMAL,)).fetchone()[0] == sorted(
        {"cash", "revenue", "stockholders_equity"})


def test_the_backlog_fills_every_company_once(conn):
    more_shapes(conn)
    conn.execute("TRUNCATE gold.screen_companies, gold.screen_figures")
    total = conn.execute("SELECT count(*) FROM silver.companies").fetchone()[0]
    done = 0
    while n := conn.execute("SELECT gold.refresh_screen_backlog(3)").fetchone()[0]:
        done += n
    assert done == total
    assert conn.execute("SELECT count(*) FROM gold.screen_backlog()").fetchone() == (0,)


def test_coverage_reads_the_stored_lists_or_the_view(conn, token):
    """The screener's coverage test: the stored lists where the caller reads the company, else
    gold.financials_coverage (owner rights, unchanged: the public catalog's membership reads it)."""
    more_shapes(conn)

    def listed():
        # a name search: no blank-row test, so coverage alone decides who is listed
        body = screen(conn, token, q="co", columns=["revenue_q"], sort="name", limit=200)
        return [r["company_id"] for r in body["rows"]]

    live = listed()
    conn.execute("SELECT gold.refresh_screen(%s)", (companies(conn),))
    assert listed() == live
    assert FOREIGN in live and LOCKED in live  # metadata rows: every company with quarters
    view = conn.execute("SELECT pg_get_viewdef('gold.financials_coverage'::regclass)").fetchone()[0]
    assert "screen_companies" not in view
