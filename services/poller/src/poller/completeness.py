"""Which filings of an EDGAR daily index are missing from the database, and the record of each
day's check (ops.filing_completeness, migration 027).

A filing counts as present when its (accession_no, cik) row is in bronze.filings (feed, daily
index, seed) or bronze.submission_filings (bulk history): the two tables silver.filings unions.
"""

from collections.abc import Sequence
from datetime import date

import psycopg

from poller.edgar import Filing

_MISSING = """
    SELECT u.accession_no, u.cik
    FROM unnest(%s::text[], %s::text[]) AS u(accession_no, cik)
    WHERE NOT EXISTS (SELECT 1 FROM bronze.filings f
                      WHERE f.accession_no = u.accession_no AND f.cik = u.cik)
      AND NOT EXISTS (SELECT 1 FROM bronze.submission_filings s
                      WHERE s.accession_no = u.accession_no AND s.cik = u.cik)
"""

_RECORD = """
    INSERT INTO ops.filing_completeness (day, indexed, missing, republished, checked_at)
    VALUES (%s, %s, %s, %s, now())
    ON CONFLICT (day) DO UPDATE SET indexed = EXCLUDED.indexed, missing = EXCLUDED.missing,
        republished = EXCLUDED.republished, checked_at = EXCLUDED.checked_at
"""


def missing(conn: psycopg.Connection, filings: Sequence[Filing]) -> list[Filing]:
    """The filings (one per filer, index order, duplicates dropped) absent from bronze."""
    unique = list({f.key: f for f in filings}.values())
    with conn.transaction():
        rows = conn.execute(_MISSING, ([f.accession_no for f in unique],
                                       [f.cik for f in unique])).fetchall()
    absent = set(rows)
    return [f for f in unique if f.key in absent]


def record(conn: psycopg.Connection, day: date, filings: Sequence[Filing],
           absent: Sequence[Filing]) -> None:
    with conn.transaction():
        conn.execute(_RECORD, (day, len({f.accession_no for f in filings}),
                               len({f.accession_no for f in absent}), len(absent)))
