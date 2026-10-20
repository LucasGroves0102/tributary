"""The Python and Rust parser services (`parser --reparse-all`) on the same stored documents:
identical silver rows apart from parser and parser_version, and the same log fields.

Needs TEST_DATABASE_URL and PARSER_RS_BIN (the Rust `parser` binary, cargo build --bin parser).
The services commit, so this test inserts its own documents into an empty bronze and deletes
them afterwards; it skips when bronze already holds documents."""

import gzip
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from filing_parser.sections import PARSER_VERSION

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
RUST = os.environ.get("PARSER_RS_BIN")
pytestmark = pytest.mark.skipif(not (DSN and RUST),
                                reason="TEST_DATABASE_URL or PARSER_RS_BIN not set")

FIXTURES = Path(__file__).parent / "fixtures"
DOCS = [("9999999999-26-000001", "10-K", "2026-02-25"),
        ("9999999999-26-000002", "10-Q", "2026-05-28"),
        ("9999999999-26-000003", "8-K", "2026-09-03")]
EXHIBIT = b"<html><body><p>NVIDIA Announces Financial Results</p><p>Revenue grew.</p></body></html>"
ROWS = ("SELECT accession_no, section_key, parser, parser_version, part, item, title, text,"
        " char_start, char_end FROM silver.filing_sections ORDER BY accession_no, section_key")


@pytest.fixture
def conn():
    with psycopg.connect(DSN, autocommit=True) as c:
        if c.execute("SELECT count(*) FROM bronze.filing_documents").fetchone()[0]:
            pytest.skip("bronze.filing_documents is not empty")
        accessions = [acc for acc, _, _ in DOCS]
        try:
            for acc, form, filed in DOCS:
                html = (FIXTURES / f"nvda-{form}.htm.gz").read_bytes()
                c.execute(
                    "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed,"
                    " document_name, url, content_gzip, content_sha256, size_bytes)"
                    " VALUES (%s, '0001045810', %s, %s, 'doc.htm', 'https://example/doc', %s,"
                    " 'x', %s)", (acc, form, filed, html, len(gzip.decompress(html))))
            c.execute(
                "INSERT INTO bronze.filing_exhibits (accession_no, exhibit_type, description,"
                " document_name, url, content_gzip, content_sha256, size_bytes)"
                " VALUES (%s, 'EX-99.1', 'PRESS RELEASE', 'ex991.htm', 'https://example/ex',"
                " %s, 'x', %s)", (DOCS[2][0], gzip.compress(EXHIBIT), len(EXHIBIT)))
            yield c
        finally:
            for table in ("silver.filing_sections", "bronze.filing_exhibits",
                          "bronze.filing_documents"):
                c.execute(f"DELETE FROM {table} WHERE accession_no = ANY(%s)", (accessions,))


def reparse_all(cmd: list[str]) -> list[dict]:
    """Run a parser service with --reparse-all; its per-document log lines."""
    env = {**os.environ, "DATABASE_URL": DSN or "", "LOG_LEVEL": "INFO"}
    out = subprocess.run([*cmd, "--reparse-all"], env=env, capture_output=True, text=True,
                         timeout=300)
    assert out.returncode == 0, out.stderr
    return [line for line in map(json.loads, out.stdout.splitlines()) if "accession_no" in line]


def comparable(rows: list[tuple]) -> list[tuple]:
    return [r[:2] + r[4:] for r in rows]


def comparable_log(line: dict) -> dict:
    fields = {k: v for k, v in line.items() if k not in ("ts", "parse_ms")}
    return fields | {"message": re.sub(r" in \d+ ms ", " in _ ms ", line["message"])}


def test_rust_service_writes_what_the_python_service_writes(conn):
    python = [sys.executable, "-m", "filing_parser.main"]
    py_logs = reparse_all(python)
    py_rows = conn.execute(ROWS).fetchall()
    assert {r[2:4] for r in py_rows} == {("python", PARSER_VERSION)}
    assert len({r[0] for r in py_rows}) == 3
    assert ("9999999999-26-000003", "EX-99.1") in {r[:2] for r in py_rows}

    rs_logs = reparse_all([RUST or ""])
    rs_rows = conn.execute(ROWS).fetchall()
    # one parser's rows per document: the Rust parse replaced the Python rows
    assert {r[2:4] for r in rs_rows} == {("rust", PARSER_VERSION.replace("python", "rust"))}
    assert comparable(rs_rows) == comparable(py_rows)

    assert [comparable_log(x) for x in rs_logs] == [comparable_log(x) for x in py_logs]
    assert [list(x) for x in rs_logs] == [list(x) for x in py_logs]
    assert all(isinstance(x["parse_ms"], int) for x in rs_logs)
    assert len(rs_logs) == 3 and {x["logger"] for x in rs_logs} == {"parser"}

    # rollback: the Python service takes the documents back
    reparse_all(python)
    rows = conn.execute(ROWS).fetchall()
    assert {r[2] for r in rows} == {"python"} and comparable(rows) == comparable(py_rows)
