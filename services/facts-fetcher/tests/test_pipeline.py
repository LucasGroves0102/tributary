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
