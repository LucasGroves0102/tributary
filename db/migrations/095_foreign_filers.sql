-- Foreign filers (roadmap f): their annual reports and results releases stored and split into
-- sections like a 10-K's and an 8-K's (db/DESIGN.md "095"). Measured on hermes 2026-11-17,
-- read-only.
--
-- 1. ops.results_release(period, filed, fiscal year end, document, description): whether a 6-K is
--    a quarterly, half-year or annual results release, from the filing index alone (no SEC
--    request): the doc crawl's plan reads it from bronze.submission_filings, the live doc-fetcher
--    from the filing's index page (Period of Report, the primary document's name and
--    description). A 6-K says nothing structured about what it is (no items; two thirds are
--    described "6-K" or "Form 6-K"), so two signals:
--      A. its period of report is a fiscal quarter's end (within 7 days before the end of a month
--         a whole number of quarters from the fiscal year end: 52-53 week quarters too; December
--         without one) and it was filed 8-120 days after it. A results release or the interim
--         statements behind it reports the quarter it covers; most other 6-Ks give their filing
--         month or day.
--      B. its document name or description says results (results, earnings, quarter, Q1-Q4,
--         interim, half-year, financial statements or report, annual report, trading update...)
--         and none of what a notice says (date, notice, voting, AGM, meeting, dividend, block
--         listing, conference call, trial, publication, 20-F...): the UK and Irish filers
--         (HSBC, Barclays, GSK, Diageo) give a results release its filing day as the period.
--      C: never one whose name or description says CVM 358 (Brazilian filers' monthly insider
--         report, at a quarter end too), notification or bought deal.
--    Of 211,135 6-Ks filed since 2015-11-17 by the 1,326 operating companies that file them, A
--    takes 13,241, B alone 3,590; less C (119), 16,712 at about 958 companies (2,388 of 25,720 in
--    2025, at 685 of 1,199). Per company and quarter A finds one 6-K 7,304 times, two 1,529 (a
--    release and its interim statements: TSMC's October 15 release and November 13 statements both
--    report September 30), three or more 702. A random 100 of A or B (before C): 71 a results
--    release, interim report or statements by name, description or XBRL; 23 opaque names whose
--    timing is a results filing's (an interim filed 25-90 days after the quarter); 6 not: two CVM
--    358 reports, a bought-deal notice, a results-date notification (all four now C), a
--    "seq1" file name, and Ryanair's notice that its annual report is published. Missed: a release
--    dated its filing day under a generic name (its interim statements are usually taken).
--
-- 2. ops.doc_crawl_plan (020's body, edits marked 095) also plans 20-F, 20-F/A, 40-F, 40-F/A and
--    the 6-Ks ops.results_release takes, of the same operating companies (gold financials) since
--    the crawl's window (CRAWL_YEARS 11 on hermes: November 2015). doc-fetcher stores a 40-F's
--    and a 6-K's EX-99.x exhibits (a 40-F's annual information form, MD&A and statements; a
--    6-K's press release) and the live path the same forms, 6-Ks by this rule. Since 2015-11-17,
--    operating companies: 20-F 7,296 (1,171 companies), 20-F/A 956, 40-F 1,184 (187), 40-F/A 132,
--    and the 16,712 6-Ks: 26,280 filings, about 64,000 SEC requests (a 20-F one; a 40-F or 6-K its
--    index, document and exhibits) at the crawl's bulk class. Size from their submissions (245 GB
--    for the annual reports, 90 GB for the 6-Ks) at the 10-K's and 8-K's stored share (1.3%, 0.8%):
--    about 4 GB stored, about 18 GB with section text and search passages (the 10-K's 3.7x and the
--    8-K's 2.4x of their stored size, doc-fetcher README "Documents before 2021"). hermes had 130 GB
--    free on 2026-11-17.
--
-- 3. Parser output checks (072's twins of services/parser invariants.py, PARSER_VERSION 8): a
--    20-F's keys are its items 1-19 with 4A, 16A-16K and 3.D (risk factors, parser rule 12), or
--    20-F alone (a cross-reference layout, rule 12c); a 40-F's and a 6-K's primary document is
--    one section keyed 40-F / 6-K (rule 13), its exhibits EX-99.x. An original 20-F has 3.D, 4, 5
--    and 18 unless laid out by a cross-reference index. ops.check_sections_version() 95: the
--    hourly catch-up re-checks every stored document once (the corpus took 24 s as one query).
--
-- Locks on hermes (lock_timeout 3 s): CREATE OR REPLACE of four functions and one new function
-- (catalog only); nothing is rewritten. The crawl (doc-crawler) calls ops.doc_crawl_plan every
-- CRAWL_PLAN_HOURS. Undo: 020's ops.doc_crawl_plan, 072's section functions, DROP FUNCTION
-- ops.results_release; the planned foreign targets stay (UPDATE ops.doc_crawl_targets SET status =
-- 'skipped' WHERE status = 'pending' AND form IN (...) to drop them).

SET LOCAL lock_timeout = '3s';

-- 1. A 6-K that is a results release (header, 1) --------------------------------------------------

CREATE FUNCTION ops.results_release(p_period date, p_filed date, p_fiscal_year_end text,
                                    p_document text, p_description text)
RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT coalesce(lower(concat_ws(' ', p_document, p_description))
                        !~ '(cvm.?358|notification|bought.?deal)', true)  -- C: never these
       AND (coalesce(
               -- A: a fiscal quarter's end (8 days later is the next month), filed 8-120 days on
               extract(month FROM p_period + 8) <> extract(month FROM p_period)
               AND (extract(month FROM p_period)::int + 12
                    - CASE WHEN p_fiscal_year_end ~ '^(0[1-9]|1[0-2])[0-3][0-9]$'
                           THEN left(p_fiscal_year_end, 2)::int ELSE 12 END) % 3 = 0
               AND p_filed - p_period BETWEEN 8 AND 120, false)
        OR coalesce(
               -- B: the name or description says results and nothing a notice says
               lower(concat_ws(' ', p_document, p_description))
                   ~ '(result|earning|quarter|q[1-4]|[1-4]q|interim|half.?year|semi.?annual|financial.?(statement|report|info)|fininfo|annual.?report|trading.?(update|statement)|quarterly|preliminary)'
               AND lower(concat_ws(' ', p_document, p_description))
                   !~ '(notice|(^|[^p])dates?|voting|vote|agm|egm|meeting|stress|block.?listing|dividend|scrip|conference.?call|webcast|trial|study|publication|exercise|tender|redemption|buy.?back|repurchase|own.?shares|director|pdmr|holding\(s\)|holdings? in |rights|prospectus|declar|framework|20-f)',
               false))
$$;

-- 2. The crawl's plan (header, 2) ----------------------------------------------------------------

-- 020's body: 10-K, 10-K/A, 10-Q, 10-Q/A and 8-K filed on or after p_since by companies with gold
-- financials (operating companies), 8-Ks only when one of their items is in p_8k_items (NULL: every
-- 8-K; unknown items: kept); 095: their 20-F, 20-F/A, 40-F, 40-F/A, and 6-Ks that are results
-- releases. A filing with several filers is queued once, under a tracked company's CIK when there
-- is one. Returns the number of filings added.
CREATE OR REPLACE FUNCTION ops.doc_crawl_plan(p_since date, p_8k_items text[] DEFAULT NULL)
RETURNS int
LANGUAGE plpgsql AS $$
DECLARE
    added int;
BEGIN
    INSERT INTO ops.doc_crawl_targets
        (accession_no, cik, company_id, form, filing_date, report_date, primary_document, items,
         priority)
    SELECT DISTINCT ON (f.accession_no)
           f.accession_no, f.cik, f.company_id, f.form, f.filing_date, f.report_date,
           f.primary_document, h.items, CASE WHEN c.tracked THEN 0 ELSE 1 END
    FROM silver.filings f
    JOIN silver.companies c ON c.company_id = f.company_id
    LEFT JOIN bronze.submission_filings h
           ON h.accession_no = f.accession_no AND h.cik = f.cik
    WHERE f.form IN ('10-K', '10-K/A', '10-Q', '10-Q/A', '8-K',
                     '20-F', '20-F/A', '40-F', '40-F/A', '6-K')  -- 095
      AND f.filing_date >= p_since
      AND EXISTS (SELECT 1 FROM gold.quarterly_financials q WHERE q.company_id = f.company_id)
      AND (f.form <> '8-K' OR p_8k_items IS NULL OR h.items IS NULL
           OR regexp_split_to_array(btrim(h.items), '\s*,\s*') && p_8k_items)
      -- 095: a 6-K only when the filing index says it is a results release; one the nightly
      -- submissions load hasn't brought yet waits for it
      AND (f.form <> '6-K'
           OR ops.results_release(h.report_date, h.filing_date, c.fiscal_year_end,
                                  h.primary_document, h.primary_doc_description))
      AND NOT EXISTS (SELECT 1 FROM bronze.filing_documents d
                      WHERE d.accession_no = f.accession_no)
    ORDER BY f.accession_no, c.tracked DESC, f.primary_document IS NULL, f.cik
    ON CONFLICT (accession_no) DO NOTHING;
    GET DIAGNOSTICS added = ROW_COUNT;
    UPDATE ops.doc_crawl_control SET planned_at = now();
    RETURN added;
END
$$;

-- 3. Parser output checks (header, 3) ------------------------------------------------------------

CREATE OR REPLACE FUNCTION ops.check_sections_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 95';

-- Whether a section key is one the form has. Its twin is services/parser filing_parser/invariants.py
-- (KEY_PATTERNS), the parser tests' invariant: test_invariants.py checks the patterns are equal.
CREATE OR REPLACE FUNCTION ops.section_key_valid(p_form text, p_key text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN p_key ~ '^EX-' THEN p_key ~ '^EX-[0-9]{1,3}(\.[0-9]{1,3})?[A-Z]?$'
        WHEN p_form ~ '^8-K' THEN p_key ~ '^(1\.0[1-5]|2\.0[1-6]|3\.0[1-3]|4\.0[12]|5\.0[1-8]|6\.(0[1-9]|10)|7\.01|8\.01|9\.01)$'
        WHEN p_form ~ '^10-K' THEN p_key ~ '^(1|1A|1B|1C|2|3|4|4A|5|6|7|7A|8|9|9A|9B|9C|10|11|12|13|14|15|16)$'
        WHEN p_form ~ '^10-Q' THEN p_key ~ '^(I-[1-4]|II-(1|1A|2|3|4|5|6))$'
        WHEN p_form ~ '^20-F' THEN p_key ~ '^(1|2|3|3\.D|4|4A|5|6|7|8|9|10|11|12|13|14|15|16|16[A-K]|17|18|19|20-F)$'
        WHEN p_form ~ '^40-F' THEN p_key ~ '^40-F$'
        WHEN p_form ~ '^6-K' THEN p_key ~ '^6-K$'
        ELSE true END
$$;

-- The items an original 10-K, 10-Q or 20-F always has (an amendment can carry only what it amends).
CREATE OR REPLACE FUNCTION ops.core_section_keys(p_form text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE p_form WHEN '10-K' THEN ARRAY['1', '1A', '7', '8']
                       WHEN '10-Q' THEN ARRAY['I-1', 'I-2']
                       WHEN '20-F' THEN ARRAY['3.D', '4', '5', '18'] END
$$;

-- 072's body; the cross-reference exemption also for 20-F (marked 095)
CREATE OR REPLACE FUNCTION ops.check_sections(p_accessions text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
    v integer := ops.check_sections_version();
BEGIN
    DELETE FROM ops.section_findings WHERE accession_no = ANY(p_accessions);

    WITH docs AS MATERIALIZED (
        SELECT d.accession_no, d.form
        FROM bronze.filing_documents d
        WHERE d.accession_no = ANY(p_accessions)
    ),
    s AS MATERIALIZED (
        SELECT DISTINCT ON (x.accession_no, x.section_key)
               x.accession_no, d.form, x.section_key, x.parser, octet_length(x.text) AS bytes,
               CASE WHEN octet_length(x.text) <= 80 THEN x.text END AS short_text
        FROM silver.filing_sections x
        JOIN docs d ON d.accession_no = x.accession_no
        ORDER BY x.accession_no, x.section_key, x.parser = 'rust' DESC, x.parsed_at DESC
    ),
    keys AS MATERIALIZED (  -- set-based per document: no lookup into s per document and key
        SELECT accession_no, array_agg(section_key) AS keys FROM s GROUP BY accession_no
    ),
    findings AS (
        SELECT accession_no, 'invalid_key' AS check_name, 'error' AS severity,
               jsonb_build_object('keys', (array_agg(section_key ORDER BY section_key))[1:20],
                                  'count', count(*)) AS detail
        FROM s
        WHERE NOT ops.section_key_valid(form, section_key)
        GROUP BY accession_no
        UNION ALL
        SELECT d.accession_no, 'missing_core_item', 'warning',
               jsonb_build_object('missing', m.missing)
        FROM docs d
        JOIN keys k ON k.accession_no = d.accession_no  -- none at all: no_sections says it
        CROSS JOIN LATERAL (
            SELECT array_agg(c.k ORDER BY c.o) AS missing
            FROM unnest(ops.core_section_keys(d.form)) WITH ORDINALITY c(k, o)
            WHERE c.k <> ALL(k.keys)
        ) m
        WHERE m.missing IS NOT NULL
          -- a 10-K or 20-F laid out by a cross-reference index (GE, Intel) numbers no items (095:
          -- 20-F too), and a 20-F with no Item heading is one section keyed 20-F (parser rule 12c)
          AND NOT (d.form IN ('10-K', '20-F') AND EXISTS (
                       SELECT 1 FROM silver.filing_sections x
                       WHERE x.accession_no = d.accession_no
                         AND x.text ~* 'cross[- ]?reference (index|table)'))
          AND NOT (d.form = '20-F' AND '20-F' = ANY(k.keys))
        UNION ALL
        SELECT accession_no, 'empty_section', 'info',
               jsonb_build_object('keys', (array_agg(section_key ORDER BY section_key))[1:20],
                                  'count', count(*))
        FROM s
        WHERE (bytes = 0
               OR short_text ~* '^(item|part)\s+[0-9ivx]{1,4}[a-z]?(\.\s?[0-9]{1,2})?[[:space:].:\-–—]*$')
          AND NOT (form ~ '^10-K' AND section_key = '6')  -- removed in 2021: "Item 6. [Reserved]"
        GROUP BY accession_no
        UNION ALL
        SELECT accession_no, 'duplicate_section', 'warning',
               jsonb_build_object('keys', (array_agg(k ORDER BY k))[1:20])
        FROM (  -- the same text under two keys: hashed only where two sections' sizes are equal
            SELECT DISTINCT x.accession_no, unnest(array_agg(x.section_key)) AS k
            FROM (SELECT accession_no, bytes FROM s WHERE bytes > 200
                  GROUP BY accession_no, bytes HAVING count(*) > 1) g
            JOIN s a ON a.accession_no = g.accession_no AND a.bytes = g.bytes
            JOIN silver.filing_sections x ON x.accession_no = a.accession_no
                                         AND x.section_key = a.section_key AND x.parser = a.parser
            GROUP BY x.accession_no, md5(x.text)
            HAVING count(*) > 1
        ) d
        GROUP BY accession_no
        UNION ALL
        SELECT d.accession_no, 'no_sections', 'error', '{}'::jsonb
        FROM docs d
        LEFT JOIN keys k ON k.accession_no = d.accession_no
        WHERE k.accession_no IS NULL
    )
    INSERT INTO ops.section_findings (accession_no, check_name, severity, detail)
    SELECT accession_no, check_name, severity, detail FROM findings
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;

    INSERT INTO ops.section_checks AS c (accession_no, form, version, checked_at, sections,
                                         findings)
    SELECT d.accession_no, d.form, v, now(),
           (SELECT count(DISTINCT x.section_key) FROM silver.filing_sections x
            WHERE x.accession_no = d.accession_no),
           (SELECT count(*) FROM ops.section_findings f WHERE f.accession_no = d.accession_no)
    FROM bronze.filing_documents d
    WHERE d.accession_no = ANY(p_accessions)
    ON CONFLICT (accession_no) DO UPDATE
    SET form = EXCLUDED.form, version = EXCLUDED.version, checked_at = EXCLUDED.checked_at,
        sections = EXCLUDED.sections, findings = EXCLUDED.findings;

    RETURN n;
END
$$;

