"""Point in time (B1, migration 086) on real companies: the reference set's companyfacts
payloads (fixtures/, test_reference_set.py's) through bronze -> silver -> gold, then every company's
history replayed (gold.rebuild_known).

- No look-ahead: as of every filing date D and the day before it, no figure, citation or input
  filing in the history was filed after D, and the history equals gold's own pipeline run over the
  facts filed by D (gold.financial_rows), row for row; the open versions equal gold today.
- Original vs restated: CrowdStrike's Q4 FY2025 net income as first filed (-92,282,000, 10-K of
  2025-03-10) until the FY2026 10-K restated it (-88,252,000, 2026-03-05), each citing its filing.
- The incremental path: a later filing's facts change only the history from its date on.

The reads as of a day (087): test_point_in_time_reads.py. Skipped unless TEST_DATABASE_URL is set;
rolled back."""

import csv
import gzip
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="TEST_DATABASE_URL not set")

HERE = Path(__file__).parent
TICKERS = {"CRWD", "BKNG", "PLXS", "NFLX", "ZION", "FDX", "AAPL"}
COMPANIES = [c for c in csv.DictReader((HERE / "reference_companies.csv").open(newline=""))
             if c["ticker"] in TICKERS]
IDS = {c["ticker"]: c["company_id"] for c in COMPANIES}
USER = "point-in-time"
KEY = "span, metric, period_end, period_start, value, unit, value_source, source_tag"


@pytest.fixture(scope="module")
def gold():
    from facts_fetcher.db import refresh_gold, refresh_silver, store_payload

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn, \
            conn.transaction(force_rollback=True):
        conn.execute("TRUNCATE gold.quarterly_financials, gold.derived_financials,"
                     " gold.segment_financials, ops.quality_findings, ops.quality_companies,"
                     " silver.financial_facts, bronze.company_facts, silver.company_aliases,"
                     " silver.company_ciks, silver.companies, gold.financials_known,"
                     " gold.known_companies")
        conn.execute("DELETE FROM ops.company_builds")
        for c in COMPANIES:
            ciks = c["ciks"].split()
            conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, tracked,"
                         " sic) VALUES (%s, %s, %s, true, %s)",
                         (c["company_id"], c["name"], ciks[-1], c["sic"] or None))
            for cik in ciks:
                conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                             (cik, c["company_id"]))
                store_payload(conn, cik, gzip.decompress(
                    (HERE / "fixtures" / f"CIK{cik}.json.gz").read_bytes()))
            refresh_silver(conn, ciks)
            conn.execute("INSERT INTO authz.public_access (company_id, tier)"
                         " VALUES (%s, 'financials') ON CONFLICT DO NOTHING", (c["company_id"],))
        conn.execute("INSERT INTO authz.user_tiers (user_id, tier) VALUES (%s, 'financials')"
                     " ON CONFLICT DO NOTHING", (USER,))
        ids = sorted(IDS.values())
        refresh_gold(conn, ids)
        conn.execute("SELECT gold.rebuild_known(%s)", (ids,))
        yield conn


def dates(conn, company: str) -> list[date]:
    return [r[0] for r in conn.execute(
        "SELECT filed FROM gold.knowledge_facts(%s) ORDER BY 1", (company,))]


def test_no_look_ahead_and_the_history_is_the_pipeline_at_every_date(gold):
    checked = 0
    for ticker, company in sorted(IDS.items()):
        days = dates(gold, company)
        assert len(days) >= 5, ticker
        for d in sorted({*days, *(x - timedelta(days=1) for x in days)}):
            late = gold.execute(
                """
                SELECT count(*) FROM gold.financials_known f
                WHERE f.company_id = %(c)s AND f.known_from <= %(d)s
                  AND (f.known_to IS NULL OR f.known_to > %(d)s)
                  AND (f.filed > %(d)s
                       OR EXISTS (SELECT 1 FROM unnest(f.source_accessions) a
                                  JOIN silver.financial_facts s ON s.accession_no = a
                                  WHERE s.filed > %(d)s))
                """, {"d": d, "c": company}).fetchone()[0]
            assert late == 0, (ticker, d)
            diff = gold.execute(
                f"""
                WITH h AS (SELECT {KEY}, gold.input_values(inputs) AS iv
                           FROM gold.financials_known
                           WHERE company_id = %(c)s AND known_from <= %(d)s
                             AND (known_to IS NULL OR known_to > %(d)s)),
                p AS (SELECT {KEY}, gold.input_values(inputs) AS iv
                      FROM gold.financial_rows(ARRAY[%(c)s], %(d)s))
                SELECT (SELECT count(*) FROM (SELECT * FROM h EXCEPT ALL SELECT * FROM p) x),
                       (SELECT count(*) FROM (SELECT * FROM p EXCEPT ALL SELECT * FROM h) x),
                       (SELECT count(*) FROM h)
                """, {"d": d, "c": company}).fetchone()
            assert diff[:2] == (0, 0), (ticker, d, diff)
            checked += diff[2]
        # today's open versions are gold itself
        today = gold.execute(
            f"""
            WITH h AS (SELECT {KEY} FROM gold.financials_known
                       WHERE company_id = %(c)s AND known_to IS NULL),
            g AS (SELECT 'quarter' AS span, metric, period_end, period_start, value, unit,
                         value_source, source_tag FROM gold.quarterly_financials
                  WHERE company_id = %(c)s
                  UNION ALL
                  SELECT span, metric, period_end, period_start, value, unit, value_source,
                         source_tag FROM gold.derived_financials WHERE company_id = %(c)s)
            SELECT (SELECT count(*) FROM (SELECT * FROM h EXCEPT ALL SELECT * FROM g) x)
                 + (SELECT count(*) FROM (SELECT * FROM g EXCEPT ALL SELECT * FROM h) x)
            """, {"c": company}).fetchone()[0]
        assert today == 0, ticker
    assert checked > 10_000


