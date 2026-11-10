-- Two revenue rules in gold.best_facts (039's body, edits marked "064"), each about a figure gold
-- believed that isn't the company's revenue.
--
-- 1. A net revenue sliver. 039 reads Revenues under 20% of revenue from contracts with customers as
-- a part, a line of the total rather than the total (Verra Mobility 13%, Teradata 1.5%).
-- RevenuesNetOfInterestExpense, revenue's first tag, had no such rule, so a filer that tags a small
-- line under it beside its total had the line believed. Rectitude Holdings (a 20-F filer, SGD) tags
-- it S$106,263 / 83,659 / 74,328 for the years to March 2023-2025 beside Revenues of S$37.6M /
-- 41.4M / 43.8M in the same filings: gold read S$74,328 for FY2025, and the screener's revenue
-- growth sorted it first at +68,870% (FY2026's S$51.26M over it). Its other way to the top, 057's
-- $100K / $10M bases applying to dollar amounts only, is deliberate and stays. Now
-- RevenuesNetOfInterestExpense under 20% of the period's largest Revenues /
-- RevenueFromContractWithCustomer(Ex|In)cludingAssessedTax is a part too, sorted after the other
-- tags exactly as 039's Revenues part (each tag's latest filing, one window per company, metric and
-- period; a zero stays with 039's zero rule). 20% as in 039: a broker's or lender's net revenue
-- really is a fraction of its gross, interest expense and fair-value marks coming off it (periods
-- at Nomura 36-49%, Atlanticus 27-48%, Oportun 23-50%, Jefferies 42%; Goldman tags no gross line at
-- all).
--
-- 2. A scale slip. Two revenue tags of one period 1,000 or 1,000,000 times apart (within 0.5%) are
-- one figure in two scales, and either can be the wrong one: Tigo Energy tags contract revenue
-- 1,000 times too large in every filing ($50.06B beside Revenues of $50.06M), while Plexus tags it
-- in thousands (982,607 beside $982,607,000) and FactSet, CHS, Rexford, BK Technologies,
-- FinVolution and most others a second tag in thousands. The rest of the period decides: the larger
-- value is the slip when it is over 100 times every other money figure of the same company, start
-- and end (cost of revenue, operating income, net income, cash flows...), else the smaller is (also
-- when the period has no other figure). A real revenue is not a hundred times every cost and result
-- of its own period, and a figure 1,000 times too small sits at a tenth of them at most. A slip is
-- no evidence for revenue's window rules (the zero rule's filings, the parts' gross line) and sorts
-- after every other tag. Without this rule, rule 1 alone handed Tigo's 2023 to its $50B tag; 039
-- already read that tag for Tigo's 2022, Q4 2024 and Q4 2025, because the slip made its right
-- Revenues a part. The pair is looked for in the period's window, not in one accession: best_facts
-- weighs each tag's latest filing, and 22 of the 74 slip windows on hermes pair two filings
-- (requiring one accession would leave Southwest's, Forward's and Ernexa's figures in thousands).
--
-- Measured on hermes (2026-11-10, at 063): best_facts' revenue choice under 039's order and this
-- one, over gold.usable_facts of the 128 companies with the net tag and the 41 with a revenue pair
-- 1,000x apart in silver (none through a bank's sum of two tags). Net revenue against the gross
-- line: 149 periods under 20% (16 companies), 239 at 20-80% (26), 1,055 at 80-100% (79), 349 above
-- (33). Scale: 74 slip windows at 25 companies, the larger value the slip at Tigo (13), SS
-- Innovations (1) and New Concept Energy (2), the smaller at the other 22. 184 picks change at 24
-- companies and nothing else does. Rule 1: StoneX 109 (its net operating revenues, 0.2-4.8% of
-- total revenues, which is what most data vendors show), Rectitude 6, Crown Equity 6, Solowin 6,
-- OptimizeRx 3, Siebert 2, Nutex 2, one each at Gulf Resources, Oportun, Purple, Zedge, Nano-X,
-- Amaze, Dolphin (2016) and OneMeta (2011). Rule 2: Plexus 15 (gold read its quarters in thousands
-- since late 2023: TTM to July 2026 $4.03B -> $4.60B), Tigo 14 (with rule 1: every 2022-2026
-- quarter now its $9-69M Revenues, latest TTM $103.5B -> $104.7M), Simulations Plus 5, Trio-Tech 2,
-- Ernexa 2, Southwest, Veru, Forward and SS Innovations 1 each (2010-2012 figures in thousands; SS
-- Innovations' 2022 the same value under another tag). Those 24 companies' silver copied to a
-- throwaway database and rebuilt: gold at 063 equal to hermes' row for row; at 064 the quarterly
-- and TTM revenue rows and the margins and growth read from them change, no other metric.
-- Rectitude's FY2025 S$74,328 -> S$43,796,144, its latest growth +68,870% -> +17.1%; StoneX's TTM
-- to June 2026 $2.86B -> $157.7B (growth 48.7% -> 20.6%); Solowin's latest growth +93,393% ->
-- +895%. Left, as noted: Oportun's Q1 2023 net revenue really was $4.8M (a fair-value mark) and now
-- reads its total revenue, $259.5M between net quarters of $143M and $119M.
--
-- Cost: one more window sort over a batch's facts and a test over the period's few revenue values
-- per revenue row. best_facts for production's first 75 covered companies, inline on hermes: 90-97
-- s either way; 24 companies on a laptop 0.70 s -> 0.79 s. Generated from 039's body by a script
-- asserting each anchor once. Locks on hermes: CREATE OR REPLACE of one function, catalog only
-- (lock_timeout 10 s). Gold changes when the financials-rebuild job runs for those 24 companies'
-- CIKs (or as the nightly jobs refresh them). Backout: db/backout/revenue_part_064.sql (039's
-- body).

SET LOCAL lock_timeout = '10s';

CREATE OR REPLACE FUNCTION gold.best_facts(p_company_ids text[])
RETURNS TABLE (company_id text, metric text, tag text, taxonomy text, unit text, period_start date,
               period_end date, period_type text, value numeric, accession_no text, form text,
               filed date, ingested_at timestamptz, cik text, priority numeric, additive boolean)
LANGUAGE sql STABLE AS $$
    WITH tag_latest AS (
        SELECT DISTINCT ON (u.company_id, u.metric, u.tag, u.period_start, u.period_end) u.*
        FROM gold.usable_facts(p_company_ids) u
        ORDER BY u.company_id, u.metric, u.tag, u.period_start, u.period_end, u.filed DESC,
                 u.accession_no DESC, u.cik
    ),
    -- 064: the period's positive revenue values, and the largest of its other money figures (any
    -- other metric with the same start and end), for the scale test below
    scales AS (
        SELECT t.*,
               array_agg(t.value) FILTER (WHERE t.metric = 'revenue' AND t.value > 0) OVER wm
                   AS revenue_values,
               max(abs(t.value)) FILTER (WHERE t.metric <> 'revenue' AND t.unit ~ '^[A-Z]{3}$')
                   OVER wp AS other_max
        FROM tag_latest t
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
                   'RevenueFromContractWithCustomerIncludingAssessedTax')) OVER w AS gross_value
        FROM slips t  -- 064
        WINDOW w AS (PARTITION BY t.company_id, t.metric, t.period_start, t.period_end)
    )
    SELECT DISTINCT ON (x.company_id, x.metric, x.period_start, x.period_end)
           x.company_id, x.metric, x.tag, x.taxonomy, x.unit, x.period_start, x.period_end,
           x.period_type, x.value, x.accession_no, x.form, x.filed, x.ingested_at, x.cik, x.priority,
           x.additive
    FROM weighed x
    ORDER BY x.company_id, x.metric, x.period_start, x.period_end,
             x.slipped,  -- 064: a scale slip after every other tag
             (x.metric = 'revenue' AND x.period_start IS NOT NULL
              AND ((x.value = 0 AND (x.large_filed >= x.filed OR x.small_filed > x.filed))
                   OR (x.tag = 'Revenues' AND x.value > 0 AND x.value < 0.2 * x.contract_value)
                   -- 064: net revenue under 20% of the gross line is a part too (Rectitude 0.2%)
                   OR (x.tag = 'RevenuesNetOfInterestExpense' AND x.value > 0
                       AND x.value < 0.2 * x.gross_value)))
                 IS TRUE,
             x.priority, x.filed DESC, x.accession_no DESC, x.cik
$$;
