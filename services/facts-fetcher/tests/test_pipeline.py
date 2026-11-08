"""bronze → silver → gold against a real Postgres with migrations applied, using trimmed real
SEC payloads. Skipped unless TEST_DATABASE_URL is set; every test is rolled back. The `conn`
and `loaded` fixtures (five companies, fixtures/) are in conftest.py."""

import gzip
import json
import os
from datetime import date
from pathlib import Path

import pytest

from facts_fetcher.db import (
    latest_hashes,
    refresh_gold,
    refresh_silver,
    store_payload,
    tracked_ciks,
)
from facts_fetcher.main import rebuild
from facts_fetcher.payload import digest

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

FIXTURES = Path(__file__).parent / "fixtures"
NVDA, COST, JPM = "0001045810", "0000909832", "0000019617"
XOM, XOM_HOLDINGS = "0000034088", "0002115436"
HD = "0000354950"


def raw(cik: str) -> bytes:
    return (FIXTURES / f"CIK{cik}.json").read_bytes()


def quarters(conn, company_id, metric):
    return conn.execute(
        "SELECT period_end, value, value_source, source_tag, source_accessions"
        " FROM gold.quarterly_financials WHERE company_id = %s AND metric = %s"
        " ORDER BY period_end DESC", (company_id, metric)
    ).fetchall()


def test_tracked_ciks(conn):
    assert tracked_ciks(conn)[XOM] == [XOM, XOM_HOLDINGS]


def test_store_payload_dedupes_on_content(conn):
    assert store_payload(conn, NVDA, raw(NVDA)) is True
    assert store_payload(conn, NVDA, raw(NVDA)) is False
    assert conn.execute("SELECT count(*) FROM bronze.company_facts").fetchone()[0] == 1


def test_nvidia_last_four_revenue_quarters(loaded):
    q = quarters(loaded, NVDA, "revenue")[:4]
    assert [(r[0], r[1], r[2]) for r in q] == [
        (date(2026, 7, 26), 96221000000, "reported"),
        (date(2026, 4, 26), 81615000000, "reported"),
        (date(2026, 1, 25), 68127000000, "derived"),  # FY 215.938B - 9 months 147.811B
        (date(2025, 10, 26), 57006000000, "reported"),
    ]
    assert q[2][3] == "us-gaap:Revenues"
    assert q[2][4] == ["0001045810-25-000230", "0001045810-26-000021"]  # 9-month 10-Q, 10-K


def test_cash_flow_quarters_derived_from_year_to_date(loaded):
    latest = quarters(loaded, NVDA, "operating_cash_flow")[0]
    assert (latest[0], latest[1], latest[2]) == (date(2026, 7, 26), 24077000000, "derived")


def test_sixteen_week_fourth_quarter(loaded):
    q4 = next(r for r in quarters(loaded, COST, "revenue") if r[0] == date(2025, 8, 31))
    assert (q4[1], q4[2]) == (86156000000, "derived")


def test_bank_revenue_and_missing_metrics(loaded):
    assert quarters(loaded, JPM, "revenue")[0][1:4] == (
        57347000000, "reported", "us-gaap:RevenuesNetOfInterestExpense")
    assert quarters(loaded, JPM, "gross_profit") == []


def test_eps_is_never_derived(loaded):
    assert all(r[2] == "reported" for r in quarters(loaded, NVDA, "eps_diluted"))


def test_preferred_tag_derived_beats_fallback_reported(loaded):
    # cash-flow D&A is year-to-date only; the income-statement line (852M) is reported quarterly
    q2 = quarters(loaded, HD, "depreciation_amortization")[0]
    assert q2[:4] == (date(2026, 8, 2), 929000000, "derived",
                      "us-gaap:DepreciationDepletionAndAmortization")  # 1.839B - 910M


