"""parser-index's release figures (migration 165, index.write_releases, the catch-up kind
'releases') against a real Postgres; skipped unless TEST_DATABASE_URL is set. Every test runs
inside a transaction that is rolled back. The 6-K is TSMC's 3Q26 results release (fixtures/releases)
stored as the parsers store it."""

import gzip
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from filing_parser import catchup, index, releases

Jsonb = pytest.importorskip("psycopg.types.json").Jsonb

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

CO = "0001046179"
ACC = "0001046179-26-000726"
FIXTURE = Path(__file__).parent / "fixtures" / "releases" / "tsmc-3q26-release.json.gz"


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE silver.filing_sections, bronze.filing_documents, silver.release_reads,"
                  " ops.catchup_runs, silver.company_aliases, silver.company_ciks,"
                  " silver.companies CASCADE")
        c.execute("UPDATE ops.catchup SET paused = false, version = NULL, started_at = NULL")
        c.execute("INSERT INTO silver.companies (company_id, name, current_cik, fiscal_year_end)"
                  " VALUES (%s, 'Taiwan Semiconductor', %s, '1231')", (CO, CO))
        c.execute("INSERT INTO silver.company_ciks VALUES (%s, %s)", (CO, CO))
        yield c


def stored(conn, acc: str = ACC) -> None:
    """The 6-K and its sections and tables as parser-rs stores them: each section's text holds the
    text before each table, the table at that offset."""
    fx = json.loads(gzip.decompress(FIXTURE.read_bytes()))
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, period_of_report,"
        " document_name, url, content_gzip, content_sha256, size_bytes, fetched_at) VALUES"
        " (%s, %s, '6-K', %s, %s, 'd.htm', %s, '\\x00', 'x', 1, '2026-10-15')",
        (acc, CO, fx["filed"], fx["period"], f"https://e/{acc}"))
    sections: dict[str, list] = {}
    for t in fx["tables"]:
        sections.setdefault(t["section_key"], []).append(t)
    for key, tables in sections.items():
        text, at = "", []
        for t in tables:
            text += t["before"]
            at.append(len(text))
            text += "\n"
        conn.execute(
            "INSERT INTO silver.filing_sections (accession_no, section_key, parser, parser_version,"
            " item, title, text, char_start, char_end) VALUES (%s, %s, 'rust', 'rust-8', %s, %s,"
            " %s, 0, %s)", (acc, key, key, key, text, len(text)))
        for t, start in zip(tables, at, strict=True):
            conn.execute(
                "INSERT INTO silver.filing_tables (accession_no, section_key, parser, table_no,"
                " char_start, char_end, rows) VALUES (%s, %s, 'rust', %s, %s, %s, %s)",
                (acc, key, t["table_no"], start, start + 1, Jsonb(t["rows"])))


def figures(conn) -> dict[tuple, tuple]:
    return {(r[0], r[1], r[2]): r[3:] for r in conn.execute(
        "SELECT metric, period_end, months, value, currency, scale_named, section_key, table_no,"
        " row_no, col_no, label FROM silver.release_figures WHERE accession_no = %s", (ACC,))}


def test_a_release_is_read_into_silver_with_its_table_and_cell(conn):
    stored(conn)
    assert index.write_releases(conn, [ACC]) == 3
    got = figures(conn)
    assert got[("revenue", date(2026, 9, 30), 3)] == (
        Decimal("1494243000000"), "TWD", True, "EX-99.1", 0, 1, 1, "Net sales")
    assert got[("net_income", date(2026, 9, 30), 3)][0] == Decimal("763064000000")
    read = conn.execute("SELECT form, period, reader, figures, left_out FROM silver.release_reads"
                        " WHERE accession_no = %s", (ACC,)).fetchone()
    assert read == ("6-K", date(2026, 9, 30), releases.READER, 3, {})
    start = conn.execute("SELECT period_start FROM silver.release_figures WHERE metric ="
                         " 'revenue'").fetchone()[0]
    assert start == date(2026, 7, 1)


def test_reading_it_again_unchanged_keeps_its_rows(conn):
    stored(conn)
    index.write_releases(conn, [ACC])
    before = conn.execute("SELECT r.read_at, array_agg(f.figure_id ORDER BY f.figure_id)"
                          " FROM silver.release_reads r JOIN silver.release_figures f"
                          " USING (accession_no) GROUP BY r.read_at").fetchall()
    assert index.write_releases(conn, [ACC]) == 3
    assert conn.execute("SELECT r.read_at, array_agg(f.figure_id ORDER BY f.figure_id)"
                        " FROM silver.release_reads r JOIN silver.release_figures f"
                        " USING (accession_no) GROUP BY r.read_at").fetchall() == before
    # another reader's read is read again
    conn.execute("UPDATE silver.release_reads SET reader = 'releases-0'")
    assert conn.execute("SELECT count(*) FROM ops.release_backlog(%s)",
                        (releases.READER,)).fetchone()[0] == 1
    index.write_releases(conn, [ACC])
    assert conn.execute("SELECT reader FROM silver.release_reads").fetchone()[0] == releases.READER


def test_the_catch_up_reads_every_stored_6k_once_per_reader(conn):
    stored(conn)
    r = catchup.release_figures(conn, 60)
    assert (r.kind, r.done, r.behind) == ("releases", 1, 0)
    assert len(figures(conn)) == 3
    assert catchup.release_figures(conn, 60).done == 0
    status = conn.execute("SELECT state, behind FROM ops.catchup_status WHERE kind = 'releases'"
                          ).fetchone()
    assert status == ("current", 0)


def test_a_10q_is_not_a_release(conn):
    stored(conn)
    conn.execute("UPDATE bronze.filing_documents SET form = '10-Q'")
    assert index.write_releases(conn, [ACC]) == 0
    assert conn.execute("SELECT count(*) FROM silver.release_reads").fetchone()[0] == 0
