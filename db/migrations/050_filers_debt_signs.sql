-- Facts that belong to another filer, debt read without its sign, debt parts in the wrong scale, and
-- stock-based compensation benefits said as what they are (F7a).
--
-- Measured on hermes 2026-11-09 (read only, one connection).
--
-- 1. Filings the SEC files under a co-registrant. Since July 2026 the SEC's companyfacts carries some
--    multi-registrant 10-K/10-Qs only under another filer of the filing, never under the filer the
--    XBRL itself names (bronze.filing_xbrl.entity_cik, from the stored document's contexts): 91 filings
--    in the last year, 26 in July, 17 in August, 23 in October, 25 so far in November, none before.
--    A parent and its utility, a REIT and its operating partnership: DTE Energy's Q2 and Q3 2026 10-Qs
--    sit under DTE Electric (CIK 28385, whose companyfacts the SEC now names "DTE Energy Co"), Southern's
--    under Mississippi Power, Dominion's under Virginia Electric, CMS's under Consumers Energy, and
--    Duke, NextEra, Exelon, AEP, PG&E, Sempra, Entergy, Ameren, Eversource, Prologis, Digital Realty,
--    Kimco, Vornado... Their gold stopped at the last filing the SEC still filed under them (DTE: Q1
--    2026). Where the other filer is a company of its own (9: Southern California Edison, Consumers
--    Energy, Pacific Gas & Electric, Union Electric, NSTAR Electric, Entergy Arkansas, Public Service
--    of New Mexico), its gold took the parent's consolidated figures (SCE's Q2 2026 revenue 4,357M is
--    Edison International's). The facts themselves are right: companyfacts holds only undimensioned
--    facts, which in a combined filing are the entity the XBRL names; a subsidiary's own figures carry
--    a legal-entity dimension and never reach companyfacts.
--    So gold.usable_facts reads a filing's facts as the company's whose CIK the filing's XBRL names,
--    when that CIK is one of the filing's filers (bronze.filings or bronze.submission_filings; a mistyped
--    identifier moves nothing): `lent` takes them from the company whose CIK the SEC used, `borrowed`
--    gives them to the named company if none of its own CIKs has that filing's facts. Silver keeps them
--    under the SEC's CIK (FR6: the accession is the provenance). Only filings with a stored document
--    and an extract (10-K/10-Q of operating companies in the crawl's years) are re-attributed; the change
--    began in 2026, all inside. gold.companies_for_ciks tells the facts loaders which companies a
--    changed payload reaches (the named company, not only the CIK's own), so the nightly bulk refreshes
--    DTE Energy when DTE Electric's payload changes.
--
-- 2. Debt below zero (impossible_value since 041): 321 gold rows, 302 long_term_debt_current and 19
--    long_term_debt, at about 120 companies, 5 of them since 2020. Read one by one they are the debt
--    note's "less current portion (x)" sign, not a negative balance: 115 have the same figure positive
--    in another filing for the same date (Euronet's 2013 current debt +10,903,000 in three 10-Qs and
--    -10,903,000 in the 10-K; TREES' 1,177,333 in four filings, negative in the fifth); DTE's 2009
--    -521M sits between +170M and +677M; SL Green tags its whole 2012-13 long-term debt negative. A
--    balance can't be below zero, so gold reads both debt metrics as abs(value), like 043's interest
--    expense, before anything is summed or compared; silver keeps the filing's figure. The restatement
--    check compares these metrics without their sign too, so a sign that changed between filings isn't
--    a "restated by -200%".
--
-- 3. Stock-based compensation keeps its sign. 793 negative quarters at 486 companies in gold, 597
--    sign_flip findings. Unlike debt, they are what the filings say and they reconcile with the same
--    filings' year-to-date figures: NextDecade's six months to June 2018 are 11,251K = 12,234K for Q1
--    less 983K for Q2; Archer's Q3 2023 -27.4M is the founders' awards forfeited; United Therapeutics'
--    liability-classified awards remeasured to -37.2M in Q2 2019; Textron -13M in Q1 2020; Angi -2.3M in
--    Q1 2025 on a CEO's forfeiture. Of the 556 between two positive quarters, 227 are under half their
--    neighbours (reversals) and 295 of similar size (large forfeitures, remeasurements); both kinds
--    reconcile. Only 24 have the same quarter positive in another filing. Reading them without the sign
--    would turn every benefit into an expense. So the figure stays as filed and the check says what it
--    is: a negative quarter of stock-based compensation is a sign_flip of severity info with rule
--    'benefit', unless another filing reports that quarter positive (then a warning as before). The
--    finding stays, at its true weight.
--
-- 4. Debt parts in the wrong scale. Bank First's 2019 10-K tags LongTermDebtCurrent 1,400,000,000 and
--    LongTermDebtNoncurrent 38,400,000,000 beside its own LongTermDebt of 39,800,000: the parts in
--    thousands of dollars too many. 041's debt_above_liabilities caught the 38.4B (over five times
--    liabilities and assets), not the 1.4B, which gold kept as a bank's current debt. In silver 10
--    filings have current + noncurrent at 1,000 times the same filing's LongTermDebt for that date (2 at
--    1,000,000 times). gold.usable_facts leaves those parts out (`slipped`) and check_financials reports
--    each as mistagged_fact, rule debt_parts_scale.
--
-- Golden figures don't move: no golden company files with a co-registrant, has negative debt or debt
-- parts at 1,000 times a total, and the checks don't feed gold.
--
-- Bodies are 043's gold.usable_facts and 041's ops.check_financials, generated by a script that asserts
-- each anchor occurs once; the edits are marked "050". A later rewrite keeps them (and what 039, 041 and
-- 043 keep).
--
-- Locks on hermes: CREATE INDEX on bronze.filing_xbrl (97k rows, 86 MB heap) holds a SHARE lock for a
-- second or two: the hourly segments job's writes wait, reads don't. The rest is CREATE (OR REPLACE)
-- FUNCTION, catalog only. Gold changes on the financials-rebuild job (deploy/helm/tributary README);
-- expected figures in its runbook. Backout: db/backout/filers_debt_050.sql.

SET LOCAL lock_timeout = '30s';

-- gold.usable_facts' borrowed facts start from the company's CIKs as an XBRL entity
CREATE INDEX filing_xbrl_entity ON bronze.filing_xbrl (entity_cik);

-- Whether a CIK is one of a filing's filers: the live feed or the submissions history says so.
CREATE FUNCTION gold.filed_by(p_accession_no text, p_cik text) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM bronze.filings
                   WHERE accession_no = p_accession_no AND cik = p_cik)
        OR EXISTS (SELECT 1 FROM bronze.submission_filings
                   WHERE accession_no = p_accession_no AND cik = p_cik)