def test_outflows_are_positive(loaded):
    q4 = date(2026, 2, 1)  # FY - 9 months
    assert {m: next(r[1] for r in quarters(loaded, HD, m) if r[0] == q4)
            for m in ("capex", "dividends_paid", "share_buybacks")} == {
        "capex": 1058000000, "dividends_paid": 2289000000, "share_buybacks": 0}


def test_per_share_values_and_share_counts_are_never_derived(loaded):
    for metric in ("eps_basic", "weighted_shares_diluted"):
        rows = quarters(loaded, HD, metric)
        assert rows and all(r[2] == "reported" for r in rows)
        assert date(2026, 2, 1) not in [r[0] for r in rows]


def test_instants_are_balance_sheet_values(loaded):
    debt = quarters(loaded, HD, "long_term_debt")[0]
    assert debt[:4] == (date(2026, 8, 2), 43951000000, "reported",
                        "us-gaap:LongTermDebtAndCapitalLeaseObligations")
    # dei cover-page counts are dated weeks after the quarter; only balance-sheet dates appear
    shares = quarters(loaded, HD, "shares_outstanding")
    assert [(r[0], r[1]) for r in shares[:2]] == [
        (date(2026, 8, 2), 998000000), (date(2026, 5, 3), 997000000)]
    assert date(2026, 8, 18) not in [r[0] for r in shares]


def test_successor_cik_facts_land_on_original_company(loaded):
    q = {r[0]: r[1] for r in quarters(loaded, XOM, "revenue")}
    assert q[date(2026, 6, 30)] == 116017000000  # reported under the holdings CIK
    assert q[date(2026, 3, 31)] == 85138000000   # reported under the original CIK


def test_restatement_wins_and_original_is_kept(loaded):
    loaded.execute(
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed)"
        " VALUES (%s, 'revenue', 'Revenues', 'USD', '2026-04-27', '2026-07-26', 99000000000,"
        " 'restated-1', '10-Q/A', '2026-09-30')", (NVDA,))
    refresh_gold(loaded, [NVDA])
    latest = quarters(loaded, NVDA, "revenue")[0]
    assert (latest[1], latest[4]) == (99000000000, ["restated-1"])
    originals = loaded.execute(
        "SELECT count(*) FROM silver.financial_facts WHERE cik = %s AND metric = 'revenue'"
        " AND period_end = '2026-07-26' AND accession_no = '0001045810-26-000075'", (NVDA,)
    ).fetchone()[0]
    assert originals > 0


def test_refresh_is_idempotent(loaded):
    before = loaded.execute("SELECT count(*) FROM gold.quarterly_financials").fetchone()[0]
    assert refresh_silver(loaded, [NVDA]) == 0
    refresh_gold(loaded, [NVDA])
    after = loaded.execute("SELECT count(*) FROM gold.quarterly_financials").fetchone()[0]
    assert before == after


def test_every_gold_row_has_provenance(loaded):
    missing = loaded.execute(
        "SELECT count(*) FROM gold.quarterly_financials WHERE accession_no IS NULL"
        " OR source_tag IS NULL OR cardinality(source_accessions) = 0"
    ).fetchone()[0]
    assert missing == 0


def test_set_based_refresh_matches_one_company_at_a_time(loaded, companies):
    def snapshot():
        return loaded.execute(
            "SELECT company_id, metric, period_end, value, value_source, source_accessions"
            " FROM gold.quarterly_financials ORDER BY 1, 2, 3").fetchall()
    together = snapshot()
    for company_id in companies:
        loaded.execute("SELECT gold.refresh_quarterly_financials(%s::text)", (company_id,))
    assert snapshot() == together


def test_preferred_tag_beats_reported_over_derived(loaded):
    # a lower-priority tag reports the quarter that Revenues (priority 2) only derives
    loaded.execute(
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed)"
        " VALUES (%s, 'revenue', 'SalesRevenueNet', 'USD', '2025-10-27', '2026-01-25', 1,"
        " 'other-tag-1', '10-K', '2026-02-25')", (NVDA,))
    refresh_gold(loaded, [NVDA])
    q = next(r for r in quarters(loaded, NVDA, "revenue") if r[0] == date(2026, 1, 25))
    assert (q[1], q[2], q[3]) == (68127000000, "derived", "us-gaap:Revenues")


