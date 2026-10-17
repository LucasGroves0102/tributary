"""Write rows to bronze.filings."""

from collections.abc import Sequence

import psycopg
from psycopg.types.json import Jsonb

from bronze_loader.rows import FilingRow

_INSERT = """
    INSERT INTO bronze.filings
        (accession_no, cik, form_type, filed_at, payload, kafka_partition, kafka_offset)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (accession_no, cik) DO NOTHING
"""


def insert_filings(conn: psycopg.Connection, rows: Sequence[FilingRow]) -> int:
    """Insert rows in one transaction; already-present keys are skipped. Returns rows inserted."""
    if not rows:
        return 0
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            _INSERT,
            [
                (r.accession_no, r.cik, r.form_type, r.filed_at, Jsonb(r.payload),
                 r.kafka_partition, r.kafka_offset)
                for r in rows
            ],
        )
        return cur.rowcount
