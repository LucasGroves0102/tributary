-- Search results with a total and a choice of order (Riley #145): "No total and no sort; ranked by
-- word density". gold.search_page is gold.search_passages (032) with two changes:
--   p_sort  'relevance' (032's order: rank, then newest) or 'date' (newest filed first, then rank);
--   total   how many sections match in all (the page's rows all carry it), so the web app can say
--           "1-20 of 347". It costs nothing extra in the ranking pass: 032 already ranks every match
--           before it takes a page; the window function counts the same rows.
-- Everything else is 032's, for 032's reasons: SECURITY DEFINER because `@@` isn't leakproof, so the
-- GIN index can't run under gold.section_search's policy as query_api; the read rule is still the only
-- rule, authz.readable_companies('filings') in the caller's session (app.user_id set by Store from the
-- JWT), which p_companies can only narrow; no text and no value leave it (the caller reads the snippet
-- under RLS); plpgsql EXECUTE so it plans with real values; search_path pinned; EXECUTE only for
-- query_api. A CREATE OR REPLACE re-issues the REVOKE/GRANT.
-- gold.search_passages stays: an API pod from before this migration calls it during the deploy.
--
-- Locks on hermes: CREATE FUNCTION and GRANT/REVOKE, catalog only; no table is locked or rewritten,
-- nothing stored changes.

SET LOCAL lock_timeout = '30s';

CREATE FUNCTION gold.search_page(p_q tsquery, p_companies text[], p_form text, p_section text,
                                 p_from date, p_to date, p_sort text, p_limit int, p_offset int)
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
    -- the ORDER BY is one of two fixed strings, never the caller's text
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
        ),
        best AS (
            SELECT DISTINCT ON (accession_no, section_key) *
            FROM matches
            ORDER BY accession_no, section_key, rank DESC, passage
        )
        SELECT b.*, count(*) OVER () AS total FROM best b
        ORDER BY %s
        LIMIT $7 OFFSET $8
    $sql$, CASE p_sort WHEN 'date' THEN 'filed DESC, rank DESC, accession_no, section_key'
                       ELSE 'rank DESC, filed DESC, accession_no, section_key' END)
    USING p_q, readable, p_form, p_section, p_from, p_to, p_limit, p_offset;
END
$fn$;
REVOKE EXECUTE ON FUNCTION gold.search_page(tsquery, text[], text, text, date, date, text, int, int)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.search_page(tsquery, text[], text, text, date, date, text, int, int)
    TO query_api;
