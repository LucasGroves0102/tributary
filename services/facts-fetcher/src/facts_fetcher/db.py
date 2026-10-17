"""Bronze writes and silver/gold refreshes. The transforms themselves live in SQL."""

import hashlib
from collections import defaultdict

import psycopg


def tracked_ciks(conn: psycopg.Connection) -> dict[str, list[str]]:
    """company_id → every CIK in its lineage, for tracked companies."""
    rows = conn.execute(
        """
        SELECT c.company_id, k.cik
        FROM silver.companies c JOIN silver.company_ciks k USING (company_id)
        WHERE c.tracked
        ORDER BY c.company_id, k.cik
        """
    ).fetchall()
    out: dict[str, list[str]] = defaultdict(list)
    for company_id, cik in rows:
        out[company_id].append(cik)
    return dict(out)


def store_payload(conn: psycopg.Connection, cik: str, raw: bytes) -> bool:
    """Insert the payload if it's new for this CIK. Returns True if it was new."""
    digest = hashlib.sha256(raw).hexdigest()
    row = conn.execute(
        """
        INSERT INTO bronze.company_facts (cik, payload_sha256, payload)
        VALUES (%s, %s, %s::jsonb)
        ON CONFLICT (cik, payload_sha256) DO UPDATE SET last_checked_at = now()
        RETURNING (xmax = 0)  -- true on insert, false on update
        """,
        (cik, digest, raw.decode()),
    ).fetchone()
    return row[0]


def refresh_silver(conn: psycopg.Connection, cik: str) -> int:
    return conn.execute("SELECT silver.refresh_financial_facts(%s)", (cik,)).fetchone()[0]


def refresh_gold(conn: psycopg.Connection, company_id: str) -> int:
    return conn.execute(
        "SELECT gold.refresh_quarterly_financials(%s)", (company_id,)
    ).fetchone()[0]
