"""Calendar labels, TTM, derived metrics and data-quality checks over gold
(db/migrations/014_gold_calendar.sql). Skipped unless TEST_DATABASE_URL is set; every test is
rolled back."""

import os
from datetime import date, timedelta

import pytest

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

PEP, CAL = "9000000201", "9000000202"  # 12/12/12/16-week quarters; calendar quarters
B = 1_000_000_000

# PepsiCo's 2024 and 2025 fiscal quarters
PEP_QUARTERS = [
    (date(2023, 12, 31), date(2024, 3, 23)), (date(2024, 3, 24), date(2024, 6, 15)),
    (date(2024, 6, 16), date(2024, 9, 7)), (date(2024, 9, 8), date(2024, 12, 28)),
    (date(2024, 12, 29), date(2025, 3, 22)), (date(2025, 3, 23), date(2025, 6, 14)),
    (date(2025, 6, 15), date(2025, 9, 6)), (date(2025, 9, 7), date(2025, 12, 27)),
]
PEP_REVENUE = [18, 22, 23, 28, 18, 23, 24, 29]


def acc(company_id, end):
    return f"{company_id}-{end:%y%m%d}"


def put(conn, company_id, metric, end, value, start=None, unit="USD", accession=None):
    a = accession or acc(company_id, end)
    conn.execute(
        "INSERT INTO gold.quarterly_financials (company_id, metric, period_start, period_end,"
        " value, unit, value_source, source_tag, accession_no, form, filed, source_accessions,"
        " ingested_at) VALUES (%s, %s, %s, %s, %s, %s, 'reported', 'us-gaap:X', %s, '10-Q',"
        " %s, ARRAY[%s], now())",
        (company_id, metric, start, end, value, unit, a, end + timedelta(days=30), a))


def refresh(conn, *company_ids):
    conn.execute("SELECT gold.refresh_derived_financials(%s::text[])", (list(company_ids),))
    conn.execute("SELECT ops.check_financials(%s::text[])", (list(company_ids),))


def rows(conn, company_id, metric, span="quarter", **where):
    sql = ("SELECT period_end, value, fiscal_year, fiscal_quarter, calendar_year,"
           " calendar_quarter, calendar_primary, inputs, accession_no, source_accessions,"
           " period_start FROM gold.financials WHERE company_id = %s AND metric = %s"
           " AND span = %s")
    params = [company_id, metric, span]
    for k, v in where.items():
        sql += f" AND {k} = %s"
        params.append(v)
    return conn.execute(sql + " ORDER BY period_end", params).fetchall()


