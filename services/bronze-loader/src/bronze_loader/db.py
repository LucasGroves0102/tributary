"""Write rows to bronze.filings."""

from collections.abc import Sequence
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from bronze_loader.rows import FilingRow

_INSERT = """
    INSERT INTO bronze.filings
        (accession_no, cik, form_type, filed_at, payload, kafka_partition, kafka_offset)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (accession_no, cik) DO NOTHING
    RETURNING ingested_at
"""


def insert_filings(conn: psycopg.Connection,
                   rows: Sequence[FilingRow]) -> list[tuple[FilingRow, datetime]]:
    """Insert rows in one transaction; already-present keys are skipped. Returns the rows
    inserted, each with its ingested_at (the latency metrics' end)."""
    if not rows:
        return []
    inserted = []
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            _INSERT,
            [
                (r.accession_no, r.cik, r.form_type, r.filed_at, Jsonb(r.payload),
                 r.kafka_partition, r.kafka_offset)
                for r in rows
            ],
            returning=True,
        )
        for r in rows:  # one result per row, in order; empty where the key was there
            got = cur.fetchone()
            if got is not None:
                inserted.append((r, got[0]))
            cur.nextset()
    return inserted
