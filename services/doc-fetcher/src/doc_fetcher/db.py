"""Tracked-company lookups and bronze document writes."""

import gzip
import hashlib
from dataclasses import dataclass

import psycopg

from doc_fetcher.index import Exhibit, FilingIndex


def tracked_ciks(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT k.cik FROM silver.company_ciks k JOIN silver.companies c USING (company_id)"
        " WHERE c.tracked"
    ).fetchall()
    return {r[0] for r in rows}


def operating_ciks(conn: psycopg.Connection) -> set[str]:
    """CIKs of the operating companies: those with gold financials, the document crawl's set
    (ops.doc_crawl_plan). ~6,000 companies, 60 ms on hermes (one index probe per CIK)."""
    rows = conn.execute(
        "SELECT k.cik FROM silver.company_ciks k WHERE EXISTS"
        " (SELECT 1 FROM gold.quarterly_financials q WHERE q.company_id = k.company_id)"
    ).fetchall()
    return {r[0] for r in rows}


def already_fetched(conn: psycopg.Connection, accession_no: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM bronze.filing_documents WHERE accession_no = %s", (accession_no,)
    ).fetchone() is not None


@dataclass(frozen=True)
class Blob:
    """A fetched file as stored: gzip bytes, plus SHA-256 and size of the uncompressed bytes."""
    content_gzip: bytes
    sha256: str
    size: int

    @classmethod
    def of(cls, content: bytes) -> "Blob":
        return cls(gzip.compress(content), hashlib.sha256(content).hexdigest(), len(content))


def insert_document(
    conn: psycopg.Connection, accession_no: str, cik: str, form: str,
    index: FilingIndex, content: bytes | Blob,
) -> str:
    """Store the document; returns its SHA-256. A concurrent duplicate is ignored."""
    blob = content if isinstance(content, Blob) else Blob.of(content)
    conn.execute(
        """
        INSERT INTO bronze.filing_documents
            (accession_no, cik, form, filed, period_of_report, document_name, url,
             content_gzip, content_sha256, size_bytes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (accession_no) DO NOTHING
        """,
        (accession_no, cik, form, index.filed, index.period_of_report, index.document_name,
         index.url, blob.content_gzip, blob.sha256, blob.size),
    )
    return blob.sha256


def insert_exhibit(conn: psycopg.Connection, accession_no: str, exhibit: Exhibit,
                   content: bytes | Blob) -> None:
    blob = content if isinstance(content, Blob) else Blob.of(content)
    conn.execute(
        """
        INSERT INTO bronze.filing_exhibits
            (accession_no, exhibit_type, description, document_name, url, content_gzip,
             content_sha256, size_bytes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (accession_no, exhibit_type) DO NOTHING
        """,
        (accession_no, exhibit.exhibit_type, exhibit.description, exhibit.document_name,
         exhibit.url, blob.content_gzip, blob.sha256, blob.size),
    )


def owe_event(conn: psycopg.Connection, accession_no: str) -> None:
    """A documents.fetched event is owed for this stored document (migration 059's outbox):
    written in the document's own transaction, deleted when Kafka confirms the event."""
    conn.execute(
        "INSERT INTO ops.document_outbox (accession_no) VALUES (%s)"
        " ON CONFLICT (accession_no) DO NOTHING", (accession_no,))


def paid_events(conn: psycopg.Connection, accessions: list[str]) -> None:
    conn.execute("DELETE FROM ops.document_outbox WHERE accession_no = ANY(%s)", (accessions,))


def owed_events(conn: psycopg.Connection, limit: int, older_than_seconds: int = 30) -> list[dict]:
    """documents.fetched events still owed, oldest first, as the consumer first sent them
    (rows queued in the last `older_than_seconds` are left to the send in progress). Marks
    each as attempted. An owed row whose document is gone (deleted by hand: the row commits
    with its document) is cleared, never sent; the `limit` oldest are taken among the rows that
    have a document, so no number of such rows can hold the others back (pre-ship review)."""
    conn.execute(
        "DELETE FROM ops.document_outbox o WHERE o.queued_at < now() - make_interval(secs => %s)"
        " AND NOT EXISTS (SELECT 1 FROM bronze.filing_documents d"
        "                 WHERE d.accession_no = o.accession_no)", (older_than_seconds,))
    rows = conn.execute(
        """
        UPDATE ops.document_outbox o SET attempts = attempts + 1, last_attempt = now()
        FROM bronze.filing_documents d
        WHERE d.accession_no = o.accession_no
          AND o.accession_no IN (SELECT x.accession_no FROM ops.document_outbox x
                                 JOIN bronze.filing_documents y USING (accession_no)
                                 WHERE x.queued_at < now() - make_interval(secs => %s)
                                 ORDER BY x.queued_at LIMIT %s)
        RETURNING d.accession_no, d.cik, d.form, d.document_name, d.url, d.content_sha256,
                  d.size_bytes, o.queued_at
        """,
        (older_than_seconds, limit),
    ).fetchall()
    keys = ("accession_no", "cik", "form", "document_name", "url", "content_sha256",
            "size_bytes")
    return [dict(zip(keys, r[:7], strict=True)) for r in sorted(rows, key=lambda r: r[7])]


def eight_ks_without_exhibits(conn: psycopg.Connection) -> list[tuple[str, str]]:
    """(accession_no, cik) of stored 8-Ks with no exhibits fetched yet."""
    return conn.execute(
        """
        SELECT d.accession_no, d.cik FROM bronze.filing_documents d
        WHERE d.form = '8-K'
          AND NOT EXISTS (SELECT 1 FROM bronze.filing_exhibits e
                          WHERE e.accession_no = d.accession_no)
        ORDER BY d.filed
        """
    ).fetchall()
