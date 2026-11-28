"""The proof layer's CI check (migrations 166-168): every fixture filing that prints inline XBRL,
parsed, with the silver facts hermes holds for it (fixtures/gate/silver-facts.json.gz, saved
2026-11-28, public SEC data), gated. It fails when a fixture filing's figures stop linking to the
line that prints them, or start disagreeing with it: a parser, gate or format-reading change that
loses the link shows here first. Skipped unless TEST_DATABASE_URL is set; rolled back.

NVIDIA's four filings are whole documents: every fact agrees and has its line. JPMorgan's 10-Q is
trimmed to whole pages (fixtures/jpm-10-Q.htm.gz): its facts on the pages kept link, those of the
pages cut are not located, its two CET1 member picks are not applicable."""

import gzip
import json
import os
from pathlib import Path

import pytest

from filing_parser.db import write_sections
from filing_parser.sections import parse_document

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

FIXTURES = Path(__file__).parent / "fixtures"
FACTS = json.loads(gzip.decompress((FIXTURES / "gate" / "silver-facts.json.gz").read_bytes()))
# accession -> (fixture, form, filer CIK, filed)
FILINGS = {
    "0001045810-26-000075": ("nvda-10-Q", "10-Q", "0001045810", "2026-08-26"),
    "0001045810-26-000052": ("nvda-10-Q-prior", "10-Q", "0001045810", "2026-05-20"),
    "0001045810-26-000021": ("nvda-10-K", "10-K", "0001045810", "2026-02-25"),
    "0001045810-25-000023": ("nvda-10-K-prior", "10-K", "0001045810", "2025-02-26"),
    "0001628280-26-069523": ("jpm-10-Q", "10-Q", "0000019617", "2026-11-03"),
}
JPM = "0001628280-26-069523"


@pytest.fixture(scope="module")
def gated():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE silver.filing_sections, bronze.filing_documents,"
                  " silver.financial_facts, silver.company_aliases, silver.company_ciks,"
                  " silver.companies CASCADE")
        for cik, name in (("0001045810", "NVIDIA CORP"), ("0000019617", "JPMORGAN CHASE & CO")):
            c.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                      " VALUES (%s, %s, %s)", (cik, name, cik))
            c.execute("INSERT INTO silver.company_ciks VALUES (%s, %s)", (cik, cik))
        for acc, (name, form, cik, filed) in FILINGS.items():
            raw = gzip.decompress((FIXTURES / f"{name}.htm.gz").read_bytes())
            c.execute(
                "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed,"
                " document_name, url, content_gzip, content_sha256, size_bytes)"
                " VALUES (%s, %s, %s, %s, 'd.htm', 'https://example/d', %s, 'x', %s)",
                (acc, cik, form, filed, gzip.compress(raw), len(raw)))
            sections, printed = parse_document(raw, form)
            write_sections(c, acc, sections, printed)
            for f in FACTS[acc]:
                c.execute(
                    "INSERT INTO silver.financial_facts (cik, metric, tag, taxonomy, unit,"
                    " period_start, period_end, value, accession_no, form, filed, source)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (f["cik"], f["metric"], f["tag"], f["taxonomy"], f["unit"], f["period_start"],
                     f["period_end"], f["value"], acc, form, filed, f["source"]))
        c.execute("SELECT silver.gate_facts(%s)", (list(FILINGS),))
        yield c


def counts(conn, acc):
    return conn.execute("SELECT facts, agrees, disagrees, not_located, not_applicable"
                        " FROM silver.fact_gates WHERE accession_no = %s", (acc,)).fetchone()


def lines(conn, acc):
    (rows,) = conn.execute("SELECT lines FROM silver.fact_gates WHERE accession_no = %s",
                           (acc,)).fetchone()
    return rows


@pytest.mark.parametrize("acc", [a for a in FILINGS if a != JPM])
def test_every_fact_of_a_whole_filing_agrees_at_its_line(gated, acc):
    facts = len(FACTS[acc])
    assert counts(gated, acc) == (facts, facts, 0, 0, 0)
    assert all(r["st"] == "a" and "k" in r for r in lines(gated, acc))


def test_jpmorgans_statements_link_now_that_rule_15_finds_them(gated):
    facts, agrees, disagrees, not_located, not_applicable = counts(gated, JPM)
    assert (facts, disagrees, not_applicable) == (102, 0, 2)
    assert agrees >= 72 and agrees + not_located == 100
    found = {(r["t"], r.get("s"), r["e"]): r for r in lines(gated, JPM)}
    net = found[("NetIncomeLoss", "2026-07-01", "2026-09-30")]
    assert (net["st"], net["k"]) == ("a", "I-1")
    text, start = gated.execute("SELECT text, char_start FROM silver.filing_sections"
                                " WHERE accession_no = %s AND section_key = 'I-1'",
                                (JPM,)).fetchone()
    assert text[net["a"] - start:net["b"] - start] == "15,305"


def test_the_gold_line_and_trust_on_the_fixtures(gated):
    """A gold row on each fixture fact reads its line through gold.printed_lines; the trust count
    over them is the CI-visible number: every NVIDIA figure linked and agreeing."""
    keys = [(acc, f["tag"], f["unit"], f["period_start"], f["period_end"])
            for acc in FILINGS if acc != JPM for f in FACTS[acc]]
    rows = gated.execute(
        "SELECT count(*), count(*) FILTER (WHERE status = 'a' AND section_key IS NOT NULL)"
        " FROM gold.printed_lines(%s, %s, %s, %s::date[], %s::date[])",
        ([k[0] for k in keys], [k[1] for k in keys], [k[2] for k in keys],
         [k[3] for k in keys], [k[4] for k in keys])).fetchone()
    assert rows == (len(keys), len(keys))
