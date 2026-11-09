-- A filing's own XBRL where SEC companyfacts doesn't carry it (F8a): the filings companyfacts never
-- received, a company's extension tags, and a balance sheet line filed only by segment.
--
-- Measured on hermes 2026-11-09 (read only, one connection).
--
-- 1. Filings companyfacts never received. Of the 12,006 10-K/10-Q filings with XBRL filed in the 200 days
--    to 2026-11-02, 52 have no fact in silver (any CIK) a week or more after filing; since 2019, 479
--    originals (10-K/10-Q, not amendments), 217 of them at a company here. Citigroup's Q1 and Q2 2026
--    10-Qs, Cardinal Health's, Alnylam's, Molson Coors' and Johnson Controls' Q1-Q2 2026, Brandywine's,
--    Freddie Mac's, Graham Holdings', NNN's, Clorox's FY2026 10-K... Their gold stopped at the filing
--    before. For 40 of the 52 the stored document's inline XBRL is already extracted
--    (bronze.filing_xbrl.raw_gzip holds every numeric fact, migration 018): Citigroup's two have 6,310
--    and 8,525 facts. Five (Clorox, Friedman, Seneca Foods, Kentucky First, Columbia Financial) carry
--    only the cover page there (1-2 dei facts): their statements are in another document of the
--    filing (Clorox's annual report exhibit), so the filing's instance (`<document>_htm.xml`, which
--    EDGAR builds from every document of an inline filing) is fetched instead: one index.json and one
--    instance request each (services/facts-fetcher README, "Filings companyfacts lacks").
--    The facts are the filing's own, the ones companyfacts would carry: undimensioned facts of the
--    mapped tags. bronze.filing_facts keeps them per filing (from bronze.filing_xbrl or the stored
--    instance, bronze.filing_instances), silver.refresh_filing_facts maps them into
--    silver.financial_facts with source = 'filing', and gold reads them like any fact. When companyfacts
--    later carries the filing (silver.filing_facts_caught_up), silver.supersede_filing_facts deletes those
--    rows before the companyfacts refresh, so a filing's facts are never in silver twice and the
--    companyfacts copy is what stays (same values: the same instance).
--
-- 2. A company's own tags, only where one is proven to be the line a standard tag carries elsewhere.
--    NVIDIA's 10-Ks for FY2016-FY2022 tag capex as nvda:PurchasesOfPropertyAndEquipmentAndIntangible-
--    Assets, while its 10-Qs use us-gaap tags: no fiscal Q4 and no annual capex for FY2017-21 in gold
--    (041's repeated series_gap). The same line is us-gaap:PaymentsToAcquireProductiveAssets in its
--    FY2024 10-K: FY2022 is 976M under both, and nine months to 2019-10-27 are 344M under
--    PaymentsToAcquirePropertyPlantAndEquipment (Q3 FY2020 10-Q) and PaymentsToAcquireProductiveAssets
--    (Q3 FY2021 10-Q). Annual values: FY2017 176M, FY2018 593M, FY2019 600M (FY2018 and FY2020 10-K
--    instances), FY2020 489M, FY2021 1,128M, FY2022 976M (FY2022 10-K, stored). silver.metric_tags rows
--    with a company_id count for that company only.
--
-- 3. Debt filed only by segment. Ford and GM tag their balance sheets' debt lines with a segment
--    member and never undimensioned, so companyfacts (which drops dimensional facts) has none: no debt,
--    no net debt (B6's "Ford absent"). A sum of members is the consolidated figure here because each
--    member is itself a line of the consolidated balance sheet: in all 21 Ford and 21 GM 10-K/10-Qs
--    stored (2021-09-30 to 2026-09-30), current liabilities equal the undimensioned current lines plus
--    the two DebtCurrent members, to the dollar at Ford (2025-12-31: 25,809 + 31,779 + 5,550 + 51,752 =
--    114,890M) and within 1M of rounding at GM (23,919 + 33,754 + 656 + 35,012 = 93,341 against 93,342M).
--      Ford, us-gaap:StatementBusinessSegmentsAxis {f:CompanyExcludingFordCreditMember,
--        f:FordCreditMember} (filings to 2024 add srt:ConsolidationItemsAxis = OperatingSegmentsMember,
--        which says nothing more): DebtCurrent -> long_term_debt_current; LongTermDebtNoncurrent, else
--        LongTermDebtAndCapitalLeaseObligations -> long_term_debt. 2025-12-31: 57,302M + 106,034M.
--      GM, gm:BusinessGroupAxis {gm:AutomotiveMember, gm:GmFinancialMember}: DebtCurrent ->
--        long_term_debt_current, LongTermDebtAndCapitalLeaseObligations -> long_term_debt. 2025-12-31:
--        35,668M + 94,609M.
--    silver.member_sums names the sum (the concept, the axis, every member it needs); the sum is made
--    only when every listed member is present for the filing and date, no other member of the axis is,
--    and the filing has no undimensioned fact of the concept for that date (which would be the
--    consolidated figure itself). Its silver tag spells the members out, e.g.
--    DebtCurrent[f:CompanyExcludingFordCreditMember+f:FordCreditMember], and source = 'filing_extra'.
--    Both are consolidated debt: Ford Credit's and GM Financial's borrowings fund their loans, so net
--    debt reads about 130B (Ford) and 100B (GM); the automotive figure alone is a segment's.
--    Documents are stored from late 2021, so the series starts there; Ford's three LongTermDebtNoncurrent
--    rows for 2018-20 (600M, 470M, 291M, an undimensioned fact of some other line) stay as they were.
--
-- Not made safe, left: Amazon's FY2017 Q2 capex (#19). Its Q2 2017 10-Q tags the line as
-- us-gaap:PaymentsForProceedsFromProductiveAssets (2,501M, net of property incentives; Q2 2016's 1,711M
-- matches gold's), but Amazon moved to the gross figure in 2018 and gold's Q3 and Q4 2017 are gross
-- (3,074M, 3,619M; FY2017 11,955M). A net Q2 would make FY2017's sum of quarters 11,055M instead of the
-- 10-K's 11,955M. The gross Q2 (3,114M) exists only as a difference of trailing-twelve-month columns
-- (Q1 2017 gross = FY2017 + Q1 2018 - TTM to Q1 2018 = 2,148M, where gold has the net 1,861M).
--
-- Provenance: silver.financial_facts.source = companyfacts | filing | filing_extra, and .taxonomy (until
-- now every row was us-gaap and the column was implied). bronze.filing_facts.source says which document
-- of the filing (ixbrl = the stored primary document, instance = the instance file at `document`).
-- gold.usable_facts (050's body, one edit marked "055", generated by a script asserting the anchor)
-- joins tags by taxonomy too, and a company's own tags only for that company. Golden figures don't move:
-- none of the five golden companies has a company tag or member sum, and every existing row is us-gaap.
--
-- Locks on hermes: ALTER TABLE silver.financial_facts ADD COLUMN x2 with constant defaults (no rewrite,
-- PG 11+) and a NOT VALID CHECK: ACCESS EXCLUSIVE for an instant, but it waits for running readers and
-- writers (the Query API's citation lookups, a facts load) and blocks those behind it while it waits,
-- so lock_timeout is 3 s (a failed hook is re-run). silver.metric_tags: new column, primary key rebuilt
-- (122 rows), ACCESS EXCLUSIVE for an instant (facts-fetcher reads it at the start of a run). New
-- tables and functions otherwise. Gold changes on the filing-facts job (facts-fetcher-filings), which
-- refreshes the companies it touches; nothing needs the financials-rebuild.

SET LOCAL lock_timeout = '3s';

-- silver: which taxonomy a fact's tag is in, and where the fact came from. companyfacts: the SEC's API
-- or bulk file; filing: the filing's own XBRL while companyfacts lacks the filing; filing_extra: what
-- companyfacts never carries (a company's own tag, a sum of members). One statement, one lock.
ALTER TABLE silver.financial_facts
    ADD COLUMN taxonomy text NOT NULL DEFAULT 'us-gaap',
    ADD COLUMN source   text NOT NULL DEFAULT 'companyfacts',
    ADD CONSTRAINT financial_facts_source
        CHECK (source IN ('companyfacts', 'filing', 'filing_extra')) NOT VALID;

-- A company's own tags: NULL = every company's
ALTER TABLE silver.metric_tags ADD COLUMN company_id text;
ALTER TABLE silver.metric_tags DROP CONSTRAINT metric_tags_pkey,
    ADD PRIMARY KEY (metric, taxonomy, tag);

-- A balance sheet line a company files only by member of one axis: the sum of every member listed, as
-- silver tag `tag` (a silver.metric_tags row of the same company says which metric it is). No FK to
-- silver.companies: test fixtures truncate it (047's reason).
CREATE TABLE silver.member_sums (
    company_id text   NOT NULL,
    tag        text   NOT NULL,  -- e.g. DebtCurrent[f:CompanyExcludingFordCreditMember+f:FordCreditMember]
    concept    text   NOT NULL,  -- us-gaap:DebtCurrent
    axis       text   NOT NULL,  -- us-gaap:StatementBusinessSegmentsAxis
    members    text[] NOT NULL CHECK (cardinality(members) >= 2),
    PRIMARY KEY (company_id, tag)
);

-- A filing's XBRL instance as fetched from EDGAR, gzip-compressed. Idempotent on the accession.
CREATE TABLE bronze.filing_instances (
    accession_no   text        PRIMARY KEY,
    cik            text        NOT NULL,  -- the EDGAR folder it was fetched from
    url            text        NOT NULL,
    content_gzip   bytea       NOT NULL,
    content_sha256 text        NOT NULL,  -- of the bytes as fetched
    size_bytes     integer     NOT NULL,  -- uncompressed
    fetched_at     timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE bronze.filing_instances ALTER COLUMN content_gzip SET STORAGE EXTERNAL;  -- already gzip

-- The facts of one filing that silver reads, from its own XBRL (facts-fetcher-filings): undimensioned
-- facts of mapped tags, and the member facts silver.member_sums needs. Same shape as
-- bronze.filing_xbrl.facts: {c, v, u, d, s, e | i, m}.
CREATE TABLE bronze.filing_facts (
    accession_no     text        PRIMARY KEY,
    cik              text        NOT NULL,  -- silver files the facts under this CIK: the XBRL's entity
                                            -- when it filed the filing, else the filer
    form             text,
    filed            date        NOT NULL,
    source           text        NOT NULL CHECK (source IN ('ixbrl', 'instance')),
    document         text        NOT NULL,  -- the stored primary document, or the instance's URL
    gap              boolean     NOT NULL,  -- companyfacts lacked the filing: its standard facts go to silver
    facts            jsonb       NOT NULL,
    fact_count       integer     NOT NULL,  -- every numeric fact the document had
    extractor_version integer    NOT NULL,
    projected_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at    timestamptz            -- companyfacts has the filing now: its standard facts left silver
);
CREATE INDEX filing_facts_cik ON bronze.filing_facts (cik);

-- Map one batch of filings' facts into silver (delete, then insert: idempotent on the accession).
--   Standard facts (tags every company shares) only while companyfacts lacks the filing, as 'filing';
--   a company's own tags and member sums always, as 'filing_extra'. Units as companyfacts names them.
CREATE FUNCTION silver.refresh_filing_facts(p_accessions text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
    total integer := 0;
BEGIN
    DELETE FROM silver.financial_facts
    WHERE accession_no = ANY(p_accessions) AND source IN ('filing', 'filing_extra');

    WITH b AS (
        SELECT b.*, k.company_id
        FROM bronze.filing_facts b
        LEFT JOIN silver.company_ciks k ON k.cik = b.cik
        WHERE b.accession_no = ANY(p_accessions)
    ),
    x AS (
        SELECT b.accession_no, b.cik, b.company_id, b.form, b.filed, b.gap, b.superseded_at,
               split_part(f->>'c', ':', 1) AS taxonomy, split_part(f->>'c', ':', 2) AS tag,
               f->>'u' AS unit, (f->>'s')::date AS period_start,
               coalesce(f->>'e', f->>'i')::date AS period_end, (f->>'v')::numeric AS value
        FROM b CROSS JOIN LATERAL jsonb_array_elements(b.facts) f
        WHERE NOT f ? 'm'
    )
    INSERT INTO silver.financial_facts
        (cik, metric, tag, unit, period_start, period_end, value, accession_no, form, filed,
         taxonomy, source)
    SELECT x.cik, t.metric, x.tag, x.unit, x.period_start, x.period_end, x.value, x.accession_no,
           x.form, x.filed, x.taxonomy,
           CASE WHEN t.company_id IS NULL THEN 'filing' ELSE 'filing_extra' END
    FROM x
    JOIN silver.metric_tags t ON t.taxonomy = x.taxonomy AND t.tag = x.tag
    JOIN silver.metrics m ON m.metric = t.metric AND m.unit = x.unit
    WHERE (t.company_id IS NULL AND x.gap AND x.superseded_at IS NULL
           AND NOT EXISTS (SELECT 1 FROM silver.financial_facts c
                           WHERE c.accession_no = x.accession_no AND c.source = 'companyfacts'))
       OR t.company_id = x.company_id
    ORDER BY x.accession_no, x.tag, t.priority
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;
    total := total + n;

    -- member sums (header): every listed member, no other, and no consolidated fact of the concept
    WITH b AS (
        SELECT b.*, k.company_id
        FROM bronze.filing_facts b
        JOIN silver.company_ciks k ON k.cik = b.cik
        WHERE b.accession_no = ANY(p_accessions)
    ),
    x AS (
        SELECT b.accession_no, b.cik, b.company_id, b.form, b.filed, f->>'c' AS concept,
               f->>'u' AS unit, (f->>'s')::date AS period_start,
               coalesce(f->>'e', f->>'i')::date AS period_end, (f->>'v')::numeric AS value,
               (f->'m') - 'srt:ConsolidationItemsAxis'::text AS m,
               f->'m'->>'srt:ConsolidationItemsAxis' AS consolidation
        FROM b CROSS JOIN LATERAL jsonb_array_elements(b.facts) f
        WHERE b.company_id IN (SELECT s.company_id FROM silver.member_sums s)
    ),
    sums AS (
        SELECT x.accession_no, x.cik, x.form, x.filed, s.tag, s.concept, x.unit, x.period_start,
               x.period_end, sum(x.value) AS value
        FROM silver.member_sums s
        JOIN x ON x.company_id = s.company_id AND x.concept = s.concept
              AND x.m ? s.axis AND (SELECT count(*) FROM jsonb_object_keys(x.m)) = 1
              -- a second dimension that says only "operating segments" (018's rule)
              AND coalesce(x.consolidation, 'us-gaap:OperatingSegmentsMember')
                  = 'us-gaap:OperatingSegmentsMember'
        GROUP BY x.accession_no, x.cik, x.form, x.filed, s.tag, s.concept, s.members, x.unit,
                 x.period_start, x.period_end
        HAVING count(DISTINCT x.m->>s.axis) = cardinality(s.members)
           AND bool_and(x.m->>s.axis = ANY(s.members))
    )
    INSERT INTO silver.financial_facts
        (cik, metric, tag, unit, period_start, period_end, value, accession_no, form, filed,
         taxonomy, source)
    SELECT s.cik, t.metric, s.tag, s.unit, s.period_start, s.period_end, s.value, s.accession_no,
           s.form, s.filed, split_part(s.concept, ':', 1), 'filing_extra'
    FROM sums s
    JOIN silver.company_ciks k ON k.cik = s.cik
    JOIN silver.metric_tags t ON t.company_id = k.company_id AND t.tag = s.tag
    JOIN silver.metrics m ON m.metric = t.metric AND m.unit = s.unit
    WHERE NOT EXISTS (
        SELECT 1 FROM bronze.filing_facts b CROSS JOIN LATERAL jsonb_array_elements(b.facts) f
        WHERE b.accession_no = s.accession_no AND f->>'c' = s.concept AND NOT f ? 'm'
          AND coalesce(f->>'e', f->>'i')::date = s.period_end
          AND (f->>'s')::date IS NOT DISTINCT FROM s.period_start)
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN total + n;
END
$$;

-- Filings read from their own XBRL that companyfacts now carries: a filer's latest payload has a fact
-- of the filing (bronze.company_facts.payload holds the mapped tags' facts).
CREATE FUNCTION silver.filing_facts_caught_up() RETURNS SETOF text
LANGUAGE sql STABLE AS $$
    SELECT b.accession_no
    FROM bronze.filing_facts b
    WHERE b.gap AND b.superseded_at IS NULL
      AND EXISTS (
          SELECT 1
          FROM (SELECT b.cik
                UNION SELECT z.cik FROM bronze.submission_filings z WHERE z.accession_no = b.accession_no
                UNION SELECT z.cik FROM bronze.filings z WHERE z.accession_no = b.accession_no) c(cik)
          CROSS JOIN LATERAL (
              SELECT p.payload FROM bronze.company_facts p
              WHERE p.cik = c.cik
              ORDER BY p.last_checked_at DESC, p.fetched_at DESC
              LIMIT 1
          ) p
          WHERE jsonb_path_exists(p.payload, '$.facts.*.*.units.*[*] ? (@.accn == $a)',
                                  jsonb_build_object('a', b.accession_no)))
$$;

-- Take these filings' own standard facts out of silver (companyfacts has them now) and return the
-- CIKs whose companyfacts payloads should be refreshed into silver again: any of the filings' filers.
CREATE FUNCTION silver.supersede_filing_facts(p_accessions text[]) RETURNS SETOF text
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM silver.financial_facts
    WHERE accession_no = ANY(p_accessions) AND source = 'filing';
    UPDATE bronze.filing_facts SET superseded_at = now()
    WHERE accession_no = ANY(p_accessions) AND superseded_at IS NULL;
    RETURN QUERY
        SELECT b.cik FROM bronze.filing_facts b WHERE b.accession_no = ANY(p_accessions)
        UNION SELECT z.cik FROM bronze.submission_filings z WHERE z.accession_no = ANY(p_accessions)
        UNION SELECT z.cik FROM bronze.filings z WHERE z.accession_no = ANY(p_accessions);
END
$$;

-- NVIDIA's 10-K capex tag (header, 2)
INSERT INTO silver.metric_tags (metric, taxonomy, tag, priority, company_id) VALUES
    ('capex', 'nvda', 'PurchasesOfPropertyAndEquipmentAndIntangibleAssets', 5, '0001045810');

-- Ford's and GM's debt by segment (header, 3)
INSERT INTO silver.member_sums (company_id, tag, concept, axis, members) VALUES
    ('0000037996', 'DebtCurrent[f:CompanyExcludingFordCreditMember+f:FordCreditMember]',
     'us-gaap:DebtCurrent', 'us-gaap:StatementBusinessSegmentsAxis',
     '{f:CompanyExcludingFordCreditMember,f:FordCreditMember}'),
    ('0000037996', 'LongTermDebtNoncurrent[f:CompanyExcludingFordCreditMember+f:FordCreditMember]',
     'us-gaap:LongTermDebtNoncurrent', 'us-gaap:StatementBusinessSegmentsAxis',
     '{f:CompanyExcludingFordCreditMember,f:FordCreditMember}'),
    ('0000037996',
     'LongTermDebtAndCapitalLeaseObligations[f:CompanyExcludingFordCreditMember+f:FordCreditMember]',
     'us-gaap:LongTermDebtAndCapitalLeaseObligations', 'us-gaap:StatementBusinessSegmentsAxis',
     '{f:CompanyExcludingFordCreditMember,f:FordCreditMember}'),
    ('0001467858', 'DebtCurrent[gm:AutomotiveMember+gm:GmFinancialMember]',
     'us-gaap:DebtCurrent', 'gm:BusinessGroupAxis', '{gm:AutomotiveMember,gm:GmFinancialMember}'),
    ('0001467858', 'LongTermDebtAndCapitalLeaseObligations[gm:AutomotiveMember+gm:GmFinancialMember]',
     'us-gaap:LongTermDebtAndCapitalLeaseObligations', 'gm:BusinessGroupAxis',
     '{gm:AutomotiveMember,gm:GmFinancialMember}');

INSERT INTO silver.metric_tags (metric, taxonomy, tag, priority, company_id) VALUES
    ('long_term_debt_current', 'us-gaap',
     'DebtCurrent[f:CompanyExcludingFordCreditMember+f:FordCreditMember]', 5, '0000037996'),
    ('long_term_debt', 'us-gaap',
     'LongTermDebtNoncurrent[f:CompanyExcludingFordCreditMember+f:FordCreditMember]', 6, '0000037996'),
    ('long_term_debt', 'us-gaap',
     'LongTermDebtAndCapitalLeaseObligations[f:CompanyExcludingFordCreditMember+f:FordCreditMember]', 7,
     '0000037996'),
    ('long_term_debt_current', 'us-gaap', 'DebtCurrent[gm:AutomotiveMember+gm:GmFinancialMember]', 6,
     '0001467858'),
    ('long_term_debt', 'us-gaap',
     'LongTermDebtAndCapitalLeaseObligations[gm:AutomotiveMember+gm:GmFinancialMember]', 8, '0001467858');

-- 050's gold.usable_facts with one edit marked "055" (header): tags joined by taxonomy, and a company's
-- own tags only for that company
CREATE OR REPLACE FUNCTION gold.usable_facts(p_company_ids text[])
RETURNS TABLE (company_id text, metric text, tag text, taxonomy text, unit text, period_start date,
               period_end date, period_type text, value numeric, accession_no text, form text,
               filed date, ingested_at timestamptz, cik text, priority numeric, additive boolean)
LANGUAGE sql STABLE AS $$
    WITH own AS (
        SELECT k.company_id, k.cik FROM silver.company_ciks k WHERE k.company_id = ANY(p_company_ids)
    ),
    -- 050: a filing's facts belong to the filer its XBRL names (header). lent: (filing, CIK) of
    -- these companies' facts that are another company's filing; borrowed: facts under another
    -- filer's CIK of a filing whose XBRL names one of these companies' CIKs, when none of the
    -- company's own CIKs has the filing's facts.
    lent AS MATERIALIZED (
        SELECT DISTINCT a.accession_no, a.cik
        FROM (SELECT DISTINCT f.accession_no, f.cik
              FROM own o JOIN silver.financial_facts f ON f.cik = o.cik) a
        JOIN bronze.filing_xbrl x ON x.accession_no = a.accession_no AND x.entity_cik <> a.cik
        JOIN silver.company_ciks e ON e.cik = x.entity_cik
        JOIN own o ON o.cik = a.cik
        WHERE e.company_id <> o.company_id
          AND gold.filed_by(a.accession_no, x.entity_cik)
    ),
    borrowed AS MATERIALIZED (
        SELECT DISTINCT o.company_id, f.accession_no, f.cik
        FROM own o
        JOIN bronze.filing_xbrl x ON x.entity_cik = o.cik
        JOIN silver.financial_facts f ON f.accession_no = x.accession_no AND f.cik <> x.entity_cik
        WHERE NOT EXISTS (SELECT 1 FROM silver.company_ciks k
                          WHERE k.cik = f.cik AND k.company_id = o.company_id)
          AND NOT EXISTS (SELECT 1 FROM own m
                          JOIN silver.financial_facts g
                            ON g.cik = m.cik AND g.accession_no = x.accession_no
                          WHERE m.company_id = o.company_id)
          AND gold.filed_by(x.accession_no, x.entity_cik)
    ),
    facts AS (
        SELECT o.company_id, f.*
        FROM own o
        JOIN silver.financial_facts f ON f.cik = o.cik
        WHERE NOT EXISTS (SELECT 1 FROM lent l
                          WHERE l.accession_no = f.accession_no AND l.cik = f.cik)
        UNION ALL
        SELECT b.company_id, f.*
        FROM borrowed b
        JOIN silver.financial_facts f ON f.accession_no = b.accession_no AND f.cik = b.cik
    ),
    tagged AS (
        -- by tag, not by silver's metric column: its identity index has no metric, so a tag two metrics
        -- map (EarningsPerShareBasicAndDiluted) is stored once, under one of them
        SELECT f.company_id, t.metric, f.tag, t.taxonomy, f.unit, f.period_start, f.period_end,
               f.period_type,
               -- 043: interest expense is a cost, its sign in a filing is presentation (header);
               -- 050: so is a debt balance's, which can't be below zero
               CASE WHEN t.metric IN ('interest_expense', 'long_term_debt', 'long_term_debt_current')
                    THEN abs(f.value) ELSE f.value END AS value,
               f.accession_no, f.form, f.filed, f.ingested_at, f.cik,
               t.priority::numeric AS priority, m.additive, t.gold_line
        FROM facts f
        -- 055: and by taxonomy (an ifrs-full tag can share a us-gaap tag's name), and a company's own
        -- tags (an extension, a sum of members) only for that company
        JOIN silver.metric_tags t ON t.tag = f.tag AND t.taxonomy = f.taxonomy
                                 AND (t.company_id IS NULL OR t.company_id = f.company_id)
        JOIN silver.metrics m ON m.metric = t.metric AND m.unit = f.unit
        WHERE TRUE
          -- proxy and information statements (DEF 14A, DEFA14A, DEFR14A, DEFM14A,
          -- DEF 14C, PRE 14A, ...): pay-versus-performance net income, rounded
          AND coalesce(f.form, '') !~ '^(DEF|PRE)'
          AND f.period_end <= f.filed + 7
    ),
    summed AS (
        SELECT x.company_id, s.metric, array_to_string(s.tags, '+') AS tag, x.taxonomy, x.unit,
               x.period_start, x.period_end, x.period_type, sum(x.value) AS value, x.accession_no,
               x.form, x.filed, max(x.ingested_at) AS ingested_at, min(x.cik) AS cik, s.priority,
               x.additive, true AS gold_line
        FROM silver.metric_sums s
        JOIN tagged x ON x.metric = s.metric AND x.tag = ANY(s.tags)
        GROUP BY x.company_id, s.metric, s.tags, x.taxonomy, x.unit, x.period_start, x.period_end,
                 x.period_type, x.accession_no, x.form, x.filed, s.priority, x.additive
        -- every part present, and none a rounding of the other: State Street's 2019 10-Qs tag
        -- NoninterestIncome as -1 and 0 (it's a securities gain), beside 613 of interest income
        HAVING count(DISTINCT x.tag) = cardinality(s.tags)
           AND min(abs(x.value)) >= 0.01 * max(abs(x.value))
    ),
    loss_eps AS (  -- a loss quarter's diluted EPS is its basic EPS; ranks after the two diluted tags
        SELECT x.company_id, 'eps_diluted' AS metric, x.tag, x.taxonomy, x.unit, x.period_start,
               x.period_end, x.period_type, x.value, x.accession_no, x.form, x.filed,
               x.ingested_at, x.cik, 3::numeric AS priority, x.additive, true AS gold_line
        FROM tagged x
        WHERE x.metric = 'eps_basic' AND x.tag = 'EarningsPerShareBasic' AND x.value < 0
    ),
    lines AS (
        SELECT * FROM tagged WHERE gold_line
        UNION ALL SELECT * FROM summed
        UNION ALL SELECT * FROM loss_eps
    ),
    -- 050: debt parts adding up to 1,000 or 1,000,000 times the same filing's total for the date
    -- are a scale slip in the parts (Bank First's 2019 10-K: 1,400,000,000 + 38,400,000,000 beside a
    -- LongTermDebt of 39,800,000)
    slipped AS (
        SELECT p.company_id, p.accession_no, p.period_end
        FROM (
            SELECT x.company_id, x.accession_no, x.period_end,
                   sum(x.value) FILTER (WHERE x.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent'))
                       AS parts,
                   max(x.value) FILTER (WHERE x.tag = 'LongTermDebt') AS total
            FROM tagged x
            WHERE x.period_start IS NULL
              AND x.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent', 'LongTermDebt')
            GROUP BY x.company_id, x.accession_no, x.period_end
        ) p
        WHERE p.total > 0
          AND (abs(p.parts / p.total - 1000) <= 5 OR abs(p.parts / p.total - 1000000) <= 5000)
    ),
    balance AS (  -- what the debt tests read at each date: each tag's latest filing
        SELECT y.company_id, y.period_end,
               max(y.value) FILTER (WHERE y.tag = 'Liabilities') AS liabilities,
               max(y.value) FILTER (WHERE y.tag = 'LiabilitiesCurrent') AS liabilities_current,
               max(y.value) FILTER (WHERE y.tag = 'Assets') AS assets,
               max(y.value) FILTER (WHERE y.metric = 'long_term_debt_current'
                                      AND y.rnk = 1) AS current_debt
        FROM (
            SELECT DISTINCT ON (x.company_id, x.tag, x.period_end) x.*,
                   dense_rank() OVER (PARTITION BY x.company_id, x.metric, x.period_end
                                      ORDER BY x.priority) AS rnk
            FROM tagged x
            WHERE x.period_start IS NULL
              AND (x.tag IN ('Liabilities', 'LiabilitiesCurrent', 'Assets')
                   OR x.metric = 'long_term_debt_current')
              AND NOT (x.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent')  -- 050
                       AND EXISTS (SELECT 1 FROM slipped s
                                   WHERE s.company_id = x.company_id
                                     AND s.accession_no = x.accession_no
                                     AND s.period_end = x.period_end))
            ORDER BY x.company_id, x.tag, x.period_end, x.filed DESC, x.accession_no DESC
        ) y
        GROUP BY y.company_id, y.period_end
    )
    SELECT l.company_id, l.metric,
           CASE WHEN c.total THEN l.tag || '-' || 'CurrentPortion' ELSE l.tag END, l.taxonomy, l.unit,
           l.period_start, l.period_end, l.period_type,
           CASE WHEN c.total THEN l.value - b.current_debt ELSE l.value END,
           l.accession_no, l.form, l.filed, l.ingested_at, l.cik, l.priority, l.additive
    FROM lines l
    LEFT JOIN balance b ON (l.metric IN ('long_term_debt', 'long_term_debt_current') OR l.tag = 'Cash')
                       AND b.company_id = l.company_id AND b.period_end = l.period_end
    LEFT JOIN silver.companies co ON l.tag IN ('LongTermDebt', 'LongTermNotesPayable',
                                               'LongTermNotesAndLoans')
                                 AND co.company_id = l.company_id
    CROSS JOIN LATERAL (
        SELECT l.tag IN ('LongTermDebt', 'LongTermNotesPayable', 'LongTermNotesAndLoans')
               AND b.current_debt > 0
               -- a total holds its current portion: equal to it (all of the debt is current), or
               -- above it and above all noncurrent liabilities; below it, it's the noncurrent line
               AND (l.value = b.current_debt
                    OR (l.value > b.current_debt
                        AND l.value > (b.liabilities - b.liabilities_current) * 1.01)) AS total
    ) c
    WHERE NOT (l.metric IN ('long_term_debt', 'long_term_debt_current')
               AND l.value > coalesce(b.liabilities * 5, 'Infinity')
               AND l.value > coalesce(b.assets * 5, 'Infinity'))
      -- Cash only on a balance sheet's date: elsewhere it's a note (cash paid in a deal, Boston
      -- Scientific on 2023-02-20)
      AND NOT (l.tag = 'Cash' AND b.assets IS NULL)
      -- a negative "investment" under the tags 038 added is a mis-signed note (HCA's 2012 10-Q)
      AND NOT (l.metric = 'short_term_investments' AND l.priority > 4 AND l.value < 0)
      AND NOT (l.tag IN ('LongTermDebt', 'LongTermNotesPayable', 'LongTermNotesAndLoans')
               AND (co.sic IS NULL OR 'net_debt' = ANY(gold.not_applicable_metrics(l.company_id, co.sic))))
      -- 050: debt parts that are a scale slip of the filing's own total
      AND NOT (l.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent') AND l.period_start IS NULL
               AND EXISTS (SELECT 1 FROM slipped s
                           WHERE s.company_id = l.company_id AND s.accession_no = l.accession_no
                             AND s.period_end = l.period_end))
$$;
