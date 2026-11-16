"""Catch-up in the section-index job (migration 071, filing_parser/catchup.py) against a real
Postgres; skipped unless TEST_DATABASE_URL is set. Every test runs inside a transaction that is
rolled back."""

import itertools
import os
from datetime import date

import pytest

from filing_parser import catchup, index

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

CO, OTHER = "9000000071", "9000000072"
TEXT = ("Item 2. Management's Discussion and Analysis. Revenue grew in the quarter as demand"
        " for accelerated computing rose. ")


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE silver.filing_sections, bronze.filing_documents, ops.section_index,"
                  " gold.section_search, gold.section_diffs, gold.section_changes,"
                  " ops.doc_crawl_targets, ops.document_outbox, ops.catchup_runs,"
                  " silver.company_aliases, silver.company_ciks, silver.companies CASCADE")
        c.execute("UPDATE ops.catchup SET paused = false, version = NULL, started_at = NULL")
        for cid in (CO, OTHER):
            c.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                      " VALUES (%s, %s, %s)", (cid, f"Test {cid}", cid))
            c.execute("INSERT INTO silver.company_ciks VALUES (%s, %s)", (cid, cid))
        yield c


def doc(conn, acc: str, filed: str, version: str = "rust-6", target: str | None = "published",
        cik: str = CO) -> None:
    """A stored, parsed 10-Q; target: its crawl row ('published' long ago, 'pending', or None:
    stored by the live doc-fetcher, which the crawl never plans)."""
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name, url,"
        " content_gzip, content_sha256, size_bytes, fetched_at) VALUES (%s, %s, '10-Q', %s,"
        " 'd.htm', %s, '\\x00', 'x', 1, '2026-01-01')", (acc, cik, filed, f"https://e/{acc}"))
    for key, part in (("I-2", "I"), ("II-1A", "II")):
        conn.execute(
            "INSERT INTO silver.filing_sections (accession_no, section_key, parser, parser_version,"
            " part, item, title, text, char_start, char_end) VALUES (%s, %s, 'rust', %s, %s, '2',"
            " 'MD&A', %s, 0, %s)", (acc, key, version, part, TEXT * 3, len(TEXT) * 3))
    if target is not None:
        conn.execute(
            "INSERT INTO ops.doc_crawl_targets (accession_no, cik, company_id, form, filing_date,"
            " priority, status, finished_at, published_at) VALUES (%s, %s, %s, '10-Q', %s, 1, %s,"
            " '2026-01-02', %s)",
            (acc, cik, cik, filed, "done" if target == "published" else target,
             "2026-01-02" if target == "published" else None))


def targets(conn) -> dict[str, tuple]:
    return {r[0]: r[1:] for r in conn.execute(
        "SELECT accession_no, status, published_at IS NULL, last_error FROM ops.doc_crawl_targets")}


def published(conn) -> None:
    """What doc-crawler does with the outbox: publish to documents.backfill, mark published."""
    conn.execute("UPDATE ops.doc_crawl_targets SET published_at = clock_timestamp()"
                 " WHERE status = 'done' AND published_at IS NULL")


def parsed(conn, version: str = "rust-7") -> None:
    """What parser-backfill does with them."""
    conn.execute("UPDATE silver.filing_sections SET parser_version = %s", (version,))


def test_older_documents_are_requeued_oldest_first_within_the_budget(conn):
    doc(conn, "a-2019", "2019-05-01", version="rust-7")  # current: never re-queued
    doc(conn, "b-2020", "2020-05-01", target=None)        # live-fetched: no crawl row
    doc(conn, "c-2021", "2021-05-01")
    doc(conn, "d-2022", "2022-05-01")
    doc(conn, "e-2023", "2023-05-01", version="python-5")
    r = catchup.parser(conn, per_run=2, version=7)
    assert (r.done, r.behind, r.in_flight, r.failed, r.oldest) == (2, 2, 2, 0, date(2022, 5, 1))
    t = targets(conn)
    assert t["b-2020"] == ("done", True, "already stored")  # the row the crawl writes for it
    assert t["c-2021"] == ("done", True, None)
    assert t["d-2022"][1] is False and t["e-2023"][1] is False and t["a-2019"][1] is False


def test_each_document_is_requeued_once_per_version(conn):
    for y in range(2020, 2024):
        doc(conn, f"x-{y}", f"{y}-05-01")
    assert catchup.parser(conn, per_run=2, version=7).done == 2
    published(conn)
    second = catchup.parser(conn, per_run=2, version=7)
    assert second.done == 2 and second.behind == 0
    published(conn)
    third = catchup.parser(conn, per_run=2, version=7)  # all requested, none parsed yet
    assert (third.done, third.behind, third.in_flight) == (0, 0, 4)
    parsed(conn)
    done = catchup.parser(conn, per_run=2, version=7)
    assert (done.done, done.behind, done.in_flight, done.failed) == (0, 0, 0, 0)
    # a new version: every document is behind again, and re-queued once more
    nxt = catchup.parser(conn, per_run=10, version=8)
    assert (nxt.done, nxt.behind) == (4, 0)