def test_instants_are_never_derived(loaded):
    rows = loaded.execute(
        "SELECT DISTINCT value_source FROM gold.quarterly_financials"
        " WHERE metric IN ('total_assets', 'cash')").fetchall()
    assert rows == [("reported",)]


def test_bronze_keeps_mapped_facts_and_the_raw_payload(conn):
    nvda = json.loads(raw(NVDA))
    nvda["facts"]["us-gaap"]["SomethingUnmapped"] = {"units": {"USD": []}}
    payload = json.dumps(nvda).encode()
    store_payload(conn, NVDA, payload)
    facts, raw_gzip, size = conn.execute(
        "SELECT payload, raw_gzip, size_bytes FROM bronze.company_facts").fetchone()
    assert "SomethingUnmapped" not in facts["facts"]["us-gaap"]
    assert "Revenues" in facts["facts"]["us-gaap"]
    assert gzip.decompress(raw_gzip) == payload and size == len(payload)


def test_only_the_latest_payload_keeps_its_raw_copy(conn):
    store_payload(conn, NVDA, raw(NVDA))
    store_payload(conn, NVDA, raw(NVDA) + b" ")
    assert conn.execute(
        "SELECT count(*), count(raw_gzip) FROM bronze.company_facts").fetchone() == (2, 1)
    # the SEC going back to an earlier payload makes it the latest again, with its raw copy
    assert store_payload(conn, NVDA, raw(NVDA)) is False
    assert latest_hashes(conn)[NVDA] == digest(raw(NVDA))
    assert conn.execute(
        "SELECT count(raw_gzip) FROM bronze.company_facts WHERE payload_sha256 = %s",
        (digest(raw(NVDA)),)).fetchone() == (1,)


def test_rebuild_reprojects_from_raw_after_metric_tags_change(loaded):
    loaded.execute("INSERT INTO silver.metric_tags (metric, tag, priority)"
                   " VALUES ('revenue', 'NoSuchTagYet', 99)")
    before = loaded.execute("SELECT count(*) FROM silver.financial_facts").fetchone()[0]
    nvda = json.loads(raw(NVDA))
    nvda["facts"]["us-gaap"]["NoSuchTagYet"] = nvda["facts"]["us-gaap"]["Revenues"]
    store_payload(loaded, NVDA, json.dumps(nvda).encode())  # new payload, projected with it
    loaded.execute("UPDATE bronze.company_facts SET payload = '{}'")  # as if projected before
    rebuild(loaded)
    after = loaded.execute(
        "SELECT count(*) FROM silver.financial_facts WHERE tag = 'NoSuchTagYet'").fetchone()[0]
    assert after > 0
    assert loaded.execute("SELECT count(*) FROM silver.financial_facts").fetchone()[0] \
        == before + after


def test_rebuild_can_stop_at_a_few_ciks(loaded):
    loaded.execute("INSERT INTO silver.metric_tags (metric, tag, priority)"
                   " VALUES ('revenue', 'NoSuchTagYet', 99)")
    for cik in (NVDA, COST):
        doc = json.loads(raw(cik))
        doc["facts"]["us-gaap"]["NoSuchTagYet"] = doc["facts"]["us-gaap"]["Revenues"]
        store_payload(loaded, cik, json.dumps(doc).encode())
    loaded.execute("UPDATE bronze.company_facts SET payload = '{}'")
    rebuild(loaded, [NVDA])
    ciks = {r[0] for r in loaded.execute(
        "SELECT DISTINCT cik FROM silver.financial_facts WHERE tag = 'NoSuchTagYet'").fetchall()}
    assert ciks == {NVDA}


