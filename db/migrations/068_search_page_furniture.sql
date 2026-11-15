-- Search without page furniture (analyst test 4, finding 19's search half; ROADMAP follow-up "the
-- search index still holds page debris (a phrase across a page break isn't found)").
--
-- Stored section text keeps its page furniture: page numbers, "Table of Contents" back-links and
-- running headers sit between two halves of a sentence wherever the printed filing turned a page
-- ("... procure sufficient" / "29" / "Table of Contents" / "foundry capacity ..."). Stripping them
-- from the text would move every stored offset (citations, thesis pins' ?at=, the search index, the
-- diffs, peers' mentions) and need a full reparse and re-index (parser README "Page furniture":
-- measured, not done). Instead search reads the text with those lines blanked: replaced by spaces,
-- one for one, so every offset stays and the words around a page break are next to each other.
--
-- The rule, gold.page_furniture_lines (the web reader's and the pin builder's copy is
-- services/web/src/reader/furniture.ts; both are checked against the same cases,
-- services/web/src/reader/furniture.cases.json). Over the text's non-blank lines, each as written:
--   1. A contents link: "Table of Contents", "Contents", "Index", optionally after "Return to " or
--      "Back to " and "the ", optionally ending in "." (any case).
--   2. A page number: "12", "- 12 -", "Page 12", "F-12" (1-3 digits; up to two letters and a "-"
--      before them; dashes around), or i-xxxxx in lower case; and the next line is a contents link,
--      or it is the first line, or the line before it ends a sentence (. ! ? : ; then any of " ” ’ ).
--   3. A running header: any other line of 3-100 characters with an ASCII letter whose form with
--      every run of digits as "#" occurs at least 3 times in the text, at least twice and at least
--      half the time within two lines of a line of kinds 1-2 ("NVIDIA Corporation | 2026 Form 10-K
--      | 29", "Notes to Consolidated Financial Statements (Continued)", a company name at the top of
--      every page). Measured on a 0.2% sample of hermes' sections (6,406, 2026-11-15): kinds 1-2 are
--      in 56% of 10-K and 53% of 10-Q sections (0.4% of their characters); kind 3 in 6.5% of all
--      sections, and a repeat rule without the page-break condition would take table labels
--      ("Total", "Three Months Ended", "December 31,") 6 times as often.
--
-- gold.section_search gains `furniture`: the blanked lines of each passage as [start, end) pairs
-- relative to the passage (flat int[], '{}' for none; NULL for a passage indexed before this
-- migration). gold.refresh_section_search (021's, redefined) computes it per section, so a running
-- header counts across the whole section, and builds the tsvector from the blanked passage. The
-- snippet (query_api Store.search_sections) and 065's exact-phrase check read the passage blanked the
-- same way, through gold.blank; a passage indexed before this migration is blanked by the rule run on
-- the passage alone. ops.section_index.passages_rule says which filings' passages were built with
-- the rule (parser-index sets it; `parser-index --passages` rebuilds the others' passages only,
-- without touching their diffs: parser README "Search without page furniture").
--
-- Locks on hermes: two ADD COLUMNs without a default (catalog only, no rewrite) take ACCESS
-- EXCLUSIVE for an instant on gold.section_search and ops.section_index, which the hourly
-- section-index job holds while it runs: pause it first or deploy outside :40-:59 (the runbook), and
-- a busy moment fails the hook fast here instead of queueing every search behind it. The rest is
-- CREATE (OR REPLACE) FUNCTION and GRANT/REVOKE, catalog only. Nothing stored changes.

SET LOCAL lock_timeout = '3s';

ALTER TABLE gold.section_search ADD COLUMN furniture int[];
ALTER TABLE ops.section_index ADD COLUMN passages_rule text;

-- Rules 1-3: the 1-based numbers of the text's furniture lines.
CREATE FUNCTION gold.page_furniture_lines(p_text text)
RETURNS SETOF int
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    WITH lines AS (
        SELECT n::int AS n, line,
               line ~* '^(return to |back to )?(the )?(table of contents|contents|index)\.?$'
                   AS contents,
               (line ~* '^(page\s+)?([-–—]\s*)?([a-z]{1,2}-)?[0-9]{1,3}(\s*[-–—])?$'
                OR line ~ '^[ivx]{1,5}$') AS page_like
        FROM string_to_table(p_text, E'\n') WITH ORDINALITY l(line, n)
        WHERE line !~ '^\s*$'
    ),
    kinds AS (
        SELECT n, line,
               contents
               OR (page_like AND (coalesce(lead(contents) OVER w, false)
                                  OR lag(line) OVER w IS NULL
                                  OR lag(line) OVER w ~ '[.!?:;]["”’)]*$')) AS furniture
        FROM lines WINDOW w AS (ORDER BY n)
    ),
    near AS (
        SELECT n, line, furniture, regexp_replace(line, '[0-9]+', '#', 'g') AS masked,
               bool_or(furniture) OVER (ORDER BY n ROWS BETWEEN 2 PRECEDING AND 2 FOLLOWING)
                   AS by_furniture
        FROM kinds
    ),
    counted AS (
        SELECT n, line, furniture,
               count(*) OVER m AS seen,
               count(*) FILTER (WHERE by_furniture) OVER m AS by_pages
        FROM near WINDOW m AS (PARTITION BY masked)
    )
    SELECT n FROM counted
    WHERE furniture
       OR (length(line) BETWEEN 3 AND 100 AND line ~ '[A-Za-z]' AND seen >= 3 AND by_pages >= 2
           AND 2 * by_pages >= seen)
    ORDER BY n
$$;

-- The furniture lines as [start, end) pairs of character offsets into the text, flat; '{}' for none.
CREATE FUNCTION gold.page_furniture(p_text text)
RETURNS int[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    WITH lines AS (
        SELECT n::int AS n, line,
               (sum(length(line) + 1) OVER (ORDER BY n))::int - length(line) - 1 AS start_char
        FROM string_to_table(p_text, E'\n') WITH ORDINALITY l(line, n)
    )
    SELECT coalesce(array_agg(v.x ORDER BY l.n, v.k), '{}')
    FROM lines l
    JOIN gold.page_furniture_lines(p_text) f(n) ON f.n = l.n
    CROSS JOIN LATERAL (VALUES (1, l.start_char), (2, l.start_char + length(l.line))) v(k, x)
$$;

-- The text with these [start, end) ranges replaced by spaces, one for one: offsets stay.
CREATE FUNCTION gold.blank(p_text text, p_spans int[])
RETURNS text
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE
    out text := p_text;
    i int;
BEGIN
    IF p_text IS NULL OR p_spans IS NULL THEN
        RETURN p_text;
    END IF;
    FOR i IN 1 .. cardinality(p_spans) / 2 LOOP
        out := overlay(out PLACING repeat(' ', p_spans[2 * i] - p_spans[2 * i - 1])
                       FROM p_spans[2 * i - 1] + 1 FOR p_spans[2 * i] - p_spans[2 * i - 1]);
    END LOOP;
    RETURN out;
END
$$;

-- 021's, with each passage's furniture (rules over the whole section) blanked before to_tsvector.
CREATE OR REPLACE FUNCTION gold.refresh_section_search(p_accessions text[])
RETURNS int
LANGUAGE plpgsql AS $$
DECLARE
    written int;
BEGIN
    DELETE FROM gold.section_search WHERE accession_no = ANY(p_accessions);
    WITH served AS MATERIALIZED (
        SELECT company_id, accession_no, section_key, form, filed, parser, text
        FROM gold.filing_sections
        WHERE accession_no = ANY(p_accessions)
    ),
    furniture AS (
        SELECT s.accession_no, s.section_key, f.n
        FROM served s, gold.page_furniture_lines(s.text) f(n)
    ),
    lines AS (
        SELECT s.accession_no, s.section_key, l.n::int AS n, l.line,
               sum(length(l.line) + 1) OVER (PARTITION BY s.accession_no, s.section_key
                                             ORDER BY l.n)::int - length(l.line) - 1 AS start_char,
               length(l.line) AS len
        FROM served s, string_to_table(s.text, E'\n') WITH ORDINALITY l(line, n)
    ),
    marked AS (
        SELECT l.*, l.start_char / 2000 AS bucket, f.n IS NOT NULL AS blank,
               min(l.start_char) OVER (PARTITION BY l.accession_no, l.section_key,
                                       l.start_char / 2000) AS first_char
        FROM lines l
        LEFT JOIN furniture f USING (accession_no, section_key, n)
    ),
    passages AS (
        SELECT accession_no, section_key, bucket,
               min(start_char) AS start_char, max(start_char + len) AS end_char,
               string_agg(CASE WHEN blank THEN repeat(' ', len) ELSE line END, E'\n'
                          ORDER BY n) AS blanked,
               coalesce(('{' || string_agg(CASE WHEN blank THEN (start_char - first_char) || ','
                                                 || (start_char + len - first_char) END, ','
                                           ORDER BY n) || '}')::int[], '{}') AS furniture
        FROM marked
        GROUP BY 1, 2, 3
    )
    INSERT INTO gold.section_search (company_id, accession_no, section_key, passage, form, filed,
                                     parser, start_char, end_char, tsv, furniture)
    SELECT s.company_id, p.accession_no, p.section_key,
           (row_number() OVER (PARTITION BY p.accession_no, p.section_key
                               ORDER BY p.bucket) - 1)::int,
           s.form, s.filed, s.parser, p.start_char, p.end_char,
           to_tsvector('english', p.blanked), p.furniture
    FROM passages p
    JOIN served s USING (accession_no, section_key);
    GET DIAGNOSTICS written = ROW_COUNT;
    RETURN written;
END
$$;

-- 065's, the exact phrase checked on the passage with its furniture blanked (else a phrase across
-- a page break matched the stemmed tsvector and failed here). Same signature: an older pod's call
-- gets the new body. 052's nine-argument function stays as it is.
CREATE OR REPLACE FUNCTION gold.search_page(p_q tsquery, p_companies text[], p_form text,
                                            p_section text, p_from date, p_to date, p_sort text,
                                            p_limit int, p_offset int, p_exact tsquery)
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
                               CROSS JOIN LATERAL (SELECT substr(s.text, p.start_char + 1,
                                                   p.end_char - p.start_char) AS t) x
                               WHERE s.accession_no = p.accession_no
                                 AND s.section_key = p.section_key AND s.parser = p.parser
                                 AND to_tsvector(''simple'', gold.blank(x.t, coalesce(
                                         p.furniture, gold.page_furniture(x.t)))) @@ $9)' END,
        CASE p_sort WHEN 'date' THEN 'filed DESC, rank DESC, accession_no, section_key'
                    ELSE 'rank DESC, filed DESC, accession_no, section_key' END)
    USING p_q, readable, p_form, p_section, p_from, p_to, p_limit, p_offset, p_exact;
END
$fn$;
REVOKE EXECUTE ON FUNCTION gold.search_page(tsquery, text[], text, text, date, date, text, int, int,
                                            tsquery) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.search_page(tsquery, text[], text, text, date, date, text, int, int,
                                           tsquery) TO query_api;
GRANT EXECUTE ON FUNCTION gold.blank(text, int[]), gold.page_furniture(text),
                          gold.page_furniture_lines(text) TO query_api;
