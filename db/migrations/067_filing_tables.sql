-- Tables as tables, beside the text (parser v7, rule 11 of services/parser's sections.py; analyst
-- test 4, findings 31 and 8: an earnings release's outlook read as alternating label and value lines,
-- its GAAP-to-adjusted reconciliation as a monospace dump).
--
-- The parsers' text is unchanged: html_to_text still writes one table row per line, its cells joined
-- by a space, and every stored offset (citations, thesis pins' ?at=, search passages, diffs, peers'
-- mentions) keeps its meaning. Beside each section the parser now records the HTML tables whose rows
-- lie in it: one row here per table, its rows and cells as jsonb, each row with its [start, end) in
-- the section's text, so a reader of the text (query_api/releases.py) can find the table a passage
-- quotes and show it with its columns.
--
--   rows: [{"start": int, "end": int, "cells": [text, ...], "spans": [int, ...], "head": true}, ...]
--         "spans" (colspans, 1-100) only when a cell spans more than one column, "head" only on a
--         head row (in a <thead>, or every non-empty cell a <th>). Cells keep their order, empty
--         ones too ("" for a spacer cell or the label column of a heading row), so a row's columns
--         are its cells' spans summed in order. A cell's text is the text's own characters ("\n"
--         read as a space); a table has at least 2 rows with text.
--
-- One parser's rows per document, like silver.filing_sections: the parser writes a document's tables
-- in the transaction that writes its sections, and deleting a section row deletes its tables (the
-- foreign key cascades). A document parsed before v7 has no rows here until it is parsed again; a
-- reader falls back to the text (releases: the lines as before).
--
-- Size, measured on 205 stored documents and 130 exhibits sampled from hermes (2026-11-15): the
-- tables' JSON is about 37% of the 10-K and 10-Q section text, 28% of 8-K items' and 54% of
-- exhibits'; 42% of tables have one row (bullets and layout) and aren't kept. All stored filings:
-- about 15 GB of JSON before TOAST compression (parser README "Tables beside the text").
--
-- Access: filing content, so the filings tier, with silver.filing_sections' policy (030's form).
-- Backups: dumped schema-only nightly with the rest of the document subtree (deploy/host/pg-backup.sh
-- SLIM_EXCLUDE_DATA: its foreign key leads to bronze.filing_documents).
--
-- Locks on hermes: CREATE TABLE and its policy only; the foreign key's validation reads the new,
-- empty table. silver.filing_sections gets a SHARE ROW EXCLUSIVE lock for the instant the
-- constraint is added (writers wait, readers don't), hence the short lock_timeout.

SET LOCAL lock_timeout = '5s';

CREATE TABLE silver.filing_tables (
    accession_no text  NOT NULL,
    section_key  text  NOT NULL,
    parser       text  NOT NULL,
    table_no     int   NOT NULL,  -- 0-based, in text order within the section
    char_start   int   NOT NULL,  -- [char_start, char_end) in the section's text
    char_end     int   NOT NULL,
    rows         jsonb NOT NULL,
    PRIMARY KEY (accession_no, section_key, parser, table_no),
    FOREIGN KEY (accession_no, section_key, parser)
        REFERENCES silver.filing_sections ON DELETE CASCADE
);

GRANT SELECT ON silver.filing_tables TO query_api;

ALTER TABLE silver.filing_tables ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON silver.filing_tables FOR SELECT TO query_api
    USING ((SELECT k.company_id
            FROM bronze.filing_documents d
            JOIN silver.company_ciks k ON k.cik = d.cik
            WHERE d.accession_no = filing_tables.accession_no)
           IN (SELECT r FROM authz.readable_companies('filings') r));