def test_ttm_of_a_fiscal_year_matches_the_10k(loaded):
    def ttm(company_id, end):
        return loaded.execute(
            "SELECT period_start, value, calendar_year, calendar_quarter FROM gold.financials"
            " WHERE company_id = %s AND span = 'ttm' AND metric = 'revenue'"
            " AND period_end = %s", (company_id, end)).fetchone()
    assert ttm(NVDA, date(2026, 1, 25))[1] == 215938000000  # FY2026 10-K
    # Costco's 52-week year with a 16-week Q4, in calendar Q3
    assert ttm(COST, date(2025, 8, 31)) == (date(2024, 9, 2), 275235000000, 2025, 3)
    assert loaded.execute("SELECT count(*) FROM ops.quality_findings"
                          " WHERE check_name = 'annual_sum'").fetchone()[0] == 0


def test_every_gold_refresh_is_checked(loaded, companies):
    checked = loaded.execute("SELECT company_id FROM ops.quality_companies ORDER BY 1")
    assert [r[0] for r in checked] == sorted(companies)


def gold_rows(conn):
    quarterly = conn.execute(
        "SELECT company_id, metric, period_start, period_end, value, value_source, source_tag,"
        " accession_no, form, filed, source_accessions, ingested_at"
        " FROM gold.quarterly_financials ORDER BY company_id, metric, period_end").fetchall()
    derived = conn.execute(
        "SELECT company_id, span, metric, period_start, period_end, value, value_source,"
        " source_tag, inputs, accession_no, form, filed, source_accessions, ingested_at"
        " FROM gold.derived_financials ORDER BY company_id, span, metric, period_end").fetchall()
    return quarterly, derived


def test_repeated_refreshes_produce_identical_rows(loaded, companies):
    first = gold_rows(loaded)
    refresh_gold(loaded, list(companies))
    assert gold_rows(loaded) == first


# Two year-to-date chains (from Jan 1 and from Feb 1) derive the same quarter from the same
# 10-Q, with equal tag, value and filing: only the earlier accession differs.
YTD_CHAINS = [
    ("2025-01-01", "2025-06-30", 600, "tie-a", "2025-08-01"),
    ("2025-02-01", "2025-06-30", 500, "tie-b", "2025-08-01"),
    ("2025-01-01", "2025-09-30", 900, "tie-c", "2025-11-01"),
    ("2025-02-01", "2025-09-30", 800, "tie-c", "2025-11-01"),
]


def test_tied_candidates_resolve_the_same_way_in_any_order(conn):
    results = []
    for facts in (YTD_CHAINS, YTD_CHAINS[::-1]):
        conn.execute("DELETE FROM silver.financial_facts")
        for start, end, value, accession, filed in facts:
            conn.execute(
                "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start,"
                " period_end, value, accession_no, form, filed, ingested_at)"
                " VALUES (%s, 'revenue', 'Revenues', 'USD', %s, %s, %s, %s, '10-Q', %s,"
                " '2025-11-02')", (HD, start, end, value, accession, filed))
        for _ in range(2):
            refresh_gold(conn, [HD])
            results.append(gold_rows(conn)[0])
    assert all(r == results[0] for r in results)
    assert quarters(conn, HD, "revenue") == [
        (date(2025, 9, 30), 300, "derived", "us-gaap:Revenues", ["tie-b", "tie-c"])]


def test_proxy_statement_figures_never_reach_gold(loaded):
    # a DEF 14A's pay-versus-performance table repeats fiscal-year net income, rounded, weeks
    # after the 10-K; as the latest filing it used to replace the 10-K's figure (migration 024)
    q4 = date(2026, 1, 25)
    before = next(r for r in quarters(loaded, NVDA, "net_income") if r[0] == q4)
    loaded.execute(
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed)"
        " VALUES (%s, 'net_income', 'NetIncomeLoss', 'USD', '2025-01-27', '2026-01-25',"
        " 120100000000, 'proxy-1', 'DEF 14A', '2026-05-01')", (NVDA,))
    refresh_gold(loaded, [NVDA])
    after = next(r for r in quarters(loaded, NVDA, "net_income") if r[0] == q4)
    assert after == before and "proxy-1" not in after[4]
    assert not loaded.execute(
        "SELECT count(*) FROM gold.derived_financials WHERE 'proxy-1' = ANY(source_accessions)"
    ).fetchone()[0]


