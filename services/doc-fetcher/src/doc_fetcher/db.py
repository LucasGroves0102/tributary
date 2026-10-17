"""Tracked-company lookups and bronze document writes."""

import gzip
import hashlib

import psycopg

from doc_fetcher.index import FilingIndex


def tracked_ciks(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT k.cik FROM silver.company_ciks k JOIN silver.companies c USING (company_id)"
        " WHERE c.tracked"
    ).fetchall()
    return {r[0] for r in rows}


def already_fetched(conn: psycopg.Connection, accession_no: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM bronze.filing_documents WHERE accession_no = %s", (accession_no,)
    ).fetchone() is not None


def insert_document(
    conn: psycopg.Connection, accession_no: str, cik: str, form: str,
    index: FilingIndex, content: bytes,
) -> str:
    """Store the document; returns its SHA-256. A concurrent duplicate is ignored."""
    digest = hashlib.sha256(content).hexdigest()
    conn.execute(
        """
        INSERT INTO bronze.filing_documents
            (accession_no, cik, form, filed, period_of_report, document_name, url,
             content_gzip, content_sha256, size_bytes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (accession_no) DO NOTHING
        """,
        (accession_no, cik, form, index.filed, index.period_of_report, index.document_name,
         index.url, gzip.compress(content), digest, len(content)),
    )
    return digest
