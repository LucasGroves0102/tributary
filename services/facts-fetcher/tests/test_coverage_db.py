"""The coverage sweep (migration 076, facts_fetcher/coverage.py) on the five loaded companies plus
a few made-up listed companies, each built to have one kind of gap; skipped unless
TEST_DATABASE_URL is set. Rolled back."""

import os
from datetime import date, timedelta

import psycopg
import pytest

from facts_fetcher import coverage

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="TEST_DATABASE_URL not set")

NVDA, COST, JPM, XOM, HD = "0001045810", "0000909832", "0000019617", "0000034088", "0000354950"
FUND, BANK, NEW = "0009000001", "0009000002", "0009000003"
TODAY = date.today()


def company(conn, company_id, name, sic=None):
    conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, sic, exchanges)"
                 " VALUES (%s, %s, %s, %s, ARRAY['NYSE'])", (company_id, name, company_id, sic))
    conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                 (company_id, company_id))


def filing(conn, cik, accession, form, filed, report=None, xbrl=True):
    conn.execute("INSERT INTO bronze.submission_filings (accession_no, cik, form, filing_date,"
                 " report_date, is_xbrl) VALUES (%s, %s, %s, %s, %s, %s)",
                 (accession, cik, form, filed, report, xbrl))


def first_and_last(conn, company_id):
    return conn.execute(
        "SELECT min(period_end), max(period_end) FROM gold.quarterly_financials"
        " WHERE company_id = %s AND metric IN ('revenue', 'net_income', 'total_assets')",
        (company_id,)).fetchone()


@pytest.fixture
def listed(loaded):
    loaded.execute("DELETE FROM bronze.submission_filings")
    loaded.execute("UPDATE silver.companies SET exchanges = ARRAY['Nasdaq']")
    return loaded


def found(conn):
    return {(r[0], r[1]): (r[2], r[3], r[4]) for r in conn.execute(
        "SELECT company_id, kind, cause, reason, detail FROM ops.coverage_found()")}


def test_a_company_with_no_figures_says_why(listed):
    company(listed, FUND, "Some Income Fund", "6726")
    filing(listed, FUND, "0009000001-25-000001", "N-CSR", TODAY - timedelta(days=90))
    company(listed, BANK, "Some Bank", "6022")
    filing(listed, BANK, "0009000002-20-000001", "8-K", date(2020, 1, 2))
    company(listed, NEW, "Brand New Co")
    filing(listed, NEW, "0009000003-26-000001", "S-1", TODAY - timedelta(days=60))
    gaps = found(listed)
    assert gaps[(FUND, "no_figures")][:2] == ("fund", "out_of_scope")
    assert gaps[(BANK, "no_figures")][:2] == ("bank_regulator", "not_in_sec_data")
    assert gaps[(NEW, "no_figures")][:2] == ("no_reports_yet", "not_in_sec_data")
    assert not any(k == "no_figures" for (c, k) in gaps if c in (NVDA, COST, JPM, XOM, HD))


def test_figures_behind_the_latest_report(listed):
    _, last = first_and_last(listed, NVDA)
    filed = TODAY - timedelta(days=20)
    filing(listed, NVDA, "0001045810-99-000001", "10-Q", filed, last + timedelta(days=120))
    cause, reason, detail = found(listed)[(NVDA, "figures_behind")]
    assert (cause, reason) == ("companyfacts_lacks", "fixable")
    assert detail["accession_no"] == "0001045810-99-000001"
    assert detail["figures_to"] == str(last)
    # a filing without XBRL: not in the SEC's structured data
    listed.execute("UPDATE bronze.submission_filings SET is_xbrl = false"
                   " WHERE accession_no = '0001045810-99-000001'")
    assert found(listed)[(NVDA, "figures_behind")][:2] == ("no_xbrl", "not_in_sec_data")


def test_an_unlinked_re_registration_and_its_history(listed):
    first, _ = first_and_last(listed, COST)
    moved = max(first + timedelta(days=100), date(2010, 1, 4))
    filing(listed, COST, "0000909832-99-000001", "8-K12B", moved)
    filing(listed, COST, "0000909832-99-000002", "10-K", date(2009, 11, 1))  # XBRL era starts
    gaps = found(listed)
    assert gaps[(COST, "re_registration")][:2] == ("unlinked", "fixable")
    assert gaps[(COST, "re_registration")][2]["moved_on"] == str(moved)
    assert gaps[(COST, "history_short")][:2] == ("re_registration", "fixable")
    # the predecessor linked: its CIK reported up to the move
    listed.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES ('0009000009', %s)",
                   (COST,))
    filing(listed, "0009000009", "0009000009-09-000001", "10-Q", moved - timedelta(days=30))
    gaps = found(listed)
    assert (COST, "re_registration") not in gaps
    assert gaps[(COST, "history_short")][0] != "re_registration"


