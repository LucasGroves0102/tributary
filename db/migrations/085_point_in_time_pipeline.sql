-- Point-in-time data, part 1: gold's pipeline as pure functions of a knowledge date (db/DESIGN.md
-- "085"). Behaviour-neutral: gold's rows are what they were, row for row; no rules_version bump.
--
-- 1. gold.usable_facts(ids, p_as_of date DEFAULT NULL) reads only facts filed by the end of p_as_of
--    (its `facts` CTE: the currency, borrowing, balance and debt rules then see only what was filed by
--    then); gold.best_facts(ids, p_as_of DEFAULT NULL) passes it on. NULL = every fact, as before.
--    The one-argument forms are dropped (a default can't sit beside them: the calls would be
--    ambiguous); every caller's one-argument call resolves to the default.
-- 2. gold.quarterly_rows(ids, p_as_of) and gold.derived_rows(ids, p_as_of, p_quarters) are the bodies
--    of 043's gold.refresh_quarterly_financials and 084's gold.refresh_derived_financials as pure
--    functions: where a statement read rows an earlier statement had just inserted, it reads a CTE of
--    those rows (043's gross profit over the series' rows; 014's stated years and 040's net debt over
--    the TTM sums), and the reads by key that became reads of a CTE are joins (040's sum of a year's
--    quarters, `in_series`, the latest short-term investments, the TTM interest expense), so no step
--    scans the rows per row. derived_rows reads its quarters from gold (p_quarters and p_as_of NULL:
--    the build just wrote them), from p_quarters, or from quarterly_rows as of p_as_of.
--    The writers are DELETE + INSERT from them: refresh_quarterly_financials returns the series'
--    rows as before (not the computed gross profit), refresh_derived_financials every derived row.
--
-- Equal row for row (db/DESIGN.md "085"): the golden reference set (97 companies) rebuilt by 084's
-- functions and by these: gold.quarterly_financials, gold.derived_financials, ops.quality_findings
-- and gold.screen_figures identical (EXCEPT both ways, every column but refreshed_at). On hermes,
-- read-only: these bodies inlined as one SELECT over 500 companies, EXCEPT gold.financials' rows: see
-- DESIGN.md for the counts.
--
-- Locks on hermes (lock_timeout 3 s): DROP FUNCTION of the one-argument gold.usable_facts and
-- gold.best_facts and CREATE of their replacements, two new composite types, CREATE OR REPLACE of
-- the two writers (catalog only: a running build keeps the bodies it started with; a build starting
-- in the instant between the DROP and the CREATE can't, as the whole file is one transaction). No
-- table is read, written or locked. The backouts' guards pin the new bodies (backout
-- db/backout/point_in_time_085.sql heads the chain).

SET LOCAL lock_timeout = '3s';

-- A quarterly row, gold.quarterly_financials' columns but refreshed_at.
CREATE TYPE gold.quarter_row AS (
    company_id        text,
    metric            text,
    period_start      date,
    period_end        date,
    value             numeric,
    unit              text,
    value_source      text,
    source_tag        text,
    accession_no      text,
    form              text,
    filed             date,
    source_accessions text[],
    ingested_at       timestamptz
);

-- A derived row, gold.derived_financials' columns but refreshed_at.
CREATE TYPE gold.derived_row AS (
    company_id        text,
    span              text,
    metric            text,
    period_start      date,
    period_end        date,
    value             numeric,
    unit              text,
    value_source      text,
    source_tag        text,
    inputs            jsonb,
    accession_no      text,
    form              text,
    filed             date,
    source_accessions text[],
    ingested_at       timestamptz
);

DROP FUNCTION gold.best_facts(text[]);
DROP FUNCTION gold.usable_facts(text[]);

CREATE FUNCTION gold.usable_facts(p_company_ids text[], p_as_of date DEFAULT NULL)
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

CREATE FUNCTION gold.best_facts(p_company_ids text[], p_as_of date DEFAULT NULL)
RETURNS TABLE (company_id text, metric text, tag text, taxonomy text, unit text, period_start date,
               period_end date, period_type text, value numeric, accession_no text, form text,
               filed date, ingested_at timestamptz, cik text, priority numeric, additive boolean)
