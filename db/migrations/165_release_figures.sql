-- Foreign filers' quarters from their 6-K results releases (db/DESIGN.md "165"). A company filing
-- 20-F or 40-F reports its quarters only in 6-K results releases: of the 1,246 such companies with
-- gold, 1,179 had under four revenue quarters in the last two years. 095 stores those 6-Ks and the
-- parsers split them, tables beside the text (silver.filing_tables); no parser change here
-- (PARSER_VERSION stays 8): parser-index reads the stored tables. Measured on hermes 2026-11-25,
-- read-only.
--
-- 1. silver.release_reads / silver.release_figures: filing_parser/releases.py (its docstring is the
--    spec; READER releases-1) on every stored 6-K: revenue, net income (the owners' share first),
--    operating income and total assets of the release's own period, as printed times the scale the
--    table names, in the currency it names, with the section, table, row and column; one figure per
--    metric and period or none (left out, counted by reason in release_reads.left_out). Tables of a
--    part of the company (segments, guarantors, a parent's or separate statements), adjusted views
--    and movements are never read; net income and operating income only from the table that gives
--    the period's revenue. parser-index reads the 6-Ks it indexes; a catch-up kind 'releases' reads
--    every stored one once per READER (300 s a run); a re-read with the same figures keeps its rows.
-- 2. gold.usable_facts (155's body, edits marked 165): release figures after every tag (priority
--    1000, taxonomy 'release', tag '<section>/<table>/<row>/<column>': gold's source tag
--    'release:EX-99.1/0/1/1' cites the cell, FR6), the one picker (best_facts) choosing among them:
--    - only where the company's XBRL gives the metric no figure ending within 7 days (a duration
--      against durations, an instant against instants): a 20-F/40-F year stands, its fiscal Q4 is
--      the year less the releases' nine months, and a release never meets an XBRL fact of its period
--      in best_facts' windows (nor votes in the reporting currency);
--    - in the company's currency: named so, a bare "$" at a dollar company, or none named (measured
--      below: 403 of 403 and 259 of 259 such figures were in the company's currency);
--    - revenue and total assets only at a size the company's own XBRL bears out: revenue at a year's
--      pace within 0.4-2.5x of the nearest XBRL year within 450 days, total assets 0.5-2x of the
--      nearest balance sheet (a scale misread is 1,000x; a part of the company is a fraction);
--    - net income and operating income only beside the same release's revenue of the period.
--    Measured against the XBRL that 467 companies' 6-Ks carry (1,231 6-Ks, at most four a company;
--    the same 6-K's own facts, gold's tag order): of the figures these checks let through, 2,688 of
--    2,708 equal the filing's fact (99.3%): revenue 727 of 733, net income 652 of 664 (most of the
--    12 the owners' share where the XBRL tags only the total), operating income 516 of 517, total
--    assets 793 of 794. Of 1,048 6-Ks since 2023 of 152 companies whose 6-Ks carry no XBRL, the
--    year-end figures their 20-F/40-F also gives: 140 of 147 equal (the 7: STMicro's statutory IFRS
--    report beside its US GAAP 20-F, Taiwan-IFRS releases beside an IFRS 20-F, the owners' share
--    against a total; all at fiscal year ends, where gold never reads a release).
-- 3. gold.knowledge_facts (086's body, edit marked 165): a release's figures count on its filing day
--    (negative ids), so a company's history replays from its first release's day when they land.
--    ops.company_backlog (086's body, edit marked 165): a release read after a company's gold build
--    makes its gold due; the facts-fetcher catch-up rebuilds it within the hour (the live 6-K too).
-- 4. gold.rules_version() 155 -> 165, scoped: with silver.release_figures empty (this migration),
--    gold.usable_facts and gold.knowledge_facts give 155's and 086's rows for every company at every
--    as-of date (tests/golden/test_reference_set.py compares the bodies over the reference set's
--    companies, today and three past days), so a company built at 155 is stamped built at 165 here
--    (on hermes: gold 8,129, known 1,150); a company still behind stays behind. Only a company whose
--    release reads land is rebuilt (3).
-- 5. ops.coverage_found (081's body, edits marked 165): a commodity pool or trust (SIC 6221) is out
--    of scope for history_short and figures_behind too, cause 'trust', as 081 made it for
--    metric_missing: its figures are net assets and gains on what it holds (Grayscale's, Teucrium,
--    iShares GSCI, ProShares Trust II, VS Trust, USCF), not the statements Tributary reads. On
--    hermes 11 such gaps were 'fixable' (history_short 8, figures_behind 3). Read time only: the
--    nightly sweep rewords them; nothing is rebuilt.
--
-- Effect, measured by running the reader and these checks over all 16,182 stored 6-Ks with tables
-- (read-only, streamed): 626 companies get figures, 286 of them quarterly revenue (2,244
-- company-quarters; net income 1,989, operating income 1,233, six and nine months besides, total
-- assets at 3,946 dates). TSMC: twelve quarters, 1Q26 NT$1,134.1bn (0001046179-26-000199), 2Q26
-- NT$1,270.4bn (-000451), 3Q26 NT$1,494,243,284 thousand (its statements, -000801, beside the press
-- release's NT$1,494,243 million, -000726), and each fiscal Q4 as its 20-F year less nine months.
-- Re-processing budget (the owner's rule, 2026-11-24): the releases catch-up reads 16,182 6-Ks (874
-- MB of stored table JSON; the statements kind reads ~1,200 filings in 30 s): a few hourly runs.
-- Gold rebuilds the 977 companies with stored 6-K tables as their reads land (~550 a run at 155);
-- their 516,000 gold rows rewritten once (~0.33 GB of dead tuples autovacuum reuses). History: 67
-- of them have a 'known' history current at 155, each replayed from its first release's day (~66 s
-- a company at today's rate: ~75 minutes of the known kind); the other 910 are behind already (155's
-- replay, unchanged). Storage added: silver ~15 MB (16,182 reads, ~32,000 figures), gold and its
-- history ~35 MB (new quarters, derived rows and their versions).
--
-- Locks on hermes (lock_timeout 3 s): new tables and functions; the CHECK on ops.catchup replaced
-- (ACCESS EXCLUSIVE for an instant: the hourly catch-ups write it; 9 rows); CREATE OR REPLACE of
-- gold.usable_facts, gold.knowledge_facts, ops.company_backlog, ops.coverage_found and
-- gold.rules_version (catalog only;
-- a running build keeps the bodies it started with); UPDATE of ops.company_builds' 9,279 gold and
-- known rows at 155 (the catch-ups read them hourly). Backout: db/backout/release_figures_165.sql.

SET LOCAL lock_timeout = '3s';

-- 1. Release figures ------------------------------------------------------------------------------

-- One row per 6-K read (filing_parser.releases, by parser-index), figures or none.
CREATE TABLE silver.release_reads (
    accession_no text        PRIMARY KEY,
    cik          text        NOT NULL,
    form         text        NOT NULL,
    period       date,                  -- the release's own period (releases.py rule 3)
    filed        date        NOT NULL,
    reader       text        NOT NULL,  -- releases.READER
    figures      integer     NOT NULL,
    left_out     jsonb       NOT NULL DEFAULT '{}',  -- reason -> candidates left out, counts only
    read_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX release_reads_cik ON silver.release_reads (cik, read_at);

-- One figure per metric and period of a release (releases.py rules 1-8), as printed times its
-- scale, signed, with the table and cell it came from. No grant: gold reads it (165's
-- gold.usable_facts) and gold's rows carry the provenance.
CREATE TABLE silver.release_figures (
    figure_id    bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    accession_no text        NOT NULL REFERENCES silver.release_reads ON DELETE CASCADE,
    metric       text        NOT NULL REFERENCES silver.metrics
        CHECK (metric IN ('revenue', 'net_income', 'operating_income', 'total_assets')),
    period_start date,                  -- NULL: an instant (total assets)
    period_end   date        NOT NULL,
    months       integer,               -- the period's length; NULL for an instant
    value        numeric     NOT NULL,
    currency     text,                  -- ISO code, '$' (a bare dollar) or NULL (none named)
    scale        bigint      NOT NULL,  -- 1 when no scale is named (scale_named false)
    scale_named  boolean     NOT NULL,
    label        text        NOT NULL,  -- as printed
    section_key  text        NOT NULL,
    table_no     integer     NOT NULL,
    row_no       integer     NOT NULL,
    col_no       integer     NOT NULL,
    titled       boolean     NOT NULL,
    attributable boolean     NOT NULL,  -- net income attributable to the parent's owners
    UNIQUE NULLS NOT DISTINCT (accession_no, metric, period_end, months),
    CHECK ((months IS NULL) = (period_start IS NULL))
);

-- 6-Ks with tables that this releases.READER hasn't read (the catch-up kind 'releases'): 079's
-- anti-join shape
CREATE FUNCTION ops.release_backlog(p_reader text)
RETURNS TABLE (accession_no text, filed date)
LANGUAGE sql STABLE AS $$
    SELECT d.accession_no, d.filed
    FROM bronze.filing_documents d
    WHERE d.form IN ('6-K', '6-K/A')
      AND NOT EXISTS (SELECT 1 FROM silver.release_reads r
                      WHERE r.accession_no = d.accession_no AND r.reader = p_reader)
      AND EXISTS (SELECT 1 FROM silver.filing_tables t WHERE t.accession_no = d.accession_no)
      AND EXISTS (SELECT 1 FROM silver.company_ciks k WHERE k.cik = d.cik)
$$;

ALTER TABLE ops.catchup DROP CONSTRAINT catchup_kind_check;
ALTER TABLE ops.catchup ADD CONSTRAINT catchup_kind_check
    CHECK (kind IN ('parser', 'passages', 'diffs', 'gold', 'check_financials', 'check_segments',
                    'check_sections', 'statements', 'known', 'releases'));
INSERT INTO ops.catchup (kind, paused) VALUES ('releases', false);

-- 2. Gold reads them: gold.usable_facts (155's body, the edits marked 165) ------------------------
CREATE OR REPLACE FUNCTION gold.usable_facts(p_company_ids text[], p_as_of date DEFAULT NULL)
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
          AND (p_as_of IS NULL OR f.filed <= p_as_of)  -- 085: what was filed by the end of p_as_of
        UNION ALL
        SELECT b.company_id, f.*
        FROM borrowed b
        JOIN silver.financial_facts f ON f.accession_no = b.accession_no AND f.cik = b.cik
        WHERE p_as_of IS NULL OR f.filed <= p_as_of  -- 085
    ),
    -- 056: each company's reporting currency: the currency most of the monetary facts of its latest
    -- annual or quarterly report are in (a 20-F's convenience translation of one year into USD is
    -- the minority). One series never mixes currencies.
    report AS (
        SELECT DISTINCT ON (f.company_id) f.company_id, f.accession_no
        FROM facts f
        WHERE f.unit ~ '^[A-Z]{3}$' AND coalesce(f.form, '') ~ '^(10-K|10-Q|20-F|40-F|6-K)'
        ORDER BY f.company_id, f.filed DESC, f.accession_no DESC
    ),
    currency AS (
        SELECT DISTINCT ON (c.company_id) c.company_id, c.unit
        FROM (
            SELECT f.company_id, f.unit, count(*) AS n
            FROM facts f
            JOIN report r ON r.company_id = f.company_id AND r.accession_no = f.accession_no
            WHERE f.unit ~ '^[A-Z]{3}$'
            GROUP BY f.company_id, f.unit
        ) c
        ORDER BY c.company_id, c.n DESC, c.unit = 'USD' DESC, c.unit
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
        -- 056: a currency for a monetary metric (silver.unit_fits), the company's own (header)
        JOIN silver.metrics m ON m.metric = t.metric AND silver.unit_fits(m.unit, f.unit)
        LEFT JOIN currency cu ON cu.company_id = f.company_id
        WHERE (m.unit NOT IN ('USD', 'USD/shares')
               OR f.unit = coalesce(cu.unit, 'USD')
                           || CASE WHEN m.unit = 'USD/shares' THEN '/shares' ELSE '' END)
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
    -- 155: what a filing says is due within a year at a date, side by side: the current portion of
    -- long-term debt (LongTermDebtCurrent, else its tag with leases), the filer's own DebtCurrent,
    -- short-term borrowings and commercial paper (header, 1)
    current_debt AS (
        SELECT x.*,
               max(x.value) FILTER (WHERE x.tag = 'LongTermDebtCurrent') OVER w AS ltdc,
               max(x.value) FILTER (WHERE x.tag = 'LongTermDebtAndCapitalLeaseObligationsCurrent')
                   OVER w AS ltdc_leases,
               max(x.value) FILTER (WHERE x.tag = 'DebtCurrent') OVER w AS debt_current,
               max(x.value) FILTER (WHERE x.tag = 'ShortTermBorrowings') OVER w AS borrowings,
               bool_or(x.gold_line AND x.tag NOT IN ('LongTermDebtCurrent', 'DebtCurrent',
                       'LongTermDebtAndCapitalLeaseObligationsCurrent')) OVER w AS other_line
        FROM tagged x
        WHERE x.metric = 'long_term_debt_current' AND x.taxonomy = 'us-gaap'
          AND x.period_start IS NULL
        WINDOW w AS (PARTITION BY x.company_id, x.accession_no, x.period_end, x.unit)
    ),
    current_lines AS (
        SELECT d.company_id, d.metric,
               CASE WHEN k.joins THEN k.portion_tag || '+' || d.tag ELSE d.tag END AS tag,
               d.taxonomy, d.unit, d.period_start, d.period_end, d.period_type,
               CASE WHEN k.joins THEN k.portion + d.value ELSE d.value END AS value,
               d.accession_no, d.form, d.filed, d.ingested_at, d.cik,
               CASE WHEN d.tag = 'DebtCurrent' AND d.debt_current > k.portion THEN 0.5
                    WHEN k.joins AND d.tag = 'ShortTermBorrowings' THEN 0.6
                    WHEN k.joins THEN 0.7
                    WHEN k.alone AND d.tag = 'ShortTermBorrowings' THEN 14
                    WHEN k.alone THEN 15
                    ELSE d.priority END AS priority,
               d.additive, true AS gold_line
        FROM current_debt d
        CROSS JOIN LATERAL (
            SELECT coalesce(d.ltdc, d.ltdc_leases) AS portion,
                   CASE WHEN d.ltdc IS NOT NULL THEN 'LongTermDebtCurrent'
                        ELSE 'LongTermDebtAndCapitalLeaseObligationsCurrent' END AS portion_tag,
                   -- short-term borrowings, else commercial paper: the one part read
                   d.tag = 'ShortTermBorrowings'
                       OR (d.tag = 'CommercialPaper' AND d.borrowings IS NULL) AS part
        ) p
        CROSS JOIN LATERAL (
            -- a part added to the current portion where the filer gives no DebtCurrent and the part
            -- isn't the portion itself (equal, or up to 10% more: a total holding it)
            SELECT p.portion, p.portion_tag,
                   p.part AND p.portion IS NOT NULL AND d.debt_current IS NULL AND d.value > 0
                   AND NOT (d.value BETWEEN 0.99 * p.portion AND 1.10 * p.portion) AS joins,
                   -- a part that is all the filing gives
                   p.part AND p.portion IS NULL AND d.debt_current IS NULL
                   AND NOT coalesce(d.other_line, false) AS alone
        ) k
        WHERE d.gold_line OR k.joins OR k.alone
    ),
    -- 155: a long-term debt of zero where a filing's balance sheet says there is none (header, 2):
    -- each periodic filing's assets at a date, whether it gives any long-term debt fact there (a
    -- line or an evidence tag) or a debt cash flow ending there, and its interest expense or paid,
    -- a year's worth
    debt_marks AS (
        SELECT x.company_id, x.accession_no, x.period_end,
               max(x.value) FILTER (WHERE x.tag = 'Assets' AND x.period_start IS NULL) AS assets,
               max(x.unit) FILTER (WHERE x.tag = 'Assets' AND x.period_start IS NULL) AS unit,
               max(x.form) AS form, max(x.filed) AS filed, max(x.ingested_at) AS ingested_at,
               min(x.cik) AS cik,
               coalesce(bool_or(x.value <> 0 AND (
                   (x.metric = 'long_term_debt' AND x.period_start IS NULL)
                   OR (x.metric = 'financing_cash_flow' AND NOT x.gold_line))), false) AS debt,
               max(x.value * 365 / (x.period_end - x.period_start + 1)) FILTER (
                   WHERE x.metric = 'interest_expense' AND x.period_end - x.period_start >= 80)
                   AS interest
        FROM tagged x
        WHERE (x.tag = 'Assets' OR x.metric IN ('long_term_debt', 'interest_expense')
               OR (x.metric = 'financing_cash_flow' AND NOT x.gold_line))
          AND coalesce(x.form, '') ~ '^(10-K|10-Q|20-F|40-F)'
        GROUP BY x.company_id, x.accession_no, x.period_end
    ),
    no_debt AS (  -- any debt fact within the year to the date, in any periodic filing
        SELECT m.*,
               bool_or(m.debt) OVER (PARTITION BY m.company_id ORDER BY m.period_end
                                     RANGE BETWEEN '365 days' PRECEDING AND CURRENT ROW)
                   AS debt_in_year
        FROM debt_marks m
    ),
    no_debt_lines AS (
        SELECT n.company_id, 'long_term_debt'::text AS metric, 'no-debt'::text AS tag,
               'derived'::text AS taxonomy, n.unit, NULL::date AS period_start, n.period_end,
               'instant'::text AS period_type, 0::numeric AS value, n.accession_no, n.form, n.filed,
               n.ingested_at, n.cik, 99::numeric AS priority, false AS additive, true AS gold_line
        FROM no_debt n
        JOIN silver.companies co ON co.company_id = n.company_id
        WHERE n.assets > 0 AND NOT n.debt_in_year
          AND coalesce(n.interest, 0) <= 0.0025 * n.assets
          AND co.sic IS NOT NULL AND co.sic !~ '^6[0-7]'
    ),
    -- 165: a results release's figures (silver.release_figures, filing_parser/releases.py), after
    -- every tag, in the company's currency, and only where the company's XBRL gives the metric no
    -- figure ending within 7 days (a fiscal Q4 is then the 20-F's year less the releases' nine
    -- months, never the release's own quarter); revenue and total assets only at a size the
    -- company's own XBRL years and balance sheets within 450 days bear out, net income and
    -- operating income only beside the same release's revenue of the period (header, 2)
    release_read AS MATERIALIZED (
        SELECT o.company_id, f.figure_id, f.metric, f.period_start, f.period_end, f.months,
               f.value, f.section_key, f.table_no, f.row_no, f.col_no, r.accession_no, r.form,
               r.filed, r.read_at, r.cik, cu.unit
        FROM own o
        JOIN silver.release_reads r ON r.cik = o.cik
        JOIN silver.release_figures f ON f.accession_no = r.accession_no
        JOIN currency cu ON cu.company_id = o.company_id
        WHERE (p_as_of IS NULL OR r.filed <= p_as_of)
          -- named the company's currency, a bare dollar at a dollar company, or none named
          AND (f.currency = cu.unit OR f.currency IS NULL
               OR (f.currency = '$' AND cu.unit IN ('USD', 'CAD', 'AUD', 'NZD', 'HKD', 'SGD',
                                                     'TWD')))
    ),
    xbrl_ends AS MATERIALIZED (  -- where the company's XBRL gives each metric a figure
        SELECT DISTINCT x.company_id, x.metric, x.period_end, x.period_start IS NULL AS instant
        FROM tagged x
        WHERE x.gold_line AND x.metric IN ('revenue', 'net_income', 'operating_income',
                                           'total_assets')
          AND x.company_id IN (SELECT r.company_id FROM release_read r)
    ),
    release_refs AS MATERIALIZED (  -- the XBRL years and balance sheets a size is checked against
        SELECT x.company_id, x.metric, x.period_end, max(x.value) AS value
        FROM tagged x
        WHERE x.gold_line AND x.company_id IN (SELECT r.company_id FROM release_read r)
          AND ((x.metric = 'revenue' AND x.period_end - x.period_start BETWEEN 350 AND 380)
               OR (x.metric = 'total_assets' AND x.period_start IS NULL))
        GROUP BY x.company_id, x.metric, x.period_end
    ),
    release_open AS (  -- no XBRL figure of the metric ends within 7 days
        SELECT r.* FROM release_read r
        WHERE NOT EXISTS (SELECT 1 FROM xbrl_ends e
                          WHERE e.company_id = r.company_id AND e.metric = r.metric
                            AND e.instant = (r.months IS NULL)
                            AND abs(e.period_end - r.period_end) <= 7)
    ),
    release_sized AS MATERIALIZED (  -- revenue and total assets the nearest XBRL figure bears out
        SELECT s.* FROM (
            SELECT r.*, (SELECT x.value FROM release_refs x
                         WHERE x.company_id = r.company_id AND x.metric = r.metric
                           AND abs(x.period_end - r.period_end) <= 450
                         ORDER BY abs(x.period_end - r.period_end), x.period_end DESC
                         LIMIT 1) AS reference
            FROM release_open r
            WHERE r.metric IN ('revenue', 'total_assets')
        ) s
        WHERE s.reference > 0
          AND CASE WHEN s.metric = 'revenue'
                   THEN s.value * 12 / s.months BETWEEN 0.4 * s.reference AND 2.5 * s.reference
                   ELSE s.value BETWEEN 0.5 * s.reference AND 2 * s.reference END
    ),
    release_facts AS (
        SELECT l.company_id, l.metric,
               l.section_key || '/' || l.table_no || '/' || l.row_no || '/' || l.col_no AS tag,
               'release'::text AS taxonomy, l.unit, l.period_start, l.period_end,
               CASE WHEN l.period_start IS NULL THEN 'instant'
                    WHEN l.period_end - l.period_start BETWEEN 80 AND 120 THEN 'quarter'
                    WHEN l.period_end - l.period_start BETWEEN 350 AND 380 THEN 'annual'
                    ELSE 'ytd' END AS period_type,
               l.value, l.accession_no, l.form, l.filed, l.read_at AS ingested_at, l.cik,
               1000::numeric AS priority, m.additive, true AS gold_line
        FROM (
            SELECT s.company_id, s.metric, s.section_key, s.table_no, s.row_no, s.col_no, s.unit,
                   s.period_start, s.period_end, s.value, s.accession_no, s.form, s.filed,
                   s.read_at, s.cik
            FROM release_sized s
            UNION ALL
            SELECT r.company_id, r.metric, r.section_key, r.table_no, r.row_no, r.col_no, r.unit,
                   r.period_start, r.period_end, r.value, r.accession_no, r.form, r.filed,
                   r.read_at, r.cik
            FROM release_open r
            WHERE r.metric IN ('net_income', 'operating_income')
              AND EXISTS (SELECT 1 FROM release_sized v
                          WHERE v.accession_no = r.accession_no AND v.metric = 'revenue'
                            AND v.period_end = r.period_end AND v.months = r.months)
        ) l
        JOIN silver.metrics m ON m.metric = l.metric
    ),
    lines AS (
        SELECT * FROM tagged
        WHERE gold_line
          -- 155: these come from current_lines
          AND NOT (metric = 'long_term_debt_current' AND taxonomy = 'us-gaap'
                   AND period_start IS NULL)
        UNION ALL SELECT * FROM current_lines
        UNION ALL SELECT * FROM no_debt_lines
        UNION ALL SELECT * FROM summed
        UNION ALL SELECT * FROM loss_eps
        UNION ALL SELECT * FROM release_facts  -- 165
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
                   -- 155: a short-term borrowing or commercial paper part isn't the current portion
                   OR (x.metric = 'long_term_debt_current' AND x.gold_line))
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

-- 3. A company's history replays from its first release's day: gold.knowledge_facts (086's body,
-- the edit marked 165) ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION gold.knowledge_facts(p_company_id text)
RETURNS TABLE (filed date, facts bigint, ids numeric)
LANGUAGE sql STABLE AS $$
    SELECT x.filed, count(*), sum(x.fact_id)
    FROM (
        SELECT f.filed, f.fact_id
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        WHERE k.company_id = p_company_id
        UNION
        SELECT f.filed, f.fact_id
        FROM silver.company_ciks k
        JOIN bronze.filing_xbrl x ON x.entity_cik = k.cik
        JOIN silver.financial_facts f ON f.accession_no = x.accession_no AND f.cik <> x.entity_cik
        WHERE k.company_id = p_company_id
        UNION  -- 165: a release's figures (negative: never a silver fact's id)
        SELECT r.filed, -f.figure_id
        FROM silver.company_ciks k
        JOIN silver.release_reads r ON r.cik = k.cik
        JOIN silver.release_figures f ON f.accession_no = r.accession_no
        WHERE k.company_id = p_company_id
    ) x
    GROUP BY x.filed
$$;

-- 4. A release read makes the company's gold due: ops.company_backlog (086's body, the edit marked
-- 165) -------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ops.company_backlog(p_kind text)
RETURNS TABLE (company_id text, built_at timestamptz, failed boolean)
LANGUAGE sql STABLE AS $$
    SELECT c.company_id, b.built_at,
           b.failed_version IS NOT DISTINCT FROM ops.company_rule_version(p_kind)
    FROM silver.companies c
    JOIN ops.catchup k ON k.kind = p_kind
    LEFT JOIN ops.company_builds b ON b.kind = p_kind AND b.company_id = c.company_id
    WHERE (coalesce(b.version, k.baseline) < ops.company_rule_version(p_kind)
           -- 074
           OR (p_kind = 'check_financials' AND EXISTS (
                  SELECT 1 FROM silver.company_ciks x
                  JOIN silver.statement_reads r ON r.cik = x.cik
                  WHERE x.company_id = c.company_id
                    AND r.read_at > coalesce(b.built_at, '-infinity'::timestamptz)))
           -- 165: a results release read since the company's gold was built
           OR (p_kind = 'gold' AND EXISTS (
                  SELECT 1 FROM silver.company_ciks x
                  JOIN silver.release_reads r ON r.cik = x.cik
                  WHERE x.company_id = c.company_id
                    AND r.read_at > coalesce(b.built_at, '-infinity'::timestamptz)))
           -- 086
           OR (p_kind = 'known' AND NOT EXISTS (
                  SELECT 1 FROM gold.known_companies kc WHERE kc.company_id = c.company_id)))
      AND (p_kind NOT IN ('check_financials', 'known') OR c.company_id NOT IN (  -- 086
              SELECT g.company_id FROM ops.company_backlog('gold') g WHERE NOT g.failed))
$$;

-- 5. Coverage: a commodity pool's history and latest figures are out of scope, as its missing
-- metrics are (081): ops.coverage_found (081's body, the edits marked 165; header, 5) --------------
CREATE OR REPLACE FUNCTION ops.coverage_found()
RETURNS TABLE (company_id text, kind text, cause text, reason text, detail jsonb)
LANGUAGE sql STABLE AS $$
    WITH u AS MATERIALIZED (
        SELECT c.company_id, c.sic, c.name
        FROM silver.companies c
        WHERE c.exchanges && ARRAY['Nasdaq', 'NYSE', 'CBOE', 'TXSE']::text[]
    ),
    ck AS MATERIALIZED (
        SELECT k.cik, k.company_id FROM silver.company_ciks k
        WHERE k.company_id IN (SELECT u.company_id FROM u)
    ),
    -- the periodic reports and successor-issuer filings of every listed company's CIKs
    pf AS MATERIALIZED (
        SELECT ck.company_id, f.cik, f.accession_no, f.form, f.filing_date, f.report_date,
               f.is_xbrl
        FROM ck JOIN bronze.submission_filings f ON f.cik = ck.cik
        WHERE f.form IN ('10-K', '10-Q', '20-F', '40-F', '8-K12B', '8-K12G3')
    ),
    rep AS MATERIALIZED (
        SELECT pf.company_id,
               min(pf.filing_date) FILTER (WHERE pf.form IN ('10-K', '10-Q')) AS first_10x,
               max(pf.filing_date) FILTER (WHERE pf.form IN ('10-K', '10-Q')) AS last_10x,
               min(pf.filing_date) FILTER (WHERE pf.form IN ('10-K', '10-Q') AND pf.is_xbrl)
                   AS first_10x_xbrl,
               count(*) FILTER (WHERE pf.form IN ('10-K', '10-Q') AND pf.is_xbrl
                                  AND pf.filing_date >= current_date - 730) AS n_10x_xbrl,
               max(pf.filing_date) FILTER (WHERE pf.form IN ('20-F', '40-F')) AS last_foreign,
               bool_or(pf.is_xbrl) FILTER (WHERE pf.form IN ('20-F', '40-F')) AS foreign_xbrl
        FROM pf GROUP BY pf.company_id
    ),
    -- core figures: each metric's first and last quarter and last TTM (probes on gold's keys)
    fig AS MATERIALIZED (
        SELECT u.company_id, m.metric, q.first_end, q.last_end, q.n, t.first_ttm, t.last_ttm
        FROM u
        CROSS JOIN (VALUES ('revenue'), ('net_income'), ('total_assets'),
                           ('stockholders_equity'), ('operating_cash_flow')) m(metric)
        CROSS JOIN LATERAL (
            SELECT min(g.period_end) AS first_end, max(g.period_end) AS last_end, count(*) AS n
            FROM gold.quarterly_financials g
            WHERE g.company_id = u.company_id AND g.metric = m.metric) q
        CROSS JOIN LATERAL (
            SELECT min(d.period_end) AS first_ttm, max(d.period_end) AS last_ttm  -- 081
            FROM gold.derived_financials d
            WHERE d.company_id = u.company_id AND d.span = 'ttm' AND d.metric = m.metric) t
    ),
    figs AS MATERIALIZED (
        SELECT fig.company_id,
               -- where the figures start: the first quarter of a flow (an equity statement's
               -- opening balance reaches years before the first income statement)
               min(fig.first_end) FILTER (WHERE fig.metric IN ('revenue', 'net_income',
                                                               'operating_cash_flow')) AS first_any,
               min(fig.first_end) FILTER (WHERE fig.metric = 'revenue') AS first_revenue,
               -- 081: revenue's first year (a TTM row: four quarters or a stated year)
               min(fig.first_ttm) FILTER (WHERE fig.metric = 'revenue') AS first_revenue_year,
               min(fig.first_end) FILTER (WHERE fig.metric = 'net_income') AS first_net_income,
               greatest(max(fig.last_end), max(fig.last_ttm)) AS last_any,
               EXISTS (SELECT 1 FROM gold.quarterly_financials g WHERE g.company_id = fig.company_id)
               OR EXISTS (SELECT 1 FROM gold.derived_financials d WHERE d.company_id = fig.company_id)
                   AS any_figures
        FROM fig GROUP BY fig.company_id
    ),

    -- no_figures -------------------------------------------------------------------------------
    nofig AS (
        SELECT u.company_id, u.sic, u.name, r.first_10x, r.last_10x, r.n_10x_xbrl,
               r.first_10x_xbrl, r.last_foreign, r.foreign_xbrl,
               (SELECT max(z.filing_date) FROM ck JOIN bronze.submission_filings z ON z.cik = ck.cik
                WHERE ck.company_id = u.company_id
                  AND z.form IN ('N-CSR', 'N-CSRS', 'NPORT-P', 'N-PORT', 'N-Q', '485BPOS', '497',
                                 'N-CEN', 'N-2', 'N-1A', '497K', '24F-2NT', 'N-30D')) AS last_fund,
               (SELECT max(z.filing_date) FROM ck JOIN bronze.submission_filings z ON z.cik = ck.cik
                WHERE ck.company_id = u.company_id AND z.form IN ('10-D', '10-D/A', 'ABS-15G'))
                   AS last_abs,
               (SELECT min(z.filing_date) FROM ck JOIN bronze.submission_filings z ON z.cik = ck.cik
                WHERE ck.company_id = u.company_id) AS first_filing,
               -- the latest 10-K/10-Q is also another company's filing (a parent's combined report)
               (SELECT o.company_id
                FROM pf p
                JOIN bronze.submission_filings z ON z.accession_no = p.accession_no AND z.cik <> p.cik
                JOIN silver.company_ciks o ON o.cik = z.cik AND o.company_id <> p.company_id
                WHERE p.company_id = u.company_id AND p.form IN ('10-K', '10-Q')
                ORDER BY p.filing_date DESC LIMIT 1) AS parent
        FROM u
        JOIN figs fg ON fg.company_id = u.company_id AND NOT fg.any_figures
        LEFT JOIN rep r ON r.company_id = u.company_id
    ),
    nofig_found AS (
        SELECT n.company_id, 'no_figures'::text AS kind, x.cause,
               CASE WHEN x.cause IN ('fund', 'abs', 'filed_with_parent') THEN 'out_of_scope'
                    WHEN x.cause IN ('foreign') AND n.foreign_xbrl THEN 'fixable'
                    WHEN x.cause IN ('awaiting', 'other') THEN 'fixable'
                    ELSE 'not_in_sec_data' END AS reason,
               jsonb_strip_nulls(jsonb_build_object(
                   'last_10x', n.last_10x, 'last_foreign', n.last_foreign,
                   'parent', CASE WHEN x.cause = 'filed_with_parent' THEN n.parent END,
                   'first_filing', n.first_filing)) AS detail
        FROM nofig n
        CROSS JOIN LATERAL (SELECT CASE
            WHEN n.last_fund IS NOT NULL THEN 'fund'
            WHEN n.last_abs IS NOT NULL OR n.sic = '6189' THEN 'abs'
            WHEN n.parent IS NOT NULL THEN 'filed_with_parent'
            WHEN n.last_foreign IS NOT NULL AND n.last_foreign >= coalesce(n.last_10x, '-infinity')
                THEN 'foreign'
            WHEN n.last_10x IS NOT NULL AND n.last_10x < current_date - 400 THEN 'stopped'
            WHEN n.last_10x IS NOT NULL AND n.n_10x_xbrl = 0 THEN 'no_xbrl'
            WHEN n.first_10x_xbrl >= current_date - 30 THEN 'awaiting'
            WHEN n.last_10x IS NOT NULL THEN 'other'
            WHEN n.first_filing < current_date - 365
                 AND (n.sic IN ('6021', '6022', '6029', '6035', '6036') OR n.name ~* '\mbank\M')
                THEN 'bank_regulator'
            WHEN n.first_filing >= current_date - 548 THEN 'no_reports_yet'
            ELSE 'no_sec_reports' END AS cause) x
    ),

    -- figures_behind ---------------------------------------------------------------------------
    latest AS (
        SELECT DISTINCT ON (pf.company_id) pf.company_id, pf.accession_no, pf.form,
               pf.filing_date, pf.report_date, pf.is_xbrl, pf.form IN ('20-F', '40-F') AS foreign_form
        FROM pf
        WHERE pf.form IN ('10-K', '10-Q', '20-F', '40-F') AND pf.report_date IS NOT NULL
          AND pf.filing_date <= current_date - 7
        ORDER BY pf.company_id, pf.filing_date DESC, pf.accession_no DESC
    ),
    behind_found AS (
        SELECT l.company_id, 'figures_behind'::text AS kind, x.cause,
               CASE WHEN x.cause = 'no_xbrl' OR (x.cause = 'foreign' AND NOT l.is_xbrl)
                    THEN 'not_in_sec_data'
                    WHEN x.cause IN ('filed_with_parent', 'trust') THEN 'out_of_scope'  -- 165
                    ELSE 'fixable' END AS reason,
               jsonb_build_object('accession_no', l.accession_no, 'form', l.form,
                                  'filed', l.filing_date, 'period_end', l.report_date,
                                  'figures_to', fg.last_any,
                                  -- the SEC's companyfacts carries the filing (silver has its facts)
                                  'in_companyfacts', EXISTS (
                                      SELECT 1 FROM silver.financial_facts s
                                      WHERE s.accession_no = l.accession_no)) AS detail
        FROM latest l
        JOIN figs fg ON fg.company_id = l.company_id AND fg.any_figures AND fg.last_any IS NOT NULL
        CROSS JOIN LATERAL (SELECT CASE
            -- 165: a commodity pool or trust (SIC 6221), as above
            WHEN EXISTS (SELECT 1 FROM u WHERE u.company_id = l.company_id AND u.sic = '6221')
                THEN 'trust'
            WHEN l.foreign_form THEN 'foreign'
            WHEN NOT l.is_xbrl THEN 'no_xbrl'
            -- the filing is also another company's (a parent's combined report): gold reads it
            -- as the company its XBRL names (050)
            WHEN EXISTS (SELECT 1 FROM bronze.submission_filings z
                         JOIN silver.company_ciks o ON o.cik = z.cik
                         WHERE z.accession_no = l.accession_no AND o.company_id <> l.company_id)
                THEN 'filed_with_parent'
            WHEN EXISTS (SELECT 1 FROM silver.financial_facts s WHERE s.accession_no = l.accession_no)
                THEN 'pipeline'
            ELSE 'companyfacts_lacks' END AS cause) x
        WHERE l.report_date > fg.last_any + 45
          AND l.filing_date >= current_date - 500
          AND (NOT l.foreign_form OR l.filing_date <= current_date - 30)
    ),

    -- re_registration --------------------------------------------------------------------------
    moves AS (
        SELECT pf.company_id, pf.cik, min(pf.filing_date) AS moved_on,
               (array_agg(pf.form ORDER BY pf.filing_date))[1] AS form
        FROM pf WHERE pf.form IN ('8-K12B', '8-K12G3')
        GROUP BY pf.company_id, pf.cik
    ),
    rereg AS (
        SELECT DISTINCT ON (m.company_id) m.company_id, m.cik, m.moved_on, m.form, fg.first_any
        FROM moves m
        JOIN figs fg ON fg.company_id = m.company_id
        WHERE m.moved_on >= '2009-06-01' AND fg.first_any >= m.moved_on - 400
          AND NOT EXISTS (SELECT 1 FROM pf p
                          WHERE p.company_id = m.company_id AND p.cik <> m.cik
                            AND p.form IN ('10-K', '10-Q', '20-F', '40-F')
                            AND p.filing_date <= m.moved_on + 120)
        ORDER BY m.company_id, m.moved_on DESC
    ),
    rereg_found AS (
        SELECT r.company_id, 're_registration'::text AS kind, 'unlinked'::text AS cause,
               'fixable'::text AS reason,
               jsonb_build_object('cik', r.cik, 'form', r.form, 'moved_on', r.moved_on,
                                  'figures_from', r.first_any) AS detail
        FROM rereg r
    ),

    -- history_short ----------------------------------------------------------------------------
    short_found AS (
        SELECT fg.company_id, 'history_short'::text AS kind, x.cause,
               -- each is in the SEC's data (a filing's XBRL): Tributary's to read; 081: but a
               -- revenue tagged only in the 10-K, the quarters under no standard revenue tag, or
               -- net sales under the company's own tag (the standard one holds sales before excise
               -- taxes); and a predecessor's filings aren't this business's
               CASE WHEN x.cause IN ('revenue_yearly', 'revenue_excise') THEN 'not_in_sec_data'
                    WHEN x.cause IN ('revenue_predecessor', 'trust') THEN 'out_of_scope'  -- 165
                    ELSE 'fixable' END AS reason,
               jsonb_strip_nulls(jsonb_build_object(
                   'first_xbrl_report', r.first_10x_xbrl, 'figures_from', fg.first_any,
                   'revenue_from', fg.first_revenue,
                   'revenue_years_from', CASE WHEN x.cause = 'revenue_yearly'  -- 081
                                              THEN fg.first_revenue_year END,
                   'moved_on', (SELECT rr.moved_on FROM rereg rr WHERE rr.company_id = fg.company_id)))
                   AS detail
        FROM figs fg
        JOIN rep r ON r.company_id = fg.company_id
        CROSS JOIN LATERAL (SELECT
            fg.first_any > greatest(r.first_10x_xbrl, '2009-06-30'::date) + 400 AS late_figures,
            fg.first_revenue > fg.first_net_income + 400 AS late_revenue) t
        CROSS JOIN LATERAL (SELECT CASE
            -- 165: a commodity pool or trust (SIC 6221, 081's metric_missing rule): its figures
            -- are net assets and gains on what it holds, not the statements Tributary reads
            WHEN EXISTS (SELECT 1 FROM u WHERE u.company_id = fg.company_id AND u.sic = '6221')
                THEN 'trust'
            WHEN EXISTS (SELECT 1 FROM rereg rr WHERE rr.company_id = fg.company_id)
                THEN 're_registration'
            -- 081: an industry total 080 never reads at this company (silver.industry_total_
            -- exclusions: sales before excise taxes, a predecessor's figures)
            WHEN NOT t.late_figures AND EXISTS (
                    SELECT 1 FROM silver.industry_total_exclusions e
                    WHERE e.company_id = fg.company_id AND e.cause = 'excise')
                THEN 'revenue_excise'
            WHEN NOT t.late_figures AND EXISTS (
                    SELECT 1 FROM silver.industry_total_exclusions e
                    WHERE e.company_id = fg.company_id AND e.cause = 'predecessor')
                THEN 'revenue_predecessor'
            -- 081: revenue read for years (stated in 10-Ks) long before its first quarter
            WHEN NOT t.late_figures AND fg.first_revenue_year <= fg.first_revenue - 400
                THEN 'revenue_yearly'
            WHEN NOT t.late_figures THEN 'revenue_tag'
            -- silver has earlier facts gold doesn't use: a gold rule, else companyfacts starts late
            WHEN EXISTS (SELECT 1 FROM ck JOIN silver.financial_facts s ON s.cik = ck.cik
                         WHERE ck.company_id = fg.company_id
                           AND s.metric IN ('revenue', 'net_income', 'total_assets')
                           AND s.period_end < fg.first_any - 200
                           AND s.form NOT LIKE 'DEF%')
                THEN 'companyfacts_gold'
            ELSE 'companyfacts' END AS cause) x
        WHERE fg.any_figures AND r.first_10x_xbrl IS NOT NULL
          AND (t.late_figures OR t.late_revenue)
    ),

    -- metric_missing ---------------------------------------------------------------------------
    missing AS (
        SELECT f.company_id, f.metric, greatest(f.last_end, f.last_ttm) AS last_end,
               EXISTS (SELECT 1 FROM ck JOIN silver.financial_facts s ON s.cik = ck.cik
                       -- 081: under a tag gold reads as the line, not a sum's part (a biotech's
                       -- net interest income on its cash is stored under revenue for banks' sum)
                       JOIN silver.metric_tags mt ON mt.metric = s.metric AND mt.tag = s.tag
                                                 AND mt.taxonomy = s.taxonomy AND mt.gold_line
                       WHERE ck.company_id = f.company_id AND s.metric = f.metric
                         AND s.period_end > greatest(f.last_end, f.last_ttm) + 45
                         AND s.form NOT LIKE 'DEF%') AS in_silver
        FROM fig f
        JOIN figs fg ON fg.company_id = f.company_id
        WHERE f.n >= 4 AND greatest(f.last_end, f.last_ttm) < fg.last_any - 400
    ),
    missing_found AS (
        SELECT m.company_id, 'metric_missing'::text AS kind, x.cause,
               CASE WHEN x.cause = 'trust' THEN 'out_of_scope' ELSE 'fixable' END AS reason,
               m.detail
        FROM (SELECT m.company_id, bool_or(m.in_silver) AS in_silver,
                     bool_and(m.metric = 'stockholders_equity') AS equity_only,
                     jsonb_build_object('metrics', jsonb_agg(jsonb_build_object(
                         'metric', m.metric, 'last', m.last_end) ORDER BY m.metric)) AS detail
              FROM missing m GROUP BY m.company_id) m
        JOIN u ON u.company_id = m.company_id
        CROSS JOIN LATERAL (SELECT CASE
            -- 081: a commodity pool or trust (SIC 6221) states net assets: no balance-sheet totals
            -- or cash flow statement
            WHEN u.sic = '6221' THEN 'trust'
            WHEN m.in_silver THEN 'gold_dropped'
            -- 081: a partnership's or LLC's equity, filed as partners' or members' capital
            WHEN m.equity_only
                 AND u.name ~* '(\mL\.?\s?P\.?$|\mpartners\M|\mLLC$|\mL\.L\.C\.$)'
                THEN 'partners_capital'
            ELSE 'not_read' END AS cause) x
    ),

    -- documents_missing ------------------------------------------------------------------------
    docs AS (
        SELECT pf.company_id,
               count(*) AS expected,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM bronze.filing_documents d
                                              WHERE d.accession_no = pf.accession_no)) AS stored,
               max(pf.filing_date) AS last_filed
        FROM pf
        WHERE pf.form IN ('10-K', '10-Q') AND pf.filing_date >= '2016-01-01'
          AND pf.filing_date <= current_date - 7
        GROUP BY pf.company_id
    ),
    docs_found AS (
        SELECT d.company_id, 'documents_missing'::text AS kind,
               CASE WHEN d.stored = 0 THEN 'none' ELSE 'partial' END AS cause,
               CASE WHEN fg.any_figures THEN 'fixable' ELSE 'out_of_scope' END AS reason,
               jsonb_build_object('expected', d.expected, 'stored', d.stored,
                                  'last_filed', d.last_filed) AS detail
        FROM docs d
        JOIN figs fg ON fg.company_id = d.company_id
        WHERE d.stored = 0 OR (d.expected >= 8 AND d.stored < d.expected / 2)
    ),

    -- statements_exhibit -----------------------------------------------------------------------
    last_10k AS (
        SELECT DISTINCT ON (pf.company_id) pf.company_id, pf.accession_no, pf.filing_date
        FROM pf
        WHERE pf.form = '10-K' AND pf.filing_date >= current_date - 500
          AND EXISTS (SELECT 1 FROM bronze.filing_documents d WHERE d.accession_no = pf.accession_no)
        ORDER BY pf.company_id, pf.filing_date DESC, pf.accession_no DESC
    ),
    ex_found AS (
        SELECT l.company_id, 'statements_exhibit'::text AS kind,
               'ex13'::text AS cause,
               'fixable'::text AS reason,
               jsonb_build_object('accession_no', l.accession_no, 'filed', l.filing_date) AS detail
        FROM last_10k l
        CROSS JOIN LATERAL (
            SELECT x.text, x.char_end - x.char_start AS length
            FROM silver.filing_sections x
            WHERE x.accession_no = l.accession_no AND x.section_key = '8'
            ORDER BY x.parser = 'rust' DESC LIMIT 1) s
        WHERE s.length < 1500
          AND s.text ~* '(exhibit\s*13|annual\s+report\s+to\s+(share|stock)holders)'
          -- doc-fetcher stores a 10-K's EX-13 since 076: then the statements are here
          AND NOT EXISTS (SELECT 1 FROM bronze.filing_exhibits e
                          WHERE e.accession_no = l.accession_no AND e.exhibit_type ~ '^EX-13')
    )

    SELECT * FROM nofig_found
    UNION ALL SELECT * FROM behind_found
    UNION ALL SELECT * FROM rereg_found
    UNION ALL SELECT * FROM short_found
    UNION ALL SELECT * FROM missing_found
    UNION ALL SELECT * FROM docs_found
    UNION ALL SELECT * FROM ex_found
$$;

-- 6. The rules' version, and the companies it provably doesn't touch (header, 4) -----------------
CREATE OR REPLACE FUNCTION gold.rules_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 165';

-- 165's rules differ from 155's only by silver.release_figures, empty here: for every company
-- gold.usable_facts and gold.knowledge_facts give what they gave, at every as-of date. A company
-- built at 155 is built at 165; one behind stays behind. A company's release reads, as they land,
-- make it due (4): its gold is rebuilt and its history replayed from its first release's day.
UPDATE ops.company_builds SET version = 165 WHERE kind IN ('gold', 'known') AND version = 155;
