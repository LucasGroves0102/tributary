"""Nightly sweep: the SEC's companyfacts.zip (every filer) → bronze → silver → gold.

Entries are read one at a time in name order. A payload with the same SHA-256 as the stored
one is only marked as seen; changed ones are written in small batches, one transaction each,
together with the run's progress in bronze.bulk_loads. A run stops after `limit` changed
entries, so the first load of every filer is a series of short runs, each resuming after the
last committed entry; once caught up, a night only touches the companies that changed. One
download can serve several runs (--rounds), with a pause between them (--pause-seconds).
"""

import argparse
import logging
import os
import re
import tempfile
import time
import zipfile
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg

from facts_fetcher.db import (
    company_ids,
    latest_hashes,
    metric_tags,
    pending_companies,
    refresh_gold,
    refresh_silver,
    store_payloads,
    touch,
)
from facts_fetcher.payload import Payload, Tag, digest, prepare
from tributary_sec import sec_client
from tributary_telemetry import configure_logging

BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
ENTRY_NAME = re.compile(r"CIK(\d{10})\.json")
LIMIT = 500                # changed entries per run
BATCH = 50                 # changed entries per transaction
BATCH_BYTES = 32 << 20     # or fewer, once their compressed payloads reach this
BATCH_UNCHANGED = 1000     # unchanged entries per transaction

log = logging.getLogger("facts_fetcher.bulk")


@dataclass(frozen=True)
class Unchanged:
    cik: str
    sha256: str


@dataclass(frozen=True)
class Downloaded:
    path: Path
    last_modified: str | None
    size_bytes: int


@dataclass(frozen=True)
class Checkpoint:
    last_modified: str | None
    cursor: str | None
    completed: bool


@dataclass
class RunStats:
    entries: int = 0
    changed: int = 0
    facts: int = 0
    gold_rows: int = 0
    completed: bool = False
    batches: list[int] = field(default_factory=list)  # entries per committed batch