def insert_facts(conn, metric, tag, facts):
    conn.execute("DELETE FROM silver.financial_facts")
    for start, end, value, accession, filed in facts:
        conn.execute(
            "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start,"
            " period_end, value, accession_no, form, filed, ingested_at)"
            " VALUES (%s, %s, %s, 'USD', %s, %s, %s, %s, '10-K', %s, '2025-11-02')",
            (HD, metric, tag, start, end, value, accession, filed))
    refresh_gold(conn, [HD])


def test_flow_metrics_tagged_at_an_instant_are_not_quarters(conn):
    # UnitedHealth tags each dividend payment at its payment date (migration 029)
    insert_facts(conn, "dividends_paid", "PaymentsOfDividendsCommonStock", [
        ("2024-01-01", "2024-09-30", 5800, "q3", "2024-11-01"),
        ("2024-01-01", "2024-12-31", 7737, "fy", "2025-02-28"),
        (None, "2024-12-17", 1932, "fy", "2025-02-28"),
    ])
    assert quarters(conn, HD, "dividends_paid") == [
        (date(2024, 12, 31), 1937, "derived", "us-gaap:PaymentsOfDividendsCommonStock",
         ["q3", "fy"])]


def test_impossible_derived_quarter_is_left_out(conn):
    # Cytek: a later 10-K re-tagged the fiscal year as 2,963,000, so Q4 = FY - 9M < 0 (029)
    insert_facts(conn, "revenue", "Revenues", [
        ("2022-01-01", "2022-09-30", 115700000, "q3", "2022-11-01"),
        ("2022-01-01", "2022-12-31", 2963000, "fy", "2024-03-13"),
    ])
    assert quarters(conn, HD, "revenue") == []


# Migration 031: one series of quarters per company and metric. Rows are (metric, start, end,
# value, accession, form, filed); a None start is a balance-sheet (instant) value.
TAGS = {"revenue": "Revenues", "net_income": "NetIncomeLoss", "total_assets": "Assets"}


def insert_rows(conn, rows):
    conn.execute("DELETE FROM silver.financial_facts")
    for metric, start, end, value, accession, form, filed in rows:
        conn.execute(
            "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start,"
            " period_end, value, accession_no, form, filed, ingested_at)"
            " VALUES (%s, %s, %s, 'USD', %s, %s, %s, %s, %s, %s, '2025-11-02')",
            (HD, metric, TAGS[metric], start, end, value, accession, form, filed))
    refresh_gold(conn, [HD])


def spans(conn, metric):
    return conn.execute(
        "SELECT period_start, period_end, value, value_source, source_accessions"
        " FROM gold.quarterly_financials WHERE company_id = %s AND metric = %s"
        " ORDER BY period_end", (HD, metric)).fetchall()


def overlap_findings(conn):
    return conn.execute(
        "SELECT count(*) FROM ops.quality_findings"
        " WHERE company_id = %s AND check_name = 'overlapping_quarters'", (HD,)).fetchone()[0]


def test_same_quarter_ending_a_day_apart_is_kept_once(conn):
    # NVIDIA Q2 FY2011: the 10-Q ends it on Aug 1 (its balance-sheet date), a later 10-K on
    # Jul 31, same value. Gold kept both (one per period_end).
    insert_rows(conn, [
        ("revenue", "2010-05-03", "2010-08-01", 811208000, "q2", "10-Q", "2010-08-25"),
        ("total_assets", None, "2010-08-01", 3600000000, "q2", "10-Q", "2010-08-25"),
        ("revenue", "2010-05-03", "2010-07-31", 811208000, "k", "10-K", "2012-03-13"),
    ])
    assert spans(conn, "revenue") == [
        (date(2010, 5, 3), date(2010, 8, 1), 811208000, "reported", ["q2"])]
    assert overlap_findings(conn) == 0