def findings(conn, company_id, check):
    return conn.execute(
        "SELECT metric, period_end, detail FROM ops.quality_findings"
        " WHERE company_id = %s AND check_name = %s ORDER BY period_end, metric",
        (company_id, check)).fetchall()


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        for cid, fye in ((PEP, "1227"), (CAL, "1231")):
            c.execute("INSERT INTO silver.companies (company_id, name, current_cik,"
                      " fiscal_year_end) VALUES (%s, %s, %s, %s)", (cid, f"Test {cid}", cid, fye))
            c.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                      (cid, cid))
        for (start, end), revenue in zip(PEP_QUARTERS, PEP_REVENUE, strict=True):
            put(c, PEP, "revenue", end, revenue * B, start)
            put(c, PEP, "gross_profit", end, revenue * B // 2, start)
            put(c, PEP, "net_income", end, B, start)
            put(c, PEP, "operating_cash_flow", end, 3 * B, start)
            put(c, PEP, "capex", end, B, start, accession=f"{PEP}-capex-{end:%y%m%d}")
            put(c, PEP, "eps_diluted", end, 2, start, unit="USD/shares")
            put(c, PEP, "stockholders_equity", end, 20 * B)
            put(c, PEP, "long_term_debt", end, 30 * B)
            put(c, PEP, "cash", end, 10 * B)
            put(c, PEP, "current_assets", end, 25 * B)
            put(c, PEP, "current_liabilities", end, 20 * B)
            put(c, PEP, "total_assets", end, 100 * B)
        yield c


@pytest.mark.parametrize("period_end, fye, fiscal, calendar", [
    (date(2025, 9, 6), "1227", (2025, 3), date(2025, 7, 1)),     # PepsiCo Q3, 12 weeks
    (date(2025, 12, 27), "1227", (2025, 4), date(2025, 10, 1)),  # PepsiCo Q4, 16 weeks
    (date(2024, 12, 28), "0927", (2025, 1), date(2024, 10, 1)),  # Apple Q1
    (date(2025, 9, 27), "0927", (2025, 4), date(2025, 7, 1)),    # Apple Q4
    (date(2023, 9, 30), "0924", (2023, 4), date(2023, 7, 1)),    # 53-week year past nominal
    (date(2025, 5, 11), "0831", (2025, 3), date(2025, 4, 1)),    # Costco Q3, 12 weeks
    (date(2023, 5, 7), "0831", (2023, 3), date(2023, 4, 1)),
    (date(2025, 8, 31), "0831", (2025, 4), date(2025, 7, 1)),    # Costco Q4, 16 weeks
    (date(2025, 4, 30), "0131", (2026, 1), date(2025, 1, 1)),    # Walmart Q1
    (date(2025, 1, 26), "0131", (2025, 4), date(2024, 10, 1)),   # NVIDIA Q4
    (date(2022, 1, 1), "0101", (2021, 4), date(2021, 10, 1)),    # year ending in January
    (date(2024, 2, 29), "0229", (2024, 4), date(2024, 1, 1)),
])
def test_fiscal_and_calendar_labels(conn, period_end, fye, fiscal, calendar):
    fye_date, cq = conn.execute(
        "SELECT gold.fiscal_year_end(%s, %s), gold.calendar_quarter(%s)",
        (period_end, fye, period_end)).fetchone()
    assert cq == calendar
    conn.execute("UPDATE silver.companies SET fiscal_year_end = %s WHERE company_id = %s",
                 (fye, CAL))
    put(conn, CAL, "total_assets", period_end, 1)
    (row,) = rows(conn, CAL, "total_assets")
    assert row[2:4] == fiscal and fye_date >= period_end - timedelta(days=10)


def test_fiscal_labels_need_a_fiscal_year_end(conn):
    conn.execute("UPDATE silver.companies SET fiscal_year_end = NULL WHERE company_id = %s",
                 (CAL,))
    put(conn, CAL, "total_assets", date(2025, 6, 30), 1)
    assert rows(conn, CAL, "total_assets")[0][2:6] == (None, None, 2025, 2)


def test_pepsico_quarters_map_one_to_one(conn):
    labels = [(r[2], r[3], r[4], r[5], r[6]) for r in rows(conn, PEP, "revenue")]
    assert labels == [(y, q, y, q, True) for y in (2024, 2025) for q in (1, 2, 3, 4)]


def test_ttm_sums_four_contiguous_quarters(conn):
    refresh(conn, PEP)
    ttm = rows(conn, PEP, "revenue", "ttm")
    assert [(r[0], r[1]) for r in ttm] == [
        (date(2024, 12, 28), 91 * B), (date(2025, 3, 22), 91 * B),
        (date(2025, 6, 14), 92 * B), (date(2025, 9, 6), 93 * B), (date(2025, 12, 27), 94 * B)]
    last = ttm[-1]
    assert last[10] == date(2024, 12, 29) and len(last[7]["quarters"]) == 4
    assert last[9] == sorted(acc(PEP, e) for _, e in PEP_QUARTERS[4:])
    assert last[2:6] == (2025, 4, 2025, 4)


def test_ttm_needs_every_quarter(conn):
    conn.execute("DELETE FROM gold.quarterly_financials WHERE company_id = %s"
                 " AND metric = 'revenue' AND period_end = '2025-06-14'", (PEP,))
    refresh(conn, PEP)
    assert [r[0] for r in rows(conn, PEP, "revenue", "ttm")] == [
        date(2024, 12, 28), date(2025, 3, 22)]
    assert findings(conn, PEP, "series_gap") == [
        ("revenue", date(2025, 9, 6),
         {"missing_from": "2025-03-23", "missing_to": "2025-06-14"})]


def test_no_ttm_for_eps_and_instants_keep_their_value(conn):
    refresh(conn, PEP)
    assert rows(conn, PEP, "eps_diluted", "ttm") == []
    assert rows(conn, PEP, "eps_growth", "ttm") == []
    assert [r[1] for r in rows(conn, PEP, "stockholders_equity", "ttm")][-1] == 20 * B


def test_derived_metrics(conn):
    refresh(conn, PEP)

    def value(metric, span="quarter", end=date(2025, 12, 27)):
        v = {r[0]: r[1] for r in rows(conn, PEP, metric, span)}.get(end)
        return None if v is None else float(v)

    assert value("gross_margin") == 0.5
    assert value("free_cash_flow") == 2 * B
    assert value("fcf_margin", "ttm") == pytest.approx(8 / 94, abs=1e-6)
    assert value("revenue_growth") == pytest.approx(29 / 28 - 1, abs=1e-6)
    assert value("revenue_growth", "ttm") == pytest.approx(94 / 91 - 1, abs=1e-6)
    assert value("eps_growth") == 0
    assert value("roe", "ttm") == 0.2  # 4B / average(20B, 20B)
    assert value("roe") is None
    assert value("debt_to_equity") == 1.5
    assert value("net_debt") == 20 * B
    assert value("current_ratio") == 1.25
    assert value("revenue_growth", end=date(2024, 12, 28)) is None  # no year-ago quarter


def test_derived_rows_cite_only_their_inputs(conn):
    refresh(conn, PEP)
    end = date(2025, 12, 27)
    margin = rows(conn, PEP, "gross_margin", period_end=end)[0]
    assert margin[7] == {"gross_profit": 14.5 * B, "revenue": 29 * B}
    assert margin[9] == [acc(PEP, end)] and margin[10] == date(2025, 9, 7)
    fcf = rows(conn, PEP, "free_cash_flow", period_end=end)[0]
    assert fcf[9] == sorted([acc(PEP, end), f"{PEP}-capex-{end:%y%m%d}"])
    growth = rows(conn, PEP, "revenue_growth", period_end=end)[0]
    assert growth[9] == [acc(PEP, date(2024, 12, 28)), acc(PEP, end)]
    ratio = rows(conn, PEP, "current_ratio", period_end=end)[0]
    assert ratio[10] is None


def test_calendar_basis_keeps_one_row_per_calendar_quarter(conn):
    # a stub quarter ending in the same calendar quarter as the full one
    put(conn, CAL, "revenue", date(2025, 2, 28), 1, date(2025, 1, 1))
    put(conn, CAL, "revenue", date(2025, 3, 31), 3, date(2025, 1, 1))
    refresh(conn, CAL)
    primary = [(r[0], r[6]) for r in rows(conn, CAL, "revenue")]
    assert primary == [(date(2025, 2, 28), False), (date(2025, 3, 31), True)]
    assert findings(conn, CAL, "calendar_collision")[0][2]["calendar_quarter"] == "2025Q1"
    assert findings(conn, CAL, "overlapping_quarters")[0][:2] == ("revenue", date(2025, 3, 31))


def test_quality_checks(conn):
    for i, (start, end) in enumerate(PEP_QUARTERS):
        put(conn, CAL, "capex", end, -5 if i == 3 else 5, start)
        put(conn, CAL, "sga", end, 500 if i == 5 else 10, start)
    put(conn, CAL, "revenue", date(2025, 3, 31), -1, date(2025, 1, 1))
    put(conn, CAL, "current_assets", date(2025, 3, 31), 2)
    put(conn, CAL, "total_assets", date(2025, 3, 31), 1)
    refresh(conn, PEP, CAL)

    assert findings(conn, PEP, "impossible_value") == []
    assert [(m, d["rule"]) for m, _, d in findings(conn, CAL, "impossible_value")] == [
        ("current_assets", "current_assets > total_assets"), ("revenue", "negative")]
    assert [(m, e) for m, e, _ in findings(conn, CAL, "sign_flip")] == [
        ("capex", date(2024, 12, 28))]
    assert [(m, e) for m, e, _ in findings(conn, CAL, "outlier")] == [
        ("sga", date(2025, 6, 14))]
    counts = conn.execute(
        "SELECT company_id, errors, warnings FROM ops.quality_companies"
        " WHERE company_id IN (%s, %s) ORDER BY 1", (PEP, CAL)).fetchall()
    assert counts == [(PEP, 0, 0), (CAL, 2, 2)]
    run = conn.execute("SELECT companies, findings, by_check FROM ops.quality_runs"
                       " ORDER BY run_id DESC LIMIT 1").fetchone()
    assert run == (2, 4, {"impossible_value": 2, "sign_flip": 1, "outlier": 1})


def test_annual_figure_disagreeing_with_quarters(conn):
    for value, accession in ((91 * B, "annual-ok"), (95 * B, "annual-restated")):
        conn.execute(
            "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start,"
            " period_end, value, accession_no, form, filed) VALUES (%s, 'revenue', 'Revenues',"
            " 'USD', '2023-12-31', '2024-12-28', %s, %s, '10-K', %s)",
            (PEP, value, accession, date(2025, 2, 1) if accession == "annual-ok"
             else date(2026, 2, 1)))
    refresh(conn, PEP)
    (metric, end, detail), = findings(conn, PEP, "annual_sum")
    assert (metric, end, detail["annual_accession"]) == (
        "revenue", date(2024, 12, 28), "annual-restated")
    assert detail["deviation"] == -0.0421


def test_checks_replace_a_companys_findings(conn):
    put(conn, CAL, "revenue", date(2025, 3, 31), -1, date(2025, 1, 1))
    refresh(conn, CAL)
    conn.execute("UPDATE gold.quarterly_financials SET value = 1 WHERE company_id = %s", (CAL,))
    refresh(conn, CAL)
    assert findings(conn, CAL, "impossible_value") == []
    assert conn.execute("SELECT errors FROM ops.quality_companies WHERE company_id = %s",
                        (CAL,)).fetchone() == (0,)


def test_refresh_financials_runs_every_step(conn):
    conn.execute("SELECT gold.refresh_financials(%s::text[])", ([PEP],))
    # no silver facts: the quarterly rebuild empties gold, and the rest follows
    assert conn.execute("SELECT count(*) FROM gold.financials WHERE company_id = %s",
                        (PEP,)).fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM ops.quality_companies WHERE company_id = %s",
                        (PEP,)).fetchone() == (1,)


def test_ops_reader_sees_quality_views_only(conn):
    refresh(conn, PEP)
    conn.execute("SET LOCAL ROLE ops_reader")
    conn.execute("SELECT * FROM ops.quality_summary")
    conn.execute("SELECT * FROM ops.quality_issues LIMIT 1")
    for table in ("ops.quality_findings", "gold.derived_financials", "gold.financials"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
