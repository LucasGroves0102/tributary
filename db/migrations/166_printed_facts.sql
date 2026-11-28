-- The proof layer at silver, part 1: where each figure is printed (parser v9, python-9 / rust-9;
-- Marek's design-QA review findings 1-3, 6 and 24, the owner's call of 2026-11-27).
--
-- Figures (XBRL facts: companyfacts and the filings' own XBRL) and documents (stored HTML parsed into
-- sections) were two pipelines never linked at the line level: a citation landed on a section, up to
-- 16,518px from the figure. A 10-K/10-Q/20-F filed since inline XBRL is its own instance: each printed
-- number is an ix:nonFraction naming its concept, context (period), unit, scale, sign and format.
-- Parser v9 (rule 14 of services/parser's sections.py, both parsers) records, beside the text, every
-- undimensioned numeric fact a primary document prints: where (document offsets, the section that
-- holds it) and what (concept, context and unit as written and resolved, scale, sign, decimals,
-- format, the printed characters). Migration 167 joins them to silver's facts (the gate).
--
--   facts: [{"k": section key, "c": concept, "x": contextref, "u": unitref, "m": unit measure,
--            "s": period start, "e": period end or instant, "sc": scale, "sg": sign,
--            "d": decimals, "f": format, "v": printed text, "a": start, "b": end}, ...]
--          document order; a key is left out when absent ("k" outside every section, "s" for an
--          instant, "m" for an unknown unit, the attributes the element doesn't have); "a"/"b" are
--          [start, end) in characters of the document's text (html_to_text), the offsets
--          silver.filing_sections.char_start and the reader's ?at= use.
--
-- One parser's row per document, like silver.filing_sections: the parser writes it in the
-- transaction that writes the sections (deleting any parser's row first), so a re-parse that yields
-- the same sections keeps their rows and parsed_at and writes only this row. A document without
-- inline XBRL facts has no row. Rule 15 (same version): a 10-Q's Part I items 1 and 2 placed by
-- its table of contents when its body heads neither (JPMorgan, BRT Apartments).
--
-- Measured on hermes 2026-11-28 (read-only; docs/test-people/reports/proof-layer/budget.md): 121,118
-- stored 10-K/10-Q (+/A) documents carry inline XBRL; 173 sampled documents: 437 (10-Q) to 679
-- (20-F) kept facts each, 62-71% of all printed facts are dimensioned (left out); est. 18.6 KB a
-- 10-Q, 27.4 KB a 10-K stored (pglz), ~2.6 GB for every stored iXBRL document.
--
-- The re-parse is scoped (165's way): ops.parser_catchup re-queues a document below version 9 only
-- when version 9 changes its output, i.e. it carries inline XBRL (bronze.filing_xbrl ok), is a 20-F
-- (no extract says which do), or is a 10-Q with a missing_core_item finding (rule 15's cases); a
-- document below version 8 stays due as before. On hermes: 135,135 of 666,263 documents (335 GB of
-- HTML; the selection took 3.9 s), ~45 hours at the catch-up's 3,000 an hour. The parser kind is
-- PAUSED by this migration: the owner unpauses it once the measured budget is OK'd
--   UPDATE ops.catchup SET paused = false WHERE kind = 'parser';
-- New filings are parsed at v9 as they arrive either way.
--
-- Access: no grant to query_api (readers get lines through 167's gate). Backups: its foreign key
-- leads to bronze.filing_documents, so it joins pg-backup's SLIM_EXCLUDE_DATA (weekly in full).
--
-- Locks on hermes: CREATE TABLE; bronze.filing_documents gets a SHARE ROW EXCLUSIVE lock for the
-- instant the foreign key is added (writers wait, readers don't), hence the short lock_timeout;
-- CREATE OR REPLACE of ops.parser_catchup (catalog only); one ops.catchup row updated.

SET LOCAL lock_timeout = '3s';

CREATE TABLE silver.printed_facts (
    accession_no   text        NOT NULL REFERENCES bronze.filing_documents ON DELETE CASCADE,
    parser         text        NOT NULL,  -- python | rust
    parser_version text        NOT NULL,
    facts          jsonb       NOT NULL,
    written_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (accession_no, parser)
);

-- 071's body; a document below p_version is due only when that version changes it: below 8 always,
-- version 9's scope below (166), any document for a later version unless it scopes itself.
CREATE OR REPLACE FUNCTION ops.parser_catchup(p_version integer, p_since timestamptz,
                                              p_limit integer, p_outbox integer,
                                              p_in_flight integer, p_crawl_busy integer)
RETURNS TABLE (behind bigint, in_flight bigint, failed bigint, oldest date, queued bigint)
LANGUAGE sql AS $$
    WITH old AS MATERIALIZED (
        SELECT s.accession_no, min(ops.parser_version_number(s.parser_version)) AS version
        FROM silver.filing_sections s
        GROUP BY s.accession_no
        HAVING min(ops.parser_version_number(s.parser_version)) < p_version
    ), scope_9 AS MATERIALIZED (  -- what version 9 changes (166): printed facts and rule 15
        SELECT x.accession_no FROM bronze.filing_xbrl x WHERE x.status = 'ok'
        UNION
        SELECT d.accession_no FROM bronze.filing_documents d WHERE d.form IN ('20-F', '20-F/A')
        UNION
        SELECT f.accession_no
        FROM ops.section_findings f
        JOIN bronze.filing_documents d ON d.accession_no = f.accession_no
        WHERE f.check_name = 'missing_core_item' AND d.form LIKE '10-Q%'
    ), due AS MATERIALIZED (
        SELECT o.accession_no FROM old o
        WHERE o.version < 8 OR p_version > 9
           OR o.accession_no IN (SELECT accession_no FROM scope_9)
    ), docs AS MATERIALIZED (
        SELECT d.accession_no, d.filed, t.status, t.published_at,
               t.accession_no IS NOT NULL AS targeted,
               coalesce(t.status = 'done' AND (t.published_at IS NULL
                                               OR t.published_at >= p_since), false) AS requested
        FROM due o
        JOIN bronze.filing_documents d ON d.accession_no = o.accession_no
        LEFT JOIN ops.doc_crawl_targets t ON t.accession_no = o.accession_no
        WHERE EXISTS (SELECT 1 FROM silver.company_ciks k WHERE k.cik = d.cik)
    ), room AS (
        SELECT CASE
            WHEN (SELECT count(*) FROM ops.doc_crawl_targets
                  WHERE status IN ('pending', 'claimed')) >= p_crawl_busy THEN 0
            ELSE greatest(0, least(
                p_limit,
                p_outbox - (SELECT count(*) FROM ops.doc_crawl_targets
                            WHERE status = 'done' AND published_at IS NULL),
                p_in_flight - (SELECT count(*) FROM docs WHERE requested
                               AND published_at > now() - interval '1 day')))
            END AS n
    ), pick AS MATERIALIZED (
        SELECT accession_no, targeted FROM docs
        WHERE NOT requested AND coalesce(status, 'done') NOT IN ('pending', 'claimed')
        ORDER BY filed, accession_no
        LIMIT (SELECT n FROM room)
    ), requeued AS (
        UPDATE ops.doc_crawl_targets t SET status = 'done', published_at = NULL
        FROM pick p WHERE p.targeted AND t.accession_no = p.accession_no
        RETURNING t.accession_no
    ), added AS (
        INSERT INTO ops.doc_crawl_targets AS t
            (accession_no, cik, company_id, form, filing_date, report_date, primary_document, items,
             priority, status, finished_at, published_at, last_error, files)
        SELECT d.accession_no, d.cik, k.company_id, d.form, d.filed, d.period_of_report,
               d.document_name, h.items, CASE WHEN c.tracked THEN 0 ELSE 1 END, 'done',
               d.fetched_at, NULL, 'already stored', 0
        FROM pick p
        JOIN bronze.filing_documents d ON d.accession_no = p.accession_no
        JOIN silver.company_ciks k ON k.cik = d.cik
        JOIN silver.companies c ON c.company_id = k.company_id
        LEFT JOIN bronze.submission_filings h ON h.accession_no = d.accession_no AND h.cik = d.cik
        WHERE NOT p.targeted
        ON CONFLICT (accession_no) DO NOTHING
        RETURNING t.accession_no
    ), queued AS (
        SELECT (SELECT count(*) FROM requeued) + (SELECT count(*) FROM added) AS n
    )
    SELECT count(*) FILTER (WHERE NOT d.requested AND p.accession_no IS NULL),
           count(*) FILTER (WHERE d.requested AND (d.published_at IS NULL
                                                   OR d.published_at > now() - interval '1 day'))
               + (SELECT n FROM queued),
           count(*) FILTER (WHERE d.requested AND d.published_at <= now() - interval '1 day'),
           min(d.filed) FILTER (WHERE NOT d.requested AND p.accession_no IS NULL),
           (SELECT n FROM queued)
    FROM docs d
    LEFT JOIN pick p ON p.accession_no = d.accession_no
$$;

-- The owner's OK of the measured budget comes first (header).
UPDATE ops.catchup SET paused = true WHERE kind = 'parser';