def test_reported_quarter_beats_derived_one_ending_a_day_earlier(conn):
    # Worthington: nine months tagged to Feb 28 in a leap year derive a quarter that duplicates
    # the reported Dec 1 - Feb 29 one
    insert_rows(conn, [
        ("total_assets", None, "2023-11-30", 3000, "q2", "10-Q", "2024-01-09"),
        ("revenue", "2023-06-01", "2023-11-30", 610, "q2", "10-Q", "2024-01-09"),
        ("revenue", "2023-06-01", "2024-02-28", 927, "q3", "10-Q", "2024-04-09"),
        ("revenue", "2023-12-01", "2024-02-29", 317, "q3", "10-Q", "2024-04-09"),
        ("total_assets", None, "2024-02-29", 3100, "q3", "10-Q", "2024-04-09"),
    ])
    assert spans(conn, "revenue") == [
        (date(2023, 12, 1), date(2024, 2, 29), 317, "reported", ["q3"])]


# ATI: its 2023 10-Qs and 10-K use a 52/53-week calendar (balance sheets on those dates); the
# next year's filings repeat 2023 as calendar quarters, restated. Both Q4s end Dec 31.
FISCAL_2023 = [
    ("total_assets", None, "2023-01-01", 4400, "k22", "10-K", "2023-02-20"),
    ("revenue", "2023-01-02", "2023-04-02", 100, "q1", "10-Q", "2023-05-01"),
    ("total_assets", None, "2023-04-02", 4500, "q1", "10-Q", "2023-05-01"),
    ("revenue", "2023-04-03", "2023-07-02", 110, "q2", "10-Q", "2023-08-01"),
    ("total_assets", None, "2023-07-02", 4600, "q2", "10-Q", "2023-08-01"),
    ("revenue", "2023-07-03", "2023-10-01", 120, "q3", "10-Q", "2023-11-01"),
    ("revenue", "2023-01-02", "2023-10-01", 330, "q3", "10-Q", "2023-11-01"),
    ("total_assets", None, "2023-10-01", 4700, "q3", "10-Q", "2023-11-01"),
    ("revenue", "2023-01-02", "2023-12-31", 460, "k23", "10-K", "2024-02-20"),
    ("total_assets", None, "2023-12-31", 4900, "k23", "10-K", "2024-02-20"),
]
CALENDAR_2023 = [
    ("revenue", "2023-01-01", "2023-03-31", 101, "r1", "10-Q", "2024-05-01"),
    ("revenue", "2023-04-01", "2023-06-30", 111, "r2", "10-Q", "2024-08-01"),
    ("revenue", "2023-07-01", "2023-09-30", 121, "r3", "10-Q", "2024-11-01"),
    ("revenue", "2023-01-01", "2023-09-30", 333, "r3", "10-Q", "2024-11-01"),
    ("revenue", "2023-01-01", "2023-12-31", 470, "k24", "10-K", "2025-02-20"),
]


def test_interleaved_calendars_keep_one_series_and_its_own_fourth_quarter(conn):
    insert_rows(conn, FISCAL_2023 + CALENDAR_2023)
    # Q4 = FY - 9M of the same series (130), not the calendar one (470 - 333 = 137)
    assert spans(conn, "revenue") == [
        (date(2023, 1, 2), date(2023, 4, 2), 100, "reported", ["q1"]),
        (date(2023, 4, 3), date(2023, 7, 2), 110, "reported", ["q2"]),
        (date(2023, 7, 3), date(2023, 10, 1), 120, "reported", ["q3"]),
        (date(2023, 10, 2), date(2023, 12, 31), 130, "derived", ["q3", "k23"]),
    ]
    assert overlap_findings(conn) == 0
    ttm = conn.execute(
        "SELECT period_start, value FROM gold.derived_financials WHERE company_id = %s"
        " AND span = 'ttm' AND metric = 'revenue'", (HD,)).fetchall()
    assert ttm == [(date(2023, 1, 2), 460)]  # the fiscal year's 10-K figure