def test_a_core_metric_missing_lately(listed):
    """Booking's kind: net income stops (its quarters end three years before the rest) while
    revenue goes on. Silver has NVIDIA's later net income: gold dropped it, a fixable gap."""
    listed.execute("UPDATE gold.quarterly_financials SET period_start = period_start - 1095,"
                   " period_end = period_end - 1095"
                   " WHERE company_id = %s AND metric = 'net_income'", (NVDA,))
    listed.execute("DELETE FROM gold.derived_financials WHERE company_id = %s"
                   " AND metric = 'net_income'", (NVDA,))
    assert listed.execute("SELECT count(*) FROM gold.quarterly_financials WHERE company_id = %s"
                          " AND metric = 'net_income'", (NVDA,)).fetchone()[0] >= 4
    cause, reason, detail = found(listed)[(NVDA, "metric_missing")]
    assert (cause, reason) == ("gold_dropped", "fixable")
    assert [m["metric"] for m in detail["metrics"]] == ["net_income"]
    listed.execute("DELETE FROM silver.financial_facts WHERE cik = %s AND metric = 'net_income'",
                   (NVDA,))
    assert found(listed)[(NVDA, "metric_missing")][:2] == ("not_read", "fixable")


def test_documents_and_statements_in_an_exhibit(listed):
    filing(listed, JPM, "0000019617-24-000001", "10-K", TODAY - timedelta(days=200))
    filing(listed, JPM, "0000019617-24-000002", "10-Q", TODAY - timedelta(days=100))
    gaps = found(listed)
    assert gaps[(JPM, "documents_missing")] == ("none", "fixable", {
        "expected": 2, "stored": 0, "last_filed": str(TODAY - timedelta(days=100))})
    listed.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name, url,"
        " content_gzip, content_sha256, size_bytes) VALUES ('0000019617-24-000001', %s, '10-K',"
        " %s, 'a.htm', 'u', '\\x00', 'x', 1)", (JPM, TODAY - timedelta(days=200)))
    listed.execute(
        "INSERT INTO silver.filing_sections (accession_no, section_key, parser, parser_version,"
        " item, title, text, char_start, char_end) VALUES ('0000019617-24-000001', '8', 'rust',"
        " 'rust-7', '8', 'Financial Statements', 'Item 8. The information required is in"
        " Exhibit 13, the 2025 Annual Report to Shareholders.', 0, 90)")
    gaps = found(listed)
    assert gaps[(JPM, "statements_exhibit")][:2] == ("ex13", "fixable")
    assert (JPM, "documents_missing") not in gaps  # one of two stored: not under half of 8+


def test_refresh_keeps_first_seen_and_closes_gaps(listed):
    company(listed, FUND, "Some Income Fund", "6726")
    filing(listed, FUND, "0009000001-25-000001", "N-CSR", TODAY - timedelta(days=90))
    listed.execute("DELETE FROM ops.coverage")
    assert coverage.refresh(listed) >= 1
    listed.execute("UPDATE ops.coverage SET first_seen = '2026-01-01'")
    coverage.refresh(listed)
    assert listed.execute("SELECT first_seen::date FROM ops.coverage WHERE company_id = %s",
                          (FUND,)).fetchone()[0] == date(2026, 1, 1)
    listed.execute("UPDATE silver.companies SET exchanges = NULL WHERE company_id = %s", (FUND,))
    coverage.refresh(listed)
    assert not listed.execute("SELECT 1 FROM ops.coverage WHERE company_id = %s",
                              (FUND,)).fetchone()
    listed_now, gaps = listed.execute("SELECT listed, gaps FROM ops.coverage_runs"
                                      " ORDER BY run_at DESC LIMIT 1").fetchone()
    assert listed_now == 5 and gaps == listed.execute("SELECT count(*) FROM ops.coverage"
                                                      ).fetchone()[0]


def test_readers_ops_and_the_public_counts(listed, capsys):
    company(listed, FUND, "Some Income Fund", "6726")
    filing(listed, FUND, "0009000001-25-000001", "N-CSR", TODAY - timedelta(days=90))
    coverage.refresh(listed)
    listed.execute("SET LOCAL ROLE ops_reader")
    assert listed.execute("SELECT name FROM ops.coverage_report WHERE company_id = %s",
                          (FUND,)).fetchone() == ("Some Income Fund",)
    assert listed.execute("SELECT companies FROM ops.coverage_summary WHERE kind = 'no_figures'"
                          " AND cause = 'fund'").fetchone() == (1,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), listed.transaction():
        listed.execute("SELECT 1 FROM ops.coverage LIMIT 1")
    listed.execute("RESET ROLE")
    listed.execute("SET LOCAL ROLE query_api")
    counts = listed.execute("SELECT kind, reason, companies, listed"
                            " FROM ops.public_coverage_gaps()").fetchall()
    assert ("no_figures", "out_of_scope", 1, 6) in counts
    listed.execute("RESET ROLE")
    print(coverage.render_summary(coverage.summary(listed)))
    print(coverage.render_rows(coverage.rows(listed, "no_figures")))
    out = capsys.readouterr().out
    assert "no_figures" in out and "Some Income Fund" in out


def test_the_monthly_sample_is_seeded_by_month(listed):
    a = coverage.sample_companies(listed, "2026-11", n=3)
    assert a == coverage.sample_companies(listed, "2026-11", n=3)
    assert len(a) == 3
    months = {tuple(c["company_id"] for c in coverage.sample_companies(listed, m, n=3))
              for m in ("2026-11", "2026-12", "2027-01", "2027-02")}
    assert len(months) > 1
    seen = {c["company_id"]: coverage.shown(listed, c["company_id"]) for c in a}
    text = coverage.checklist("2026-11", a, seen)
    assert text.startswith("# Coverage audit 2026-11")
    assert text.count("\n## ") == 3 and "- [ ] revenue TTM to" in text
