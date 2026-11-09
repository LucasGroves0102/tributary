"""parser-peers against a real Postgres (migration 047), on real Item 1 openings; skipped unless
TEST_DATABASE_URL is set. Every test runs inside a transaction that is rolled back."""

import json
import os
from pathlib import Path

import pytest

from filing_parser import business as b

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

FIX = Path(__file__).parent / "fixtures" / "business"
INDEX = json.loads((FIX / "index.json").read_text())


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(b, "MIN_DF", 2)
    monkeypatch.setattr(b, "MAX_DF_SHARE", 0.6)
    monkeypatch.setattr(b, "COMMON_SHARE", 0.9)
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE silver.filing_sections, bronze.filing_documents, gold.business_peers,"
                  " gold.business_mentions, gold.business_profiles, gold.quarterly_financials,"
                  " silver.company_aliases, silver.company_ciks, silver.companies CASCADE")
        for key, v in INDEX.items():
            cid = v["company_id"]
            c.execute("INSERT INTO silver.companies (company_id, name, current_cik, sic)"
                      " VALUES (%s, %s, %s, %s)", (cid, v["name"], cid, v["sic"]))
            c.execute("INSERT INTO silver.company_ciks VALUES (%s, %s)", (cid, cid))
            c.execute("INSERT INTO silver.company_aliases (company_id, cik, alias_type, alias,"
                      " source) VALUES (%s, %s, 'name', %s, 'sec_submissions')",
                      (cid, cid, v["name"]))
            c.execute("INSERT INTO gold.quarterly_financials (company_id, metric, period_start,"
                      " period_end, value, unit, value_source, source_tag, accession_no, filed,"
                      " source_accessions, ingested_at) VALUES (%s, 'revenue', '2026-01-01',"
                      " '2026-03-31', 1, 'USD', 'reported', 'x', 'x', '2026-05-01', '{x}', now())",
                      (cid,))
            store(c, key, v)
        yield c


def store(c, key, v, form="10-K", filed=None):
    text = (FIX / f"{key}.txt").read_text()
    title = "Financial Statements" if key == "mchp" else "Business"
    c.execute("INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name,"
              " url, content_gzip, content_sha256, size_bytes) VALUES (%s, %s, %s, %s, 'd.htm',"
              " 'u', '\\x00', 'x', 1)", (v["accession_no"], v["company_id"], form,
                                          filed or "2026-02-20"))
    c.execute("INSERT INTO silver.filing_sections (accession_no, section_key, parser,"
              " parser_version, item, title, text, char_start, char_end) VALUES (%s, '1', 'rust',"
              " 'x', '1', %s, %s, 0, %s)", (v["accession_no"], title, text, len(text)))


def rows(c, sql, *args):
    return c.execute(sql, args).fetchall()


def test_run_writes_profiles_peers_and_mentions(conn):
    counts = b.run(conn)
    # Intel's Item 1 is a cross-reference, Microchip's a mis-parsed balance sheet: no profile
    assert counts["profiles"] == 8
    profiled = {r[0] for r in rows(conn, "SELECT company_id FROM gold.business_profiles")}
    assert not {INDEX["intc"]["company_id"], INDEX["mchp"]["company_id"]} & profiled
    vrt, etn = INDEX["vrt"]["company_id"], INDEX["etn"]["company_id"]
    (acc, terms, algo), = rows(conn, "SELECT accession_no, terms, algorithm FROM"
                                     " gold.business_profiles WHERE company_id = %s", vrt)
    assert acc == INDEX["vrt"]["accession_no"] and terms and algo == b.ALGORITHM
    peers = rows(conn, "SELECT peer_id, similarity, terms FROM gold.business_peers"
                       " WHERE company_id = %s ORDER BY similarity DESC", vrt)
    assert peers and all(0 <= s <= 1 for _, s, _ in peers)
    (named, start, end), = rows(conn, "SELECT named_id, char_start, char_end FROM"
                                      " gold.business_mentions WHERE company_id = %s"
                                      " AND named_id = %s", vrt, etn)
    text = (FIX / "vrt.txt").read_text()
    assert "Eaton" in text[start:end]
    # a second run replaces everything (same answer, no duplicates)
    again = b.run(conn)
    assert again == counts
    assert rows(conn, "SELECT count(*) FROM gold.business_peers")[0][0] == counts["peers"]


def test_an_older_usable_10k_stands_in_for_a_stub(conn):
    """Intel's latest Item 1 is a stub; an older 10-K with a real one is used instead."""
    v = {**INDEX["intc"], "accession_no": "0000050863-25-000009"}
    store(conn, "nvt", v, filed="2025-01-30")
    b.run(conn)
    (acc,), = rows(conn, "SELECT accession_no FROM gold.business_profiles WHERE company_id = %s",
                   v["company_id"])
    assert acc == "0000050863-25-000009"


def test_query_api_reads_them_under_rls(conn):
    """Readable at `filings` only: a peer row needs both companies readable."""
    b.run(conn)
    vrt, etn = INDEX["vrt"]["company_id"], INDEX["etn"]["company_id"]
    conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier) VALUES"
                 " ('u-f', %s, 'filings'), ('u-f', %s, 'filings'), ('u-fin', %s, 'financials')",
                 (vrt, etn, vrt))
    conn.execute("SET LOCAL ROLE query_api")
    conn.execute("SELECT set_config('app.user_id', 'u-f', true)")
    assert {r[0] for r in rows(conn, "SELECT peer_id FROM gold.business_peers")} == {etn, vrt}
    assert {r[0] for r in rows(conn, "SELECT company_id FROM gold.business_profiles")} == {vrt, etn}
    assert rows(conn, "SELECT count(*) FROM gold.business_mentions WHERE company_id = %s",
                vrt)[0][0] >= 1
    conn.execute("SELECT set_config('app.user_id', 'u-fin', true)")
    for table in ("business_peers", "business_profiles", "business_mentions"):
        assert rows(conn, f"SELECT count(*) FROM gold.{table}")[0][0] == 0, table
    conn.execute("SELECT set_config('app.user_id', '', true)")
    assert rows(conn, "SELECT count(*) FROM gold.business_peers")[0][0] == 0