$$;

-- The companies whose gold reads these CIKs' facts: the CIKs' own companies, and each company whose
-- filing the SEC filed under one of them (gold.usable_facts' `borrowed`). The facts loaders refresh
-- these after a payload changes.
CREATE FUNCTION gold.companies_for_ciks(p_ciks text[]) RETURNS SETOF text
LANGUAGE sql STABLE AS $$
    SELECT k.company_id FROM silver.company_ciks k WHERE k.cik = ANY(p_ciks)
    UNION
    SELECT e.company_id
    FROM (SELECT DISTINCT f.accession_no, f.cik
          FROM silver.financial_facts f WHERE f.cik = ANY(p_ciks)) a
    JOIN bronze.filing_xbrl x ON x.accession_no = a.accession_no AND x.entity_cik <> a.cik
    JOIN silver.company_ciks e ON e.cik = x.entity_cik
    WHERE NOT EXISTS (SELECT 1 FROM silver.company_ciks k
                      WHERE k.cik = a.cik AND k.company_id = e.company_id)
      AND gold.filed_by(a.accession_no, x.entity_cik)
$$;

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
        JOIN silver.metric_tags t ON t.tag = f.tag
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
    annual AS (  -- each reported fiscal year, picked as gold picks its facts
        SELECT b.company_id, b.metric, b.period_start, b.period_end, b.value, b.accession_no
        FROM gold.best_facts(p_company_ids) b
        WHERE b.period_type = 'annual' AND b.additive
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
            ORDER BY x.filed DESC, x.accession_no DESC LIMIT 1
        ) l ON f.value > l.value * 5
        JOIN LATERAL (
            SELECT x.value FROM silver.financial_facts x
            WHERE x.cik = f.cik AND x.metric = 'total_assets' AND x.tag = 'Assets'
              AND x.period_end = f.period_end AND x.period_start IS NULL
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
            GROUP BY f.period_end, f.accession_no
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
        SELECT d.company_id, 'annual_sum', d.metric, d.period_end, 'warning',
               jsonb_build_object('deviation', round((d.value - a.value) / abs(a.value), 4),
                                  'annual_accession', a.accession_no,
                                  'quarter_accessions', d.source_accessions)
        FROM gold.derived_financials d
        JOIN annual a ON a.company_id = d.company_id AND a.metric = d.metric
                     AND a.period_end = d.period_end
                     AND abs(a.period_start - d.period_start) <= 7
        WHERE d.company_id = ANY(p_company_ids) AND d.span = 'ttm' AND d.value_source = 'ttm'
          AND a.value <> 0
          AND abs(d.value - a.value) > 0.01 * abs(a.value)
        UNION ALL
        SELECT company_id, 'series_gap', metric, period_end, 'warning',
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
        SELECT company_id, 'outlier', metric, period_end, 'warning',
               jsonb_build_object('vs_neighbours',
                                  round(value / greatest(abs(prev_value), abs(next_value)), 1),
                                  'accession_no', accession_no)
        FROM q
        WHERE prev_value <> 0 AND next_value <> 0 AND sign(prev_value) = sign(next_value)
          AND greatest(abs(prev_value), abs(next_value))
              <= 2 * least(abs(prev_value), abs(next_value))
          AND abs(value) >= 10 * greatest(abs(prev_value), abs(next_value))
        UNION ALL
        -- restatement: the period's first filing (same tag, unit, dates) said something else
        SELECT g.company_id, 'restatement', g.metric, g.period_end, 'info',
               jsonb_build_object('original_accession', o.accession_no,
                                  'original_filed', o.filed,
                                  'restated_accession', g.accession_no,
                                  'restated_filed', g.filed,
                                  'change', round((g.value - o.value) / abs(o.value), 4))
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
        UNION ALL
        SELECT company_id, 'mistagged_fact', metric, period_end, 'error',
               jsonb_build_object('rule', 'period_after_filing', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'tag', tag, 'periods', periods)
        FROM mistagged
        UNION ALL
        SELECT company_id, 'mistagged_fact', metric, period_end, 'error',
               jsonb_build_object('rule', 'debt_above_liabilities', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'tag', tag)
        FROM debt_scale
        UNION ALL
        SELECT company_id, 'mistagged_fact', 'long_term_debt_current', period_end, 'error',
               jsonb_build_object('rule', 'debt_parts_scale', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'factor', factor)
        FROM parts_scale
        UNION ALL
        SELECT company_id, 'series_gap', metric, period_end, 'warning',
               jsonb_build_object('missing_from', period_start - gap + 1,
                                  'missing_to', period_start - 1, 'repeated', years)
        FROM repeated_gap
        WHERE years >= 3
        UNION ALL
        SELECT company_id, 'net_debt_gap', 'net_debt', period_end, 'warning',
               jsonb_build_object('investments_end', investments_end)
        FROM net_debt_gap
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

    INSERT INTO ops.quality_companies (company_id, run_id, checked_at, errors, warnings)
    SELECT c.company_id, run, now(),
           count(f.*) FILTER (WHERE f.severity = 'error'),
           count(f.*) FILTER (WHERE f.severity = 'warning')
    FROM silver.companies c
    LEFT JOIN ops.quality_findings f ON f.company_id = c.company_id
    WHERE c.company_id = ANY(p_company_ids)
    GROUP BY c.company_id
    ON CONFLICT (company_id) DO UPDATE SET run_id = EXCLUDED.run_id,
        checked_at = EXCLUDED.checked_at, errors = EXCLUDED.errors,
        warnings = EXCLUDED.warnings;

    RETURN n;
END
$$;
