"""Runs against a real Postgres with migrations applied; skipped unless TEST_DATABASE_URL is set.
Every test runs inside a transaction that is rolled back."""

import gzip
import os
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from filing_parser.db import load_document, load_exhibits, table_rows, write_sections
from filing_parser.main import parse_one
from filing_parser.sections import PARSER_VERSION, parse
from tributary_bodies import Body, MissingBody, Store

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


def test_bodies_in_the_store_read_like_bodies_in_the_column(conn, tmp_path):
    """Migration 150: a moved row (content_gzip NULL, body_bytes set) reads from its zstd file;
    rows in either place parse to the same sections."""
    store = Store(tmp_path)
    doc, press = Body.of(HTML), Body.of(b"<html><p>Results press release</p></html>")
    for b in (doc, press):
        store.put(b)
    conn.execute("UPDATE bronze.filing_documents SET content_gzip = NULL, body_bytes = %s,"
                 " content_sha256 = %s WHERE accession_no = %s", (len(doc.data), doc.sha256, ACC))
    conn.execute("INSERT INTO bronze.filing_exhibits (accession_no, exhibit_type, description,"
                 " document_name, url, content_sha256, size_bytes, body_bytes) VALUES"
                 " (%s, 'EX-99.1', 'PRESS RELEASE', 'pr.htm', 'u', %s, %s, %s)",
                 (ACC, press.sha256, press.size, len(press.data)))
    assert load_document(conn, ACC, store) == ("8-K", HTML)
    assert load_exhibits(conn, ACC, store) == [
        ("EX-99.1", "PRESS RELEASE", b"<html><p>Results press release</p></html>")]
    parse_one(conn, ACC, store)
    assert conn.execute("SELECT count(*) FROM silver.filing_sections WHERE accession_no = %s"
                        " AND section_key = 'EX-99.1'", (ACC,)).fetchone() == (1,)
    with pytest.raises(MissingBody):  # no store: a KeyError the consumer logs and skips
        load_document(conn, ACC)
    store.path(doc.sha256).unlink()
    with pytest.raises(KeyError):
        parse_one(conn, ACC, store)


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


def _tables(conn):
    return conn.execute("SELECT section_key, parser, table_no, char_start, char_end, rows"
                        " FROM silver.filing_tables ORDER BY section_key, table_no").fetchall()


def test_tables_are_written_with_their_sections(conn):
    sections = parse(HTML, "8-K")
    write_sections(conn, ACC, sections)
    rows = _tables(conn)
    (s,) = [s for s in sections if s.key == "9.01"]
    assert [r[:5] for r in rows] == [("9.01", "python", n, t.start, t.end)
                                     for n, t in enumerate(s.tables)]
    assert len(rows) == 2
    first = rows[0][5]
    assert first == table_rows(s.tables[0])
    assert all(set(r) <= {"start", "end", "cells", "spans", "head"} for r in first)
    assert s.text[first[1]["start"]:first[1]["end"]].replace("\n", " ") == " ".join(
        c for c in first[1]["cells"] if c)


def test_a_reparse_with_the_same_text_keeps_the_rows_and_their_parsed_at(conn):
    """Only parser_version and the tables change: the search index and diffs key on parsed_at."""
    write_sections(conn, ACC, parse(HTML, "8-K"))
    conn.execute("UPDATE silver.filing_sections SET parsed_at = '2020-01-01',"
                 " parser_version = 'python-6'")
    conn.execute("DELETE FROM silver.filing_tables")
    write_sections(conn, ACC, parse(HTML, "8-K"))
    assert set(conn.execute("SELECT parsed_at::date::text, parser_version"
                            " FROM silver.filing_sections").fetchall()) == {
        ("2020-01-01", PARSER_VERSION)}
    assert len(_tables(conn)) == 2


def test_a_reparse_with_other_text_replaces_the_rows_and_their_tables(conn):
    write_sections(conn, ACC, parse(HTML, "8-K"))
    conn.execute("UPDATE silver.filing_sections SET parsed_at = '2020-01-01',"
                 " text = text || ' changed' WHERE section_key = '8.01'")
    write_sections(conn, ACC, parse(HTML, "8-K"))
    assert conn.execute("SELECT count(*) FROM silver.filing_sections"
                        " WHERE parsed_at = '2020-01-01'").fetchone() == (0,)
    assert len(_tables(conn)) == 2
    # deleting a section deletes its tables
    conn.execute("DELETE FROM silver.filing_sections WHERE section_key = '9.01'")
    assert _tables(conn) == []
