"""Segment and KPI facts: stored 10-K/10-Q documents → bronze.filing_xbrl → silver → gold.

companyfacts has no dimensional facts, so segments, geographies and product lines come from
each filing's inline XBRL, which is the 10-K/10-Q primary document the doc-fetcher and the
document crawl already store (bronze.filing_documents). No SEC requests.

A run takes the stored 10-K/10-Q documents that have no extract for their current content hash
and EXTRACTOR_VERSION (that row is the checkpoint), in (CIK, accession) order so a company's
filings land together. Each document is decompressed and parsed in chunks (ixbrl.py); a batch
of them is written in one transaction together with its silver rows and its companies' gold
rows and segment quality check. A run stops after `limit` documents; the next resumes with what
is left, so the first load (~90k documents) is a series of short runs and a crash loses one
batch at most. --rounds runs several in one process with a pause between them. When nothing is
left, companies with silver segment facts but no gold rows (a CIK mapped after its documents
were extracted) get theirs.

--rebuild re-projects every stored extract (raw_gzip) with the current silver.segment_axes and
labels, then re-derives silver and gold: no document is re-read.
"""

import argparse
import gzip
import json
import logging
import os
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import psycopg

from facts_fetcher.ixbrl import EXTRACTOR_VERSION, Extract, Fact, extract_gzip, plain
from facts_fetcher.labels import member_label
from tributary_telemetry import configure_logging

FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A")
LIMIT = 2000   # documents per run
BATCH = 25     # documents per transaction
MAX_DIMENSIONS = 2

log = logging.getLogger("facts_fetcher.segments")


@dataclass(frozen=True)
class Projected:
    facts: list[dict]
    labels: dict[str, str]


@dataclass(frozen=True)
class Row:
    accession_no: str
    cik: str
    document_sha256: str
    status: str
    entity_cik: str | None = None
    facts_json: str = "[]"
    labels_json: str = "{}"
    raw_gzip: bytes | None = None
    fact_count: int = 0
    stats_json: str = "{}"
    error: str | None = None


@dataclass
class RunStats:
    documents: int = 0
    ok: int = 0
    silver_rows: int = 0
    gold_rows: int = 0
    completed: bool = False
    batches: list[int] = field(default_factory=list)


def fact_dict(f: Fact) -> dict:
    d: dict = {"c": f.concept, "v": plain(f.value), "u": f.unit, "d": f.decimals}
    if f.instant:
        d["i"] = f.instant
    else:
        d["s"], d["e"] = f.start, f.end
    if f.members:
        d["m"] = dict(f.members)
    return d


def project(facts: Iterable[dict], axes: set[str]) -> Projected:
    """The facts SQL reads: those dimensioned only by `axes` (at most MAX_DIMENSIONS), plus
    the undimensioned facts of the same concepts (the consolidated totals), and a label for
    every member they use."""
    facts = list(facts)
    keep = [f for f in facts
            if f.get("m") and len(f["m"]) <= MAX_DIMENSIONS and set(f["m"]) <= axes]
    concepts = {f["c"] for f in keep}
    totals = [f for f in facts if not f.get("m") and f["c"] in concepts]
    members = sorted({m for f in keep for m in f["m"].values()})
    return Projected(totals + keep, {m: member_label(m) for m in members})


def compress(facts: list[dict]) -> bytes:
    return gzip.compress(json.dumps(facts, separators=(",", ":")).encode(), mtime=0)


def decompress(raw_gzip: bytes) -> list[dict]:
    return json.loads(gzip.decompress(raw_gzip))


def build_row(accession_no: str, cik: str, sha256: str, content_gzip: bytes,
              axes: set[str]) -> Row:
    """Extract one stored document. Never raises: a broken document becomes an error row, so it
    isn't retried until the extractor version changes."""
    try:
        ex: Extract = extract_gzip(content_gzip)
    except Exception as e:  # noqa: BLE001 - one bad document must not stop the load
        log.warning("%s: extraction failed: %s", accession_no, e)
        return Row(accession_no, cik, sha256, "error", error=f"{type(e).__name__}: {e}"[:500])
    stats = {"contexts": ex.contexts, "unsupported": ex.unsupported, "conflicts": ex.conflicts,
             "missing_context": ex.missing_context}
    if not ex.has_header or not ex.contexts:
        return Row(accession_no, cik, sha256, "no_ixbrl", stats_json=json.dumps(stats))
    facts = [fact_dict(f) for f in ex.facts]
    p = project(facts, axes)
    return Row(accession_no, cik, sha256, "ok", entity_cik=ex.entity_cik,
               facts_json=json.dumps(p.facts, separators=(",", ":")),
               labels_json=json.dumps(p.labels, separators=(",", ":")),
               raw_gzip=compress(facts), fact_count=len(facts), stats_json=json.dumps(stats))


