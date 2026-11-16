"""The parser-output checks in the database (migration 072: ops.check_sections, run by
section-index and the catch-up); skipped unless TEST_DATABASE_URL is set. Every test runs inside a
transaction that is rolled back."""

import gzip
import itertools
import os
from pathlib import Path

import pytest

from filing_parser import catchup, index
from filing_parser.db import write_sections
from filing_parser.sections import parse

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

FIXTURES = Path(__file__).parent / "fixtures"
CO = "9000000072"
LONG = "Revenue grew in the quarter as demand for accelerated computing rose. " * 6


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE silver.filing_sections, bronze.filing_documents, ops.section_index,"
                  " gold.section_search, gold.section_diffs, gold.section_changes,"
                  " ops.section_findings, ops.section_checks, ops.catchup_runs,"
                  " silver.company_aliases, silver.company_ciks, silver.companies CASCADE")
        c.execute("UPDATE ops.catchup SET paused = false, version = NULL, started_at = NULL")
        c.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                  " VALUES (%s, 'Test Co', %s)", (CO, CO))
        c.execute("INSERT INTO silver.company_ciks VALUES (%s, %s)", (CO, CO))
        yield c


def doc(conn, acc: str, form: str, sections: dict[str, str] | None = None,
        filed: str = "2026-05-01", fetched: str = "2026-05-02", parser: str = "rust") -> None:
    """A stored document and its sections (key -> text), as one parser wrote them."""
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name, url,"
        " content_gzip, content_sha256, size_bytes, fetched_at) VALUES (%s, %s, %s, %s, 'd.htm',"
        " %s, '\\x00', 'x', 1, %s)", (acc, CO, form, filed, f"https://e/{acc}", fetched))
    for n, (key, text) in enumerate((sections or {}).items()):
        conn.execute(
            "INSERT INTO silver.filing_sections (accession_no, section_key, parser, parser_version,"
            " part, item, title, text, char_start, char_end) VALUES (%s, %s, %s, %s, NULL, %s,"
            " '', %s, %s, %s)", (acc, key, parser, f"{parser}-7", key, text, n * 1000,
                                 n * 1000 + len(text)))


def findings(conn, acc: str | None = None) -> dict[tuple[str, str], tuple]:
    return {(a, c): (s, d) for a, c, s, d in conn.execute(
        "SELECT accession_no, check_name, severity, detail FROM ops.section_findings"
        " WHERE %s::text IS NULL OR accession_no = %s ORDER BY 1, 2", (acc, acc))}


def check(conn, *accessions: str) -> int:
    return conn.execute("SELECT ops.check_sections(%s)", (list(accessions),)).fetchone()[0]


def test_keys_the_form_does_not_have(conn):
    doc(conn, "8k", "8-K", {"7": "Item 7.01Regulation FD Disclosure. " + LONG, "9.01": LONG + "x",
                           "EX-99.1": LONG + "y"})
    doc(conn, "10q", "10-Q", {"I-1": LONG, "I-2": LONG + "a", "I-6": LONG + "b",
                              "II-5.02": LONG + "c"})
    doc(conn, "10k", "10-K", {"1": LONG, "1A": LONG + "a", "4A": LONG + "b", "7": LONG + "c",
                              "8": LONG + "d"})
    assert check(conn, "8k", "10q", "10k") == 2
    assert findings(conn) == {
        ("8k", "invalid_key"): ("error", {"keys": ["7"], "count": 1}),
        ("10q", "invalid_key"): ("error", {"keys": ["I-6", "II-5.02"], "count": 2})}


def test_core_items_missing_unless_amended_or_cross_referenced(conn):
    doc(conn, "k", "10-K", {"1": LONG, "1A": LONG + "a", "7": LONG + "b"})
    doc(conn, "ka", "10-K/A", {"10": LONG})
    doc(conn, "kx", "10-K", {"1": "Form 10-K Cross-Reference Index. " + LONG})
    doc(conn, "q", "10-Q", {"I-2": LONG, "II-1A": LONG + "a"})
    check(conn, "k", "ka", "kx", "q")
    assert findings(conn) == {
        ("k", "missing_core_item"): ("warning", {"missing": ["8"]}),
        ("q", "missing_core_item"): ("warning", {"missing": ["I-1"]})}


def test_empty_and_repeated_sections(conn):
    doc(conn, "8k", "8-K", {"2.02": "Item 2.02.", "7.01": "Item 7.01 " + LONG,
                           "EX-99.1": "", "EX-99.2": LONG, "EX-99.3": LONG})
    doc(conn, "10k", "10-K", {"1": LONG, "1A": LONG + "a", "6": "Item 6.", "7": LONG + "b",
                              "8": LONG + "c", "9B": "ITEM 9B", "4": "Item 4. Not applicable."})
    check(conn, "8k", "10k")
    assert findings(conn) == {
        ("8k", "duplicate_section"): ("warning", {"keys": ["EX-99.2", "EX-99.3"]}),
        ("8k", "empty_section"): ("info", {"keys": ["2.02", "EX-99.1"], "count": 2}),
        ("10k", "empty_section"): ("info", {"keys": ["9B"], "count": 1})}  # Item 6 is reserved


