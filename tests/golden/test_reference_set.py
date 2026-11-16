"""Gold against the reference set: figures printed in about 100 companies' latest 10-Ks
(tests/golden/reference_set.csv, README "Reference set").

The companies' real companyfacts payloads (fixtures/, trimmed by trim_facts.py's rule: the mapped
tags, two fiscal years, later filings included) go through bronze -> silver -> gold with their
real SICs, once for the module, and each row's check:

- `annual`: gold's fiscal-year figure equals the printed one within rounding (`tolerance`).
  Flows (revenue, net income, operating cash flow, capex): the TTM ending at fiscal year-end;
  balance-sheet lines: the year-end value.
- `annual_per_share`: diluted EPS as filed, as the API reads it (gold.annual_per_share, 036: the
  10-K's own figure).
- `adjusted`: diluted EPS on today's share basis, as /per-share computes it (query_api.per_share,
  splits from the company's own restatements): the printed figure over the splits since the 10-K.
- `differs`: printed, but gold holds another figure or none: a documented exception (the row's
  notes say why), asserted so a fix shows up here and the row becomes `annual`.

Every future gold rule change must keep this passing; a failure means gold disagrees with the
filing: fix the pipeline, never the CSV. Skipped unless TEST_DATABASE_URL is set; rolled back.
"""

import csv
import gzip
import os
from decimal import Decimal
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="TEST_DATABASE_URL not set")

HERE = Path(__file__).parent
ROWS = list(csv.DictReader((HERE / "reference_set.csv").open(newline="")))
COMPANIES = list(csv.DictReader((HERE / "reference_companies.csv").open(newline="")))
USER = "reference-set"
FLOWS = {"revenue", "net_income", "operating_cash_flow", "capex"}
CHECKS = {"annual", "annual_per_share", "adjusted", "differs"}


@pytest.fixture(scope="module")
def gold():
    from facts_fetcher.db import refresh_gold, refresh_silver, store_payload

    loaded = [c for c in COMPANIES if c["ciks"]]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn, \
            conn.transaction(force_rollback=True):
        conn.execute("TRUNCATE gold.quarterly_financials, gold.derived_financials,"
                     " gold.segment_financials, ops.quality_findings, ops.quality_companies,"
                     " silver.financial_facts, bronze.company_facts, silver.company_aliases,"
                     " silver.company_ciks, silver.companies")
        for c in loaded:
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
            # the API's reads (gold.annual_per_share) apply the access rule: a reader of all
            conn.execute("INSERT INTO authz.public_access (company_id, tier)"
                         " VALUES (%s, 'financials') ON CONFLICT DO NOTHING", (c["company_id"],))
        conn.execute("INSERT INTO authz.user_tiers (user_id, tier) VALUES (%s, 'financials')"
                     " ON CONFLICT DO NOTHING", (USER,))
        ids = [c["company_id"] for c in loaded]
        for i in range(0, len(ids), 25):
            refresh_gold(conn, ids[i:i + 25])
        conn.execute("SELECT set_config('app.user_id', %s, true)", (USER,))
        yield conn


def row_id(r: dict) -> str:
    return f"{r['ticker']}-{r['metric']}"


def tolerance(r: dict) -> Decimal:
    """Within rounding: a flow is gold's TTM, four quarters each rounded as filed (Q4 is the year
    less nine months), so up to two printed units; a balance can be a later filing's comparative,
    re-rounded, one unit; EPS half a cent (over the split ratio when adjusted)."""
    unit = Decimal(r["printed_unit"])
    if r["metric"] in FLOWS:
        return 2 * unit
    if r["metric"].startswith("eps"):
        return unit / 2 + Decimal("0.0001")
    return unit


