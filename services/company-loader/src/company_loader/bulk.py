"""Batch job: the SEC's bulk submissions.zip → bronze.submissions, bronze.submission_filings
and company metadata in silver.companies.

Each run loads at most --max-ciks changed CIKs, committing every few CIKs, so the first load of
~1M CIKs is many short resumable runs. CIKs whose content hash is unchanged are skipped. One
download can serve several runs (--rounds), with a pause between them (--pause-seconds).
"""

import argparse
import logging
import os
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from itertools import batched
from pathlib import Path
from typing import BinaryIO

import httpx
import psycopg

from company_loader import bulkzip, submissions_db
from company_loader.submissions import Submission, content_hash, entry_cik, parse
from tributary_sec import sec_client
from tributary_telemetry import configure_logging

SUBMISSIONS_ZIP_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
LOOKUP_CHUNK = 1000    # CIKs per content-hash lookup
BATCH_CIKS = 100       # CIKs per transaction...
BATCH_FILINGS = 50_000  # ...or fewer, once this many filing rows are staged

log = logging.getLogger("company_loader.bulk")


def download(client: httpx.Client, dest: Path,
             if_modified_since: datetime | None) -> datetime | None:
    """Stream the zip to dest. Returns its Last-Modified, or None if not modified since."""
    headers = {}
    if if_modified_since:
        headers["If-Modified-Since"] = format_datetime(if_modified_since, usegmt=True)
    part = dest.with_name(dest.name + ".part")
    with client.stream("GET", SUBMISSIONS_ZIP_URL, headers=headers) as resp:
        if resp.status_code == 304:
            return None
        resp.raise_for_status()
        written = 0
        with part.open("wb") as f:
            for chunk in resp.iter_raw(1 << 20):
                f.write(chunk)
                written += len(chunk)
        expected = resp.headers.get("Content-Length")
        if expected is not None and int(expected) != written:
            raise OSError(f"truncated download: {written} of {expected} bytes")
        modified = resp.headers.get("Last-Modified")
    part.replace(dest)
    return parsedate_to_datetime(modified) if modified else datetime.now(UTC)


def cik_entries(f: BinaryIO) -> Iterator[tuple[str, list[bulkzip.Entry]]]:
    """(cik, [main entry, page entries...]) in zip order. Pages are indexed up front (a few
    thousand); main entries are streamed."""
    pages: dict[str, list[bulkzip.Entry]] = defaultdict(list)
    for e in bulkzip.entries(f):
        parsed = entry_cik(e.name)
        if parsed and parsed[1]:
            pages[parsed[0]].append(e)
    for e in bulkzip.entries(f):
        parsed = entry_cik(e.name)
        if parsed and not parsed[1]:
            cik = parsed[0]
            yield cik, [e, *sorted(pages.get(cik, []), key=lambda p: p.name)]


def run(conn: psycopg.Connection, f: BinaryIO, snapshot: datetime, max_ciks: int) -> dict:
    """Load up to max_ciks changed CIKs from the zip. Marks the snapshot finished when every
    CIK in it is loaded."""
    submissions_db.start_run(conn, snapshot)
    counts = {"scanned": 0, "unchanged": 0, "loaded": 0, "filings": 0, "companies_changed": 0}
    batch: list[Submission] = []

    def flush() -> None:
        if batch:
            changed = submissions_db.write_batch(conn, batch, snapshot)
            counts["companies_changed"] += changed
            counts["filings"] += sum(len(s.filings) for s in batch)
            log.info("loaded %d CIKs (%d so far)", len(batch), counts["loaded"])
            batch.clear()

    complete = True
    staged = 0
    for chunk in batched(cik_entries(f), LOOKUP_CHUNK):
        known = submissions_db.known(conn, [cik for cik, _ in chunk])
        for cik, group in chunk:
            counts["scanned"] += 1
            stored_hash, stored_snapshot = known.get(cik, (None, None))
            if stored_snapshot == snapshot:  # loaded by an earlier run of this snapshot
                counts["unchanged"] += 1
                continue
            blobs = [bulkzip.read(f, e) for e in group]
            digest = content_hash(blobs)
            if stored_hash == digest:
                counts["unchanged"] += 1
                continue
            if counts["loaded"] >= max_ciks:
                complete = False
                break
            batch.append(parse(cik, blobs, digest))
            counts["loaded"] += 1
            staged += len(batch[-1].filings)
            if len(batch) >= BATCH_CIKS or staged >= BATCH_FILINGS:
                flush()
                staged = 0
        if not complete:
            break
    flush()
    if complete:
        submissions_db.finish_load(conn, snapshot)
    counts["complete"] = complete
    return counts


def run_rounds(run_once: Callable[[], dict], rounds: int, pause_seconds: float,
               sleep: Callable[[float], None] = time.sleep) -> dict:
    """Call run_once up to `rounds` times, pausing between calls, until a run completes."""
    counts: dict = {}
    for i in range(rounds):
        if i:
            log.info("pausing %.0fs before round %d of %d", pause_seconds, i + 1, rounds)
            sleep(pause_seconds)
        counts = run_once()
        log.info("round %d: %s", i + 1, counts)
        if counts.get("complete"):
            break
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, help="use this local zip instead of downloading")
    parser.add_argument("--download-dir", type=Path, default=Path("/tmp"),
                        help="where to download submissions.zip (default /tmp)")
    parser.add_argument("--max-ciks", type=int, default=500,
                        help="changed CIKs to load in this run (default 500)")
    parser.add_argument("--rounds", type=int, default=1,
                        help="runs per download, stopping early once complete (default 1)")
    parser.add_argument("--pause-seconds", type=float, default=0,
                        help="pause between rounds (default 0)")
    parser.add_argument("--dbname", default=os.environ.get("SUBMISSIONS_DBNAME") or None,
                        help="database on DATABASE_URL's server to load instead of its own"
                             " (default: $SUBMISSIONS_DBNAME)")
    args = parser.parse_args()
    configure_logging()

    dbname = {"dbname": args.dbname} if args.dbname else {}
    with psycopg.connect(os.environ["DATABASE_URL"], **dbname) as conn:
        if args.zip:
            path = args.zip
            snapshot = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        else:
            latest = submissions_db.latest_load(conn)
            # an unfinished load needs the file even if the SEC hasn't changed it
            since = latest[0] if latest and latest[1] else None
            path = args.download_dir / "submissions.zip"
            with sec_client(os.environ["SEC_USER_AGENT"], timeout=60.0) as client:
                modified = download(client, path, since)
            if modified is None:
                log.info("submissions.zip unchanged since %s", since)
                return
            snapshot = modified
            log.info("downloaded submissions.zip (%d MB, %s)",
                     path.stat().st_size >> 20, snapshot.isoformat())
        def run_once() -> dict:
            with path.open("rb") as f:
                return run(conn, f, snapshot, args.max_ciks)

        counts = run_rounds(run_once, args.rounds, args.pause_seconds)
    log.info("done: %s", counts)


if __name__ == "__main__":
    main()