def test_a_document_with_no_sections_and_the_stamps(conn):
    doc(conn, "none", "8-K")
    doc(conn, "ok", "8-K", {"8.01": LONG})
    assert check(conn, "none", "ok") == 1
    assert findings(conn) == {("none", "no_sections"): ("error", {})}
    stamps = {r[0]: r[1:] for r in conn.execute(
        "SELECT accession_no, form, version, sections, findings FROM ops.section_checks")}
    assert stamps == {"none": ("8-K", 72, 0, 1), "ok": ("8-K", 72, 1, 0)}
    # parsed later: checked again, the finding goes
    doc(conn, "late", "8-K")
    conn.execute("INSERT INTO silver.filing_sections (accession_no, section_key, parser,"
                 " parser_version, item, title, text, char_start, char_end) VALUES"
                 " ('none', '8.01', 'rust', 'rust-7', '8.01', '', %s, 0, 10)", (LONG,))
    assert check(conn, "none") == 0 and findings(conn) == {}


def test_gold_serves_the_rust_rows(conn):
    doc(conn, "8k", "8-K", {"7": LONG}, parser="python")
    conn.execute("INSERT INTO silver.filing_sections (accession_no, section_key, parser,"
                 " parser_version, item, title, text, char_start, char_end) VALUES"
                 " ('8k', '7.01', 'rust', 'rust-7', '7.01', '', %s, 0, 10)", (LONG,))
    check(conn, "8k")
    # the python row's "7" is still a key of the document (no rust row for it)
    assert findings(conn)[("8k", "invalid_key")][1]["keys"] == ["7"]


def test_section_index_checks_what_it_indexes(conn):
    doc(conn, "a", "10-Q", {"I-1": LONG, "I-2": LONG + "a", "I-6": LONG + "b"})
    stats = index.run(conn)
    assert stats.indexed == 1 and stats.section_findings == 1
    assert set(findings(conn)) == {("a", "invalid_key")}


def test_the_real_8k_checks_clean(conn):
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name, url,"
        " content_gzip, content_sha256, size_bytes) VALUES ('cvr', %s, '8-K', '2024-07-01',"
        " 'd.htm', 'https://e/cvr', '\\x00', 'x', 1)", (CO,))
    raw = gzip.decompress((FIXTURES / "cvr-8-K.htm.gz").read_bytes())
    write_sections(conn, "cvr", parse(raw, "8-K"))
    assert check(conn, "cvr") == 0


def test_catch_up_checks_every_document_once_per_version(conn):
    for n, filed in enumerate(("2019-01-02", "2021-01-02", "2020-01-02")):
        doc(conn, f"d{n}", "8-K", {"8.01": LONG} if n else None, filed=filed)
    doc(conn, "today", "8-K", fetched="now()")  # waits for its parse
    ticks = itertools.chain([0, 0], itertools.repeat(100))  # time for one batch only
    first = catchup.sections(conn, 1, batch=2, clock=lambda: next(ticks))
    assert (first.done, first.behind) == (2, 1)
    assert {r[0] for r in conn.execute("SELECT accession_no FROM ops.section_checks")} == \
        {"d0", "d2"}  # oldest filings first
    rest = catchup.sections(conn, 60)
    assert (rest.done, rest.behind) == (1, 0)
    assert catchup.sections(conn, 60).done == 0  # nothing left
    assert set(findings(conn)) == {("d0", "no_sections")}
    conn.execute("CREATE OR REPLACE FUNCTION ops.check_sections_version() RETURNS integer"
                 " LANGUAGE sql STABLE AS 'SELECT 999'")
    assert catchup.sections(conn, 60).done == 3  # a new version checks everything again
    assert conn.execute("SELECT kind, version, behind FROM ops.catchup_status"
                        " WHERE kind = 'check_sections'").fetchone() == \
        ("check_sections", "999", 0)


def test_the_public_page_and_ops_read_counts_only(conn):
    doc(conn, "8k", "8-K", {"7": LONG})
    doc(conn, "none", "8-K")
    check(conn, "8k", "none")
    conn.execute("SET LOCAL ROLE query_api")
    rows = {r[0]: r[1:] for r in conn.execute("SELECT * FROM ops.public_parser_quality()")}
    assert rows["invalid_key"] == ("error", 1, 2, rows["invalid_key"][3])
    assert rows["no_sections"][1] == 1 and rows["empty_section"][1] == 0
    for table in ("ops.section_findings", "ops.section_checks"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
    conn.execute("RESET ROLE")
    conn.execute("SET LOCAL ROLE ops_reader")
    assert conn.execute("SELECT count(*) FROM ops.parser_quality_summary").fetchone() == (5,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SELECT 1 FROM ops.section_findings LIMIT 1")
