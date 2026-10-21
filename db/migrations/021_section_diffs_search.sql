-- Section diffs ("what changed") and full-text search over parsed sections.
--
-- Both are built by the parser image's `parser-index` job (filing_parser.index) from the sections
-- gold.filing_sections serves, incrementally: ops.section_index is the checkpoint, one row per
-- indexed filing. A filing is (re)indexed when its sections are new or re-parsed; its diffs are
-- (re)computed when the prior filing they compare against changes (a prior arrives later, as the
-- document crawl goes newest first, or is re-parsed) or the diff algorithm version changes.
--
-- Search: gold.section_search holds one row per passage (consecutive lines of a section, cut at
-- line boundaries every ~2,000 characters) with its tsvector, but no text: snippets are cut from
-- silver.filing_sections at query time, so the 10-15 GB of section text isn't stored twice. A
-- separate table instead of a generated column on silver.filing_sections because: adding a
-- stored column rewrites that table; silver holds a row per parser (Python and Rust); whole
-- sections are poor ranking/snippet units (ts_headline re-parses the whole document, MD&A runs
-- to 200k characters) and a tsvector caps at 1 MB.
--
-- Diffs: paragraph-level comparison in Python (difflib alignment and word diffs don't fit SQL;
-- algorithm in filing_parser/diff.py). gold.section_diffs has one row per compared section with
-- counts, gold.section_changes one row per new / removed / rewritten paragraph with offsets into
-- both sections' text (and word-diff opcodes), again no text.

-- Search passages.
CREATE TABLE gold.section_search (
    company_id   text     NOT NULL,
    accession_no text     NOT NULL,
    section_key  text     NOT NULL,
    passage      int      NOT NULL,  -- 0-based, in section order
    form         text     NOT NULL,
    filed        date     NOT NULL,
    parser       text     NOT NULL,  -- the silver.filing_sections row the offsets refer to
    start_char   int      NOT NULL,  -- [start_char, end_char) in that section's text
    end_char     int      NOT NULL,
    tsv          tsvector NOT NULL,
    PRIMARY KEY (accession_no, section_key, passage)
);
CREATE INDEX section_search_tsv ON gold.section_search USING gin (tsv);
CREATE INDEX section_search_company ON gold.section_search (company_id, filed DESC);

-- Rebuild the passages of these filings from the sections gold.filing_sections serves. Lines are
-- grouped by start offset / 2000, so a passage is whole lines; a line longer than that is one
-- passage on its own. Returns the number of passages written.
CREATE FUNCTION gold.refresh_section_search(p_accessions text[])
RETURNS int
LANGUAGE plpgsql AS $$
DECLARE
    n int;
BEGIN
    DELETE FROM gold.section_search WHERE accession_no = ANY(p_accessions);
    WITH served AS MATERIALIZED (
        SELECT company_id, accession_no, section_key, form, filed, parser, text
        FROM gold.filing_sections
        WHERE accession_no = ANY(p_accessions)
    ),
    lines AS (
        SELECT s.accession_no, s.section_key, l.n,
               sum(length(l.line) + 1) OVER (PARTITION BY s.accession_no, s.section_key
                                             ORDER BY l.n)::int - length(l.line) - 1 AS start_char,
               length(l.line) AS len
        FROM served s, string_to_table(s.text, E'\n') WITH ORDINALITY l(line, n)
    ),
    passages AS (
        SELECT accession_no, section_key, start_char / 2000 AS bucket,
               min(start_char) AS start_char, max(start_char + len) AS end_char
        FROM lines
        GROUP BY 1, 2, 3
    )
    INSERT INTO gold.section_search (company_id, accession_no, section_key, passage, form, filed,
                                     parser, start_char, end_char, tsv)
    SELECT s.company_id, p.accession_no, p.section_key,
           (row_number() OVER (PARTITION BY p.accession_no, p.section_key
                               ORDER BY p.bucket) - 1)::int,
           s.form, s.filed, s.parser, p.start_char, p.end_char,
           to_tsvector('english', substr(s.text, p.start_char + 1, p.end_char - p.start_char))
    FROM passages p
    JOIN served s USING (accession_no, section_key);
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END
$$;

