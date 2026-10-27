"""DB fixtures shared by test_pipeline.py and test_golden_10k.py: five companies loaded from
trimmed real companyfacts payloads. Only used by tests that skip without TEST_DATABASE_URL."""

import os
from pathlib import Path

import pytest

from facts_fetcher.db import refresh_gold, refresh_silver, store_payload

NVDA, COST, JPM = "0001045810", "0000909832", "0000019617"
XOM, XOM_HOLDINGS = "0000034088", "0002115436"
HD = "0000354950"
# company_id → CIKs; Exxon's holdings CIK belongs to the original company
COMPANIES = {NVDA: [NVDA], COST: [COST], JPM: [JPM], XOM: [XOM, XOM_HOLDINGS], HD: [HD]}


@pytest.fixture
def companies():
    return COMPANIES


@pytest.fixture
def fixtures_dir():
    """Where `loaded` reads CIK<cik>.json; test_golden_10k.py overrides it."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def conn():
    import psycopg

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as c, \
            c.transaction(force_rollback=True):
        c.execute("TRUNCATE gold.quarterly_financials, gold.derived_financials,"
                  " gold.segment_financials,"
                  " ops.quality_findings, ops.quality_companies, silver.financial_facts,"
                  " bronze.company_facts, silver.company_aliases, silver.company_ciks,"
                  " silver.companies")
        for company_id, ciks in COMPANIES.items():
            c.execute("INSERT INTO silver.companies (company_id, name, current_cik, tracked)"
                      " VALUES (%s, %s, %s, true)", (company_id, company_id, ciks[-1]))
            for cik in ciks:
                c.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                          (cik, company_id))
        yield c


@pytest.fixture
def loaded(conn, fixtures_dir):
    for ciks in COMPANIES.values():
        for cik in ciks:
            store_payload(conn, cik, (fixtures_dir / f"CIK{cik}.json").read_bytes())
        refresh_silver(conn, ciks)
    refresh_gold(conn, list(COMPANIES))
    return conn