def test_relabelled_quarter_with_a_slightly_different_value_is_kept_once(conn):
    # Dine Brands: the Q1 2025 10-Q (balance sheet on Mar 31) says 4,598,000; the Q1 2026
    # 10-Q's comparative calls the quarter Dec 30 - Mar 30 and says 4,600,000
    insert_rows(conn, [
        ("total_assets", None, "2024-12-31", 1900, "k24", "10-K", "2025-03-05"),
        ("net_income", "2025-01-01", "2025-03-31", 4598000, "q1-25", "10-Q", "2025-05-07"),
        ("total_assets", None, "2025-03-31", 1950, "q1-25", "10-Q", "2025-05-07"),
        ("net_income", "2024-12-30", "2025-03-30", 4600000, "q1-26", "10-Q", "2026-05-06"),
    ])
    assert spans(conn, "net_income") == [
        (date(2025, 1, 1), date(2025, 3, 31), 4598000, "reported", ["q1-25"])]


def test_conflicts_are_resolved_best_first_without_losing_a_chain(conn):
    # B overlaps both A and C; A beats B (balance sheets), B beats C (filed later). Dropping B
    # leaves nothing that overlaps C, so C stays: a quarter is only dropped for a kept one.
    insert_rows(conn, [
        ("total_assets", None, "2022-12-31", 10, "a", "10-K", "2023-02-01"),
        ("revenue", "2023-01-01", "2023-03-31", 1, "a", "10-Q", "2023-05-01"),
        ("total_assets", None, "2023-03-31", 11, "a", "10-Q", "2023-05-01"),
        ("revenue", "2023-02-15", "2023-05-15", 2, "b", "10-Q", "2023-09-01"),
        ("revenue", "2023-04-02", "2023-06-30", 3, "c", "10-Q", "2023-08-01"),
    ])
    assert [r[2] for r in spans(conn, "revenue")] == [1, 3]


def test_a_clean_series_is_unchanged(conn):
    # calendar quarters with a derived Q4, and a next quarter tagged to start a day early
    # (Dec 30 after a quarter ending Dec 31): one series, overlap tolerated (up to 7 days)
    insert_rows(conn, [
        ("revenue", "2023-01-01", "2023-03-31", 100, "q1", "10-Q", "2023-05-01"),
        ("revenue", "2023-04-01", "2023-06-30", 110, "q2", "10-Q", "2023-08-01"),
        ("revenue", "2023-01-01", "2023-06-30", 210, "q2", "10-Q", "2023-08-01"),
        ("revenue", "2023-07-01", "2023-09-30", 120, "q3", "10-Q", "2023-11-01"),
        ("revenue", "2023-01-01", "2023-09-30", 330, "q3", "10-Q", "2023-11-01"),
        ("revenue", "2023-01-01", "2023-12-31", 460, "k", "10-K", "2024-02-20"),
        ("revenue", "2023-12-30", "2024-03-31", 105, "q1-24", "10-Q", "2024-05-01"),
    ])
    assert spans(conn, "revenue") == [
        (date(2023, 1, 1), date(2023, 3, 31), 100, "reported", ["q1"]),
        (date(2023, 4, 1), date(2023, 6, 30), 110, "reported", ["q2"]),
        (date(2023, 7, 1), date(2023, 9, 30), 120, "reported", ["q3"]),
        (date(2023, 10, 1), date(2023, 12, 31), 130, "derived", ["q3", "k"]),
        (date(2023, 12, 30), date(2024, 3, 31), 105, "reported", ["q1-24"]),
    ]