-- One row per compared section: this filing's section vs the prior comparable filing's.
--   basis 'prior':    10-K vs the previous 10-K; 10-Q vs the previous 10-Q
--   basis 'year_ago': 10-Q vs the 10-Q for the same quarter a year earlier
-- parser/parsed_at (both sides) identify the section texts the offsets refer to; the Query API
-- treats a diff whose texts have since been re-parsed as stale until the job recomputes it.
CREATE TABLE gold.section_diffs (
    company_id         text        NOT NULL,
    accession_no       text        NOT NULL,
    section_key        text        NOT NULL,
    basis              text        NOT NULL CHECK (basis IN ('prior', 'year_ago')),
    form               text        NOT NULL,
    filed              date        NOT NULL,
    parser             text        NOT NULL,
    parsed_at          timestamptz NOT NULL,
    prior_accession_no text        NOT NULL,
    prior_form         text        NOT NULL,
    prior_filed        date        NOT NULL,
    prior_parser       text        NOT NULL,
    prior_parsed_at    timestamptz NOT NULL,
    paragraphs         int         NOT NULL,
    prior_paragraphs   int         NOT NULL,
    unchanged          int         NOT NULL,  -- includes minor edits
    minor_edits        int         NOT NULL,  -- aligned, similarity >= the rewrite threshold
    numbers_only       int         NOT NULL,  -- unchanged once numbers are masked
    rewritten          int         NOT NULL,
    new                int         NOT NULL,
    removed            int         NOT NULL,
    algorithm          text        NOT NULL,  -- filing_parser.diff.ALGORITHM
    computed_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (accession_no, section_key, basis)
);
CREATE INDEX section_diffs_company ON gold.section_diffs (company_id, filed DESC);

-- One row per new / removed / rewritten paragraph. Offsets are [start, end) pairs, flattened,
-- one pair per line of the paragraph (a paragraph split by a page break spans several lines),
-- into the section text of this filing (new_spans) and of the prior filing (prior_spans); the
-- paragraph text is those slices joined by a space. words: word-diff opcodes over whitespace
-- tokens of the two paragraph texts, [tag, i1, i2, j1, j2] with tag e/r/d/i (rewritten only).
CREATE TABLE gold.section_changes (
    company_id   text  NOT NULL,
    accession_no text  NOT NULL,
    section_key  text  NOT NULL,
    basis        text  NOT NULL,
    ordinal      int   NOT NULL,  -- reading order within the section
    kind         text  NOT NULL CHECK (kind IN ('new', 'removed', 'rewritten')),
    similarity   real,            -- rewritten only
    new_spans    int[],           -- NULL for removed
    prior_spans  int[],           -- NULL for new
    words        jsonb,
    PRIMARY KEY (accession_no, section_key, basis, ordinal),
    FOREIGN KEY (accession_no, section_key, basis)
        REFERENCES gold.section_diffs ON DELETE CASCADE
);

-- Finds re-parsed filings with an index-only scan instead of reading every section row.
CREATE INDEX filing_sections_parsed ON silver.filing_sections (accession_no, parsed_at);

-- Checkpoint: one row per filing whose sections are indexed.
CREATE TABLE ops.section_index (
    accession_no          text        PRIMARY KEY,
    company_id            text        NOT NULL,
    form                  text        NOT NULL,
    filed                 date        NOT NULL,
    period                date        NOT NULL,  -- period of report, else filing date
    sections_parsed_at    timestamptz NOT NULL,  -- latest parse of its sections when indexed
    sections              int         NOT NULL,
    passages              int         NOT NULL,
    indexed_at            timestamptz NOT NULL DEFAULT now(),
    -- diffs (10-K and 10-Q only): what they were computed against; NULL prior = none existed
    algorithm             text,
    prior_accession_no    text,
    year_ago_accession_no text,
    diffed_at             timestamptz,
    diff_error            text
);
CREATE INDEX section_index_priors ON ops.section_index (company_id, form, period);

-- Comparable filings of these filings, from what is indexed: the previous filing of the same
-- form by period (then filing date, accession) and, for a 10-Q, the 10-Q whose period ends
-- 330-400 days earlier (closest to 364). Only original 10-K and 10-Q filings are compared.
CREATE FUNCTION ops.section_index_priors(p_accessions text[])
RETURNS TABLE (accession_no text, prior_accession_no text, year_ago_accession_no text)
LANGUAGE sql STABLE AS $$
    SELECT f.accession_no,
           (SELECT p.accession_no FROM ops.section_index p
            WHERE p.company_id = f.company_id AND p.form = f.form
              AND (p.period, p.filed, p.accession_no) < (f.period, f.filed, f.accession_no)
            ORDER BY p.period DESC, p.filed DESC, p.accession_no DESC
            LIMIT 1),
           CASE WHEN f.form = '10-Q' THEN
               (SELECT y.accession_no FROM ops.section_index y
                WHERE y.company_id = f.company_id AND y.form = '10-Q'
                  AND y.period BETWEEN f.period - 400 AND f.period - 330
                ORDER BY abs(f.period - y.period - 364), y.filed DESC, y.accession_no DESC
                LIMIT 1)
           END
    FROM ops.section_index f
    WHERE f.accession_no = ANY(p_accessions) AND f.form IN ('10-K', '10-Q')
$$;

