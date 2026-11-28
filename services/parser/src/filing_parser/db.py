"""Read documents from bronze, write sections to silver."""

import gzip

import psycopg
from psycopg.types.json import Jsonb

from filing_parser.sections import PARSER_VERSION, PrintedFact, Section, Table

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


def write_sections(conn: psycopg.Connection, accession_no: str, sections: list[Section],
                   facts: list[PrintedFact] | None = None) -> None:
    """Replace the document's sections, their tables (rule 11) and its printed facts (rule 14),
    whichever parser wrote them, atomically: a document has one parser's sections, so gold never
    mixes parsers or keeps the other parser's rows. When this parser's stored sections are
    exactly the new ones (keys, parts, items, titles, text and offsets), their rows stay,
    parsed_at included, and only parser_version, the tables and the printed facts change: the
    search index and What changed diffs key on parsed_at, so a re-parse that changes no text
    re-indexes nothing (parser v7's tables, v9's printed facts)."""
    new = sorted(((s.key, PARSER, s.part, s.item, s.title, s.text, s.char_start, s.char_end)
                  for s in sections), key=lambda r: r[0])
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "SELECT section_key, parser, part, item, title, text, char_start, char_end"
            " FROM silver.filing_sections WHERE accession_no = %s", (accession_no,))
        if sorted(cur.fetchall(), key=lambda r: r[0]) == new:
            cur.execute("UPDATE silver.filing_sections SET parser_version = %s"
                        " WHERE accession_no = %s AND parser_version <> %s",
                        (PARSER_VERSION, accession_no, PARSER_VERSION))
            cur.execute("DELETE FROM silver.filing_tables WHERE accession_no = %s",
                        (accession_no,))
        else:  # the tables go with their sections (the foreign key cascades)
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
        cur.executemany(
            "INSERT INTO silver.filing_tables (accession_no, section_key, parser, table_no,"
            " char_start, char_end, rows) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(accession_no, s.key, PARSER, n, t.start, t.end, Jsonb(table_rows(t)))
             for s in sections for n, t in enumerate(s.tables)],
        )
        cur.execute("DELETE FROM silver.printed_facts WHERE accession_no = %s", (accession_no,))
        if facts:
            cur.execute(
                "INSERT INTO silver.printed_facts (accession_no, parser, parser_version, facts)"
                " VALUES (%s, %s, %s, %s)",
                (accession_no, PARSER, PARSER_VERSION, Jsonb(printed_rows(facts))))


def table_rows(table: Table) -> list[dict]:
    """silver.filing_tables.rows (migration 067): spans only when a cell spans more than one
    column, head only on a head row."""
    out = []
    for r in table.rows:
        row: dict = {"start": r.start, "end": r.end, "cells": [c.text for c in r.cells]}
        if any(c.span != 1 for c in r.cells):
            row["spans"] = [c.span for c in r.cells]
        if r.head:
            row["head"] = True
        out.append(row)
    return out


def printed_rows(facts: list[PrintedFact]) -> list[dict]:
    """silver.printed_facts.facts (migration 166): one object per fact, short keys, absent ones
    left out (parser-rs db.rs `printed_rows` writes the same)."""
    out = []
    for f in facts:
        row = {"k": f.section, "c": f.concept, "x": f.context, "u": f.unit_ref, "m": f.unit,
               "s": f.start, "e": f.end, "sc": f.scale, "sg": f.sign, "d": f.decimals,
               "f": f.format, "v": f.text, "a": f.char_start, "b": f.char_end}
        out.append({k: v for k, v in row.items() if v is not None})
    return out


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
