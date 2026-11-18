"""The storage diet's catch-up kind 'bodies' (migration 150): document bodies leave Postgres for
zstd files in the body store (libs/bodies), a budget per hourly section-index run (catchup.py).

1. move: bodies still in content_gzip become files, oldest filing first, BATCH documents a
   transaction, each with its exhibits: gunzip, check the SHA-256 and size against the row, write
   the file (fsynced, renamed into place), read it back from disk and check both again, then NULL
   content_gzip and set body_bytes in the same transaction. A body that fails a check stays in
   its column, is logged and counted (`failed`); the run goes on past it and the next run tries it
   again. A filesystem error (disk full, no mount) stops the move for this run.
2. sweep, once a day (SWEEP_HOURS): files no moved row references, older than ORPHAN_HOURS (a
   writer's transaction that never committed, or a DB restored from an older dump: its rows hold
   their bodies again), go to trash/, which keeps them TRASH_DAYS; a moved row whose file is
   missing gets it back from the trash when it's there, else counts as missing (`failed`, until
   a sweep finds it: re-fetch with `doc-fetcher-bodies`, doc-fetcher README). More orphans than
   MAX_ORPHANS (or 1% of the files) holds the sweep: BODIES_DIR may be another database's store.
   Temporary files a crashed writer left go too. A writer that stores a body already there
   touches it, and `Store.trash` checks the mtime again, so a file a new row needs is never old;
   a missing file is looked for again before it counts (a row that committed during the walk).

Postgres reuses the moved bodies' TOAST pages itself; the filesystem gets them back only with
VACUUM FULL of the two tables once the move is done (services/parser README "Document bodies").
"""

import gzip
import hashlib
import logging
import re
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import psycopg

from tributary_bodies import Body, Store, compress

log = logging.getLogger("parser_index.bodies")

VERSION = "zstd-1"   # the kind's version: bodies as zstd files (libs/bodies LEVEL)
BATCH = 50           # documents (with their exhibits) per transaction
SECONDS = 900        # moving per run, at most
SWEEP_HOURS = 24     # between sweeps
ORPHAN_HOURS = 24    # an unreferenced file this old is an orphan
TRASH_DAYS = 7       # ... kept in trash/ this long
MAX_ORPHANS = 1000   # ... more than this (and 1% of the files) in one sweep: held, logged

DOCUMENTS = ("bronze.filing_documents", "accession_no = %s")
EXHIBITS = ("bronze.filing_exhibits", "accession_no = %s AND exhibit_type = %s")


@dataclass
class Moved:
    bodies: int = 0
    gzip_bytes: int = 0
    file_bytes: int = 0
    failed: list[str] = field(default_factory=list)


@dataclass
class Swept:
    files: int = 0
    orphans: int = 0
    trashed: int = 0
    restored: int = 0
    missing: int = 0
    purged: int = 0
    temporaries: int = 0
    held: bool = False

    @property
    def note(self) -> str:
        held = " (held)" if self.held else ""
        return (f"swept: {self.files} files, {self.missing} missing, {self.restored} restored,"
                f" {self.trashed} of {self.orphans} orphans trashed{held}, {self.purged} purged")


def backlog(conn: psycopg.Connection) -> tuple[int, date | None]:
    """Bodies still in Postgres, and the oldest such document's filing date."""
    row = conn.execute(
        "SELECT (SELECT count(*) FROM bronze.filing_documents WHERE content_gzip IS NOT NULL)"
        "     + (SELECT count(*) FROM bronze.filing_exhibits WHERE content_gzip IS NOT NULL),"
        "       (SELECT min(filed) FROM bronze.filing_documents WHERE content_gzip IS NOT NULL)"
    ).fetchone()
    assert row is not None
    return int(row[0]), row[1]


def _move(conn: psycopg.Connection, store: Store, table: tuple[str, str], key: tuple,
          sha256: str, size: int, moved: Moved) -> None:
    name, where = table
    row = conn.execute(f"SELECT content_gzip FROM {name} WHERE {where}", key).fetchone()
    if row is None or row[0] is None:
        return
    label = "/".join(key)
    try:
        raw = gzip.decompress(row[0])
    except (OSError, EOFError, zlib.error) as e:
        moved.failed.append(label)
        log.error("%s: the stored gzip doesn't decompress (%s); left in Postgres", label, e)
        return
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha256:
        moved.failed.append(label)
        log.error("%s: the stored body isn't its SHA-256 or size; left in Postgres", label)
        return
    written = store.put(Body(compress(raw), sha256, size))
    if not store.verify(sha256, size):
        moved.failed.append(label)
        log.error("%s: its file %s doesn't read back; left in Postgres", label,
                  store.path(sha256))
        return
    conn.execute(f"UPDATE {name} SET content_gzip = NULL, body_bytes = %s WHERE {where}",
                 (written, *key))
    moved.bodies += 1
    moved.gzip_bytes += len(row[0])
    moved.file_bytes += written