def gold_value(conn, r: dict) -> Decimal | None:
    company, metric, end = r["company_id"], r["metric"], r["period_end"]
    if metric in FLOWS:
        row = conn.execute(
            "SELECT value FROM gold.derived_financials WHERE company_id = %s AND span = 'ttm'"
            " AND metric = %s AND period_end BETWEEN %s::date - 3 AND %s::date + 3"
            " ORDER BY abs(period_end - %s::date) LIMIT 1",
            (company, metric, end, end, end)).fetchone()
    elif metric == "eps_diluted":
        row = conn.execute(
            "SELECT value FROM gold.annual_per_share(%s, 5) WHERE metric = 'eps_diluted'"
            " AND period_end BETWEEN %s::date - 7 AND %s::date + 7", (company, end, end)).fetchone()
    elif metric == "eps_diluted_adjusted":
        return adjusted(conn, company, end)
    else:
        row = conn.execute(
            "SELECT value FROM gold.quarterly_financials WHERE company_id = %s AND metric = %s"
            " AND period_end BETWEEN %s::date - 3 AND %s::date + 3"
            " ORDER BY abs(period_end - %s::date) LIMIT 1",
            (company, metric, end, end, end)).fetchone()
    return None if row is None else row[0]


def adjusted(conn, company: str, end: str) -> Decimal | None:
    """/per-share's annual diluted EPS, split-adjusted, from the same reads as
    Store.per_share_inputs (one company, the access rule applied)."""
    from datetime import date

    from psycopg.rows import dict_row

    from query_api import per_share

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT metric, period_start, period_end, fiscal_year, fiscal_quarter, calendar_year,"
            " calendar_quarter, calendar_primary, value, unit, value_source, source_tag,"
            " accession_no, form, filed, source_accessions, ingested_at, NULL AS filer_cik"
            " FROM gold.financials WHERE company_id = %s AND span = 'quarter'"
            " AND metric = ANY(%s) ORDER BY metric, period_end DESC",
            (company, list(per_share.INPUT_METRICS)))
        quarters = cur.fetchall()
        cur.execute("SELECT * FROM gold.annual_per_share(%s, 30) ORDER BY metric, period_end DESC",
                    (company,))
        annual = cur.fetchall()
        cur.execute("SELECT company_id, check_name, metric, period_end, severity, detail"
                    " FROM ops.quality_findings WHERE company_id = %s"
                    " AND check_name = 'restatement' AND metric = ANY(%s)",
                    (company, list(per_share.FINDING_METRICS)))
        findings = cur.fetchall()
    out = per_share.compute({"quarters": quarters, "annual": annual, "findings": findings},
                            "annual", "adjusted")
    day = date.fromisoformat(end)
    for r in out["rows"]:
        e = r["period_end"]
        e = date.fromisoformat(str(e)[:10]) if not isinstance(e, date) else e
        if r["metric"] == "eps_diluted" and abs((e - day).days) <= 7:
            return Decimal(str(r["value"]))
    return None


def test_the_csv_is_well_formed():
    assert {r["check"] for r in ROWS} <= CHECKS
    assert all(r["accession_no"] and r["line_label"] for r in ROWS)
    assert all(r["notes"].strip() for r in ROWS if r["check"] == "differs")
    assert len({(r["ticker"], r["metric"]) for r in ROWS}) == len(ROWS)
    tickers = {c["ticker"] for c in COMPANIES}
    assert {r["ticker"] for r in ROWS} <= tickers
    # every company is either read or a documented exception (README "Exceptions")
    for c in COMPANIES:
        assert bool(c["exception"]) != any(r["ticker"] == c["ticker"] for r in ROWS), c


@pytest.mark.parametrize("r", ROWS, ids=row_id)
def test_printed_figure(gold, r):
    value = gold_value(gold, r)
    printed = Decimal(r["value"])
    if r["check"] == "differs":
        assert value is None or abs(value - printed) > tolerance(r), (
            f"gold now matches the filing ({value}): make the row `{expected(r)}`")
        return
    assert value is not None, f"no gold {r['metric']} at {r['period_end']}"
    assert abs(value - printed) <= tolerance(r), (
        f"gold {value} vs printed {printed} ({r['line_label']}, {r['accession_no']})")


def expected(r: dict) -> str:
    return {"eps_diluted": "annual_per_share", "eps_diluted_adjusted": "adjusted"}.get(
        r["metric"], "annual")
