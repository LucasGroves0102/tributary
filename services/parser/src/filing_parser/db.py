"""Read documents from bronze, write sections to silver."""

import gzip

import psycopg

from filing_parser.sections import PARSER_VERSION, Section

PARSER = "python"


def load_document(conn: psycopg.Connection, accession_no: str) -> tuple[str, bytes]:
    """(form, uncompressed HTML)."""
    row = conn.execute(
        "SELECT form, content_gzip FROM bronze.filing_documents WHERE accession_no = %s",
        (accession_no,),
    ).fetchone()
    if row is None:
        raise KeyError(f"no bronze document for {accession_no}")
    return row[0], gzip.decompress(row[1])


def write_sections(conn: psycopg.Connection, accession_no: str, sections: list[Section]) -> None:
    """Replace the document's sections, whichever parser wrote them, atomically: a document
    has one parser's sections, so gold never mixes parsers or keeps the other parser's rows."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "DELETE FROM silver.filing_sections WHERE accession_no = %s", (accession_no,),
        )
        cur.executemany(
            """
            INSERT INTO silver.filing_sections
                (accession_no, section_key, parser, parser_version, part, item, title, text,
                 char_start, char_end)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [(accession_no, s.key, PARSER, PARSER_VERSION, s.part, s.item, s.title, s.text,
              s.char_start, s.char_end) for s in sections],
        )


def all_accessions(conn: psycopg.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT accession_no FROM bronze.filing_documents ORDER BY filed"
    ).fetchall()]


def load_exhibits(conn: psycopg.Connection,
                  accession_no: str) -> list[tuple[str, str | None, bytes]]:
    """(exhibit_type, description, uncompressed HTML) for each stored exhibit."""
    rows = conn.execute(
        "SELECT exhibit_type, description, content_gzip FROM bronze.filing_exhibits"
        " WHERE accession_no = %s ORDER BY exhibit_type", (accession_no,),
    ).fetchall()
    return [(t, d, gzip.decompress(c)) for t, d, c in rows]