def download(client: httpx.Client, dest: Path, if_modified_since: str | None) -> Downloaded | None:
    """Stream the bulk file to dest in one request. None if unchanged since if_modified_since."""
    headers = {"If-Modified-Since": if_modified_since} if if_modified_since else {}
    part = dest.with_name(dest.name + ".part")
    with client.stream("GET", BULK_URL, headers=headers) as resp:
        if resp.status_code == 304:
            return None
        resp.raise_for_status()
        with part.open("wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                f.write(chunk)
        expected = resp.headers.get("Content-Length")
        encoded = "Content-Encoding" in resp.headers
    size = part.stat().st_size
    if expected is not None and not encoded and int(expected) != size:
        part.unlink()
        raise OSError(f"truncated download: {size} of {expected} bytes")
    part.rename(dest)
    return Downloaded(dest, resp.headers.get("Last-Modified"), size)


def cik_of(name: str) -> str | None:
    m = ENTRY_NAME.fullmatch(name)
    return m.group(1) if m else None


def entry_names(zf: zipfile.ZipFile, after: str | None = None) -> list[str]:
    """CIK entries in name order, those after `after` only."""
    names = sorted({n for n in zf.namelist() if cik_of(n)})
    return [n for n in names if n > after] if after else names


def read_entry(zf: zipfile.ZipFile, name: str, known: dict[str, str],
               tags: Collection[Tag]) -> Payload | Unchanged:
    """The entry as a Payload, or Unchanged if its hash matches the stored one."""
    cik = cik_of(name)
    assert cik is not None
    raw = zf.read(name)  # checks the entry's CRC
    sha = digest(raw)
    if known.get(cik) == sha:
        return Unchanged(cik, sha)
    return prepare(cik, raw, tags, sha)


def checkpoint(conn: psycopg.Connection, source: str) -> Checkpoint | None:
    row = conn.execute(
        "SELECT last_modified, cursor, completed_at IS NOT NULL FROM bronze.bulk_loads"
        " WHERE source = %s", (source,)
    ).fetchone()
    return Checkpoint(*row) if row else None


def begin(conn: psycopg.Connection, source: str, last_modified: str | None) -> str | None:
    """Resume the unfinished load of this version of the file, or start a new one.
    Returns the entry to resume after."""
    cp = checkpoint(conn, source)
    if cp and cp.last_modified == last_modified and not cp.completed:
        return cp.cursor
    conn.execute(
        """
        INSERT INTO bronze.bulk_loads (source, last_modified) VALUES (%s, %s)
        ON CONFLICT (source) DO UPDATE SET last_modified = EXCLUDED.last_modified,
            cursor = NULL, entries = 0, changed = 0, started_at = now(), updated_at = now(),
            completed_at = NULL
        """,
        (source, last_modified),
    )
    return None


def _flush(conn: psycopg.Connection, source: str, batch: list[tuple[str, Payload | Unchanged]],
           stats: RunStats) -> None:
    payloads = [e for _, e in batch if isinstance(e, Payload)]
    unchanged = [e for _, e in batch if isinstance(e, Unchanged)]
    with conn.transaction():
        new = store_payloads(conn, payloads, "bulk")
        touch(conn, [u.cik for u in unchanged], [u.sha256 for u in unchanged])
        stats.facts += refresh_silver(conn, new)
        stats.gold_rows += refresh_gold(conn, company_ids(conn, new))
        conn.execute(
            "UPDATE bronze.bulk_loads SET cursor = %s, entries = entries + %s,"
            " changed = changed + %s, updated_at = now() WHERE source = %s",
            (batch[-1][0], len(batch), len(new), source),
        )
    stats.entries += len(batch)
    stats.changed += len(new)
    stats.batches.append(len(batch))


def run(conn: psycopg.Connection, path: Path, source: str, last_modified: str | None,
        limit: int = LIMIT, batch_size: int = BATCH) -> RunStats:
    """Load up to `limit` changed entries of the zip, resuming the unfinished load of the same
    version. When the end is reached, gives gold rows to companies with silver facts but none
    yet, and marks the load complete."""
    with conn.transaction():
        after = begin(conn, source, last_modified)
        tags = metric_tags(conn)
        known = latest_hashes(conn)
    stats = RunStats()
    started = time.monotonic()
    with zipfile.ZipFile(path) as zf:
        names = entry_names(zf, after)
        log.info("%s: %d entries to go%s", source, len(names),
                 f" after {after}" if after else "")
        batch: list[tuple[str, Payload | Unchanged]] = []
        queued = payloads = batch_bytes = 0  # changed entries read this run / in this batch
        stats.completed = True
        for name in names:
            if queued >= limit:
                stats.completed = False
                break
            entry = read_entry(zf, name, known, tags)
            batch.append((name, entry))
            if isinstance(entry, Payload):
                queued += 1
                payloads += 1
                batch_bytes += len(entry.raw_gzip) + len(entry.facts_json)
            if (payloads >= batch_size or batch_bytes >= BATCH_BYTES
                    or len(batch) >= BATCH_UNCHANGED):
                _flush(conn, source, batch, stats)
                batch, payloads, batch_bytes = [], 0, 0
                log.info("%d entries, %d changed, %d new facts (%.0fs)", stats.entries,
                         stats.changed, stats.facts, time.monotonic() - started)
        if batch:
            _flush(conn, source, batch, stats)
    if stats.completed:
        with conn.transaction():
            pending = pending_companies(conn)
        for i in range(0, len(pending), batch_size):
            with conn.transaction():
                stats.gold_rows += refresh_gold(conn, pending[i:i + batch_size])
        with conn.transaction():
            conn.execute("UPDATE bronze.bulk_loads SET completed_at = now(),"
                         " updated_at = now() WHERE source = %s", (source,))
    return stats


def run_rounds(run_once: Callable[[], RunStats], rounds: int, pause_seconds: float,
               sleep: Callable[[float], None] = time.sleep) -> RunStats:
    """Call run_once up to `rounds` times, pausing between calls, until a run completes."""
    stats = RunStats()
    for i in range(rounds):
        if i:
            log.info("pausing %.0fs before round %d of %d", pause_seconds, i + 1, rounds)
            sleep(pause_seconds)
        stats = run_once()
        log.info("round %d: %d entries, %d changed, %d new facts", i + 1, stats.entries,
                 stats.changed, stats.facts)
        if stats.completed:
            break
    return stats


def local_source(path: Path) -> tuple[str, str]:
    """(source, version) for a zip on disk: its file:// URI and modification time."""
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    return path.resolve().as_uri(), mtime


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zip", type=Path, help="load this companyfacts.zip; no download")
    parser.add_argument("--dir", type=Path,
                        default=Path(os.environ.get("BULK_DIR", tempfile.gettempdir())),
                        help="where to download the zip (default: $BULK_DIR or the temp dir)")
    parser.add_argument("--limit", type=int, default=int(os.environ.get("BULK_LIMIT", LIMIT)),
                        help=f"changed entries per run (default: $BULK_LIMIT or {LIMIT})")
    parser.add_argument("--rounds", type=int, default=int(os.environ.get("BULK_ROUNDS", 1)),
                        help="runs per download, stopping early once complete"
                             " (default: $BULK_ROUNDS or 1)")
    parser.add_argument("--pause-seconds", type=float,
                        default=float(os.environ.get("BULK_PAUSE_SECONDS", 0)),
                        help="pause between rounds (default: $BULK_PAUSE_SECONDS or 0)")
    args = parser.parse_args(None if argv is None else list(argv))
    configure_logging()
    started = time.monotonic()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        got: Downloaded | None = None
        if args.zip:
            path = args.zip
            source, version = local_source(path)
        else:
            with conn.transaction():
                cp = checkpoint(conn, BULK_URL)
            since = cp.last_modified if cp and cp.completed else None
            with sec_client(os.environ["SEC_USER_AGENT"], timeout=60) as client:
                got = download(client, args.dir / "companyfacts.zip", since)
            if got is None:
                log.info("companyfacts.zip unchanged since %s", since)
                return
            log.info("downloaded %d bytes (Last-Modified %s) in %.0fs", got.size_bytes,
                     got.last_modified, time.monotonic() - started)
            path, source, version = got.path, BULK_URL, got.last_modified
        try:
            stats = run_rounds(lambda: run(conn, path, source, version, args.limit),
                               args.rounds, args.pause_seconds)
        finally:
            if got is not None:
                got.path.unlink()
    log.info("%s: %d entries, %d changed, %d new facts, %d gold rows refreshed in %.0fs",
             "complete" if stats.completed else "limit reached, run again to continue",
             stats.entries, stats.changed, stats.facts, stats.gold_rows,
             time.monotonic() - started)


if __name__ == "__main__":
    main()
