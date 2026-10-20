"""bronze.submissions / submission_filings writes, one transaction per batch of CIKs.
Every call runs in its own transaction, so a crash loses at most one batch."""

import json
from datetime import datetime

import psycopg

from company_loader.submissions import FILING_COLUMNS, Submission

_STAGING = """
    CREATE TEMP TABLE IF NOT EXISTS stg_submissions (
        cik text, content_sha256 text, payload jsonb, filings int);
    CREATE TEMP TABLE IF NOT EXISTS stg_filings (LIKE bronze.submission_filings);
    TRUNCATE stg_submissions, stg_filings;
"""

_COLUMNS = ", ".join(FILING_COLUMNS)
_UPDATABLE = [c for c in FILING_COLUMNS if c not in ("accession_no", "cik")]

_MERGE = [
    # a CIK's pages can repeat a filing from its recent list
    f"""
    INSERT INTO bronze.submission_filings AS f ({_COLUMNS})
    SELECT DISTINCT ON (accession_no, cik) {_COLUMNS} FROM stg_filings
    ORDER BY accession_no, cik
    ON CONFLICT (accession_no, cik) DO UPDATE SET
        {", ".join(f"{c} = EXCLUDED.{c}" for c in _UPDATABLE)}
    WHERE ({", ".join(f"f.{c}" for c in _UPDATABLE)})
        IS DISTINCT FROM ({", ".join(f"EXCLUDED.{c}" for c in _UPDATABLE)})
    """,
    # filings the SEC no longer lists for a reloaded CIK
    """
    DELETE FROM bronze.submission_filings f
    USING stg_submissions s
    WHERE f.cik = s.cik
      AND NOT EXISTS (SELECT 1 FROM stg_filings g
                      WHERE g.accession_no = f.accession_no AND g.cik = f.cik)
    """,
    """
    INSERT INTO bronze.submissions (cik, content_sha256, payload, filings, snapshot)
    SELECT cik, content_sha256, payload, filings, %(snapshot)s FROM stg_submissions
    ON CONFLICT (cik) DO UPDATE SET
        content_sha256 = EXCLUDED.content_sha256, payload = EXCLUDED.payload,
        filings = EXCLUDED.filings, snapshot = EXCLUDED.snapshot, loaded_at = now()
    """,
    """
    UPDATE bronze.submissions_loads SET ciks_loaded = ciks_loaded + %(ciks)s
    WHERE snapshot = %(snapshot)s
    """,
]


def latest_load(conn: psycopg.Connection) -> tuple[datetime, bool] | None:
    """(snapshot, finished) of the newest zip snapshot seen, if any."""
    with conn.transaction():
        row = conn.execute(
            "SELECT snapshot, finished_at IS NOT NULL FROM bronze.submissions_loads"
            " ORDER BY snapshot DESC LIMIT 1").fetchone()
    return (row[0], row[1]) if row else None


def start_run(conn: psycopg.Connection, snapshot: datetime) -> None:
    with conn.transaction():
        conn.execute(
            "INSERT INTO bronze.submissions_loads (snapshot, runs) VALUES (%s, 1)"
            " ON CONFLICT (snapshot) DO UPDATE"
            " SET runs = bronze.submissions_loads.runs + 1", (snapshot,))


def finish_load(conn: psycopg.Connection, snapshot: datetime) -> None:
    with conn.transaction():
        conn.execute("UPDATE bronze.submissions_loads SET finished_at = coalesce(finished_at,"
                     " now()) WHERE snapshot = %s", (snapshot,))


def known(conn: psycopg.Connection, ciks: list[str]) -> dict[str, tuple[str, datetime]]:
    """cik → (content_sha256, snapshot) of what's stored."""
    with conn.transaction():
        rows = conn.execute("SELECT cik, content_sha256, snapshot FROM bronze.submissions"
                            " WHERE cik = ANY(%s)", (ciks,)).fetchall()
    return {cik: (digest, snap) for cik, digest, snap in rows}


def write_batch(conn: psycopg.Connection, batch: list[Submission], snapshot: datetime) -> int:
    """Upsert these CIKs' metadata and filings, then refresh their companies' metadata.
    Returns the number of companies whose metadata changed."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_STAGING)
        with cur.copy("COPY stg_submissions FROM STDIN") as copy:
            for s in batch:
                copy.write_row((s.cik, s.content_sha256, json.dumps(s.metadata), len(s.filings)))
        with cur.copy(f"COPY stg_filings ({_COLUMNS}) FROM STDIN") as copy:
            for s in batch:
                for row in s.filings:
                    copy.write_row(row)
        cur.execute("ANALYZE stg_submissions, stg_filings")
        params = {"snapshot": snapshot, "ciks": len(batch)}
        for sql in _MERGE:
            cur.execute(sql, params if "%(" in sql else None)
        cur.execute("SELECT silver.refresh_company_metadata(%s)", ([s.cik for s in batch],))
        row = cur.fetchone()
        return row[0] if row else 0