def figure(conn, d, company, metric, end, span="quarter"):
    """A figure as believed at the end of day d: the version open then."""
    return conn.execute(
        "SELECT value, accession_no, filed, known_from FROM gold.financials_known"
        " WHERE company_id = %s AND span = %s AND metric = %s AND period_end = %s"
        " AND known_from <= %s AND (known_to IS NULL OR known_to > %s)",
        (company, span, metric, end, d, d)).fetchone()


def test_original_then_restated_each_with_its_filing(gold):
    crwd, end = IDS["CRWD"], date(2025, 1, 31)
    assert figure(gold, date(2025, 3, 9), crwd, "net_income", end) is None  # not filed yet
    first = figure(gold, date(2025, 3, 10), crwd, "net_income", end)
    assert first[0] == Decimal(-92282000) and first[2] == date(2025, 3, 10)
    assert figure(gold, date(2026, 3, 4), crwd, "net_income", end) == first
    later = figure(gold, date(2026, 3, 5), crwd, "net_income", end)
    assert later[0] == Decimal(-88252000) and later[2] == date(2026, 3, 5)
    assert later[3] == date(2026, 3, 5) and later[1] != first[1]
    # today's gold is the restated figure
    assert gold.execute("SELECT value FROM gold.quarterly_financials WHERE company_id = %s"
                        " AND metric = 'net_income' AND period_end = %s",
                        (crwd, end)).fetchone()[0] == Decimal(-88252000)


def test_a_new_filing_changes_only_the_history_from_its_date(gold):
    crwd = IDS["CRWD"]
    with gold.transaction(force_rollback=True):
        before = gold.execute("SELECT * FROM gold.financials_known WHERE company_id = %s"
                              " ORDER BY span, metric, period_end, known_from",
                              (crwd,)).fetchall()
        assert gold.execute("SELECT gold.refresh_known(%s)", ([crwd],)).fetchone()[0] == 0
        # a 10-Q filed after every fixture filing restates one quarter's revenue
        cik = gold.execute("SELECT cik FROM silver.company_ciks WHERE company_id = %s",
                           (crwd,)).fetchone()[0]
        gold.execute(
            """
            INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,
                                                value, accession_no, form, filed, taxonomy)
            SELECT cik, metric, tag, unit, period_start, period_end, value + 1000000,
                   '0001535527-26-999999', '10-Q', DATE '2026-12-01', taxonomy
            FROM silver.financial_facts
            WHERE cik = %s AND metric = 'revenue' AND period_end = DATE '2025-10-31'
              AND period_type = 'quarter'
            ORDER BY filed DESC LIMIT 1
            """, (cik,))
        written = gold.execute("SELECT gold.refresh_financials(%s)", ([crwd],)).fetchone()[0]
        assert written > 0
        after = gold.execute("SELECT * FROM gold.financials_known WHERE company_id = %s"
                             " ORDER BY span, metric, period_end, known_from",
                             (crwd,)).fetchall()
        # rows are (company_id, span, metric, period_end, known_from, known_to, ...)
        new = [r for r in after if r not in before]
        gone = [r for r in before if r not in after]
        assert gone and all(r[5] is None for r in gone)  # only versions believed until then
        assert all(r[4] == date(2026, 12, 1) or r[5] == date(2026, 12, 1) for r in new)
        # as of the day before, nothing moved
        assert figure(gold, date(2026, 11, 30), crwd, "revenue", date(2025, 10, 31))[0] \
            != figure(gold, date(2026, 12, 1), crwd, "revenue", date(2025, 10, 31))[0]