# database ------------------------------------------------------------------------------------

def segment_axes(conn: psycopg.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT axis_qname FROM silver.segment_axes")}


def pending(conn: psycopg.Connection, limit: int) -> list[tuple[str, str, str]]:
    """(accession, cik, content SHA-256) of stored 10-K/10-Q documents without a current
    extract, in (CIK, accession) order."""
    return conn.execute(
        """
        SELECT d.accession_no, d.cik, d.content_sha256
        FROM bronze.filing_documents d
        LEFT JOIN bronze.filing_xbrl x ON x.accession_no = d.accession_no
        WHERE d.form = ANY(%s)
          AND (x.accession_no IS NULL OR x.document_sha256 <> d.content_sha256
               OR x.extractor_version <> %s)
        ORDER BY d.cik, d.accession_no
        LIMIT %s
        """, (list(FORMS), EXTRACTOR_VERSION, limit)).fetchall()


def document(conn: psycopg.Connection, accession_no: str) -> bytes | None:
    row = conn.execute("SELECT content_gzip FROM bronze.filing_documents WHERE accession_no = %s",
                       (accession_no,)).fetchone()
    return bytes(row[0]) if row else None


def store_rows(conn: psycopg.Connection, rows: Sequence[Row]) -> None:
    """Upsert extracts (the checkpoint). Call inside a transaction."""
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO bronze.filing_xbrl
                (accession_no, cik, entity_cik, document_sha256, extractor_version, status,
                 facts, labels, raw_gzip, fact_count, stats, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s)
            ON CONFLICT (accession_no) DO UPDATE SET
                cik = EXCLUDED.cik, entity_cik = EXCLUDED.entity_cik,
                document_sha256 = EXCLUDED.document_sha256,
                extractor_version = EXCLUDED.extractor_version, status = EXCLUDED.status,
                facts = EXCLUDED.facts, labels = EXCLUDED.labels, raw_gzip = EXCLUDED.raw_gzip,
                fact_count = EXCLUDED.fact_count, stats = EXCLUDED.stats, error = EXCLUDED.error,
                extracted_at = now()
            """,
            [(r.accession_no, r.cik, r.entity_cik, r.document_sha256, EXTRACTOR_VERSION,
              r.status, r.facts_json, r.labels_json, r.raw_gzip, r.fact_count, r.stats_json,
              r.error) for r in rows])


def refresh_silver(conn: psycopg.Connection, accessions: Sequence[str]) -> int:
    if not accessions:
        return 0
    return conn.execute("SELECT silver.refresh_segment_facts(%s)",
                        (list(accessions),)).fetchone()[0]  # type: ignore[index]


def company_ids(conn: psycopg.Connection, ciks: Iterable[str]) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT company_id FROM silver.company_ciks WHERE cik = ANY(%s) ORDER BY 1",
        (sorted(set(ciks)),))]


def refresh_gold(conn: psycopg.Connection, companies: Sequence[str]) -> int:
    """Segment gold rows and the segment quality check for these companies."""
    if not companies:
        return 0
    return conn.execute("SELECT gold.refresh_segments(%s)",
                        (list(companies),)).fetchone()[0]  # type: ignore[index]


def pending_companies(conn: psycopg.Connection) -> list[str]:
    """Companies with silver segment facts but no gold segment rows."""
    return [r[0] for r in conn.execute(
        """
        SELECT DISTINCT k.company_id
        FROM silver.segment_facts f
        JOIN silver.company_ciks k ON k.cik = f.cik
        WHERE f.axis <> 'total'
          AND NOT EXISTS (SELECT 1 FROM gold.segment_financials g
                          WHERE g.company_id = k.company_id)
        ORDER BY 1
        """)]


# jobs ----------------------------------------------------------------------------------------

def _flush(conn: psycopg.Connection, rows: list[Row], stats: RunStats) -> None:
    with conn.transaction():
        store_rows(conn, rows)
        stats.silver_rows += refresh_silver(conn, [r.accession_no for r in rows])
        stats.gold_rows += refresh_gold(conn, company_ids(conn, [r.cik for r in rows]))
    stats.documents += len(rows)
    stats.ok += sum(r.status == "ok" for r in rows)
    stats.batches.append(len(rows))


def run(conn: psycopg.Connection, limit: int = LIMIT, batch_size: int = BATCH) -> RunStats:
    """Extract up to `limit` pending documents, `batch_size` per transaction."""
    with conn.transaction():
        axes = segment_axes(conn)
        todo = pending(conn, limit + 1)
    stats = RunStats(completed=len(todo) <= limit)
    todo = todo[:limit]
    started = time.monotonic()
    batch: list[Row] = []
    for accession_no, cik, sha in todo:
        with conn.transaction():
            content = document(conn, accession_no)
        if content is None:  # deleted meanwhile
            continue
        batch.append(build_row(accession_no, cik, sha, content, axes))
        del content
        if len(batch) >= batch_size:
            _flush(conn, batch, stats)
            batch = []
            log.info("%d/%d documents, %d with iXBRL, %d silver rows (%.0fs)", stats.documents,
                     len(todo), stats.ok, stats.silver_rows, time.monotonic() - started)
    if batch:
        _flush(conn, batch, stats)
    if stats.completed:
        with conn.transaction():
            missing = pending_companies(conn)
        for i in range(0, len(missing), batch_size):
            with conn.transaction():
                stats.gold_rows += refresh_gold(conn, missing[i:i + batch_size])
    return stats


def run_rounds(run_once: Callable[[], RunStats], rounds: int, pause_seconds: float,
               sleep: Callable[[float], None] = time.sleep) -> RunStats:
    """Call run_once up to `rounds` times, pausing between calls, until nothing is left."""
    stats = RunStats()
    for i in range(rounds):
        if i:
            log.info("pausing %.0fs before round %d of %d", pause_seconds, i + 1, rounds)
            sleep(pause_seconds)
        stats = run_once()
        log.info("round %d: %d documents, %d with iXBRL, %d silver rows, %d gold rows", i + 1,
                 stats.documents, stats.ok, stats.silver_rows, stats.gold_rows)
        if stats.completed:
            break
    return stats


def rebuild(conn: psycopg.Connection, batch_size: int = 200) -> RunStats:
    """Re-project every stored extract with the current axes and labels, then re-derive silver
    and gold. Commits per batch of documents; reads raw_gzip, never the documents."""
    stats = RunStats()
    with conn.transaction():
        axes = segment_axes(conn)
        accessions = [r[0] for r in conn.execute(
            "SELECT accession_no FROM bronze.filing_xbrl WHERE status = 'ok'"
            " ORDER BY cik, accession_no")]
    for i in range(0, len(accessions), batch_size):
        chunk = accessions[i:i + batch_size]
        with conn.transaction():
            rows = conn.execute("SELECT accession_no, cik, raw_gzip FROM bronze.filing_xbrl"
                                " WHERE accession_no = ANY(%s)", (chunk,)).fetchall()
            ciks = []
            for accession_no, cik, raw in rows:
                p = project(decompress(bytes(raw)), axes)
                conn.execute("UPDATE bronze.filing_xbrl SET facts = %s::jsonb,"
                             " labels = %s::jsonb WHERE accession_no = %s",
                             (json.dumps(p.facts, separators=(",", ":")),
                              json.dumps(p.labels, separators=(",", ":")), accession_no))
                ciks.append(cik)
            stats.silver_rows += refresh_silver(conn, chunk)
            stats.gold_rows += refresh_gold(conn, company_ids(conn, ciks))
        stats.documents += len(chunk)
        log.info("%d/%d extracts re-projected, %d silver rows", stats.documents,
                 len(accessions), stats.silver_rows)
    with conn.transaction():
        missing = pending_companies(conn)
    for i in range(0, len(missing), 50):
        with conn.transaction():
            stats.gold_rows += refresh_gold(conn, missing[i:i + 50])
    stats.completed = True
    return stats


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int,
                        default=int(os.environ.get("SEGMENTS_LIMIT", LIMIT)),
                        help=f"documents per run (default: $SEGMENTS_LIMIT or {LIMIT})")
    parser.add_argument("--batch", type=int,
                        default=int(os.environ.get("SEGMENTS_BATCH", BATCH)),
                        help=f"documents per transaction (default: $SEGMENTS_BATCH or {BATCH})")
    parser.add_argument("--rounds", type=int,
                        default=int(os.environ.get("SEGMENTS_ROUNDS", 1)),
                        help="runs per process, stopping early once nothing is left"
                             " (default: $SEGMENTS_ROUNDS or 1)")
    parser.add_argument("--pause-seconds", type=float,
                        default=float(os.environ.get("SEGMENTS_PAUSE_SECONDS", 0)),
                        help="pause between rounds (default: $SEGMENTS_PAUSE_SECONDS or 0)")
    parser.add_argument("--rebuild", action="store_true",
                        help="re-project stored extracts and re-derive silver and gold")
    args = parser.parse_args(None if argv is None else list(argv))
    configure_logging()
    started = time.monotonic()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        if args.rebuild:
            stats = rebuild(conn)
        else:
            stats = run_rounds(lambda: run(conn, args.limit, args.batch), args.rounds,
                               args.pause_seconds)
    log.info("%s: %d documents, %d with iXBRL, %d silver rows, %d gold rows in %.0fs",
             "complete" if stats.completed else "limit reached, run again to continue",
             stats.documents, stats.ok, stats.silver_rows, stats.gold_rows,
             time.monotonic() - started)


if __name__ == "__main__":
    main()
