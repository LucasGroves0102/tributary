"""Runs against a real Postgres with migrations applied; skipped unless TEST_DATABASE_URL is set.
Every test runs inside a transaction that is rolled back."""

import gzip
import os
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from filing_parser.db import load_document, write_sections
from filing_parser.main import parse_one
from filing_parser.sections import parse

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

ACC = "0001045810-26-000078"
HTML = gzip.decompress((Path(__file__).parent / "fixtures" / "nvda-8-K.htm.gz").read_bytes())


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE silver.filing_sections, bronze.filing_documents,"
                  " silver.company_aliases, silver.company_ciks, silver.companies CASCADE")
        c.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                  " VALUES ('0001045810', 'NVIDIA CORP', '0001045810')")
        c.execute("INSERT INTO silver.company_ciks VALUES ('0001045810', '0001045810')")
        c.execute(
            "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name,"
            " url, content_gzip, content_sha256, size_bytes) VALUES (%s, '0001045810', '8-K',"
            " '2026-09-03', 'nvda-20260902.htm', 'https://example/doc', %s, 'x', %s)",
            (ACC, gzip.compress(HTML), len(HTML)))
        yield c


def test_parse_and_write_is_idempotent(conn):
    form, content = load_document(conn, ACC)
    assert form == "8-K" and content == HTML
    for _ in range(2):
        write_sections(conn, ACC, parse(content, form))
    rows = conn.execute(
        "SELECT section_key, company_id, document_url, parser FROM gold.filing_sections"
        " ORDER BY section_key").fetchall()
    assert rows == [("8.01", "0001045810", "https://example/doc", "python"),
                    ("9.01", "0001045810", "https://example/doc", "python")]


def test_parse_one_counts_parsed_documents(conn):
    parsed = REGISTRY.get_sample_value("tributary_documents_parsed_total", {"form": "8-K"}) or 0
    parse_one(conn, ACC)
    assert REGISTRY.get_sample_value("tributary_documents_parsed_total",
                                     {"form": "8-K"}) == parsed + 1
    assert REGISTRY.get_sample_value("tributary_parse_duration_seconds_count", {"form": "8-K"})


def test_gold_prefers_rust_parser(conn):
    write_sections(conn, ACC, parse(HTML, "8-K"))
    conn.execute(
        "INSERT INTO silver.filing_sections (accession_no, section_key, parser, parser_version,"
        " item, title, text, char_start, char_end)"
        " VALUES (%s, '8.01', 'rust', 'rust-1', '8.01', 'Other Events.', 'from rust', 0, 9)",
        (ACC,))
    assert conn.execute(
        "SELECT parser, text FROM gold.filing_sections WHERE section_key = '8.01'"
    ).fetchone() == ("rust", "from rust")


def test_write_replaces_the_other_parsers_rows(conn):
    conn.execute(
        "INSERT INTO silver.filing_sections (accession_no, section_key, parser, parser_version,"
        " item, title, text, char_start, char_end)"
        " VALUES (%s, '7.01', 'rust', 'rust-1', '7.01', 'Regulation FD.', 'stale', 0, 5)",
        (ACC,))
    write_sections(conn, ACC, parse(HTML, "8-K"))
    assert conn.execute(
        "SELECT DISTINCT parser FROM silver.filing_sections WHERE accession_no = %s", (ACC,)
    ).fetchall() == [("python",)]
    assert conn.execute(
        "SELECT section_key FROM gold.filing_sections ORDER BY section_key").fetchall() == [
        ("8.01",), ("9.01",)]
