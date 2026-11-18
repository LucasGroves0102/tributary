"""The 'bodies' catch-up kind (migration 150, filing_parser/bodies.py) against a real Postgres and a
temporary body store; skipped unless TEST_DATABASE_URL is set. Every test is rolled back."""

import gzip
import hashlib
import itertools
import os
import time
from pathlib import Path

import pytest

from filing_parser import bodies, catchup
from filing_parser.db import load_document, load_exhibits
from tributary_bodies import Body, Store

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

CO = "9000000150"
HTML = gzip.decompress((Path(__file__).parent / "fixtures" / "nvda-8-K.htm.gz").read_bytes())
DAY = 86400


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE silver.filing_sections, bronze.filing_documents, ops.catchup_runs,"
                  " silver.company_aliases, silver.company_ciks, silver.companies CASCADE")
        c.execute("UPDATE ops.catchup SET paused = false, version = NULL, started_at = NULL")
        c.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                  " VALUES (%s, 'Test', %s)", (CO, CO))
        c.execute("INSERT INTO silver.company_ciks VALUES (%s, %s)", (CO, CO))
        yield c


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "bodies")


def html(acc: str) -> bytes:
    return HTML.replace(b"</body>", f"<p>{acc}</p></body>".encode())


def doc(conn, acc: str, filed: str, content: bytes | None = None, sha: str | None = None,
        exhibits: tuple[str, ...] = ()) -> bytes:
    """A document stored before migration 150: its body gzip in content_gzip."""
    content = html(acc) if content is None else content
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name, url,"
        " content_gzip, content_sha256, size_bytes) VALUES (%s, %s, '8-K', %s, 'd.htm', 'u',"
        " %s, %s, %s)", (acc, CO, filed, gzip.compress(content),
                         sha or hashlib.sha256(content).hexdigest(), len(content)))
    for t in exhibits:
        exhibit(conn, acc, t)
    return content


def exhibit(conn, acc: str, exhibit_type: str) -> bytes:
    content = f"<html><p>{acc} {exhibit_type} press release</p></html>".encode()
    conn.execute(
        "INSERT INTO bronze.filing_exhibits (accession_no, exhibit_type, document_name, url,"
        " content_gzip, content_sha256, size_bytes) VALUES (%s, %s, 'x.htm', 'u', %s, %s, %s)",
        (acc, exhibit_type, gzip.compress(content), hashlib.sha256(content).hexdigest(),
         len(content)))
    return content


def in_column(conn) -> list[str]:
    return [f"{r[0]}" for r in conn.execute(
        "SELECT accession_no FROM bronze.filing_documents WHERE content_gzip IS NOT NULL"
        " UNION ALL SELECT accession_no || ' ' || exhibit_type FROM bronze.filing_exhibits"
        " WHERE content_gzip IS NOT NULL ORDER BY 1")]


def test_the_move_goes_oldest_first_with_exhibits_and_reads_the_same(conn, store):
    docs = {acc: doc(conn, acc, filed, exhibits=ex) for acc, filed, ex in (
        ("c-2023", "2023-05-01", ()), ("a-2021", "2021-05-01", ("EX-99.1", "EX-99.2")),
        ("b-2022", "2022-05-01", ("EX-99.1",)))}
    before = {acc: (load_document(conn, acc), load_exhibits(conn, acc)) for acc in docs}
    ticks = itertools.count()  # one batch of one document, then the clock runs out
    moved = bodies.move(conn, store, deadline=1, batch=1, clock=lambda: next(ticks))
    assert (moved.bodies, moved.failed) == (3, [])  # a-2021 and its two exhibits
    assert in_column(conn) == ["b-2022", "b-2022 EX-99.1", "c-2023"]
    assert bodies.backlog(conn) == (3, __import__("datetime").date(2022, 5, 1))
    moved = bodies.move(conn, store, deadline=time.monotonic() + 60)
    assert moved.bodies == 3 and in_column(conn) == [] and bodies.backlog(conn) == (0, None)
    rows = conn.execute("SELECT content_sha256, size_bytes, body_bytes FROM"
                        " bronze.filing_documents UNION ALL SELECT content_sha256, size_bytes,"
                        " body_bytes FROM bronze.filing_exhibits").fetchall()
    for sha, size, body_bytes in rows:
        assert store.path(sha).stat().st_size == body_bytes and store.verify(sha, size)
    for acc, read in before.items():  # the parsers read the same bytes from the files
        assert (load_document(conn, acc, store), load_exhibits(conn, acc, store)) == read


def test_a_body_that_fails_its_checks_stays_in_postgres(conn, store):
    doc(conn, "a-bad", "2021-01-01", sha="0" * 64)      # not its hash
    conn.execute("INSERT INTO bronze.filing_documents (accession_no, cik, form, filed,"
                 " document_name, url, content_gzip, content_sha256, size_bytes) VALUES"
                 " ('b-corrupt', %s, '8-K', '2021-02-01', 'd', 'u', '\\x1f8b00', %s, 3)",
                 (CO, "1" * 64))                         # not gzip
    doc(conn, "c-good", "2021-03-01")
    moved = bodies.move(conn, store, deadline=time.monotonic() + 60, batch=1)
    assert moved.failed == ["a-bad", "b-corrupt"] and moved.bodies == 1
    assert in_column(conn) == ["a-bad", "b-corrupt"]
    assert sorted(h for h, _ in store.files()) == [hashlib.sha256(html("c-good")).hexdigest()]