def move(conn: psycopg.Connection, store: Store, deadline: float, *, batch: int = BATCH,
         clock: Callable[[], float] = time.monotonic) -> Moved:
    """Move bodies out of Postgres until `deadline` (clock time) or none are left: documents
    oldest filing first, each with its exhibits, then exhibits whose document moved without them
    (stored by an older doc-fetcher during the deploy)."""
    moved = Moved()
    after: tuple = (date.min, "")
    while clock() < deadline:
        with conn.transaction():
            rows = conn.execute(
                "SELECT accession_no, filed, content_sha256, size_bytes"
                " FROM bronze.filing_documents"
                " WHERE content_gzip IS NOT NULL AND (filed, accession_no) > (%s, %s)"
                " ORDER BY filed, accession_no LIMIT %s FOR UPDATE SKIP LOCKED",
                (*after, batch)).fetchall()
            for accession_no, _, sha256, size in rows:
                _move(conn, store, DOCUMENTS, (accession_no,), sha256, size, moved)
                for exhibit_type, esha, esize in conn.execute(
                        "SELECT exhibit_type, content_sha256, size_bytes"
                        " FROM bronze.filing_exhibits"
                        " WHERE accession_no = %s AND content_gzip IS NOT NULL"
                        " ORDER BY exhibit_type FOR UPDATE", (accession_no,)).fetchall():
                    _move(conn, store, EXHIBITS, (accession_no, exhibit_type), esha, esize,
                          moved)
        if not rows:
            break
        after = (rows[-1][1], rows[-1][0])
    after = ("", "")
    while clock() < deadline:
        with conn.transaction():
            rows = conn.execute(
                "SELECT accession_no, exhibit_type, content_sha256, size_bytes"
                " FROM bronze.filing_exhibits"
                " WHERE content_gzip IS NOT NULL AND (accession_no, exhibit_type) > (%s, %s)"
                " ORDER BY accession_no, exhibit_type LIMIT %s FOR UPDATE SKIP LOCKED",
                (*after, batch)).fetchall()
            for accession_no, exhibit_type, sha256, size in rows:
                _move(conn, store, EXHIBITS, (accession_no, exhibit_type), sha256, size, moved)
        if not rows:
            break
        after = (rows[-1][0], rows[-1][1])
    return moved


def sweep(conn: psycopg.Connection, store: Store, now: float | None = None, *,
          max_orphans: int = MAX_ORPHANS) -> Swept:
    """Trash orphans, restore or count missing files, purge the trash (module docstring, 2)."""
    now = time.time() if now is None else now
    old = now - ORPHAN_HOURS * 3600
    swept = Swept()
    with conn.transaction():
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS body_files (sha256 text NOT NULL,"
                     " old boolean NOT NULL) ON COMMIT DROP")
        conn.execute("TRUNCATE body_files")
        with conn.cursor() as cur, cur.copy("COPY body_files (sha256, old) FROM STDIN") as copy:
            for sha256, mtime in store.files():
                copy.write_row((sha256, mtime < old))
                swept.files += 1
        conn.execute("ANALYZE body_files")
        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS body_orphans ON COMMIT DROP AS"
            " SELECT f.sha256 FROM body_files f WHERE false")
        conn.execute("TRUNCATE body_orphans")
        swept.orphans = conn.execute(
            "INSERT INTO body_orphans SELECT f.sha256 FROM body_files f WHERE f.old"
            " AND NOT EXISTS (SELECT 1 FROM bronze.filing_documents d"
            "                 WHERE d.content_sha256 = f.sha256 AND d.content_gzip IS NULL)"
            " AND NOT EXISTS (SELECT 1 FROM bronze.filing_exhibits e"
            "                 WHERE e.content_sha256 = f.sha256 AND e.content_gzip IS NULL)"
        ).rowcount
        if swept.orphans > max(max_orphans, swept.files // 100):
            swept.held = True
            log.error("sweep held: %d of %d files no row references (is BODIES_DIR this"
                      " database's store?); nothing trashed", swept.orphans, swept.files)
        else:
            with conn.cursor(name="orphan_bodies") as cur:
                cur.execute("SELECT sha256 FROM body_orphans")
                swept.trashed = sum(store.trash(sha256, old, now) for (sha256,) in cur)
        with conn.cursor(name="missing_bodies") as cur:
            cur.execute(
                "SELECT r.content_sha256 FROM ("
                "  SELECT content_sha256 FROM bronze.filing_documents WHERE content_gzip IS NULL"
                "  UNION"
                "  SELECT content_sha256 FROM bronze.filing_exhibits WHERE content_gzip IS NULL"
                ") r WHERE NOT EXISTS (SELECT 1 FROM body_files f"
                "                      WHERE f.sha256 = r.content_sha256)")
            for (sha256,) in cur:
                if store.exists(sha256):  # written during the walk
                    continue
                if store.untrash(sha256):
                    swept.restored += 1
                else:
                    swept.missing += 1
                    if swept.missing <= 10:
                        log.error("body %s is missing from the store", sha256)
    swept.purged = store.purge_trash(now - TRASH_DAYS * 86400)
    swept.temporaries = store.remove_temporaries(old)
    log.info("bodies %s, %d temporaries removed", swept.note, swept.temporaries)
    return swept


def sweep_due(conn: psycopg.Connection) -> bool:
    row = conn.execute("SELECT max(run_at) < now() - make_interval(hours => %s)"
                       " OR max(run_at) IS NULL FROM ops.catchup_runs"
                       " WHERE kind = 'bodies' AND note LIKE 'swept:%%'", (SWEEP_HOURS,)
                       ).fetchone()
    return bool(row and row[0])


_MISSING = re.compile(r"swept: \d+ files, (\d+) missing")


def last_missing(conn: psycopg.Connection) -> int:
    """Files the last sweep found missing (its note): carried in `failed` until the next one."""
    row = conn.execute("SELECT note FROM ops.catchup_runs WHERE kind = 'bodies'"
                       " AND note LIKE 'swept:%%' ORDER BY run_at DESC LIMIT 1").fetchone()
    m = _MISSING.match(row[0]) if row else None
    return int(m.group(1)) if m else 0