def test_requeue_waits_for_the_crawl_the_outbox_and_parser_backfill(conn):
    doc(conn, "a", "2020-05-01")
    doc(conn, "b", "2021-05-01")
    doc(conn, "crawl", "2026-05-01", version="rust-7", target="pending")
    assert catchup.parser(conn, per_run=5, version=7, crawl_busy=1).done == 0
    assert catchup.parser(conn, per_run=5, version=7, outbox=1).done == 1  # outbox now 1
    assert catchup.parser(conn, per_run=5, version=7, outbox=1).done == 0
    published(conn)
    assert catchup.parser(conn, per_run=5, version=7, in_flight=1).done == 0
    assert catchup.parser(conn, per_run=5, version=7, in_flight=2).done == 1
    assert targets(conn)["crawl"][0] == "pending"  # the crawl's own work is never touched


def test_the_live_path_is_never_used(conn):
    doc(conn, "a", "2020-05-01", target=None)
    doc(conn, "b", "2021-05-01")
    before = conn.execute("SELECT count(*) FROM ops.document_outbox").fetchone()
    assert catchup.parser(conn, per_run=5, version=7).done == 2
    # documents.fetched is published only from ops.document_outbox (doc-fetcher, migration 059)
    assert conn.execute("SELECT count(*) FROM ops.document_outbox").fetchone() == before


def test_paused_measures_and_queues_nothing(conn):
    doc(conn, "a", "2020-05-01")
    conn.execute("UPDATE ops.catchup SET paused = true WHERE kind = 'parser'")
    r = catchup.parser(conn, per_run=5, version=7)
    assert (r.paused, r.done, r.behind) == (True, 0, 1)
    assert targets(conn)["a"][1] is False


def test_passages_catch_up_stops_at_its_deadline_and_resumes(conn):
    for y in range(2020, 2024):
        doc(conn, f"p-{y}", f"{y}-05-01", version="rust-7")
    assert index.run(conn).indexed == 4
    conn.execute("UPDATE ops.section_index SET passages_rule = NULL")
    ticks = itertools.count()
    stats = index.refresh_passages(conn, batch=1, deadline=2, clock=lambda: next(ticks))
    assert stats.filings == 1 and not stats.completed  # the clock ran out after one batch
    assert conn.execute("SELECT accession_no FROM ops.section_index"
                        " WHERE passages_rule IS NOT NULL").fetchall() == [("p-2020",)]
    r = catchup.passages(conn, 60)
    assert (r.done, r.behind) == (3, 0)
    assert catchup.passages(conn, 60).done == 0  # nothing left: a second run does nothing


def test_a_run_locked_out_does_nothing_and_says_so(conn):
    doc(conn, "a", "2020-05-01", version="rust-7")
    with psycopg.connect(DSN, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (index.LOCK,))
        try:
            stats = index.run(conn)
            assert stats.locked_out and not stats.completed and stats.filings == 0
            assert index.outcome(stats).startswith("another run holds the lock")
            assert index.refresh_passages(conn).locked_out
        finally:
            other.execute("SELECT pg_advisory_unlock(%s)", (index.LOCK,))
    assert index.run(conn).indexed == 1


def test_new_filings_are_indexed_before_history_is_reindexed(conn):
    doc(conn, "old-2019", "2019-05-01", version="rust-7")
    index.run(conn)
    conn.execute("UPDATE silver.filing_sections SET parsed_at = now() + interval '1 minute'")
    doc(conn, "new-2026", "2026-05-01", version="rust-7")
    rows = conn.execute("SELECT accession_no, reason FROM ops.section_index_pending(%s, 1)",
                        (index.ALGORITHM,)).fetchall()
    assert rows[0] == ("new-2026", "sections")


def test_status_view_counts_each_kind(conn):
    doc(conn, "a", "2020-05-01")
    doc(conn, "b", "2021-05-01")
    index.run(conn)
    catchup.run(conn, seconds=60, per_run=1, diffed=0)
    rows = {r[0]: r[1:] for r in conn.execute(
        "SELECT kind, version, behind, in_flight, done_last_day, progress_at IS NOT NULL"
        " FROM ops.catchup_status")}
    assert rows["parser"] == (str(catchup.parser_version()), 1, 1, 1, True)
    assert rows["passages"] == (index.SEARCH_RULE, 0, 0, 0, True)  # built with the rule
    assert rows["diffs"][1] == 0
    types = {c.name: c.type_code for c in conn.execute(
        "SELECT * FROM ops.catchup_status LIMIT 0").description}
    text_oid = 25
    assert {n for n, t in types.items() if t == text_oid} == {"kind", "version"}