def test_exhibits_stored_after_their_document_moved_move_too(conn, store):
    doc(conn, "a", "2021-01-01")
    bodies.move(conn, store, deadline=time.monotonic() + 60)
    exhibit(conn, "a", "EX-99.1")  # an older doc-fetcher, during the deploy
    assert in_column(conn) == ["a EX-99.1"]
    assert bodies.move(conn, store, deadline=time.monotonic() + 60).bodies == 1
    assert in_column(conn) == []


def test_identical_bodies_share_one_file(conn, store):
    same = html("same")
    doc(conn, "a", "2021-01-01", content=same)
    doc(conn, "b", "2022-01-01", content=same)
    assert bodies.move(conn, store, deadline=time.monotonic() + 60).bodies == 2
    assert len(list(store.files())) == 1


def status(conn) -> tuple:
    return conn.execute("SELECT state, behind, failed, done_last_day, note"
                        " FROM ops.catchup_status WHERE kind = 'bodies'").fetchone()


def test_the_kind_records_its_backlog_pauses_and_needs_a_store(conn, store):
    doc(conn, "a", "2021-01-01", exhibits=("EX-99.1",))
    doc(conn, "b", "2022-01-01")
    r = catchup.body_files(conn, None, 60)  # no BODIES_DIR: measured, says why
    assert (r.behind, r.done) == (3, 0)
    assert status(conn) == ("catching_up", 3, 0, 0, "skipped: no body store (BODIES_DIR)")
    conn.execute("UPDATE ops.catchup SET paused = true WHERE kind = 'bodies'")
    assert catchup.body_files(conn, store, 60).done == 0
    assert status(conn)[:2] == ("paused", 3) and in_column(conn) != []
    conn.execute("UPDATE ops.catchup SET paused = false WHERE kind = 'bodies'")
    results = {x.kind: x for x in catchup.run(conn, seconds=10, per_run=0, store=store,
                                              bodies_seconds=60)}
    assert (results["bodies"].done, results["bodies"].behind) == (3, 0)
    assert status(conn)[:4] == ("current", 0, 0, 3)
    assert status(conn)[4].startswith("swept: 0 files")  # the first run sweeps, then moves
    assert not bodies.sweep_due(conn)
    skipped = {x.kind: x for x in catchup.skipped(conn)}
    assert skipped["bodies"].behind == 0


def test_the_sweep_trashes_old_orphans_and_restores_or_counts_missing_files(conn, store):
    for acc, filed in (("a", "2021-01-01"), ("b", "2022-01-01"), ("c", "2023-01-01")):
        doc(conn, acc, filed)
    bodies.move(conn, store, deadline=time.monotonic() + 60)
    old, fresh, touched = Body.of(b"old orphan"), Body.of(b"new orphan"), Body.of(b"re-stored")
    for b in (old, fresh, touched):
        store.put(b)
    now = time.time()
    for b in (old, touched):
        os.utime(store.path(b.sha256), (now - 2 * DAY, now - 2 * DAY))
    store.put(touched)  # a writer stores it again before its row commits: touched, kept
    sha = {acc: hashlib.sha256(html(acc)).hexdigest() for acc in "abc"}
    os.utime(store.path(sha["a"]), (now - 2 * DAY, now - 2 * DAY))  # old, referenced: kept
    store.path(sha["b"]).unlink()                                      # gone
    os.utime(store.path(sha["c"]), (now - 2 * DAY, now - 2 * DAY))
    assert store.trash(sha["c"], now - DAY)                            # trashed by mistake
    (store.root / sha["a"][:2] / f".{sha['a']}.1.1.tmp").write_bytes(b"x")
    os.utime(store.root / sha["a"][:2] / f".{sha['a']}.1.1.tmp", (now - 2 * DAY,) * 2)

    swept = bodies.sweep(conn, store, now)
    assert (swept.orphans, swept.trashed, swept.restored, swept.missing) == (1, 1, 1, 1)
    assert not store.exists(old.sha256) and store.exists(fresh.sha256)
    assert store.exists(touched.sha256) and store.exists(sha["c"]) and store.exists(sha["a"])
    assert swept.temporaries == 1
    assert swept.note.startswith("swept: 4 files, 1 missing, 1 restored, 1 of 1 orphans")
    # a week later the trash is emptied (of what was trashed then)
    later = bodies.sweep(conn, store, now + 8 * DAY)
    assert later.purged == 1 and later.trashed == 2  # the orphans that are old now


def test_a_sweep_that_finds_mostly_orphans_is_held(conn, store):
    """BODIES_DIR pointing at another database's store must not empty it."""
    now = time.time()
    for i in range(3):
        b = Body.of(f"someone else's {i}".encode())
        store.put(b)
        os.utime(store.path(b.sha256), (now - 2 * DAY, now - 2 * DAY))
    swept = bodies.sweep(conn, store, now, max_orphans=2)
    assert swept.held and swept.trashed == 0 and len(list(store.files())) == 3
    assert "(held)" in swept.note


def test_missing_files_stay_counted_until_the_next_sweep(conn, store):
    doc(conn, "a", "2021-01-01")
    bodies.move(conn, store, deadline=time.monotonic() + 60)
    store.path(hashlib.sha256(html("a")).hexdigest()).unlink()
    assert catchup.body_files(conn, store, 60).failed == 1  # swept: one missing
    assert catchup.body_files(conn, store, 60).failed == 1  # no sweep: the last one's count
    assert status(conn)[2] == 1
