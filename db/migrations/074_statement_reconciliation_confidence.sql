-- Data trust, part 2: an independent reconciliation against each filing's own statement tables, and a
-- confidence mark per company (db/DESIGN.md "074").
--
-- 1. Statement lines. parser-index (filing_parser/statements.py, READER statements-1, its docstring is
--    the spec) reads each 10-K's and 10-Q's v7 tables (silver.filing_tables, 067) independently of
--    the XBRL gold is built from: revenue, net income, total assets and total liabilities and equity
--    as printed in the current period's column, times the scale words of the table ("in millions").
--    silver.statement_lines holds every candidate (a line can print in the statement and in MD&A);
--    silver.statement_reads one row per filing read (the reader's version, when).
-- 2. statement_mismatch (ops.check_financials, warning, tier "to double-check"): a filing's own
--    statement prints another headline figure than the one gold holds and the filing states
--    (ops.statement_comparisons). Compared: total assets and total liabilities and equity with gold's
--    total assets at the period end, a 10-Q's income lines with gold's quarter, a 10-K's with gold's
--    TTM. Agreement: gold equals any candidate of the line (the titled tables' when the filing has
--    any) within one printed unit (two for a 10-K's revenue and net income: gold's year is four
--    quarters, each rounded as filed); a line with no scale words agrees at any of 1, 1,000, 10^6,
--    10^9. A finding needs gold's figure to be one this filing states (a fact of it, same metric and
--    period, equal: a figure a later filing restated, ASU 2015-03's debt costs or FedEx Freight's
--    separation, differs from the original by design) and an unambiguous print: the first titled
--    candidate in the statements' home (a 10-K's Item 8, 15 or 16, a 10-Q's Part I Item 1), with a
--    scale, alone in its row. Detail: rule (scale: 1,000x apart; sign; differs), line, factor (gold
--    over printed), the filing; never values (014's rule).
--    Measured, read-only: hermes' 300 10-K/10-Qs with v7 tables (2026-11-16: 136 of 2026, 164 of 2015
--    re-parsed by the catch-up) and the 97 latest 10-Ks of tests/golden's reference set parsed
--    locally. On hermes, where gold's figure is the filing's own: total assets 93 agree, total
--    liabilities and equity 87, revenue 85, net income 98; 4 findings (1.1% of 367): Tetra Tech's
--    2015 TTM read from ProfitLoss where the statement prints net income attributable to it (gold's
--    tag choice), Helmerich & Payne's FY2015 NetIncomeLoss 420.4M against its printed NET INCOME
--    422.2M (the filing's XBRL against its print), and two of the reader's (D.R. Horton's segment
--    "Revenues" rows above its total; a 10-Q whose attributable line it didn't find). 78 more differ
--    where gold holds a later filing's restated figure (no finding); 3 are unclear prints (no
--    finding); not read 17-37 per line (statements in an exhibit, an unusual header), no gold yet
--    141-176 (filed that day: companyfacts lags a day). On the 97 10-Ks the reader agrees with the
--    filing's own XBRL on 87 of 88 total assets, 85 of 85 total liabilities and equity, 79 of 83
--    revenue, 83 of 84 net income (the rest: a broker's net revenue beside its gross, Deere's
--    consolidating columns, a BDC).
-- 3. A confidence mark per company, high / medium / low, with its reasons in plain words:
--    gold.company_confidence(company, sic, latest period), read time, plain SQL, inlinable (the
--    035 readings family: the Query API's screener rows and so the company header, under RLS).
--    Its inputs are stored by the check in ops.quality_companies (statements_checked/_agreed,
--    core_figures: how many of revenue, net income, total assets, equity, cash and operating cash
--    flow gold has at the latest period, entity: ops.entity_kind) beside 072's errors, warnings and
--    figures; the latest period, the currency and today are read at read time. Rules and their
--    measurement in the function's header below.
-- 4. Catch-up: ops.check_financials_version() 72 -> 74 (every company re-checked, 071); a company's
--    check is also due when a filing of it was read after its last check (ops.company_backlog), so
--    statement lines reach the findings within the hour. parser-index reads the filings it indexes;
--    its catch-up (kind 'statements', ops.statement_backlog) every stored 10-K/10-Q with tables once
--    per READER.
--
-- Locks on hermes (lock_timeout 3 s): new tables, functions and a catch-up row; ALTER TABLE
-- ops.quality_companies ADD COLUMN x4 (no default: catalog only, ACCESS EXCLUSIVE for an instant;
-- the Query API's data notes read it), the CHECK on ops.catchup replaced (7 rows), CREATE OR REPLACE
-- of ops.check_financials, its version, ops.company_backlog and the view ops.quality_tiers (catalog).
-- Nothing is rebuilt: findings change as the catch-up re-checks each company. Undo: 072's
-- ops.check_financials (version 72), 071's ops.company_backlog and 072's ops.quality_tiers; drop what
-- this file creates (the columns may stay).

SET LOCAL lock_timeout = '3s';

-- 1. Statement lines ------------------------------------------------------------------------------

-- One row per filing read (filing_parser.statements, by parser-index), lines or none.
CREATE TABLE silver.statement_reads (
    accession_no text        PRIMARY KEY,
    cik          text        NOT NULL,
    form         text        NOT NULL,
    period       date        NOT NULL,  -- the filing's period of report
    filed        date        NOT NULL,
    reader       text        NOT NULL,  -- statements.READER
    lines        integer     NOT NULL,
    read_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX statement_reads_cik ON silver.statement_reads (cik, read_at);

-- Every candidate of a line in the current period's column (statements.py rules 1-6). Filing text
-- and the numbers as printed; no grant: only ops.check_financials reads it.
CREATE TABLE silver.statement_lines (
    accession_no text    NOT NULL REFERENCES silver.statement_reads ON DELETE CASCADE,
    line         text    NOT NULL
        CHECK (line IN ('revenue', 'net_income', 'total_assets', 'liabilities_and_equity')),
    n            integer NOT NULL,  -- in the filing's table order
    label        text    NOT NULL,
    value        numeric NOT NULL,  -- as printed, times the scale (1 without one)
    scale        bigint,            -- NULL: no scale words by the table
    period_end   date,              -- the column's date, when its head has one
    months       integer,           -- the column's duration, when its head says one
    section_key  text    NOT NULL,
    table_no     integer NOT NULL,
    row_no       integer NOT NULL,
    titled       boolean NOT NULL,  -- a statement's title by the table (rule 6)
    loss_label   boolean NOT NULL,  -- "Net loss": read both ways
    PRIMARY KEY (accession_no, line, n)
);

-- The confidence mark's stored inputs (header, 3)
ALTER TABLE ops.quality_companies
    ADD COLUMN statements_checked integer,  -- 074: statement lines compared with gold
    ADD COLUMN statements_agreed  integer,  -- 074: ... and agreeing
    ADD COLUMN statements_differ_at date,   -- 074: the latest period a statement_mismatch is at
    ADD COLUMN core_figures       integer,  -- 074: of the six core figures at the latest period
    ADD COLUMN entity             text;     -- 074: ops.entity_kind

-- Each filing's statement lines against the figure gold holds for the period (header, 2): one row
-- per company, filing and line.
--   own     gold's figure is one this filing states: a fact of the filing of the metric and period
--           equal to it (a 10-K's TTM: the filing's annual fact within two printed units). A figure
--           a later filing restated isn't the filing's own and is never a finding.
--   agree   gold equals a candidate of the line (statements.py rule 6: the titled tables' when the
--           filing has any)
--   clear   the print to compare with is unambiguous: the first titled candidate in a statement's
--           home (a 10-K's Item 8, 15 or 16, a 10-Q's Part I Item 1) with a scale, alone in its row
--   printed, scale, rule: that candidate, and how gold differs from it (scale, sign, differs)
CREATE FUNCTION ops.statement_comparisons(p_company_ids text[])
RETURNS TABLE (company_id text, accession_no text, form text, filed date, period date, line text,
               gold_end date, gold numeric, own boolean, agree boolean, clear boolean,
               printed numeric, scale bigint, rule text)
LANGUAGE sql STABLE AS $$
    WITH s AS MATERIALIZED (
        SELECT k.company_id, r.accession_no, r.form, r.filed, r.period, l.line, l.n, l.value,
               l.scale, l.titled, l.loss_label, l.section_key, l.table_no, l.row_no,
               count(*) OVER (PARTITION BY l.accession_no, l.line, l.section_key, l.table_no,
                                           l.row_no) AS in_row
        FROM silver.company_ciks k
        JOIN silver.statement_reads r ON r.cik = k.cik
        JOIN silver.statement_lines l ON l.accession_no = r.accession_no
        WHERE k.company_id = ANY(p_company_ids)
    ),
    candidates AS (
        SELECT s.* FROM s
        WHERE s.titled OR NOT EXISTS (SELECT 1 FROM s t WHERE t.accession_no = s.accession_no
                                                         AND t.line = s.line AND t.titled)
    ),
    lines AS (
        SELECT c.company_id, c.accession_no, c.form, c.filed, c.period, c.line,
               coalesce(max(c.scale), 1) AS unit
        FROM candidates c
        GROUP BY c.company_id, c.accession_no, c.form, c.filed, c.period, c.line
    ),
    gold AS (
        SELECT l.*, g.period_end AS gold_end, g.value AS gold, g.own
        FROM lines l
        CROSS JOIN LATERAL (
            (SELECT q.period_end, q.value,
                    EXISTS (SELECT 1 FROM silver.financial_facts f
                            WHERE f.accession_no = l.accession_no AND f.metric = 'total_assets'
                              AND f.period_end = q.period_end AND f.period_start IS NULL
                              AND abs(f.value - q.value) <= 0.5) AS own
             FROM gold.quarterly_financials q
             WHERE l.line IN ('total_assets', 'liabilities_and_equity')
               AND q.company_id = l.company_id AND q.metric = 'total_assets'
               AND q.period_end BETWEEN l.period - 3 AND l.period + 3
             ORDER BY abs(q.period_end - l.period) LIMIT 1)
            UNION ALL
            (SELECT q.period_end, q.value,
                    EXISTS (SELECT 1 FROM silver.financial_facts f
                            WHERE f.accession_no = l.accession_no AND f.metric = l.line
                              AND f.period_end = q.period_end AND f.period_start = q.period_start
                              AND abs(f.value - q.value) <= 0.5)
             FROM gold.quarterly_financials q
             WHERE l.line IN ('revenue', 'net_income') AND l.form = '10-Q'
               AND q.company_id = l.company_id AND q.metric = l.line
               AND q.period_end BETWEEN l.period - 7 AND l.period + 7
               AND q.period_end - q.period_start BETWEEN 80 AND 120
             ORDER BY abs(q.period_end - l.period) LIMIT 1)
            UNION ALL
            (SELECT d.period_end, d.value,
                    EXISTS (SELECT 1 FROM silver.financial_facts f
                            WHERE f.accession_no = l.accession_no AND f.metric = l.line
                              AND f.period_type = 'annual'
                              AND f.period_end BETWEEN d.period_end - 7 AND d.period_end + 7
                              AND abs(f.value - d.value) <= 2 * l.unit)
             FROM gold.derived_financials d
             WHERE l.line IN ('revenue', 'net_income') AND l.form = '10-K'
               AND d.company_id = l.company_id AND d.span = 'ttm' AND d.metric = l.line
               AND d.period_end BETWEEN l.period - 7 AND l.period + 7
             ORDER BY abs(d.period_end - l.period) LIMIT 1)
        ) g
    ),
    judged AS (
        SELECT g.company_id, g.accession_no, g.form, g.filed, g.period, g.line, g.gold_end,
               g.gold, g.own, c.n, c.value, c.scale, c.in_row,
               c.titled AND c.scale IS NOT NULL
                   AND c.section_key IN ('8', '15', '16', 'I-1') AS home,
               CASE WHEN c.scale IS NULL
                    THEN EXISTS (SELECT 1 FROM unnest('{1,1000,1000000,1000000000}'::numeric[]) k
                                 WHERE abs(g.gold - c.value * k) <= k / 2
                                    OR (c.loss_label AND abs(g.gold + c.value * k) <= k / 2))
                    ELSE abs(g.gold - c.value) <= c.scale * u.units
                         OR (c.loss_label AND abs(g.gold + c.value) <= c.scale * u.units) END AS agree
        FROM gold g
        JOIN candidates c ON c.accession_no = g.accession_no AND c.line = g.line
        CROSS JOIN LATERAL (
            SELECT CASE WHEN g.form = '10-K' AND g.line IN ('revenue', 'net_income') THEN 2
                        ELSE 1 END AS units
        ) u
    ),
    one AS (
        SELECT j.company_id, j.accession_no, j.form, j.filed, j.period, j.line, j.gold_end, j.gold,
               j.own, bool_or(j.agree) AS agree,
               (array_agg(j.in_row = 1 ORDER BY j.n) FILTER (WHERE j.home))[1] AS clear,
               (array_agg(j.value ORDER BY j.n) FILTER (WHERE j.home))[1] AS printed,
               (array_agg(j.scale ORDER BY j.n) FILTER (WHERE j.home))[1] AS scale
        FROM judged j
        GROUP BY j.company_id, j.accession_no, j.form, j.filed, j.period, j.line, j.gold_end, j.gold,
                 j.own
    )
    SELECT o.company_id, o.accession_no, o.form, o.filed, o.period, o.line, o.gold_end, o.gold,
           o.own, o.agree, coalesce(o.clear, false), o.printed, o.scale,
           CASE WHEN o.printed IS NULL OR o.printed = 0 OR o.agree THEN NULL
                WHEN abs(abs(o.gold / o.printed) - 1000) <= 5
                  OR abs(abs(o.gold / o.printed) - 1000000) <= 5000
                  OR abs(abs(o.printed / nullif(o.gold, 0)) - 1000) <= 5
                  OR abs(abs(o.printed / nullif(o.gold, 0)) - 1000000) <= 5000 THEN 'scale'
                WHEN abs(o.gold + o.printed) <= o.scale THEN 'sign'
                ELSE 'differs' END
    FROM one o
$$;

-- How many of the six core figures gold has at the company's latest period (header, 3): revenue,
-- net income, total assets, stockholders' equity, cash, operating cash flow (a quarter or a TTM: an
-- annual filer's flows are TTM rows only), each within 100 days before the latest period end any of
-- them has.
CREATE FUNCTION ops.core_figures(p_company_id text) RETURNS integer
LANGUAGE sql STABLE AS $$
    WITH core AS (
        SELECT x.metric, max(x.period_end) AS latest
        FROM (SELECT q.metric, q.period_end FROM gold.quarterly_financials q
              WHERE q.company_id = p_company_id
                AND q.metric IN ('revenue', 'net_income', 'total_assets', 'stockholders_equity',
                                 'cash', 'operating_cash_flow')
              UNION ALL
              SELECT d.metric, d.period_end FROM gold.derived_financials d
              WHERE d.company_id = p_company_id AND d.span = 'ttm'
                AND d.metric IN ('revenue', 'net_income', 'operating_cash_flow')) x
        GROUP BY x.metric
    )
    SELECT count(*)::integer FROM core
    WHERE latest >= (SELECT max(latest) FROM core) - 100
$$;

-- What kind of filer the figures come from, where that changes what they can say (header, 3): a
-- blank-check company (SIC 6770), a commodity pool (6221: futures funds and the gold and silver
-- trusts), a royalty trust (6792 oil, 6795 mineral royalty traders), a business development company
-- (an N-54A election on file and no N-54C withdrawal after it); NULL for an operating company.
CREATE FUNCTION ops.entity_kind(p_company_id text, p_sic text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN p_sic = '6770' THEN 'shell'
        WHEN p_sic = '6221' THEN 'commodity_pool'
        WHEN p_sic IN ('6792', '6795') THEN 'royalty_trust'
        WHEN (SELECT max(z.filing_date) FILTER (WHERE z.form = 'N-54A')
              > coalesce(max(z.filing_date) FILTER (WHERE z.form = 'N-54C'), '-infinity')
              FROM silver.company_ciks k
              JOIN bronze.submission_filings z ON z.cik = k.cik
              WHERE k.company_id = p_company_id AND z.form IN ('N-54A', 'N-54C'))
            THEN 'bdc'
    END
$$;

-- 072's body with the edits marked "074" (header, 2).
CREATE OR REPLACE FUNCTION ops.check_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    run bigint;
    n integer;
BEGIN
    INSERT INTO ops.quality_runs (companies)
    SELECT count(DISTINCT id) FROM unnest(p_company_ids) id
    RETURNING run_id INTO run;

    DELETE FROM ops.quality_findings
    WHERE company_id = ANY(p_company_ids) AND check_name NOT LIKE 'segment\_%';

    WITH q AS (
        SELECT g.company_id, g.metric, g.period_start, g.period_end, g.value, m.kind,
               m.additive, g.accession_no,
               g.period_start - lag(g.period_end) OVER w AS gap,
               lag(g.value) OVER w AS prev_value,
               lead(g.value) OVER w AS next_value
        FROM gold.quarterly_financials g
        JOIN silver.metrics m ON m.metric = g.metric
        WHERE g.company_id = ANY(p_company_ids)
        WINDOW w AS (PARTITION BY g.company_id, g.metric ORDER BY g.period_end)
    ),
    runs AS (  -- durations: are the three quarters before this one contiguous?
        SELECT q.*,
               count(*) FILTER (WHERE gap BETWEEN 0 AND 7) OVER (
                   PARTITION BY company_id, metric ORDER BY period_end
                   ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS contiguous_before
        FROM q
        WHERE kind = 'duration'
    ),
    pairs AS (
        SELECT company_id, period_end,
               max(value) FILTER (WHERE metric = 'revenue') AS revenue,
               max(value) FILTER (WHERE metric = 'gross_profit') AS gross_profit,
               max(value) FILTER (WHERE metric = 'current_assets') AS current_assets,
               max(value) FILTER (WHERE metric = 'total_assets') AS total_assets,
               max(value) FILTER (WHERE metric = 'current_liabilities') AS current_liabilities,
               max(value) FILTER (WHERE metric = 'total_liabilities') AS total_liabilities
        FROM q
        GROUP BY company_id, period_end
    ),
    -- 072: what gold believes, once: the annual comparison and the tag check read it
    best AS MATERIALIZED (
        SELECT * FROM gold.best_facts(p_company_ids)
    ),
    annual AS (  -- each reported fiscal year, picked as gold picks its facts
        SELECT b.company_id, b.metric, b.period_start, b.period_end, b.value, b.accession_no,
               b.tag, b.unit, b.filed
        FROM best b
        WHERE b.period_type = 'annual' AND b.additive
    ),
    -- 072: the metric's other gold tags in the believed fact's own filing and period, positive
    -- (interest expense and debt without their sign, as gold reads them)
    tag_facts AS MATERIALIZED (
        SELECT k.company_id, t.metric, f.tag, f.unit, f.period_start, f.period_end,
               f.accession_no,
               CASE WHEN t.metric IN ('interest_expense', 'long_term_debt',
                                      'long_term_debt_current')
                    THEN abs(f.value) ELSE f.value END AS value
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.taxonomy = f.taxonomy AND t.tag = f.tag AND t.gold_line
                                 AND (t.company_id IS NULL OR t.company_id = k.company_id)
        WHERE k.company_id = ANY(p_company_ids) AND f.value <> 0
    ),
    -- 072: two tags of one line in one filing and period that can't both be right: the figure gold
    -- reads under a fifth of a part of it (ops.tag_relations 'part'), two names of one line 5x
    -- apart ('same'), or any two 1,000 or 1,000,000 times apart (one figure in two scales; revenue's
    -- 064 resolves in best_facts, so a revenue pair that far apart is no finding at all). One per
    -- company, metric and period end: the shortest period, then the largest gap.
    disagree AS (
        SELECT DISTINCT ON (b.company_id, b.metric, b.period_end)
               b.company_id, b.metric, b.period_start, b.period_end, b.tag, b.accession_no,
               b.form, b.filed, o.tag AS other_tag, o.value / b.value AS factor,
               CASE WHEN x.scale THEN 'scale'
                    WHEN r.relation = 'same' THEN 'same_line'
                    ELSE 'total_below_part' END AS rule
        FROM best b
        JOIN tag_facts o ON o.company_id = b.company_id AND o.metric = b.metric
                        AND o.accession_no = b.accession_no AND o.unit = b.unit
                        AND o.period_start IS NOT DISTINCT FROM b.period_start
                        AND o.period_end = b.period_end AND o.tag <> b.tag
        LEFT JOIN ops.tag_relations r
          ON r.metric = b.metric
         AND ((r.tag = b.tag AND r.other = o.tag)
              OR (r.relation = 'same' AND r.tag = o.tag AND r.other = b.tag))
        CROSS JOIN LATERAL (
            SELECT greatest(o.value, b.value) / least(o.value, b.value) AS gap
        ) g
        CROSS JOIN LATERAL (
            SELECT abs(g.gap - 1000) <= 5 OR abs(g.gap - 1000000) <= 5000 AS scale
        ) x
        WHERE b.value > 0 AND o.value > 0
          AND CASE WHEN x.scale THEN b.metric <> 'revenue'
                   ELSE (r.relation = 'same' AND g.gap > 5)
                        OR (r.relation = 'part' AND r.tag = b.tag AND b.value * 5 < o.value) END
        ORDER BY b.company_id, b.metric, b.period_end,
                 b.period_end - coalesce(b.period_start, b.period_end), g.gap DESC, o.tag
    ),
    -- 072: quarters whose first filing (same tag, unit, dates) said something else; the
    -- restatement finding, and what explains a year whose quarters don't add up
    restated AS MATERIALIZED (
        SELECT g.company_id, g.metric, g.period_start, g.period_end, g.accession_no, g.filed,
               o.accession_no AS original_accession, o.filed AS original_filed,
               round((g.value - o.value) / abs(o.value), 4) AS change
        FROM gold.quarterly_financials g
        CROSS JOIN LATERAL (
            -- 050: the metrics gold reads without their sign are compared without it
            SELECT f.accession_no, f.filed,
                   CASE WHEN g.metric IN ('interest_expense', 'long_term_debt',
                                          'long_term_debt_current')
                        THEN abs(f.value) ELSE f.value END AS value
            FROM silver.company_ciks k
            JOIN silver.financial_facts f ON f.cik = k.cik
            WHERE k.company_id = g.company_id AND f.metric = g.metric
              AND f.period_end = g.period_end
              AND f.period_start IS NOT DISTINCT FROM g.period_start
              AND f.tag = split_part(g.source_tag, ':', 2) AND f.unit = g.unit
            ORDER BY f.filed, f.accession_no
            LIMIT 1
        ) o
        WHERE g.company_id = ANY(p_company_ids) AND g.value_source = 'reported'
          AND o.filed < g.filed AND o.value <> 0
          AND abs(g.value - o.value) > 0.005 * abs(o.value)
    ),
    -- what gold.usable_facts drops, for the reader: facts of mapped tags that can't be a figure of
    -- the period. Never the value (014's rule for detail).
    mistagged AS (  -- one per company and metric, its latest, with how many periods
        SELECT DISTINCT ON (x.company_id, x.metric) x.*, count(*) OVER (PARTITION BY x.company_id, x.metric) AS periods
        FROM (
            SELECT DISTINCT ON (k.company_id, t.metric, f.period_end)
                   k.company_id, t.metric, f.period_end, f.tag, f.accession_no, f.form, f.filed
            FROM silver.company_ciks k
            JOIN silver.financial_facts f ON f.cik = k.cik
            JOIN silver.metric_tags t ON t.tag = f.tag AND t.gold_line
            WHERE k.company_id = ANY(p_company_ids)
              AND f.period_end > f.filed + 7
              AND coalesce(f.form, '') !~ '^(DEF|PRE)'
            ORDER BY k.company_id, t.metric, f.period_end, f.filed DESC, f.accession_no DESC
        ) x
        ORDER BY x.company_id, x.metric, x.period_end DESC
    ),
    debt_scale AS (  -- a debt fact over five times the same date's total liabilities and total assets
        SELECT DISTINCT ON (k.company_id, t.metric, f.period_end)
               k.company_id, t.metric, f.period_end, f.tag, f.accession_no, f.form, f.filed
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.tag = f.tag AND t.metric IN ('long_term_debt', 'long_term_debt_current')
        JOIN LATERAL (
            SELECT x.value FROM silver.financial_facts x
            WHERE x.cik = f.cik AND x.metric = 'total_liabilities' AND x.tag = 'Liabilities'
              AND x.period_end = f.period_end AND x.period_start IS NULL
              AND x.unit = f.unit  -- 057: in the debt's own currency (header, 4)
            ORDER BY x.filed DESC, x.accession_no DESC LIMIT 1
        ) l ON f.value > l.value * 5
        JOIN LATERAL (
            SELECT x.value FROM silver.financial_facts x
            WHERE x.cik = f.cik AND x.metric = 'total_assets' AND x.tag = 'Assets'
              AND x.period_end = f.period_end AND x.period_start IS NULL
              AND x.unit = f.unit  -- 057
            ORDER BY x.filed DESC, x.accession_no DESC LIMIT 1
        ) a ON f.value > a.value * 5
        WHERE k.company_id = ANY(p_company_ids) AND f.period_start IS NULL
        ORDER BY k.company_id, t.metric, f.period_end, f.filed DESC, f.accession_no DESC
    ),
    -- 050: debt parts adding up to 1,000 or 1,000,000 times the same filing's total for the date
    -- (gold.usable_facts leaves them out); one per company and date, its latest filing
    parts_scale AS (
        SELECT DISTINCT ON (k.company_id, p.period_end)
               k.company_id, p.period_end, p.accession_no, p.form, p.filed,
               round(p.parts / p.total) AS factor
        FROM silver.company_ciks k
        CROSS JOIN LATERAL (
            SELECT f.period_end, f.accession_no, max(f.form) AS form, max(f.filed) AS filed,
                   sum(abs(f.value)) FILTER (WHERE f.tag IN ('LongTermDebtCurrent',
                                                             'LongTermDebtNoncurrent')) AS parts,
                   max(abs(f.value)) FILTER (WHERE f.tag = 'LongTermDebt') AS total
            FROM silver.financial_facts f
            WHERE f.cik = k.cik AND f.metric IN ('long_term_debt', 'long_term_debt_current')
              AND f.period_start IS NULL
              AND f.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent', 'LongTermDebt')
            GROUP BY f.period_end, f.accession_no, f.unit  -- 057: parts and total in one unit
        ) p
        WHERE k.company_id = ANY(p_company_ids) AND p.total > 0
          AND (abs(p.parts / p.total - 1000) <= 5 OR abs(p.parts / p.total - 1000000) <= 5000)
        ORDER BY k.company_id, p.period_end, p.filed DESC, p.accession_no DESC
    ),
    -- net debt left out: the latest balance sheet with debt and cash has no figure (040's rule)
    net_debt_gap AS (
        SELECT b.company_id, b.period_end,
               (SELECT max(s.period_end) FROM q s
                WHERE s.company_id = b.company_id AND s.metric = 'short_term_investments'
                  AND s.period_end < b.period_end AND s.value > 0
                  AND s.period_end >= b.period_end - 730
                  AND NOT EXISTS (SELECT 1 FROM q x
                                  WHERE x.company_id = b.company_id AND x.period_end = b.period_end
                                    AND x.metric = 'short_term_investments')) AS investments_end
        FROM (
            SELECT DISTINCT ON (d.company_id) d.company_id, d.period_end
            FROM q d
            JOIN q c ON c.company_id = d.company_id AND c.metric = 'cash' AND c.period_end = d.period_end
            WHERE d.metric = 'long_term_debt'
            ORDER BY d.company_id, d.period_end DESC
        ) b
        WHERE NOT EXISTS (SELECT 1 FROM gold.derived_financials n
                          WHERE n.company_id = b.company_id AND n.span = 'quarter'
                            AND n.metric = 'net_debt' AND n.period_end = b.period_end)
    ),
    -- the same quarter missing year after year: the 10-K tags the year in a way the 10-Qs don't, so
    -- no fourth quarter and no annual total (NVIDIA's capex, FY2017-21). series_gap needs four contiguous
    -- quarters first, which a hole every fourth quarter never has. One finding, the latest.
    repeated_gap AS (
        SELECT DISTINCT ON (company_id, metric) company_id, metric, period_end, period_start, gap,
               count(*) OVER (PARTITION BY company_id, metric) AS years
        FROM runs
        WHERE additive AND gap > 7 AND contiguous_before = 2
        ORDER BY company_id, metric, period_end DESC
    ),
    -- 074: each filing's statement lines against the figure gold holds for the period
    stmt AS MATERIALIZED (
        SELECT * FROM ops.statement_comparisons(p_company_ids)
    ),
    findings AS (
        SELECT company_id, 'impossible_value' AS check_name, metric, period_end,
               'error' AS severity, jsonb_build_object('rule', 'negative',
                                                       'accession_no', accession_no) AS detail
        FROM q
        WHERE value < 0 AND metric IN (
            'revenue', 'total_assets', 'current_assets', 'total_liabilities',
            'current_liabilities', 'cash', 'short_term_investments', 'inventory',
            'receivables', 'shares_outstanding', 'weighted_shares_basic',
            'weighted_shares_diluted', 'long_term_debt', 'long_term_debt_current')
        UNION ALL
        SELECT company_id, 'impossible_value', r.metric, period_end, 'error',
               jsonb_build_object('rule', r.rule)
        FROM pairs
        CROSS JOIN LATERAL (VALUES
            ('current_assets', 'current_assets > total_assets', current_assets > total_assets),
            ('current_liabilities', 'current_liabilities > total_liabilities',
             current_liabilities > total_liabilities),
            ('gross_profit', 'gross_profit > revenue', revenue > 0 AND gross_profit > revenue)
        ) r(metric, rule, failed)
        WHERE r.failed
        UNION ALL
        SELECT company_id, 'overlapping_quarters', metric, period_end, 'error',
               jsonb_build_object('overlap_days', -gap, 'accession_no', accession_no)
        FROM runs
        WHERE gap < 0
        UNION ALL
        SELECT company_id, 'calendar_collision', metric, max(period_end), 'warning',
               jsonb_build_object('calendar_quarter', to_char(cq, 'YYYY"Q"Q'),
                                  'period_ends', array_agg(period_end ORDER BY period_end))
        FROM (SELECT r.*, gold.calendar_quarter(period_end) AS cq FROM runs r) c
        GROUP BY company_id, metric, cq
        HAVING count(*) > 1
        UNION ALL
        -- 072: a year whose own figure a later filing changed, or with a restated quarter, is
        -- info and says which (restated: year | quarter); otherwise a warning as before
        SELECT d.company_id, 'annual_sum', d.metric, d.period_end,
               CASE WHEN y.restated IS NOT NULL THEN 'info' ELSE 'warning' END,
               jsonb_build_object('deviation', round((d.value - a.value) / abs(a.value), 4),
                                  'annual_accession', a.accession_no,
                                  'quarter_accessions', d.source_accessions)
               || CASE WHEN y.restated IS NOT NULL
                       THEN jsonb_build_object('restated', y.restated) ELSE '{}'::jsonb END
        FROM gold.derived_financials d
        JOIN annual a ON a.company_id = d.company_id AND a.metric = d.metric
                     AND a.period_end = d.period_end
                     AND abs(a.period_start - d.period_start) <= 7
        CROSS JOIN LATERAL (
            SELECT CASE
                WHEN EXISTS (
                    SELECT 1 FROM (
                        SELECT f.filed,
                               CASE WHEN a.metric IN ('interest_expense', 'long_term_debt',
                                                      'long_term_debt_current')
                                    THEN abs(f.value) ELSE f.value END AS value
                        FROM silver.company_ciks k
                        JOIN silver.financial_facts f ON f.cik = k.cik
                        WHERE k.company_id = a.company_id AND f.tag = a.tag
                          AND f.unit = a.unit AND f.period_start = a.period_start
                          AND f.period_end = a.period_end
                        ORDER BY f.filed, f.accession_no
                        LIMIT 1) o
                    WHERE o.filed < a.filed AND o.value <> 0
                      AND abs(a.value - o.value) > 0.005 * abs(o.value)) THEN 'year'
                WHEN EXISTS (SELECT 1 FROM restated r
                             WHERE r.company_id = d.company_id AND r.metric = d.metric
                               AND r.period_end > d.period_start
                               AND r.period_end <= d.period_end) THEN 'quarter'
                END AS restated
        ) y
        WHERE d.company_id = ANY(p_company_ids) AND d.span = 'ttm' AND d.value_source = 'ttm'
          AND a.value <> 0
          AND abs(d.value - a.value) > 0.01 * abs(a.value)
        UNION ALL
        SELECT company_id, 'series_gap', metric, period_end, 'info',  -- 072: coverage
               jsonb_build_object('missing_from', period_start - gap + 1,
                                  'missing_to', period_start - 1)
        FROM runs
        WHERE additive AND gap > 7 AND contiguous_before = 3
        UNION ALL
        -- 050: a negative stock-based compensation quarter is a benefit as filed (forfeitures
        -- reversing earlier expense, liability awards remeasured), not a slip, unless another filing
        -- reports the same quarter positive: info, rule 'benefit'
        SELECT company_id, 'sign_flip', metric, period_end, s.severity,
               jsonb_build_object('accession_no', accession_no)
               || CASE WHEN s.severity = 'info' THEN '{"rule": "benefit"}'::jsonb
                       ELSE '{}'::jsonb END
        FROM q
        CROSS JOIN LATERAL (
            SELECT CASE WHEN q.metric = 'stock_based_compensation' AND q.value < 0
                             AND NOT EXISTS (
                                 SELECT 1 FROM silver.company_ciks k
                                 JOIN silver.financial_facts f ON f.cik = k.cik
                                 WHERE k.company_id = q.company_id
                                   AND f.metric = 'stock_based_compensation'
                                   AND f.period_end = q.period_end
                                   AND f.period_start = q.period_start AND f.value > 0)
                        THEN 'info' ELSE 'warning' END AS severity
        ) s
        WHERE metric IN ('cost_of_revenue', 'research_and_development', 'sga',
                         'interest_expense', 'capex', 'depreciation_amortization',
                         'stock_based_compensation', 'dividends_paid', 'share_buybacks')
          AND value <> 0 AND prev_value <> 0 AND next_value <> 0
          AND sign(value) <> sign(prev_value) AND sign(prev_value) = sign(next_value)
        UNION ALL
        -- 072: a spike on a line that swings by nature (cash flow subtotals, the change in cash,
        -- buybacks, dividends, income tax) is info, rule 'lumpy'; the test is 041's
        SELECT company_id, 'outlier', metric, period_end,
               CASE WHEN metric IN ('financing_cash_flow', 'investing_cash_flow', 'change_in_cash',
                                    'share_buybacks', 'dividends_paid', 'income_tax')
                    THEN 'info' ELSE 'warning' END,
               jsonb_build_object('vs_neighbours',
                                  round(value / greatest(abs(prev_value), abs(next_value)), 1),
                                  'accession_no', accession_no)
               || CASE WHEN metric IN ('financing_cash_flow', 'investing_cash_flow',
                                       'change_in_cash', 'share_buybacks', 'dividends_paid',
                                       'income_tax')
                       THEN '{"rule": "lumpy"}'::jsonb ELSE '{}'::jsonb END
        FROM q
        WHERE prev_value <> 0 AND next_value <> 0 AND sign(prev_value) = sign(next_value)
          AND greatest(abs(prev_value), abs(next_value))
              <= 2 * least(abs(prev_value), abs(next_value))
          AND abs(value) >= 10 * greatest(abs(prev_value), abs(next_value))
        UNION ALL
        -- restatement: the period's first filing (same tag, unit, dates) said something else
        SELECT company_id, 'restatement', metric, period_end, 'info',  -- 072: from `restated`
               jsonb_build_object('original_accession', original_accession,
                                  'original_filed', original_filed,
                                  'restated_accession', accession_no,
                                  'restated_filed', filed,
                                  'change', change)
        FROM restated
        UNION ALL
        SELECT company_id, 'mistagged_fact', metric, period_end, 'info',  -- 072: kept out
               jsonb_build_object('rule', 'period_after_filing', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'tag', tag, 'periods', periods)
        FROM mistagged
        UNION ALL
        SELECT company_id, 'mistagged_fact', metric, period_end, 'info',  -- 072: kept out
               jsonb_build_object('rule', 'debt_above_liabilities', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'tag', tag)
        FROM debt_scale
        UNION ALL
        SELECT company_id, 'mistagged_fact', 'long_term_debt_current', period_end, 'info',
               jsonb_build_object('rule', 'debt_parts_scale', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'factor', factor)
        FROM parts_scale
        UNION ALL
        SELECT company_id, 'series_gap', metric, period_end, 'info',  -- 072: coverage
               jsonb_build_object('missing_from', period_start - gap + 1,
                                  'missing_to', period_start - 1, 'repeated', years)
        FROM repeated_gap
        WHERE years >= 3
        UNION ALL
        SELECT company_id, 'net_debt_gap', 'net_debt', period_end, 'info',  -- 072: coverage
               jsonb_build_object('investments_end', investments_end)
        FROM net_debt_gap
        UNION ALL
        -- 072: the tags, the factor (the other tag's figure over gold's) and the filing; never the
        -- values (014's rule for detail)
        SELECT company_id, 'tag_disagreement', metric, period_end, 'warning',
               jsonb_build_object('rule', rule, 'tag', tag, 'other_tag', other_tag,
                                  'factor', round(factor, 6)::float8, 'accession_no', accession_no,
                                  'form', form, 'filed', filed, 'period_start', period_start)
        FROM disagree
        UNION ALL
        -- 074: the filing's own statement prints, unambiguously, another figure than the one gold
        -- holds and the filing states (ops.statement_comparisons: own, clear). The line, the
        -- factor (gold's over the printed), the filing; never the values (014's rule).
        SELECT company_id, 'statement_mismatch',
               CASE WHEN line = 'liabilities_and_equity' THEN 'total_assets' ELSE line END,
               gold_end, 'warning',
               jsonb_build_object('rule', rule, 'line', line,
                                  'factor', round(gold / printed, 6)::float8,
                                  'accession_no', accession_no, 'form', form, 'filed', filed)
        FROM stmt
        WHERE NOT agree AND own AND clear AND printed <> 0
    )
    INSERT INTO ops.quality_findings
        (company_id, check_name, metric, period_end, severity, detail, run_id)
    SELECT company_id, check_name, metric, period_end, severity, detail, run
    FROM findings
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;

    UPDATE ops.quality_runs SET findings = n, by_check = coalesce((
        SELECT jsonb_object_agg(check_name, c)
        FROM (SELECT check_name, count(*) AS c FROM ops.quality_findings
              WHERE run_id = run GROUP BY check_name) x), '{}')
    WHERE run_id = run;

    -- 072: and the gold figures checked (quarters, TTM and computed), for findings per 1,000
    -- 074: and the confidence mark's inputs (gold.company_confidence): statement lines compared
    -- and agreeing, the core figures at the latest period, what kind of entity it is
    INSERT INTO ops.quality_companies (company_id, run_id, checked_at, errors, warnings, figures,
                                       statements_checked, statements_agreed,
                                       statements_differ_at, core_figures, entity)
    SELECT c.company_id, run, now(),
           count(f.*) FILTER (WHERE f.severity = 'error'),
           count(f.*) FILTER (WHERE f.severity = 'warning'),
           (SELECT count(*) FROM gold.quarterly_financials g WHERE g.company_id = c.company_id)
           + (SELECT count(*) FROM gold.derived_financials g WHERE g.company_id = c.company_id),
           s.checked, s.agreed,
           max(f.period_end) FILTER (WHERE f.check_name = 'statement_mismatch'),
           ops.core_figures(c.company_id),
           ops.entity_kind(c.company_id, c.sic)
    FROM silver.companies c
    CROSS JOIN LATERAL (
        SELECT count(*) FILTER (WHERE x.agree OR (x.own AND x.clear)) AS checked,
               count(*) FILTER (WHERE x.agree) AS agreed
        FROM ops.statement_comparisons(ARRAY[c.company_id]) x
    ) s
    LEFT JOIN ops.quality_findings f ON f.company_id = c.company_id
    WHERE c.company_id = ANY(p_company_ids)
    GROUP BY c.company_id, c.sic, s.checked, s.agreed
    ON CONFLICT (company_id) DO UPDATE SET run_id = EXCLUDED.run_id,
        checked_at = EXCLUDED.checked_at, errors = EXCLUDED.errors,
        warnings = EXCLUDED.warnings, figures = EXCLUDED.figures,
        statements_checked = EXCLUDED.statements_checked,
        statements_agreed = EXCLUDED.statements_agreed,
        statements_differ_at = EXCLUDED.statements_differ_at,
        core_figures = EXCLUDED.core_figures, entity = EXCLUDED.entity;

    RETURN n;
END
$$;

-- 074: the hourly catch-up re-checks every company (071).
CREATE OR REPLACE FUNCTION ops.check_financials_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 74';

-- 071's body with the edit marked "074": a company's check is also due when one of its filings was
-- read (silver.statement_reads) after the check, so new statement lines reach its findings and mark.
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
                    AND r.read_at > coalesce(b.built_at, '-infinity'::timestamptz))))
      AND (p_kind <> 'check_financials' OR c.company_id NOT IN (
              SELECT g.company_id FROM ops.company_backlog('gold') g WHERE NOT g.failed))
$$;

-- 2. The status page lists the new check -------------------------------------------------------------

-- 072's view, with ('statement_mismatch', 'warning') in the expected list (074).
CREATE OR REPLACE VIEW ops.quality_tiers AS
WITH found AS (
    SELECT f.check_name, f.severity, count(*) AS findings,
           count(DISTINCT f.company_id) AS companies,
           count(*) FILTER (WHERE q.figures IS NOT NULL) AS counted
    FROM ops.quality_findings f
    LEFT JOIN ops.quality_companies q ON q.company_id = f.company_id
    GROUP BY f.check_name, f.severity
),
checks AS (
    SELECT check_name, severity
    FROM (VALUES ('impossible_value', 'error'), ('overlapping_quarters', 'error'),
                 ('tag_disagreement', 'warning'), ('statement_mismatch', 'warning'),
                 ('outlier', 'warning'), ('sign_flip', 'warning'),
                 ('annual_sum', 'warning'), ('calendar_collision', 'warning'),
                 ('segment_sum_business_segment', 'warning'), ('segment_sum_geography', 'warning'),
                 ('segment_sum_product_service', 'warning'),
                 ('mistagged_fact', 'info'), ('series_gap', 'info'), ('net_debt_gap', 'info'),
                 ('outlier', 'info'), ('sign_flip', 'info'), ('annual_sum', 'info'),
                 ('restatement', 'info')) v(check_name, severity)
    UNION
    SELECT check_name, severity FROM found
),
figures AS (
    SELECT sum(figures) AS figures FROM ops.quality_companies
)
SELECT c.check_name, c.severity,
       coalesce(f.findings, 0) AS findings,
       coalesce(f.companies, 0) AS companies,
       (SELECT count(*) FROM ops.quality_companies) AS companies_checked,
       (SELECT max(checked_at) FROM ops.quality_runs) AS last_checked_at,
       ops.quality_tier(c.check_name, c.severity) AS tier,
       g.figures,
       round(1000.0 * coalesce(f.counted, 0) / nullif(g.figures, 0), 3) AS per_1000
FROM checks c
LEFT JOIN found f ON f.check_name = c.check_name AND f.severity = c.severity
CROSS JOIN figures g;

-- 3. Catch-up: every stored 10-K and 10-Q with tables read once per statements.READER -------------

ALTER TABLE ops.catchup DROP CONSTRAINT catchup_kind_check;
ALTER TABLE ops.catchup ADD CONSTRAINT catchup_kind_check
    CHECK (kind IN ('parser', 'passages', 'diffs', 'gold', 'check_financials', 'check_segments',
                    'check_sections', 'statements'));
INSERT INTO ops.catchup (kind, paused, baseline) VALUES ('statements', false, NULL);

-- Stored original 10-Ks and 10-Qs of mapped companies, with a period of report and v7 tables, that
-- this reader version hasn't read; parser-index reads what it indexes, the catch-up the rest.
CREATE FUNCTION ops.statement_backlog(p_reader text)
RETURNS TABLE (accession_no text, filed date)
LANGUAGE sql STABLE AS $$
    SELECT d.accession_no, d.filed
    FROM bronze.filing_documents d
    LEFT JOIN silver.statement_reads r ON r.accession_no = d.accession_no
    WHERE d.form IN ('10-K', '10-Q') AND d.period_of_report IS NOT NULL
      AND (r.accession_no IS NULL OR r.reader <> p_reader)
      AND EXISTS (SELECT 1 FROM silver.filing_tables t WHERE t.accession_no = d.accession_no)
      AND EXISTS (SELECT 1 FROM silver.company_ciks k WHERE k.cik = d.cik)
$$;

-- 4. The confidence mark --------------------------------------------------------------------------

-- How far to trust a company's figures, from public signals only, in the readings family (035): read
-- time, plain SQL, one SELECT, inlinable (the screener reads it per page row under RLS: no row of
-- ops.quality_companies for a company the caller can't read, and then no mark). p_latest is
-- gold.latest_period. Level and reasons, the reasons of the level only (high: what was checked):
--   low     a blank-check company, a commodity pool or trust, a royalty trust, a business development
--           company (ops.entity_kind); errors readers could see at 2 or more per 1,000 figures;
--           figures to double-check at 25 or more per 1,000; no report for 500 days; at most two of
--           the six core figures at the latest period
--   medium  any error; 10 or more figures to double-check per 1,000; a statement_mismatch (medium,
--           not low: about half of them are the table reader's, header 2);
--           reported in another currency than the dollar; a quarterly filer's figures over 270 days
--           old (069's rule: an annual filer's at 500); three or four of the six core figures; not
--           checked yet
--   high    none of these
-- Per 1,000 only once the check counted figures (072). Measured on hermes (2026-11-16, read-only;
-- the findings predate 072, so 072's tiers were applied to them by name): of 6,617 companies with gold,
-- errors at 605 (p95 0.7, p99 3.6 per 1,000), warnings at 4,399 (median 1.5, p90 8.8, p99 20.2 per
-- 1,000); 523 report in another currency; latest period p90 320 days, p95 595. The rules give high
-- 3,881, medium 1,675, low 1,061 (stale 431, shells 309, errors 141, commodity pools 118, business
-- development companies 59, core 49, warnings 27, royalty trusts 9). NVIDIA (18 warnings in 5,467
-- figures), JPMorgan, Apple, Microsoft, Exxon: high; Goldman Sachs (4 errors), GM (15 warnings per
-- 1,000), Canadian Pacific (CAD): medium; Ares Capital, the United States Oil Fund, a SPAC, TSMC
-- (nothing since Dec 2024 reached Tributary): low. Thresholds to revisit once 072's checks have run
-- everywhere (its tag_disagreement adds warnings, its tiers move others to info).
CREATE FUNCTION gold.company_confidence(p_company_id text, p_sic text, p_latest date)
RETURNS TABLE (level text, reasons text[])
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT CASE WHEN q.company_id IS NULL AND p_latest IS NULL THEN NULL
                WHEN cardinality(x.low) > 0 THEN 'low'
                WHEN cardinality(x.medium) > 0 THEN 'medium'
                ELSE 'high' END,
           CASE WHEN q.company_id IS NULL AND p_latest IS NULL THEN NULL
                WHEN cardinality(x.low) > 0 THEN x.low
                WHEN cardinality(x.medium) > 0 THEN x.medium
                ELSE x.high END
    FROM (SELECT 1) one
    LEFT JOIN ops.quality_companies q ON q.company_id = p_company_id
    LEFT JOIN LATERAL gold.amount_currency_of(p_company_id) cu ON true
    CROSS JOIN LATERAL (
        SELECT coalesce(current_setting('tributary.today', true)::date, current_date) - p_latest
                   AS age,
               CASE WHEN q.figures > 0 THEN 1000.0 * q.errors / q.figures END AS errors_k,
               CASE WHEN q.figures > 0 THEN 1000.0 * q.warnings / q.figures END AS warnings_k
    ) t
    -- 069's annual filer (no quarterly revenue or net income in the 200 days before its latest
    -- period), probed only for a company past 270 days
    LEFT JOIN LATERAL (
        SELECT NOT EXISTS (
                   SELECT 1 FROM gold.quarterly_financials g
                   WHERE g.company_id = p_company_id AND g.metric IN ('revenue', 'net_income')
                     AND g.period_end BETWEEN p_latest - 200 AND p_latest
                     AND g.period_end - g.period_start BETWEEN 80 AND 120) AS annual
        WHERE t.age > 270
    ) a ON true
    CROSS JOIN LATERAL (
        SELECT array_remove(ARRAY[
                   CASE q.entity
                       WHEN 'shell' THEN 'a blank-check company with no business yet, so its '
                                         'figures say little about one'
                       WHEN 'commodity_pool' THEN 'a commodity pool or trust: its results are gains '
                                         'on what it holds, not sales and profits'
                       WHEN 'royalty_trust' THEN 'a royalty trust: it passes royalties through, so '
                                         'most operating figures don''t apply'
                       WHEN 'bdc' THEN 'a business development company: it reports investment '
                                         'income and net assets, which the standard lines read '
                                         'only in part' END,
                   CASE WHEN t.errors_k >= 2
                        THEN format('%s %s a reader could see in its %s figures', q.errors,
                                    CASE WHEN q.errors = 1 THEN 'error' ELSE 'errors' END,
                                    to_char(q.figures, 'FM999,999,999')) END,
                   CASE WHEN t.warnings_k >= 25
                        THEN format('%s %s to double-check in its %s', q.warnings,
                                    CASE WHEN q.warnings = 1 THEN 'figure' ELSE 'figures' END,
                                    to_char(q.figures, 'FM999,999,999')) END,
                   CASE WHEN t.age > 500
                        THEN 'no report since the period ended '
                             || to_char(p_latest, 'FMMon FMDD, YYYY') END,
                   CASE WHEN q.core_figures <= 2
                        THEN format('only %s of the six core figures for its latest period',
                                    q.core_figures) END
               ], NULL) AS low,
               array_remove(ARRAY[
                   CASE WHEN q.company_id IS NULL THEN 'not checked yet' END,
                   CASE WHEN q.errors > 0
                        THEN format('%s %s a reader could see', q.errors,
                                    CASE WHEN q.errors = 1 THEN 'error' ELSE 'errors' END) END,
                   CASE WHEN t.warnings_k >= 10
                        THEN format('%s %s to double-check in its %s', q.warnings,
                                    CASE WHEN q.warnings = 1 THEN 'figure' ELSE 'figures' END,
                                    to_char(q.figures, 'FM999,999,999')) END,
                   CASE WHEN q.statements_differ_at IS NOT NULL
                        THEN 'a filing''s own statement prints another headline figure than '
                             'the one here' END,
                   CASE WHEN cu.unit IS NOT NULL AND cu.unit <> 'USD'
                        THEN 'reported in ' || cu.unit || ': fewer of its lines map to the '
                             'standard ones, and nothing is converted' END,
                   CASE WHEN t.age > 270 AND t.age <= 500 AND NOT a.annual
                        THEN 'its latest figures are for the period ended '
                             || to_char(p_latest, 'FMMon FMDD, YYYY') END,
                   CASE WHEN q.core_figures IN (3, 4)
                        THEN format('%s of the six core figures for its latest period',
                                    q.core_figures) END
               ], NULL) AS medium,
               array_remove(ARRAY[
                   CASE WHEN q.figures > 0
                        THEN format('no error found in its %s figures',
                                    to_char(q.figures, 'FM999,999,999')) END,
                   CASE WHEN q.statements_agreed > 0
                        THEN format('%s %s from its filings'' own statements %s the figures here',
                                    q.statements_agreed,
                                    CASE WHEN q.statements_agreed = 1 THEN 'line' ELSE 'lines' END,
                                    CASE WHEN q.statements_agreed = 1 THEN 'agrees with'
                                         ELSE 'agree with' END) END
               ], NULL) AS high
    ) x
$$;
