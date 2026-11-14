-- A quoted phrase matches its exact words (analyst test 4, regression 23): "liquid cooling" in quotes
-- matched "liquid-cooled". The search's tsquery is english-stemmed (websearch_to_tsquery('english')),
-- so the phrase was 'liquid' <-> 'cool', which "liquid-cooled" (liquid, cool) satisfies too.
--
-- gold.search_page gains a tenth argument, p_exact: a 'simple'-config tsquery of the query's quoted
-- phrases (the Query API builds it from the query: store.exact_phrases). A passage matches only when
-- its own text, read with the 'simple' configuration (no stemming), also matches p_exact. NULL (no quoted phrase)
-- leaves the search exactly 052's: the clause is one of two fixed strings, never the caller's text.
-- 'simple' still splits a hyphenated word into its parts, so "liquid-cooling" matches "liquid
-- cooling" (the same words), and "Liquid Cooling" does (it lower-cases); "liquid-cooled" doesn't.
-- The check runs before DISTINCT ON and the window counts, so `passages_matched` and `total` count
-- exact matches: totals stay exact, not estimates.
--
-- Cost (hermes, 2026-11-14, as the owner): "liquid cooling" over every company: 1,313 stemmed
-- matches, 765 exact; 23 ms for the stemmed count, 651 ms with the check (102 ms of it reading the
-- sections' text, the rest to_tsvector over each passage: about 0.4 ms a passage). "supply chain" in
-- the newest 30 days: 8,152 stemmed matches, 7,486 exact; the check adds about 4.4 s to a match set
-- that already takes 45 s and so meets SEARCH_TIMEOUT (8 s, "too broad") either way. A phrase costs
-- in proportion to its stemmed matches, which the 8 s budget already bounds.
--
-- Everything else is 052's, for 052's reasons: SECURITY DEFINER because `@@` isn't leakproof (the
-- GIN index can't run under gold.section_search's policy as query_api); the read rule is still the only
-- rule, authz.readable_companies('filings') in the caller's session, which p_companies can only narrow;
-- the section text is read inside the function only to test it, and no text or value leaves it (the
-- caller reads the snippet under RLS); plpgsql EXECUTE so it plans with real values; search_path
-- pinned; EXECUTE only for query_api. A CREATE OR REPLACE re-issues the REVOKE/GRANT.
-- The nine-argument gold.search_page stays: an API pod from before this migration calls it during the
-- deploy (like 052 kept 032's search_passages).
--
-- Locks on hermes: CREATE FUNCTION and GRANT/REVOKE, catalog only; no table is locked or rewritten,
-- nothing stored changes, no rebuild.

SET LOCAL lock_timeout = '30s';

CREATE FUNCTION gold.search_page(p_q tsquery, p_companies text[], p_form text, p_section text,
                                 p_from date, p_to date, p_sort text, p_limit int, p_offset int,
                                 p_exact tsquery)
RETURNS TABLE (company_id text, accession_no text, section_key text, passage int, form text,
               filed date, parser text, start_char int, end_char int, rank real,
               passages_matched bigint, total bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
DECLARE
    readable text[];
BEGIN
    IF p_sort IS DISTINCT FROM 'relevance' AND p_sort IS DISTINCT FROM 'date' THEN
        RAISE EXCEPTION 'unknown search order %', p_sort;
    END IF;
    readable := ARRAY(SELECT r FROM authz.readable_companies('filings') r
                      WHERE p_companies IS NULL OR r = ANY(p_companies));
    IF cardinality(readable) = 0 THEN
        RETURN;
    END IF;
    -- the ORDER BY and the exact clause are fixed strings, never the caller's text
    RETURN QUERY EXECUTE format($sql$
        WITH matches AS (
            SELECT p.company_id, p.accession_no, p.section_key, p.passage, p.form, p.filed,
                   p.parser, p.start_char, p.end_char, ts_rank_cd(p.tsv, $1) AS rank,
                   count(*) OVER (PARTITION BY p.accession_no, p.section_key) AS passages_matched
            FROM gold.section_search p
            WHERE p.tsv @@ $1 AND p.company_id = ANY($2)
              AND ($3::text IS NULL OR p.form = $3)
              AND ($4::text IS NULL OR p.section_key = $4)
              AND ($5::date IS NULL OR p.filed >= $5)
              AND ($6::date IS NULL OR p.filed <= $6)
              %s
        ),
        best AS (
            SELECT DISTINCT ON (accession_no, section_key) *
            FROM matches
            ORDER BY accession_no, section_key, rank DESC, passage
        )
        SELECT b.*, count(*) OVER () AS total FROM best b
        ORDER BY %s
        LIMIT $7 OFFSET $8
    $sql$,
        CASE WHEN p_exact IS NULL THEN ''
             ELSE 'AND EXISTS (SELECT 1 FROM silver.filing_sections s
                               WHERE s.accession_no = p.accession_no
                                 AND s.section_key = p.section_key AND s.parser = p.parser
                                 AND to_tsvector(''simple'', substr(s.text, p.start_char + 1,
                                                 p.end_char - p.start_char)) @@ $9)' END,
        CASE p_sort WHEN 'date' THEN 'filed DESC, rank DESC, accession_no, section_key'
                    ELSE 'rank DESC, filed DESC, accession_no, section_key' END)
    USING p_q, readable, p_form, p_section, p_from, p_to, p_limit, p_offset, p_exact;
END
$fn$;
REVOKE EXECUTE ON FUNCTION gold.search_page(tsquery, text[], text, text, date, date, text, int, int,
                                            tsquery) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.search_page(tsquery, text[], text, text, date, date, text, int, int,
                                           tsquery) TO query_api;