-- Work for the next batch, oldest filing first:
--   'sections': sections not indexed yet, or re-parsed since
--   'diffs':    indexed 10-K/10-Q whose diffs are missing, from another algorithm version, or
--               compared against a filing that is no longer the comparable one or was
--               re-indexed after the comparison
CREATE FUNCTION ops.section_index_pending(p_algorithm text, p_limit int)
RETURNS TABLE (accession_no text, reason text)
LANGUAGE sql STABLE AS $$
    (SELECT s.accession_no, 'sections'
     FROM (SELECT accession_no, max(parsed_at) AS parsed_at
           FROM silver.filing_sections GROUP BY accession_no) s
     JOIN bronze.filing_documents d ON d.accession_no = s.accession_no
     JOIN silver.company_ciks k ON k.cik = d.cik  -- gold serves only filings of a company
     LEFT JOIN ops.section_index i ON i.accession_no = s.accession_no
     WHERE i.accession_no IS NULL OR i.sections_parsed_at < s.parsed_at
     ORDER BY d.filed, s.accession_no
     LIMIT p_limit)
    UNION ALL
    (SELECT i.accession_no, 'diffs'
     FROM ops.section_index i
     JOIN ops.section_index_priors(ARRAY(
         SELECT accession_no FROM ops.section_index WHERE form IN ('10-K', '10-Q'))) e
       ON e.accession_no = i.accession_no
     LEFT JOIN ops.section_index p ON p.accession_no = i.prior_accession_no
     LEFT JOIN ops.section_index y ON y.accession_no = i.year_ago_accession_no
     WHERE i.diffed_at IS NULL
        OR i.algorithm IS DISTINCT FROM p_algorithm
        OR i.prior_accession_no IS DISTINCT FROM e.prior_accession_no
        OR i.year_ago_accession_no IS DISTINCT FROM e.year_ago_accession_no
        OR p.indexed_at > i.diffed_at
        OR y.indexed_at > i.diffed_at
     ORDER BY i.filed, i.accession_no
     LIMIT p_limit)
$$;

-- Query API: RLS by the filings tier, like silver.filing_sections.
GRANT SELECT ON gold.section_search, gold.section_diffs, gold.section_changes TO query_api;

ALTER TABLE gold.section_search ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON gold.section_search FOR SELECT TO query_api
    USING (EXISTS (
        SELECT 1 FROM authz.user_access a
        WHERE a.user_id = current_setting('app.user_id', true)
          AND a.company_id = section_search.company_id
          AND a.tier = 'filings'
    ));

ALTER TABLE gold.section_diffs ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON gold.section_diffs FOR SELECT TO query_api
    USING (EXISTS (
        SELECT 1 FROM authz.user_access a
        WHERE a.user_id = current_setting('app.user_id', true)
          AND a.company_id = section_diffs.company_id
          AND a.tier = 'filings'
    ));

ALTER TABLE gold.section_changes ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON gold.section_changes FOR SELECT TO query_api
    USING (EXISTS (
        SELECT 1 FROM authz.user_access a
        WHERE a.user_id = current_setting('app.user_id', true)
          AND a.company_id = section_changes.company_id
          AND a.tier = 'filings'
    ));

-- Ops: counts only, never text.
CREATE VIEW ops.section_change_summary AS
SELECT company_id, accession_no, form, filed, section_key, basis, prior_accession_no,
       prior_filed, paragraphs, prior_paragraphs, unchanged, minor_edits, numbers_only,
       rewritten, new, removed, algorithm, computed_at
FROM gold.section_diffs;

-- One row: indexing progress and health.
CREATE VIEW ops.section_index_status AS
SELECT count(*) AS filings,
       coalesce(sum(sections), 0) AS sections,
       coalesce(sum(passages), 0) AS passages,
       count(*) FILTER (WHERE form IN ('10-K', '10-Q')) AS comparable_filings,
       count(*) FILTER (WHERE diffed_at IS NOT NULL AND prior_accession_no IS NOT NULL)
           AS diffed_filings,
       count(*) FILTER (WHERE form IN ('10-K', '10-Q') AND diffed_at IS NOT NULL
                          AND prior_accession_no IS NULL) AS without_prior,
       count(*) FILTER (WHERE diff_error IS NOT NULL) AS diff_errors,
       (SELECT count(*) FROM gold.section_diffs) AS section_diffs,
       (SELECT count(*) FROM gold.section_changes) AS section_changes,
       (SELECT count(DISTINCT accession_no) FROM silver.filing_sections) AS parsed_filings,
       max(indexed_at) AS last_indexed_at,
       max(diffed_at) AS last_diffed_at
FROM ops.section_index;

GRANT SELECT ON ops.section_change_summary, ops.section_index_status TO ops_reader;
