"""parser-index: search passages and section diffs for parsed filings (migration 021).

Each run asks ops.section_index_pending for work and processes it in transactions of --batch
filings. Per filing: (re)index when its sections are new or re-parsed (checkpoint row in
ops.section_index, passages via gold.refresh_section_search), then, for 10-K and 10-Q, compare
Risk Factors and MD&A with the comparable filings (ops.section_index_priors) and replace its
gold.section_diffs / gold.section_changes rows. Everything for a filing commits together, so a
stopped run resumes where it left off and a re-run changes nothing.
"""

import argparse
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from filing_parser.diff import ALGORITHM, Diff, diff
from tributary_telemetry.logs import configure_logging

log = logging.getLogger("parser_index")

# Sections compared per form: Risk Factors and MD&A.
DIFF_SECTIONS = {"10-K": ("1A", "7"), "10-Q": ("I-2", "II-1A")}
LIMIT = 5000
BATCH = 25
LOCK = 2_021_001  # pg_advisory_lock key: one indexer at a time


@dataclass
class Stats:
    filings: int = 0
    indexed: int = 0
    passages: int = 0
    diffed: int = 0
    section_diffs: int = 0
    changes: int = 0
    errors: int = 0
    completed: bool = False


@dataclass(frozen=True)
class Section:
    accession_no: str
    form: str
    filed: object
    parser: str
    parsed_at: object
    text: str


def run(conn: psycopg.Connection, limit: int = LIMIT, batch: int = BATCH) -> Stats:
    stats = Stats()
    with conn.transaction():
        locked = conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK,)).fetchone()
    if not (locked and locked[0]):
        log.warning("another parser-index run holds the lock; exiting")
        stats.completed = True
        return stats
    last: list = []
    try:
        while stats.filings < limit:
            with conn.transaction():
                rows = conn.execute(
                    "SELECT accession_no, reason FROM ops.section_index_pending(%s, %s)",
                    (ALGORITHM, min(limit - stats.filings, 1000))).fetchall()
            if not rows:
                stats.completed = True
                break
            if rows == last:  # processing them changed nothing: don't spin
                log.warning("filings stay pending after processing: %s", rows[:5])
                break
            last = rows
            order = list(dict.fromkeys(a for a, _ in rows))
            reindex = {a for a, reason in rows if reason == "sections"}
            for i in range(0, len(order), batch):
                process(conn, order[i:i + batch], reindex, stats)
                stats.filings += len(order[i:i + batch])
                log.info("indexed %d filings (%d passages), diffed %d (%d sections, %d changes)",
                         stats.indexed, stats.passages, stats.diffed, stats.section_diffs,
                         stats.changes)
    finally:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))
    return stats


def process(conn: psycopg.Connection, accessions: list[str], reindex: set[str],
            stats: Stats) -> None:
    with conn.transaction():
        fresh = [a for a in accessions if a in reindex]
        if fresh:
            stats.indexed += index(conn, fresh)
            stats.passages += conn.execute("SELECT gold.refresh_section_search(%s)",
                                           (fresh,)).fetchone()[0]  # type: ignore[index]
            conn.execute("UPDATE ops.section_index i SET passages = (SELECT count(*)"
                         " FROM gold.section_search s WHERE s.accession_no = i.accession_no)"
                         " WHERE accession_no = ANY(%s)", (fresh,))
        priors = conn.execute(
            "SELECT p.accession_no, i.company_id, i.form, p.prior_accession_no,"
            "       p.year_ago_accession_no"
            " FROM ops.section_index_priors(%s) p JOIN ops.section_index i USING (accession_no)",
            (accessions,)).fetchall()
        if not priors:
            return
        wanted = {a for row in priors for a in (row[0], row[3], row[4]) if a}
        keys = sorted({k for ks in DIFF_SECTIONS.values() for k in ks})
        sections = load_sections(conn, sorted(wanted), keys)
        conn.execute("DELETE FROM gold.section_diffs WHERE accession_no = ANY(%s)",
                     ([r[0] for r in priors],))
        for accession_no, company_id, form, prior, year_ago in priors:
            error = None
            try:
                for basis, other in (("prior", prior), ("year_ago", year_ago)):
                    if other is None:
                        continue
                    for key in DIFF_SECTIONS[form]:
                        new, old = sections.get((accession_no, key)), sections.get((other, key))
                        if new is None or old is None:
                            continue
                        stats.changes += write_diff(conn, company_id, key, basis, new, old,
                                                    diff(old.text, new.text))
                        stats.section_diffs += 1
            except Exception as e:  # noqa: BLE001 - recorded, the run goes on
                log.exception("diff failed for %s", accession_no)
                error = f"{type(e).__name__}: {e}"[:500]
                stats.errors += 1
            conn.execute(
                "UPDATE ops.section_index SET algorithm = %s, prior_accession_no = %s,"
                " year_ago_accession_no = %s, diffed_at = clock_timestamp(), diff_error = %s"
                " WHERE accession_no = %s", (ALGORITHM, prior, year_ago, error, accession_no))
            stats.diffed += 1