LANGUAGE sql STABLE AS $$
    WITH tag_latest AS (
        SELECT DISTINCT ON (u.company_id, u.metric, u.tag, u.period_start, u.period_end) u.*,
               -- 077: when the tag first gave this period (a later filing's comparative or not)
               min(u.filed) OVER (PARTITION BY u.company_id, u.metric, u.tag, u.period_start,
                                               u.period_end) AS first_filed
        FROM gold.usable_facts(p_company_ids, p_as_of) u  -- 085
        ORDER BY u.company_id, u.metric, u.tag, u.period_start, u.period_end, u.filed DESC,
                 u.accession_no DESC, u.cik
    ),
    -- 077: a year whose net income the company's own filings of the year tag ProfitLoss only (the
    -- annual figure ProfitLoss, never NetIncomeLoss; a year: 350-380 days), NetIncomeLoss for its
    -- periods coming only from later filings' comparatives (first filed 100+ days after the year
    -- ended). Inside it ProfitLoss reads first, so the year's quarters, its year-to-date figures and
    -- the fiscal Q4 derived from them are one line. PNC's 2025: ProfitLoss in every filing of the
    -- year, NetIncomeLoss only in two 2026 10-Qs' comparatives (Q2, Q3, six and nine months): gold
    -- read Q1 ProfitLoss, Q2-Q3 NetIncomeLoss and a Q4 of the year's ProfitLoss less nine months of
    -- NetIncomeLoss.
    profit_loss_years AS MATERIALIZED (
        SELECT y.company_id, y.period_start, y.period_end
        FROM (SELECT t.company_id, t.period_start, t.period_end
              FROM tag_latest t
              WHERE t.metric = 'net_income' AND t.taxonomy = 'us-gaap'
                AND t.tag IN ('NetIncomeLoss', 'ProfitLoss')
                AND t.period_end - t.period_start BETWEEN 350 AND 380
              GROUP BY t.company_id, t.period_start, t.period_end
              HAVING NOT bool_or(t.tag = 'NetIncomeLoss')) y
        WHERE NOT EXISTS (SELECT 1 FROM tag_latest n
                          WHERE n.company_id = y.company_id AND n.metric = 'net_income'
                            AND n.taxonomy = 'us-gaap' AND n.tag = 'NetIncomeLoss'
                            AND n.period_start >= y.period_start
                            AND n.period_end <= y.period_end
                            AND n.first_filed <= y.period_end + 100)
    ),
    ranked AS (  -- 077: the order tags are believed in; priority, but for that rule
        SELECT t.*,
               CASE WHEN t.metric = 'net_income' AND t.taxonomy = 'us-gaap' AND t.tag = 'ProfitLoss'
                         AND EXISTS (SELECT 1 FROM profit_loss_years y
                                     WHERE y.company_id = t.company_id
                                       AND t.period_start >= y.period_start
                                       AND t.period_end <= y.period_end)
                    THEN 0 ELSE t.priority END AS rank,
               -- 080: an industry total, read only where no other revenue tag gives the period
               (t.metric = 'revenue' AND t.taxonomy = 'us-gaap'
                AND t.tag IN ('RealEstateRevenueNet', 'SalesRevenueGoodsNet',
                              'SalesRevenueServicesNet')) AS fallback
        FROM tag_latest t
    ),
    -- 064: the period's positive revenue values, and the largest of its other money figures (any
    -- other metric with the same start and end), for the scale test below
    scales AS (
        SELECT t.*,
               array_agg(t.value) FILTER (WHERE t.metric = 'revenue' AND t.value > 0) OVER wm
                   AS revenue_values,
               max(abs(t.value)) FILTER (WHERE t.metric <> 'revenue' AND t.unit ~ '^[A-Z]{3}$')
                   OVER wp AS other_max
        FROM ranked t  -- 077
        WINDOW wp AS (PARTITION BY t.company_id, t.period_start, t.period_end),
               wm AS (PARTITION BY t.company_id, t.period_start, t.period_end, t.metric)
    ),
    -- 064: two revenue tags of a period 1,000 or 1,000,000 times apart (within 0.5%) are one figure
    -- in two scales. The larger is the slip when it is over 100 times every other money figure of
    -- the period, else the smaller is (also when the period has no other figure). A slip is no
    -- evidence for revenue's rules below and sorts after every other tag.
    slips AS (
        SELECT s.*,
               CASE WHEN s.metric = 'revenue' AND s.value > 0 THEN EXISTS (
                   SELECT 1 FROM unnest(s.revenue_values) v
                   WHERE ((s.value BETWEEN v * 995 AND v * 1005
                           OR s.value BETWEEN v * 995000 AND v * 1005000)
                          AND s.value > 100 * s.other_max)
                      OR ((v BETWEEN s.value * 995 AND s.value * 1005
                           OR v BETWEEN s.value * 995000 AND s.value * 1005000)
                          AND (s.other_max IS NULL OR v <= 100 * s.other_max)))
               ELSE false END AS slipped
        FROM scales s
    ),
    weighed AS (  -- revenue's rules (039's and 064's headers) read the period's other tags
        SELECT t.*,
               -- 064: a slip counts for none of these
               max(t.filed) FILTER (WHERE t.value >= 1000000 AND NOT t.slipped) OVER w AS large_filed,
               max(t.filed) FILTER (WHERE t.value > 0 AND t.value < 1000000 AND NOT t.slipped) OVER w
                   AS small_filed,
               max(t.value) FILTER (WHERE NOT t.slipped AND t.tag IN (
                   'RevenueFromContractWithCustomerExcludingAssessedTax',
                   'RevenueFromContractWithCustomerIncludingAssessedTax')) OVER w AS contract_value,
               -- 064: the gross line a net revenue figure is compared with
               max(t.value) FILTER (WHERE NOT t.slipped AND t.tag IN ('Revenues',
                   'RevenueFromContractWithCustomerExcludingAssessedTax',
                   'RevenueFromContractWithCustomerIncludingAssessedTax')) OVER w AS gross_value,
               -- 080: the period's industry totals (the first and last of their tags) and the
               -- largest of its other revenue values; positive, neither a slip
               min(t.tag) FILTER (WHERE t.fallback AND t.value > 0) OVER w AS fallback_tag,
               max(t.tag) FILTER (WHERE t.fallback AND t.value > 0) OVER w AS fallback_tag_last,
               max(t.value) FILTER (WHERE t.fallback AND t.value > 0 AND NOT t.slipped) OVER w
                   AS fallback_value,
               max(t.value) FILTER (WHERE t.metric = 'revenue' AND NOT t.fallback AND t.value > 0
                                      AND NOT t.slipped) OVER w AS trusted_value
        FROM slips t  -- 064
        WINDOW w AS (PARTITION BY t.company_id, t.metric, t.period_start, t.period_end)
    ),
    -- 080: an industry total the company's own filings give as a part: under 80% of another
    -- revenue tag's value for the same period, in any filing (Ultragenyx's product sales beside
    -- its total, Tiptree's real estate line beside an insurer's revenues). Never read there; nor
    -- where silver.industry_total_exclusions says so (sales before excise taxes, a predecessor).
    fallback_parts AS MATERIALIZED (
        SELECT x.company_id, x.tag
        FROM weighed x
        WHERE x.fallback AND x.value < 0.8 * x.trusted_value
        UNION
        SELECT e.company_id, e.tag
        FROM silver.industry_total_exclusions e
        WHERE e.company_id = ANY(p_company_ids)
    ),
    judged AS (
        SELECT x.*,
               -- 080: the period's one industry total, at a company that never gives it as a part
               -- (two of them in one period are parts of a total no tag gives: neither is read)
               CASE WHEN x.fallback_tag IS NULL OR x.fallback_tag <> x.fallback_tag_last THEN false
                    ELSE NOT EXISTS (SELECT 1 FROM fallback_parts p
                                     WHERE p.company_id = x.company_id
                                       AND p.tag = x.fallback_tag) END AS fallback_readable
        FROM weighed x
    )
    SELECT DISTINCT ON (x.company_id, x.metric, x.period_start, x.period_end)
           x.company_id, x.metric, x.tag, x.taxonomy, x.unit, x.period_start, x.period_end,
           x.period_type, x.value, x.accession_no, x.form, x.filed, x.ingested_at, x.cik, x.priority,
           x.additive
    FROM judged x  -- 080
    WHERE NOT (x.fallback AND (x.value <= 0 OR NOT x.fallback_readable))  -- 080
    ORDER BY x.company_id, x.metric, x.period_start, x.period_end,
             x.slipped,  -- 064: a scale slip after every other tag
             (x.metric = 'revenue' AND x.period_start IS NOT NULL
              AND ((x.value = 0 AND (x.large_filed >= x.filed OR x.small_filed > x.filed))
                   OR (x.tag = 'Revenues' AND x.value > 0 AND x.value < 0.2 * x.contract_value)
                   -- 064: net revenue under 20% of the gross line is a part too (Rectitude 0.2%)
                   OR (x.tag = 'RevenuesNetOfInterestExpense' AND x.value > 0
                       AND x.value < 0.2 * x.gross_value)
                   -- 080: any other revenue tag under 20% of the period's industry total is a part
                   -- (Conagra's Revenues of $1.6-1.7B beside $13B of net sales, FY2012-2016)
                   OR (NOT x.fallback AND x.fallback_readable AND x.value > 0
                       AND x.value < 0.2 * x.fallback_value)))
                 IS TRUE,
             x.rank, x.filed DESC, x.accession_no DESC, x.cik  -- 077: rank, not priority
$$;

CREATE FUNCTION gold.quarterly_rows(p_company_ids text[], p_as_of date DEFAULT NULL)
RETURNS SETOF gold.quarter_row
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE best AS MATERIALIZED (
        SELECT * FROM gold.best_facts(p_company_ids, p_as_of)  -- 085
    ),
    year_to_date AS (
        SELECT b.*,
               lag(value)        OVER w AS prev_value,
               lag(period_end)   OVER w AS prev_end,
               lag(accession_no) OVER w AS prev_accession,
               lag(ingested_at)  OVER w AS prev_ingested_at,
               lag(priority)     OVER w AS prev_priority
        FROM best b
        WHERE period_type <> 'instant'
        WINDOW w AS (PARTITION BY company_id, metric, period_start ORDER BY period_end)
    ),
    reported AS (
        SELECT company_id, metric, period_start, period_end, value, unit,
               'reported' AS value_source, taxonomy || ':' || tag AS source_tag, accession_no,
               form, filed, ARRAY[accession_no] AS source_accessions, ingested_at, priority
        FROM best
        -- instants are balance-sheet values; a flow metric tagged at an instant (UnitedHealth
        -- tags each dividend payment on its payment date) is not a quarter
        WHERE period_type = 'quarter' OR (period_type = 'instant' AND NOT additive)
    ),
    derived AS (
        SELECT company_id, metric, prev_end + 1 AS period_start, period_end, value - prev_value AS value,
               unit, 'derived' AS value_source, taxonomy || ':' || tag AS source_tag, accession_no,
               form, filed, ARRAY[prev_accession, accession_no] AS source_accessions,
               greatest(ingested_at, prev_ingested_at) AS ingested_at,
               greatest(priority, prev_priority) AS priority
        FROM year_to_date
        WHERE additive AND period_end - prev_end BETWEEN 80 AND 120
          -- a derived quarter of a metric that can't be negative came out negative: one of its
          -- two inputs is wrong (Cytek's 2024 10-K re-tagged 2022 Revenues as 2,963,000 against
          -- 164,036,000 everywhere else). Leave the quarter missing rather than publish it.
          AND NOT (value - prev_value < 0 AND metric IN (
              'revenue', 'cost_of_revenue', 'research_and_development', 'sga', 'capex',
              'depreciation_amortization', 'stock_based_compensation', 'share_buybacks',
              'dividends_paid', 'interest_expense'))  -- interest_expense since 043
    ),
    both_kinds AS (
        SELECT * FROM reported UNION ALL SELECT * FROM derived
    ),
    compared AS (
        SELECT b.*, max(b.value) FILTER (WHERE b.value_source = 'derived') OVER (
                   PARTITION BY b.company_id, b.metric, b.period_end, b.accession_no, b.source_tag
               ) AS difference
        FROM both_kinds b
    ),
    candidates AS (
        SELECT company_id, metric, period_start, period_end, value, unit, value_source, source_tag,
               accession_no, form, filed, source_accessions, ingested_at, priority
        FROM compared c
        -- a figure rounded for a sentence ("we paid $6.0 billion"), tagged in the same filing as an
        -- exact year to date: the difference is the exact quarter (NVIDIA's Q2 FY2027 dividends,
        -- 6,290 - 243 = 6,047, not 6,000; Cisco's "$1.5 billion" against 1,486; American Eagle's
        -- "$9.3 million" of stock compensation against 9.257). The reported figure has at most two
        -- significant digits, the difference at least three that aren't zero, and they're 0.2-2%
        -- apart. Tried on the sample and narrowed: three digits also took IBM's 5,260 of SG&A, which a
        -- filer in millions can state exactly while an earlier quarter was restated; and a difference
        -- like Lowe's 449 against 450 is that filer's own rounding of each year-to-date figure.
        WHERE NOT (c.value_source = 'reported' AND c.difference IS NOT NULL AND c.value <> 0
                   AND abs(c.value - c.difference) BETWEEN 0.002 * abs(c.difference)
                                                       AND 0.02 * abs(c.difference)
                   AND mod(c.value, power(10::numeric, floor(log(nullif(abs(c.value), 0))) - 1)) = 0
                   AND mod(c.difference, power(10::numeric, floor(log(nullif(abs(c.value), 0))) - 2)) <> 0)
    ),
    -- calendar votes per company and date: metrics with a balance-sheet value on it, and
    -- metrics with any fact ending on it
    votes AS (
        SELECT company_id, period_end AS day,
               count(DISTINCT metric) FILTER (WHERE period_type = 'instant') AS instants,
               count(DISTINCT metric) AS metrics
        FROM best
        GROUP BY company_id, period_end
    ),
    scored AS (  -- a quarter's votes: its last day's plus the day before its first day's
        SELECT c.*,
               coalesce(e.instants, 0) + coalesce(s.instants, 0) AS calendar_instants,
               coalesce(e.metrics, 0) + coalesce(s.metrics, 0) AS calendar_metrics
        FROM candidates c
        LEFT JOIN votes e ON e.company_id = c.company_id AND e.day = c.period_end
        LEFT JOIN votes s ON s.company_id = c.company_id AND s.day = c.period_start - 1
    ),
    per_end AS (  -- one row per period end
        SELECT DISTINCT ON (company_id, metric, period_end) *
        FROM scored
        ORDER BY company_id, metric, period_end, calendar_instants DESC, calendar_metrics DESC,
                 priority, value_source = 'reported' DESC, filed DESC, accession_no DESC,
                 source_accessions DESC, period_start DESC, source_tag, value,
                 ingested_at DESC
    ),
    ranked AS MATERIALIZED (  -- the same order across period ends: rnk 1 is the best row
        SELECT p.*, daterange(period_start, period_end, '[]') AS span,
               row_number() OVER (
                   PARTITION BY company_id, metric
                   ORDER BY calendar_instants DESC, calendar_metrics DESC, priority,
                            value_source = 'reported' DESC, filed DESC, accession_no DESC,
                            source_accessions DESC, period_start DESC, source_tag, value,
                            ingested_at DESC, period_end DESC) AS rnk
        FROM per_end p
    ),
    clashing AS (  -- duration rows overlapping another of their metric by more than 7 days
        SELECT DISTINCT a.company_id, a.metric, a.rnk, a.span
        FROM ranked a
        JOIN ranked b ON b.company_id = a.company_id AND b.metric = a.metric AND b.rnk <> a.rnk
        WHERE a.period_start IS NOT NULL AND b.period_start IS NOT NULL
          AND upper(a.span * b.span) - lower(a.span * b.span) > 7
    ),
    queue AS MATERIALIZED (
        SELECT c.*, row_number() OVER (PARTITION BY company_id, metric ORDER BY rnk) AS i
        FROM clashing c
    ),
    greedy AS (  -- best first: keep a row unless it overlaps one already kept by > 7 days
        SELECT company_id, metric, i, ARRAY[span] AS kept, ARRAY[rnk] AS kept_ranks
        FROM queue
        WHERE i = 1
        UNION ALL
        SELECT q.company_id, q.metric, q.i,
               CASE WHEN x.clash THEN g.kept ELSE g.kept || q.span END,
               CASE WHEN x.clash THEN g.kept_ranks ELSE g.kept_ranks || q.rnk END
        FROM greedy g
        JOIN queue q ON q.company_id = g.company_id AND q.metric = g.metric AND q.i = g.i + 1
        CROSS JOIN LATERAL (
            SELECT EXISTS (SELECT 1 FROM unnest(g.kept) k
                           WHERE upper(k * q.span) - lower(k * q.span) > 7) AS clash
        ) x
    ),
    dropped AS (
        SELECT q.company_id, q.metric, q.rnk
        FROM queue q
        JOIN (SELECT DISTINCT ON (company_id, metric) company_id, metric, kept_ranks
              FROM greedy
              ORDER BY company_id, metric, i DESC) g
          ON g.company_id = q.company_id AND g.metric = q.metric
        WHERE q.rnk <> ALL (g.kept_ranks)
    ),
    -- 043's first INSERT: the series' rows
    series AS MATERIALIZED (
        SELECT r.company_id, r.metric, r.period_start, r.period_end, r.value, r.unit,
               r.value_source, r.source_tag, r.accession_no, r.form, r.filed, r.source_accessions,
               r.ingested_at
        FROM ranked r
        LEFT JOIN dropped d
          ON d.company_id = r.company_id AND d.metric = r.metric AND d.rnk = r.rnk
        WHERE d.rnk IS NULL
    ),
    -- revenue less cost, where the filing prints no gross profit (043's second INSERT, over the
    -- series' rows above instead of the table it read them back from). 085: its keyed reads of
    -- that table as one pass: each quarter's revenue, cost, operating income, SG&A and R&D side by
    -- side (one row per metric and end in the series)
    lines AS (
        SELECT s.company_id, s.period_end,
               max(s.value) FILTER (WHERE s.metric = 'revenue') AS revenue,
               max(s.period_start) FILTER (WHERE s.metric = 'revenue') AS revenue_start,
               max(s.unit) FILTER (WHERE s.metric = 'revenue') AS unit,
               max(s.accession_no) FILTER (WHERE s.metric = 'revenue') AS revenue_accession,
               max(s.form) FILTER (WHERE s.metric = 'revenue') AS revenue_form,
               max(s.filed) FILTER (WHERE s.metric = 'revenue') AS revenue_filed,
               max(s.source_accessions) FILTER (WHERE s.metric = 'revenue') AS revenue_accessions,
               max(s.ingested_at) FILTER (WHERE s.metric = 'revenue') AS revenue_ingested_at,
               max(s.value) FILTER (WHERE s.metric = 'cost_of_revenue') AS cost,
               max(s.period_start) FILTER (WHERE s.metric = 'cost_of_revenue') AS cost_start,
               max(s.accession_no) FILTER (WHERE s.metric = 'cost_of_revenue') AS cost_accession,
               max(s.form) FILTER (WHERE s.metric = 'cost_of_revenue') AS cost_form,
               max(s.filed) FILTER (WHERE s.metric = 'cost_of_revenue') AS cost_filed,
               max(s.source_accessions) FILTER (WHERE s.metric = 'cost_of_revenue')
                   AS cost_accessions,
               max(s.ingested_at) FILTER (WHERE s.metric = 'cost_of_revenue') AS cost_ingested_at,
               max(s.value) FILTER (WHERE s.metric = 'operating_income') AS operating_income,
               max(s.value) FILTER (WHERE s.metric = 'sga') AS sga,
               max(s.value) FILTER (WHERE s.metric = 'research_and_development') AS rnd
        FROM series s
        WHERE s.metric IN ('revenue', 'cost_of_revenue', 'operating_income', 'sga',
                           'research_and_development')
        GROUP BY s.company_id, s.period_end
    ),
    computable AS (  -- 043's tests of a quarter's revenue and cost
        SELECT l.*, (l.revenue - l.cost) / l.revenue AS margin
        FROM lines l
        JOIN silver.companies co ON co.company_id = l.company_id
        WHERE l.revenue > 0 AND abs(l.cost_start - l.revenue_start) <= 7
          AND l.period_end >= DATE '2019-01-01'
          AND co.sic IS NOT NULL
          AND co.sic !~ '^(6[0-7]|13|48|58)' AND co.sic !~ '^(4911|493.|2911|5331|5912)$'
          AND l.cost >= 0.15 * l.revenue
          AND (l.operating_income IS NULL OR l.revenue - l.cost >= l.operating_income)
          AND NOT (l.operating_income IS NOT NULL AND l.sga IS NOT NULL
                   AND l.revenue - l.cost - l.operating_income - l.sga - coalesce(l.rnd, 0)
                       > 0.25 * l.revenue)
    ),
    -- 085: no gross profit line of the series overlapping the quarter by more than 7 days (043's NOT
    -- EXISTS). A series' lines overlap each other by 7 days at most and last 79-120 days, so none
    -- lies inside another: the first one ending on or after the quarter's start + 7 is the only one
    -- to test (it overlaps when it starts by the quarter's end - 7). Found in one pass: the lines'
    -- ends and the quarters' starts + 7 in one order, the first line at or after each quarter.
    overlap AS (
        SELECT x.*,
               min(x.line COLLATE "C") OVER (PARTITION BY x.company_id ORDER BY x.k
                                             RANGE BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)
                   AS next_line
        FROM (
            SELECT c.company_id, c.revenue_start + 7 AS k, NULL::text AS line, c.period_end,
                   c.revenue, c.cost, c.unit, c.margin, c.revenue_accession, c.revenue_form,
                   c.revenue_filed, c.revenue_accessions, c.revenue_ingested_at, c.cost_accession,
                   c.cost_form, c.cost_filed, c.cost_accessions, c.cost_ingested_at,
                   c.revenue_start AS period_start
            FROM computable c
            UNION ALL
            SELECT s.company_id, s.period_end,
                   to_char(s.period_end, 'YYYYMMDD') || to_char(s.period_start, 'YYYYMMDD'), NULL,
                   NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                   NULL, NULL
            FROM series s
            WHERE s.metric = 'gross_profit'
        ) x
    ),
    pairs AS (
        SELECT o.company_id, o.period_start, o.period_end, o.revenue, o.cost, o.unit, o.margin,
               CASE WHEN o.cost_filed > o.revenue_filed THEN o.cost_accession
                    ELSE o.revenue_accession END AS accession_no,
               CASE WHEN o.cost_filed > o.revenue_filed THEN o.cost_form
                    ELSE o.revenue_form END AS form,
               greatest(o.cost_filed, o.revenue_filed) AS filed,
               ARRAY(SELECT DISTINCT a FROM unnest(o.revenue_accessions || o.cost_accessions) a
                     ORDER BY 1) AS source_accessions,
               greatest(o.cost_ingested_at, o.revenue_ingested_at) AS ingested_at
        FROM overlap o
        WHERE o.line IS NULL
          AND NOT (o.next_line IS NOT NULL
                   AND to_date(substr(o.next_line, 9, 8), 'YYYYMMDD') <= o.period_end - 7)
    ),
    usual AS (
        SELECT company_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY margin)::numeric AS margin
        FROM pairs GROUP BY company_id
    )
    SELECT s.company_id, s.metric, s.period_start, s.period_end, s.value, s.unit, s.value_source,
           s.source_tag, s.accession_no, s.form, s.filed, s.source_accessions, s.ingested_at
    FROM series s
    UNION ALL
    SELECT p.company_id, 'gross_profit', p.period_start, p.period_end, p.revenue - p.cost, p.unit,
           'derived', 'derived:revenue-cost_of_revenue', p.accession_no, p.form, p.filed,
           p.source_accessions, p.ingested_at
    FROM pairs p
    JOIN usual u USING (company_id)
    WHERE abs(p.margin - u.margin) <= 0.08
$$;

CREATE FUNCTION gold.derived_rows(p_company_ids text[], p_as_of date DEFAULT NULL,
                                  p_quarters gold.quarter_row[] DEFAULT NULL)
RETURNS SETOF gold.derived_row
LANGUAGE sql STABLE AS $$
    -- 085: the quarters these rows are computed from: gold's (the build just wrote them), the
    -- rows given, or the quarters as of p_as_of
    WITH stored AS MATERIALIZED (
        SELECT g.company_id, g.metric, g.period_start, g.period_end, g.value, g.unit,
               g.value_source, g.source_tag, g.accession_no, g.form, g.filed,
               g.source_accessions, g.ingested_at
        FROM gold.quarterly_financials g
        WHERE p_quarters IS NULL AND p_as_of IS NULL AND g.company_id = ANY(p_company_ids)
        UNION ALL
        SELECT u.* FROM unnest(p_quarters) u
        WHERE p_quarters IS NOT NULL AND u.company_id = ANY(p_company_ids)
        UNION ALL
        SELECT r.* FROM gold.quarterly_rows(p_company_ids, p_as_of) r
        WHERE p_quarters IS NULL AND p_as_of IS NOT NULL
    ),
    quarters AS (
        SELECT g.*,
               coalesce(g.period_start - lag(g.period_end) OVER w BETWEEN 0 AND 7, false)
                   AS follows
        FROM stored g
        JOIN silver.metrics m ON m.metric = g.metric
        WHERE m.kind = 'duration' AND m.additive
        WINDOW w AS (PARTITION BY g.company_id, g.metric ORDER BY g.period_end)
    ),
    windows AS (
        SELECT q.*,
               count(*) OVER w4 AS n_quarters,
               bool_and(follows) OVER w3 AS contiguous,
               first_value(period_start) OVER w4 AS ttm_start,
               sum(value) OVER w4 AS ttm_value,
               max(ingested_at) OVER w4 AS ttm_ingested_at,
               jsonb_agg(jsonb_build_object('period_start', period_start,
                                            'period_end', period_end, 'value', value,
                                            'accession_no', accession_no)) OVER w4 AS parts,
               source_accessions || lag(source_accessions, 1) OVER w
                   || lag(source_accessions, 2) OVER w || lag(source_accessions, 3) OVER w
                   AS accessions
        FROM quarters q
        WINDOW w AS (PARTITION BY company_id, metric ORDER BY period_end),
               w4 AS (w ROWS 3 PRECEDING),
               w3 AS (w ROWS 2 PRECEDING)
    ),
    -- 014's TTM sums (the first INSERT)
    sums AS MATERIALIZED (
        SELECT company_id, 'ttm'::text AS span, metric, ttm_start AS period_start, period_end,
               ttm_value AS value, unit, 'ttm'::text AS value_source, source_tag,
               jsonb_build_object('quarters', parts) AS inputs, accession_no, form, filed,
               ARRAY(SELECT DISTINCT a FROM unnest(accessions) a ORDER BY 1) AS source_accessions,
               ttm_ingested_at AS ingested_at
        FROM windows
        WHERE n_quarters = 4 AND contiguous AND period_end - ttm_start + 1 BETWEEN 350 AND 380
    ),
    -- a year the 10-K states, where four quarters don't exist to sum (header). Not the quarters'
    -- gross_profit: a filing that printed one has it here, and revenue less cost is a quarter's rule.
    -- Ends within 7 days are one period (031's rule): ATI's FY2021 is tagged ending 2021-12-31 in one
    -- 10-K and 2022-01-02 in a later one, and was in gold twice. One per period end: two annual
    -- facts can end on one day (a re-labelled start).
    stated_years AS MATERIALIZED (
        SELECT DISTINCT ON (f.company_id, f.metric, f.period_end) f.*
        FROM gold.best_facts(p_company_ids, p_as_of) f  -- 085
        JOIN silver.metrics m ON m.metric = f.metric AND m.kind = 'duration'
        WHERE f.period_type = 'annual' AND f.additive
        ORDER BY f.company_id, f.metric, f.period_end, f.priority, f.filed DESC,
                 f.accession_no DESC, f.period_start DESC
    ),
    -- 085: 040's two reads of the quarters for each year (one ending on the year's end; the sum of
    -- those inside it, from 7 days before its start to 7 days after its end) in one pass over the
    -- quarters and the years. The quarters inside = those ending by its end + 7, less those starting
    -- before its start - 7: a quarter lasts 120 days at most, so one starting that early ends long
    -- before the year does.
    year_marks AS (
        SELECT x.*,
               sum(x.quarter_value) OVER (PARTITION BY x.company_id, x.metric ORDER BY x.k_end
                                          RANGE UNBOUNDED PRECEDING) AS ending_by,
               sum(x.quarter_value) OVER (PARTITION BY x.company_id, x.metric ORDER BY x.k_start
                                          RANGE BETWEEN UNBOUNDED PRECEDING AND '1 day' PRECEDING)
                   AS starting_before,
               bool_or(x.quarter_value IS NOT NULL)
                   OVER (PARTITION BY x.company_id, x.metric, x.own_end) AS in_series
        FROM (
            SELECT y.company_id, y.metric, y.period_end + 7 AS k_end, y.period_start - 7 AS k_start,
                   y.period_end AS own_end, NULL::numeric AS quarter_value, true AS is_year,
                   y.tag, y.taxonomy, y.unit, y.period_start, y.period_end, y.value, y.accession_no,
                   y.form, y.filed, y.ingested_at
            FROM stated_years y
            UNION ALL
            SELECT q.company_id, q.metric, q.period_end, q.period_start, q.period_end, q.value, false,
                   NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
            FROM stored q
            WHERE q.metric IN (SELECT m.metric FROM silver.metrics m
                               WHERE m.kind = 'duration' AND m.additive)
        ) x
    ),
    years AS (
        SELECT y.company_id, y.metric, y.tag, y.taxonomy, y.unit, y.period_start, y.period_end,
               y.value, y.accession_no, y.form, y.filed, y.ingested_at, y.in_series
        FROM year_marks y
        WHERE y.is_year
          -- a year that can't hold the quarters gold has for it is another tag's part, not the year
          -- (Clover Health's 2022 revenue at 1,097 beside quarters of about 800 each; Peoples
          -- Bancorp's 2018 at -127): only for the metrics that can't be negative (029's list)
          AND NOT (y.metric IN ('revenue', 'cost_of_revenue', 'research_and_development', 'sga',
                                'capex', 'depreciation_amortization', 'stock_based_compensation',
                                'share_buybacks', 'dividends_paid')
                   AND (y.value < 0 OR y.value < 0.99 * (coalesce(y.ending_by, 0)
                                                         - coalesce(y.starting_before, 0))))
    ),
    -- 040's stated years (the second INSERT): none where a TTM sum ends within 7 days (085: in the
    -- same pass as 063's window, the sums in the years' order); of two stated years ending within 7
    -- days, one: the end gold's quarters use, then the later filing, then the later end (063: the
    -- greatest (in_series, filed, period_end) among the years ending within 7 days of this one, as
    -- text that sorts like the row)
    year_choice AS (
        SELECT x.*,
               max(x.rank_key) OVER w AS best_key,
               bool_or(x.is_sum) OVER w AS near_sum
        FROM (
            SELECT y.company_id, y.metric, y.period_end, false AS is_sum,
                   y.in_series::int::text || to_char(y.filed, 'YYYYMMDD')
                       || to_char(y.period_end, 'YYYYMMDD') AS rank_key,
                   y.tag, y.taxonomy, y.unit, y.period_start, y.value, y.accession_no, y.form,
                   y.filed, y.ingested_at
            FROM years y
            UNION ALL
            SELECT s.company_id, s.metric, s.period_end, true, NULL, NULL, NULL, NULL, NULL, NULL,
                   NULL, NULL, NULL, NULL
            FROM sums s
        ) x
        WINDOW w AS (PARTITION BY x.company_id, x.metric ORDER BY x.period_end
                     RANGE BETWEEN '7 days' PRECEDING AND '7 days' FOLLOWING)
    ),
    stated AS (
        SELECT b.company_id, 'ttm'::text AS span, b.metric, b.period_start, b.period_end, b.value,
               b.unit, 'ttm'::text AS value_source, b.taxonomy || ':' || b.tag AS source_tag,
               jsonb_build_object('quarters', jsonb_build_array(jsonb_build_object(
                   'period_start', b.period_start, 'period_end', b.period_end, 'value', b.value,
                   'accession_no', b.accession_no))) AS inputs,
               b.accession_no, b.form, b.filed, ARRAY[b.accession_no] AS source_accessions,
               b.ingested_at
        FROM year_choice b
        WHERE NOT b.is_sum AND NOT b.near_sum AND b.rank_key = b.best_key
    ),
    ttm AS MATERIALIZED (
        SELECT * FROM sums
        UNION ALL
        SELECT * FROM stated
    ),

    -- 056: a computed amount (free cash flow, net debt, cash and investments) is in the currency of
    -- its inputs: the company's, which every monetary row of its gold shares (gold.usable_facts)
    currency AS (
        SELECT DISTINCT ON (q.company_id) q.company_id, q.unit
        FROM stored q
        WHERE q.unit ~ '^[A-Z]{3}$'
        ORDER BY q.company_id, q.period_end DESC
    ),
    facts AS (
        SELECT 'quarter' AS span, company_id, metric, period_start, period_end, value,
               accession_no, form, filed, source_accessions, ingested_at
        FROM stored
        UNION ALL
        SELECT 'ttm', company_id, metric, period_start, period_end, value,
               accession_no, form, filed, source_accessions, ingested_at
        FROM ttm  -- the TTM sums and stated years above
        UNION ALL
        SELECT 'ttm', g.company_id, g.metric, g.period_start, g.period_end, g.value,
               g.accession_no, g.form, g.filed, g.source_accessions, g.ingested_at
        FROM stored g
        JOIN silver.metrics m ON m.metric = g.metric
        WHERE m.kind = 'instant'
    ),
    base AS (  -- the inputs of derived metrics
        SELECT * FROM facts
        WHERE metric IN ('revenue', 'gross_profit', 'operating_income', 'net_income',
                         'eps_diluted', 'operating_cash_flow', 'capex', 'dividends_paid',
                         'long_term_debt', 'long_term_debt_current', 'cash',
                         'short_term_investments', 'current_assets', 'current_liabilities',
                         'stockholders_equity', 'total_assets', 'receivables', 'inventory',
                         -- 048: the efficiency ratio's inputs
                         'net_interest_income', 'noninterest_income', 'noninterest_expense',
                         -- 084: gross margin on net sales
                         'net_sales', 'cost_of_revenue')
    ),
    year_ago AS (
        SELECT b.*,
               last_value(value) OVER ya AS ya_value,
               last_value(source_accessions) OVER ya AS ya_accessions,
               -- 085: 040's two reads by key of each period (debt, below) in this pass: the latest
               -- positive short-term investments of gold's quarters in the two years before the
               -- period (its end and value as text: the latest sorts last), and the TTM interest
               -- expense ending there (any span's period reads the TTM one)
               max((to_char(b.period_end, 'YYYYMMDD') || b.value::text) COLLATE "C") FILTER (
                   WHERE b.span = 'quarter' AND b.metric = 'short_term_investments'
                     AND b.value > 0)
                   OVER (PARTITION BY b.company_id ORDER BY b.period_end
                         RANGE BETWEEN '730 days' PRECEDING AND '1 day' PRECEDING)
                   AS investments_mark,
               max(b.value) FILTER (WHERE b.span = 'ttm' AND b.metric = 'interest_expense')
                   OVER (PARTITION BY b.company_id, b.period_end) AS interest_mark
        FROM (
            SELECT f.*, false AS mark_only FROM base f
            UNION ALL
            SELECT f.*, true FROM facts f WHERE f.span = 'ttm' AND f.metric = 'interest_expense'
        ) b
        WINDOW ya AS (PARTITION BY span, company_id, metric ORDER BY period_end
                      RANGE BETWEEN '380 days' PRECEDING AND '350 days' PRECEDING)
    ),
    periods AS (  -- one row per (span, company, period end), with each input metric's value
        SELECT span, company_id, period_end,
               max(value) FILTER (WHERE metric = 'revenue') AS revenue,
               max(value) FILTER (WHERE metric = 'gross_profit') AS gross_profit,
               max(value) FILTER (WHERE metric = 'operating_income') AS operating_income,
               max(value) FILTER (WHERE metric = 'net_income') AS net_income,
               max(value) FILTER (WHERE metric = 'eps_diluted') AS eps_diluted,
               max(value) FILTER (WHERE metric = 'operating_cash_flow') AS operating_cash_flow,
               max(value) FILTER (WHERE metric = 'capex') AS capex,
               max(value) FILTER (WHERE metric = 'dividends_paid') AS dividends_paid,
               max(value) FILTER (WHERE metric = 'long_term_debt') AS long_term_debt,
               max(value) FILTER (WHERE metric = 'long_term_debt_current')
                   AS long_term_debt_current,
               max(value) FILTER (WHERE metric = 'cash') AS cash,
               max(value) FILTER (WHERE metric = 'short_term_investments')
                   AS short_term_investments,
               max(value) FILTER (WHERE metric = 'current_assets') AS current_assets,
               max(value) FILTER (WHERE metric = 'current_liabilities') AS current_liabilities,
               max(value) FILTER (WHERE metric = 'stockholders_equity') AS stockholders_equity,
               max(value) FILTER (WHERE metric = 'total_assets') AS total_assets,
               max(value) FILTER (WHERE metric = 'receivables') AS receivables,
               max(value) FILTER (WHERE metric = 'inventory') AS inventory,
               -- 048
               max(value) FILTER (WHERE metric = 'net_interest_income') AS net_interest_income,
               max(value) FILTER (WHERE metric = 'noninterest_income') AS noninterest_income,
               max(value) FILTER (WHERE metric = 'noninterest_expense') AS noninterest_expense,
               -- 084
               max(value) FILTER (WHERE metric = 'net_sales') AS net_sales,
               max(value) FILTER (WHERE metric = 'cost_of_revenue') AS cost_of_revenue,
               max(ya_value) FILTER (WHERE metric = 'revenue') AS revenue_year_ago,
               max(ya_value) FILTER (WHERE metric = 'net_income') AS net_income_year_ago,
               max(ya_value) FILTER (WHERE metric = 'eps_diluted') AS eps_diluted_year_ago,
               max(ya_value) FILTER (WHERE metric = 'stockholders_equity')
                   AS stockholders_equity_year_ago,
               max(ya_value) FILTER (WHERE metric = 'long_term_debt') AS long_term_debt_year_ago,
               jsonb_object_agg(metric, jsonb_build_object(
                   'period_start', period_start, 'accession_no', accession_no, 'form', form,
                   'filed', filed, 'accessions', source_accessions,
                   'ingested_at', ingested_at, 'year_ago_accessions', ya_accessions)) AS prov,
               max(investments_mark) AS investments_mark,  -- 085: one value per period
               max(interest_mark) AS interest_mark
        FROM year_ago
        WHERE NOT mark_only  -- 085
        GROUP BY span, company_id, period_end
    ),
    debt AS (  -- the net debt rule's reading of each balance sheet (header)
        SELECT p.*,
               coalesce(p.short_term_investments, 0) AS investments_now,
               p.current_assets - p.cash - coalesce(p.receivables, 0) - coalesce(p.inventory, 0)
                   AS room,
               -- 049: also where there is cash and no debt, for cash_and_investments (header);
               -- net_debt reads it only with debt, so it doesn't change (085: year_ago's marks)
               CASE WHEN p.short_term_investments IS NULL
                         AND (p.long_term_debt IS NOT NULL OR p.cash IS NOT NULL)
                    THEN substr(p.investments_mark, 9)::numeric END AS last_investments,
               CASE WHEN p.short_term_investments IS NULL
                         AND (p.long_term_debt IS NOT NULL OR p.cash IS NOT NULL)
                    THEN to_date(left(p.investments_mark, 8), 'YYYYMMDD') END
                   AS last_investments_end,
               CASE WHEN p.long_term_debt IS NOT NULL THEN p.interest_mark END AS interest
        FROM periods p
    ),
    derived AS (
        SELECT p.span, p.company_id, p.period_end, p.prov, d.metric,
               CASE WHEN dm.unit = 'ratio' THEN round(d.value, 6) ELSE d.value END AS value,
               dm.unit, jsonb_strip_nulls(d.inputs) AS inputs
        FROM debt p
        CROSS JOIN LATERAL (
            SELECT p.long_term_debt + coalesce(p.long_term_debt_current, 0) AS total,
                   p.long_term_debt + coalesce(p.long_term_debt_current, 0) > 0
                   AND p.interest > 2 * (p.long_term_debt + coalesce(p.long_term_debt_current, 0))
                   AND coalesce(p.long_term_debt_year_ago <= 2 * (p.long_term_debt
                                + coalesce(p.long_term_debt_current, 0)), true) AS sliver,
                   p.short_term_investments > p.room * 1.01 AS in_cash,
                   p.last_investments >= 0.01 * p.total_assets
                   AND p.last_investments >= 0.10 * abs(p.long_term_debt
                                                        + coalesce(p.long_term_debt_current, 0) - p.cash)
                   AND (p.room IS NULL OR p.last_investments <= p.room) AS unknown,
                   -- 049: investments missing here but reported lately: unknown, not zero
                   -- (unknown's test without the part about net debt's size)
                   p.last_investments >= 0.01 * p.total_assets
                   AND (p.room IS NULL OR p.last_investments <= p.room) AS investments_unknown
        ) r
        CROSS JOIN LATERAL (VALUES
            -- 084: on net sales where gold reads a company's net sales apart from its revenue
            -- (membership and other income: Walmart, Costco), for every period that has both
            ('gross_margin',
             CASE WHEN p.net_sales > 0 AND p.cost_of_revenue IS NOT NULL
                  THEN (p.net_sales - p.cost_of_revenue) / p.net_sales
                  WHEN p.revenue > 0 THEN p.gross_profit / p.revenue END,
             CASE WHEN p.net_sales > 0 AND p.cost_of_revenue IS NOT NULL
                  THEN jsonb_build_object('net_sales', p.net_sales,
                                          'cost_of_revenue', p.cost_of_revenue)
                  ELSE jsonb_build_object('gross_profit', p.gross_profit, 'revenue', p.revenue)
             END),
            ('operating_margin',
             CASE WHEN p.revenue > 0 THEN p.operating_income / p.revenue END,
             jsonb_build_object('operating_income', p.operating_income, 'revenue', p.revenue)),
            ('net_margin',
             CASE WHEN p.revenue > 0 THEN p.net_income / p.revenue END,
             jsonb_build_object('net_income', p.net_income, 'revenue', p.revenue)),
            ('free_cash_flow',
             p.operating_cash_flow - p.capex,
             jsonb_build_object('operating_cash_flow', p.operating_cash_flow,
                                'capex', p.capex)),
            ('fcf_margin',
             CASE WHEN p.revenue > 0 THEN (p.operating_cash_flow - p.capex) / p.revenue END,
             jsonb_build_object('operating_cash_flow', p.operating_cash_flow,
                                'capex', p.capex, 'revenue', p.revenue)),
            ('revenue_growth',
             CASE WHEN p.revenue_year_ago > 0 THEN p.revenue / p.revenue_year_ago - 1 END,
             jsonb_build_object('revenue', p.revenue,
                                'revenue_year_ago', p.revenue_year_ago)),
            ('net_income_growth',
             CASE WHEN p.net_income_year_ago > 0
                  THEN p.net_income / p.net_income_year_ago - 1 END,
             jsonb_build_object('net_income', p.net_income,
                                'net_income_year_ago', p.net_income_year_ago)),
            ('eps_growth',
             CASE WHEN p.eps_diluted_year_ago > 0
                  THEN p.eps_diluted / p.eps_diluted_year_ago - 1 END,
             jsonb_build_object('eps_diluted', p.eps_diluted,
                                'eps_diluted_year_ago', p.eps_diluted_year_ago)),
            ('payout_ratio',
             CASE WHEN p.net_income > 0 THEN p.dividends_paid / p.net_income END,
             jsonb_build_object('dividends_paid', p.dividends_paid,
                                'net_income', p.net_income)),
            ('debt_to_equity',
             CASE WHEN p.stockholders_equity > 0 AND r.sliver IS NOT TRUE
                  THEN r.total / p.stockholders_equity END,
             jsonb_build_object('long_term_debt', p.long_term_debt,
                                'long_term_debt_current', p.long_term_debt_current,
                                'stockholders_equity', p.stockholders_equity)),
            ('net_debt',
             CASE WHEN r.sliver IS NOT TRUE AND r.unknown IS NOT TRUE
                  THEN r.total - p.cash
                       - CASE WHEN r.in_cash THEN 0 ELSE p.investments_now END END,
             jsonb_build_object('long_term_debt', p.long_term_debt,
                                'long_term_debt_current', p.long_term_debt_current,
                                'cash', p.cash,
                                'short_term_investments',
                                CASE WHEN r.in_cash IS NOT TRUE THEN p.short_term_investments END)),
            ('current_ratio',
             CASE WHEN p.current_liabilities > 0
                  THEN p.current_assets / p.current_liabilities END,
             jsonb_build_object('current_assets', p.current_assets,
                                'current_liabilities', p.current_liabilities)),
            ('roe',
             CASE WHEN p.span = 'ttm'
                       AND p.stockholders_equity + p.stockholders_equity_year_ago > 0
                  THEN p.net_income
                       / ((p.stockholders_equity + p.stockholders_equity_year_ago) / 2) END,
             jsonb_build_object('net_income', p.net_income,
                                'stockholders_equity', p.stockholders_equity,
                                'stockholders_equity_year_ago',
                                p.stockholders_equity_year_ago)),
            -- 048: a bank's cost per dollar of revenue (header)
            ('efficiency_ratio',
             CASE WHEN p.net_interest_income + p.noninterest_income > 0
                  THEN p.noninterest_expense / (p.net_interest_income + p.noninterest_income) END,
             jsonb_build_object('noninterest_expense', p.noninterest_expense,
                                'net_interest_income', p.net_interest_income,
                                'noninterest_income', p.noninterest_income)),
            -- 049: cash and short-term investments, by net debt's reading of them (header)
            ('cash_and_investments',
             CASE WHEN r.investments_unknown IS NOT TRUE
                  THEN p.cash + CASE WHEN r.in_cash THEN 0 ELSE p.investments_now END END,
             jsonb_build_object('cash', p.cash,
                                'short_term_investments',
                                CASE WHEN r.in_cash IS NOT TRUE THEN p.short_term_investments END))
        ) d(metric, value, inputs)
        JOIN gold.derived_metrics dm ON dm.metric = d.metric
        WHERE d.value IS NOT NULL
    )
    SELECT t.company_id, t.span, t.metric, t.period_start, t.period_end, t.value, t.unit,
           t.value_source, t.source_tag, t.inputs, t.accession_no, t.form, t.filed,
           t.source_accessions, t.ingested_at
    FROM ttm t
    UNION ALL
    -- the computed metrics (the third INSERT)
    SELECT d.company_id, d.span, d.metric, c.period_start, d.period_end, d.value,
           CASE WHEN d.unit = 'USD' THEN coalesce(cu.unit, 'USD') ELSE d.unit END,  -- 056
           'computed', NULL, d.inputs, c.accession_no, c.form, c.filed, s.accessions, c.ingested_at
    FROM derived d
    LEFT JOIN currency cu ON cu.company_id = d.company_id  -- 056
    -- provenance of the inputs at this period end: latest filing, and the earliest start
    -- (NULL when all are instants)
    CROSS JOIN LATERAL (
        SELECT (array_agg(j->>'accession_no' ORDER BY j->>'filed' DESC,
                          j->>'accession_no' DESC))[1] AS accession_no,
               (array_agg(j->>'form' ORDER BY j->>'filed' DESC,
                          j->>'accession_no' DESC))[1] AS form,
               max((j->>'filed')::date) AS filed,
               min((j->>'period_start')::date) AS period_start,
               max((j->>'ingested_at')::timestamptz) AS ingested_at
        FROM jsonb_object_keys(d.inputs) k
        CROSS JOIN LATERAL (SELECT d.prov->k) x(j)
        WHERE k NOT LIKE '%\_year\_ago'
    ) c
    -- every filing behind the inputs, year-ago ones included
    CROSS JOIN LATERAL (
        SELECT array_agg(DISTINCT a ORDER BY a) AS accessions
        FROM jsonb_object_keys(d.inputs) k
        CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE WHEN k LIKE '%\_year\_ago' THEN d.prov->left(k, -9)->'year_ago_accessions'
                 ELSE d.prov->k->'accessions' END) a
    ) s
$$;

-- 043's writer: the rows from gold.quarterly_rows. Returns the series' rows (043's first INSERT's
-- count), as before.
CREATE OR REPLACE FUNCTION gold.refresh_quarterly_financials(p_company_ids text[])
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('gold.quarterly_financials'), hashtext(id))
    FROM (SELECT DISTINCT id FROM unnest(p_company_ids) id ORDER BY id) ids;

    DELETE FROM gold.quarterly_financials WHERE company_id = ANY(p_company_ids);

    WITH inserted AS (
        INSERT INTO gold.quarterly_financials
            (company_id, metric, period_start, period_end, value, unit, value_source, source_tag,
             accession_no, form, filed, source_accessions, ingested_at)
        SELECT r.company_id, r.metric, r.period_start, r.period_end, r.value, r.unit,
               r.value_source, r.source_tag, r.accession_no, r.form, r.filed, r.source_accessions,
               r.ingested_at
        FROM gold.quarterly_rows(p_company_ids) r
        ORDER BY r.company_id, r.metric, r.period_end
        RETURNING source_tag
    )
    SELECT count(*) FILTER (WHERE source_tag <> 'derived:revenue-cost_of_revenue') INTO n
    FROM inserted;

    RETURN n;
END
$$;

-- 084's writer: the rows from gold.derived_rows over the quarters just written.
CREATE OR REPLACE FUNCTION gold.refresh_derived_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('gold.quarterly_financials'), hashtext(id))
    FROM (SELECT DISTINCT id FROM unnest(p_company_ids) id ORDER BY id) ids;

    DELETE FROM gold.derived_financials WHERE company_id = ANY(p_company_ids);

    INSERT INTO gold.derived_financials
        (company_id, span, metric, period_start, period_end, value, unit, value_source,
         source_tag, inputs, accession_no, form, filed, source_accessions, ingested_at)
    SELECT r.company_id, r.span, r.metric, r.period_start, r.period_end, r.value, r.unit,
           r.value_source, r.source_tag, r.inputs, r.accession_no, r.form, r.filed,
           r.source_accessions, r.ingested_at
    FROM gold.derived_rows(p_company_ids) r
    ORDER BY r.company_id, r.span, r.metric, r.period_end;
    GET DIAGNOSTICS n = ROW_COUNT;

    RETURN n;
END
$$;
