"""The crawl's queue and checkpoint (ops.doc_crawl_targets, db/migrations/020_doc_crawl.sql)."""

from dataclasses import dataclass
from datetime import date, timedelta

import psycopg

from doc_fetcher.crawl_fetch import Fetched, Target
from doc_fetcher.db import insert_document, insert_exhibit


@dataclass(frozen=True)
class Control:
    paused: bool


def plan(conn: psycopg.Connection, since: date, eight_k_items: list[str] | None) -> int:
    """Queue new targets filed on or after `since`; returns how many were added."""
    row = conn.execute("SELECT ops.doc_crawl_plan(%s, %s)", (since, eight_k_items)).fetchone()
    return row[0] if row else 0


def claim(conn: psycopg.Connection, limit: int, lease: timedelta) -> list[Target]:
    """The next `limit` targets in crawl order: pending ones whose backoff has passed, and
    claimed ones whose lease expired (a crashed or killed run)."""
    rows = conn.execute(
        """
        UPDATE ops.doc_crawl_targets t SET status = 'claimed', claimed_at = now()
        FROM (
            SELECT accession_no FROM ops.doc_crawl_targets
            WHERE (status = 'pending' AND coalesce(not_before, '-infinity') <= now())
               OR (status = 'claimed' AND claimed_at < now() - %s::interval)
            ORDER BY priority, filing_date DESC, accession_no
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        ) next
        WHERE t.accession_no = next.accession_no
        RETURNING t.accession_no, t.cik, t.form, t.filing_date, t.report_date,
                  t.primary_document, t.attempts, t.priority
        """,
        (lease, limit),
    ).fetchall()
    rows.sort(key=lambda r: (r[7], -r[3].toordinal(), r[0]))
    return [Target(*r[:7]) for r in rows]


def already_stored(conn: psycopg.Connection, accessions: list[str]) -> set[str]:
    rows = conn.execute(
        "SELECT accession_no FROM bronze.filing_documents WHERE accession_no = ANY(%s)",
        (accessions,),
    ).fetchall()
    return {r[0] for r in rows}


def mark_already_stored(conn: psycopg.Connection, accessions: list[str]) -> None:
    """Stored by someone else (the live doc-fetcher), who also published its event."""
    conn.execute(
        """
        UPDATE ops.doc_crawl_targets
        SET status = 'done', finished_at = now(), published_at = now(), files = 0,
            last_error = 'already stored'
        WHERE accession_no = ANY(%s)
        """,
        (accessions,),
    )


def store(conn: psycopg.Connection, target: Target, fetched: Fetched) -> None:
    """Documents and the done mark in one transaction: stored iff done."""
    with conn.transaction():
        insert_document(conn, target.accession_no, target.cik, target.form, fetched.index,
                        fetched.document)
        for exhibit, blob in fetched.exhibits:
            insert_exhibit(conn, target.accession_no, exhibit, blob)
        conn.execute(
            """
            UPDATE ops.doc_crawl_targets
            SET status = 'done', finished_at = now(), published_at = NULL, last_error = NULL,
                files = %s, raw_bytes = %s, stored_bytes = %s
            WHERE accession_no = %s
            """,
            (fetched.files, fetched.raw_bytes, fetched.stored_bytes, target.accession_no),
        )


def finish(conn: psycopg.Connection, accession_no: str, status: str, error: str) -> None:
    """A target that won't be retried: failed (SEC said no) or skipped (unusable)."""
    conn.execute(
        """
        UPDATE ops.doc_crawl_targets
        SET status = %s, finished_at = now(), last_error = left(%s, 500),
            attempts = attempts + (%s = 'failed')::int
        WHERE accession_no = %s
        """,
        (status, error, status, accession_no),
    )


def retry_later(conn: psycopg.Connection, accession_no: str, error: str,
                delay: timedelta) -> None:
    conn.execute(
        """
        UPDATE ops.doc_crawl_targets
        SET status = 'pending', attempts = attempts + 1, not_before = now() + %s::interval,
            claimed_at = NULL, last_error = left(%s, 500)
        WHERE accession_no = %s
        """,
        (delay, error, accession_no),
    )


def release(conn: psycopg.Connection, accessions: list[str]) -> None:
    """Hand back claimed targets that weren't attempted (shutdown)."""
    conn.execute(
        "UPDATE ops.doc_crawl_targets SET status = 'pending', claimed_at = NULL"
        " WHERE accession_no = ANY(%s) AND status = 'claimed'",
        (accessions,),
    )


def unpublished(conn: psycopg.Connection, limit: int) -> list[dict]:
    """documents.backfill events for done targets whose event isn't confirmed yet."""
    rows = conn.execute(
        """
        SELECT d.accession_no, d.cik, d.form, d.document_name, d.url, d.content_sha256,
               d.size_bytes
        FROM ops.doc_crawl_targets t
        JOIN bronze.filing_documents d USING (accession_no)
        WHERE t.status = 'done' AND t.published_at IS NULL
        ORDER BY t.finished_at
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    keys = ("accession_no", "cik", "form", "document_name", "url", "content_sha256",
            "size_bytes")
    return [dict(zip(keys, r, strict=True)) for r in rows]


def mark_published(conn: psycopg.Connection, accessions: list[str]) -> None:
    conn.execute(
        "UPDATE ops.doc_crawl_targets SET published_at = now() WHERE accession_no = ANY(%s)",
        (accessions,),
    )


def remaining(conn: psycopg.Connection) -> int:
    row = conn.execute(
        "SELECT count(*) FROM ops.doc_crawl_targets WHERE status IN ('pending', 'claimed')"
    ).fetchone()
    return row[0] if row else 0


def control(conn: psycopg.Connection) -> Control:
    row = conn.execute("SELECT paused FROM ops.doc_crawl_control").fetchone()
    return Control(paused=bool(row and row[0]))


def report(conn: psycopg.Connection, state: str, disk_free: int | None,
           min_free: int) -> None:
    conn.execute(
        """
        UPDATE ops.doc_crawl_control
        SET state = %s, disk_free_bytes = %s, min_free_bytes = %s, heartbeat_at = now()
        """,
        (state, disk_free, min_free),
    )