def index(conn: psycopg.Connection, accessions: list[str]) -> int:
    """Upsert the checkpoint rows of these filings from the sections gold serves."""
    cur = conn.execute(
        """
        INSERT INTO ops.section_index AS i (accession_no, company_id, form, filed, period,
                                            sections_parsed_at, sections, passages,
                                            indexed_at)
        SELECT g.accession_no, min(g.company_id), min(g.form), min(g.filed),
               coalesce(min(g.period_of_report), min(g.filed)),
               (SELECT max(s.parsed_at) FROM silver.filing_sections s
                WHERE s.accession_no = g.accession_no),
               count(*), 0, clock_timestamp()
        FROM gold.filing_sections g
        WHERE g.accession_no = ANY(%s)
        GROUP BY g.accession_no
        ON CONFLICT (accession_no) DO UPDATE SET
            company_id = excluded.company_id, form = excluded.form, filed = excluded.filed,
            period = excluded.period, sections_parsed_at = excluded.sections_parsed_at,
            sections = excluded.sections, indexed_at = clock_timestamp()
        """, (accessions,))
    return cur.rowcount


def load_sections(conn: psycopg.Connection, accessions: list[str],
                  keys: list[str]) -> dict[tuple[str, str], Section]:
    rows = conn.execute(
        "SELECT accession_no, section_key, form, filed, parser, parsed_at, text"
        " FROM gold.filing_sections WHERE accession_no = ANY(%s) AND section_key = ANY(%s)",
        (accessions, keys)).fetchall()
    return {(r[0], r[1]): Section(r[0], r[2], r[3], r[4], r[5], r[6]) for r in rows}


def _flat(spans: Iterable[tuple[int, int]]) -> list[int]:
    return [x for span in spans for x in span]


def write_diff(conn: psycopg.Connection, company_id: str, key: str, basis: str, new: Section,
               old: Section, d: Diff) -> int:
    conn.execute(
        """
        INSERT INTO gold.section_diffs (company_id, accession_no, section_key, basis, form,
            filed, parser, parsed_at, prior_accession_no, prior_form, prior_filed, prior_parser,
            prior_parsed_at, paragraphs, prior_paragraphs, unchanged, minor_edits, numbers_only,
            rewritten, new, removed, algorithm)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s)
        """, (company_id, new.accession_no, key, basis, new.form, new.filed, new.parser,
              new.parsed_at, old.accession_no, old.form, old.filed, old.parser, old.parsed_at,
              d.paragraphs, d.prior_paragraphs, d.unchanged, d.minor_edits, d.numbers_only,
              d.rewritten, d.new, d.removed, ALGORITHM))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO gold.section_changes (company_id, accession_no, section_key, basis,"
            " ordinal, kind, similarity, new_spans, prior_spans, words)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(company_id, new.accession_no, key, basis, n, c.kind, c.similarity,
              _flat(c.new.spans) if c.new else None, _flat(c.old.spans) if c.old else None,
              Jsonb(c.words) if c.words is not None else None)
             for n, c in enumerate(d.changes)])
    return len(d.changes)


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int,
                        default=int(os.environ.get("SECTION_INDEX_LIMIT", LIMIT)),
                        help=f"filings per run (default: $SECTION_INDEX_LIMIT or {LIMIT})")
    parser.add_argument("--batch", type=int,
                        default=int(os.environ.get("SECTION_INDEX_BATCH", BATCH)),
                        help=f"filings per transaction (default: $SECTION_INDEX_BATCH or {BATCH})")
    args = parser.parse_args(None if argv is None else list(argv))
    configure_logging()
    started = time.monotonic()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        stats = run(conn, args.limit, args.batch)
    log.info("%s: %d filings, %d indexed (%d passages), %d diffed (%d section diffs, %d changes,"
             " %d errors) in %.0fs",
             "complete" if stats.completed else "limit reached, run again to continue",
             stats.filings, stats.indexed, stats.passages, stats.diffed, stats.section_diffs,
             stats.changes, stats.errors, time.monotonic() - started)


if __name__ == "__main__":
    main()
