"""parser-index against a real Postgres (migration 021) with real NVIDIA filings; skipped unless
TEST_DATABASE_URL is set. Every test runs inside a transaction that is rolled back."""

import gzip
import os
from functools import cache
from pathlib import Path

import pytest

from filing_parser import index
from filing_parser.db import write_sections
from filing_parser.diff import diff
from filing_parser.sections import Section, parse

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

FIXTURES = Path(__file__).parent / "fixtures"
NVDA = "0001045810"
# accession, form, filed, period of report, fixture
K25 = ("0001045810-25-000023", "10-K", "2025-02-26", "2025-01-26", "nvda-10-K-prior")
K26 = ("0001045810-26-000021", "10-K", "2026-02-25", "2026-01-25", "nvda-10-K")
Q25 = ("0001045810-25-000209", "10-Q", "2025-08-27", "2025-07-27", "nvda-10-Q-year-ago")
Q26A = ("0001045810-26-000052", "10-Q", "2026-05-20", "2026-04-26", "nvda-10-Q-prior")
Q26B = ("0001045810-26-000075", "10-Q", "2026-08-26", "2026-07-26", "nvda-10-Q")


@cache
def sections(fixture: str, form: str) -> list[Section]:
    return parse(gzip.decompress((FIXTURES / f"{fixture}.htm.gz").read_bytes()), form)


def store(conn, filing) -> None:
    acc, form, filed, period, fixture = filing
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, period_of_report,"
        " document_name, url, content_gzip, content_sha256, size_bytes) VALUES (%s, %s, %s, %s,"
        " %s, 'd.htm', %s, '\\x00', 'x', 1)",
        (acc, NVDA, form, filed, period, f"https://example/{acc}"))
    write_sections(conn, acc, sections(fixture, form))


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE silver.filing_sections, bronze.filing_documents, ops.section_index,"
                  " gold.section_search, gold.section_diffs, gold.section_changes,"
                  " silver.company_aliases, silver.company_ciks, silver.companies CASCADE")
        c.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                  " VALUES (%s, 'NVIDIA CORP', %s)", (NVDA, NVDA))
        c.execute("INSERT INTO silver.company_ciks VALUES (%s, %s)", (NVDA, NVDA))
        yield c


def state(conn, acc):
    return conn.execute("SELECT prior_accession_no, year_ago_accession_no, algorithm"
                        " FROM ops.section_index WHERE accession_no = %s", (acc,)).fetchone()


def summary(conn, acc):
    return {(k, b): (p, n) for k, b, p, n in conn.execute(
        "SELECT section_key, basis, prior_accession_no, new FROM gold.section_diffs"
        " WHERE accession_no = %s", (acc,)).fetchall()}


def test_indexes_passages_and_diffs_against_comparable_filings(conn):
    for f in (K25, K26, Q25, Q26A, Q26B):
        store(conn, f)
    stats = index.run(conn)
    assert stats.completed and stats.indexed == 5 and stats.errors == 0
    assert state(conn, K26[0])[:2] == (K25[0], None)
    assert state(conn, Q26B[0])[:2] == (Q26A[0], Q25[0])
    assert state(conn, K25[0])[:2] == (None, None)
    assert set(summary(conn, Q26B[0])) == {("I-2", "prior"), ("II-1A", "prior"),
                                          ("I-2", "year_ago"), ("II-1A", "year_ago")}

    # stored counts are the pure diff's
    new = {s.key: s.text for s in sections("nvda-10-K", "10-K")}
    old = {s.key: s.text for s in sections("nvda-10-K-prior", "10-K")}
    d = diff(old["1A"], new["1A"])
    assert conn.execute(
        "SELECT paragraphs, unchanged, rewritten, new, removed FROM gold.section_diffs"
        " WHERE accession_no = %s AND section_key = '1A'", (K26[0],)
    ).fetchone() == (d.paragraphs, d.unchanged, d.rewritten, d.new, d.removed)

    # spans point into the served section text
    text = conn.execute("SELECT text FROM gold.filing_sections WHERE accession_no = %s"
                        " AND section_key = '1A'", (K26[0],)).fetchone()[0]
    spans = [r[0] for r in conn.execute(
        "SELECT new_spans FROM gold.section_changes WHERE accession_no = %s"
        " AND section_key = '1A' AND kind = 'new' ORDER BY ordinal", (K26[0],)).fetchall()]
    assert any("Groq" in text[s[0]:s[1]] for s in spans)

    # passages cover every section and find words
    assert conn.execute(
        "SELECT count(DISTINCT (accession_no, section_key)) FROM gold.section_search"
    ).fetchone()[0] == conn.execute("SELECT count(*) FROM gold.filing_sections").fetchone()[0]
    hits = conn.execute(
        "SELECT DISTINCT accession_no, section_key FROM gold.section_search"
        " WHERE tsv @@ websearch_to_tsquery('english', 'Groq license')").fetchall()
    assert (K26[0], "1A") in hits


def test_rerun_does_nothing(conn):
    for f in (K25, K26):
        store(conn, f)
    index.run(conn)
    before = conn.execute("SELECT computed_at FROM gold.section_diffs ORDER BY 1").fetchall()
    stats = index.run(conn)
    assert stats.completed and stats.filings == 0
    assert conn.execute("SELECT computed_at FROM gold.section_diffs ORDER BY 1"
                        ).fetchall() == before


def test_prior_arriving_later_triggers_a_new_diff(conn):
    store(conn, K26)  # the crawl goes newest first
    index.run(conn)
    assert state(conn, K26[0])[:2] == (None, None) and summary(conn, K26[0]) == {}
    store(conn, K25)
    stats = index.run(conn)
    assert stats.completed
    assert state(conn, K26[0])[:2] == (K25[0], None)
    assert set(summary(conn, K26[0])) == {("1A", "prior"), ("7", "prior")}


def test_reparse_reindexes_the_filing_and_its_successors(conn):
    for f in (K25, K26):
        store(conn, f)
    index.run(conn)
    store_again = sections("nvda-10-K-prior", "10-K")
    write_sections(conn, K25[0], store_again)  # re-parse: new parsed_at
    conn.execute("UPDATE silver.filing_sections SET parsed_at = now() + interval '1 minute'"
                 " WHERE accession_no = %s", (K25[0],))
    stats = index.run(conn)
    assert stats.indexed == 1  # K25 re-indexed
    assert stats.diffed == 2  # K25 (no prior) and K26, whose prior changed
    prior_parsed = conn.execute(
        "SELECT DISTINCT prior_parsed_at = (SELECT max(parsed_at) FROM silver.filing_sections"
        " WHERE accession_no = %s) FROM gold.section_diffs WHERE accession_no = %s",
        (K25[0], K26[0])).fetchall()
    assert prior_parsed == [(True,)]


def test_algorithm_change_recomputes_diffs(conn, monkeypatch):
    for f in (K25, K26):
        store(conn, f)
    index.run(conn)
    monkeypatch.setattr(index, "ALGORITHM", "diff-test")
    stats = index.run(conn)
    assert stats.indexed == 0 and stats.diffed == 2
    assert state(conn, K26[0])[2] == "diff-test"


def test_status_views_count_without_text(conn):
    for f in (K25, K26):
        store(conn, f)
    index.run(conn)
    row = conn.execute("SELECT filings, comparable_filings, diffed_filings, without_prior,"
                       " section_diffs FROM ops.section_index_status").fetchone()
    assert row == (2, 2, 1, 1, 2)
    cols = [c.name for c in conn.execute(
        "SELECT * FROM ops.section_change_summary LIMIT 0").description]
    assert "text" not in cols and "new" in cols
