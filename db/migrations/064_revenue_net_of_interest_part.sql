-- Revenue's part rule for a net revenue tag. gold.best_facts (039) reads Revenues under 20% of
-- revenue from contracts with customers as a part, a line of the total rather than the total (Verra
-- Mobility 13%, Teradata 1.5%). RevenuesNetOfInterestExpense, revenue's first tag, had no such
-- rule, so a filer that tags a small line under it beside its total revenue had the line believed.
-- Rectitude Holdings (a 20-F filer, SGD) tags it S$106,263 / 83,659 / 74,328 for the years to March
-- 2023-2025 beside Revenues of S$37.6M / 41.4M / 43.8M in the same filings: gold read S$74,328 for
-- FY2025, and the screener's revenue growth sorted it first at +68,870% (FY2026's S$51.26M over
-- it). Its other way to the top, 057's $100K / $10M bases applying to dollar amounts only, is
-- deliberate and stays.
--
-- gold.best_facts (039's body, edits marked "064"): RevenuesNetOfInterestExpense under 20% of the
-- period's largest Revenues / RevenueFromContractWithCustomer(Ex|In)cludingAssessedTax is a part
-- too, sorted after the other tags exactly as 039's Revenues part (each tag's latest filing, one
-- window per company, metric and period; a zero stays with 039's zero rule). 20% as in 039: a
-- broker's or lender's net revenue really is a fraction of its gross, interest expense and
-- fair-value marks coming off it (periods at Nomura 36-49%, Atlanticus 27-48%, Oportun 23-50%,
-- Jefferies 42%; Goldman tags no gross line at all).
--
-- Measured on hermes (2026-11-10, at 063): best_facts' revenue choice under 039's order and this
-- one, over gold.usable_facts of the 128 companies with the tag. Of 1,792 periods where it sits
-- beside a gross line, 149 are under 20% (16 companies), 239 at 20-80% (26), 1,055 at 80-100% (79),
-- 349 above (33). The 149 change pick (143 to Revenues, 6 to contract revenue) and nothing else
-- does: StoneX 109 (its net operating revenues, 0.2-4.8% of total revenues, which is what most data
-- vendors show), Tigo Energy 7, Rectitude 6, Crown Equity 6, Solowin 6, OptimizeRx 3, Siebert 2,
-- Nutex 2, and one each at Gulf Resources, Oportun, Purple, Zedge, Nano-X, Amaze, Dolphin (2016)
-- and OneMeta (2011). Those 16 companies' silver copied to a throwaway database and rebuilt: gold
-- at 063 equal to hermes' row for row; at 064, 85 quarterly revenue rows differ (5 of them fourth
-- quarters that now derive: Siebert's 2, Gulf's, Purple's, Amaze's), 125 TTM revenue rows, and the
-- margins and growth read from them; no other metric. Rectitude's FY2025 S$74,328 -> S$43,796,144,
-- its latest growth +68,870% -> +17.1%; StoneX's TTM to June 2026 $2.86B -> $157.7B (growth 48.7%
-- -> 20.6%); Solowin's latest growth +93,393% -> +895%. Two read worse and are left: Oportun's Q1
-- 2023 net revenue really was $4.8M (a fair-value mark) and now reads its total revenue, $259.5M
-- between net quarters of $143M and $119M; Tigo's 2023 quarters read its contract revenue, which it
-- tags 1,000 times too large ($50.06B for $50.06M) so that 039's rule makes its right Revenues a
-- part, as gold already reads Tigo's 2022, Q4 2024 and Q4 2025 (a scale rule is a change of its
-- own).
--
-- Generated from 039's body by a script asserting each anchor once. Locks on hermes: CREATE OR
-- REPLACE of one function, catalog only (lock_timeout 10 s). Gold changes when the
-- financials-rebuild job runs for those 16 companies' CIKs (or as the nightly jobs refresh them).
-- Backout: db/backout/revenue_part_064.sql (039's body).

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
    weighed AS (  -- revenue's rules (039's and 064's headers) read the period's other tags
        SELECT t.*,
               max(t.filed) FILTER (WHERE t.value >= 1000000) OVER w AS large_filed,
               max(t.filed) FILTER (WHERE t.value > 0 AND t.value < 1000000) OVER w AS small_filed,
               max(t.value) FILTER (WHERE t.tag IN (
                   'RevenueFromContractWithCustomerExcludingAssessedTax',
                   'RevenueFromContractWithCustomerIncludingAssessedTax')) OVER w AS contract_value,
               -- 064: the gross line a net revenue figure is compared with
               max(t.value) FILTER (WHERE t.tag IN ('Revenues',
                   'RevenueFromContractWithCustomerExcludingAssessedTax',
                   'RevenueFromContractWithCustomerIncludingAssessedTax')) OVER w AS gross_value
        FROM tag_latest t
        WINDOW w AS (PARTITION BY t.company_id, t.metric, t.period_start, t.period_end)
    )
    SELECT DISTINCT ON (x.company_id, x.metric, x.period_start, x.period_end)
           x.company_id, x.metric, x.tag, x.taxonomy, x.unit, x.period_start, x.period_end,
           x.period_type, x.value, x.accession_no, x.form, x.filed, x.ingested_at, x.cik, x.priority,
           x.additive
    FROM weighed x
    ORDER BY x.company_id, x.metric, x.period_start, x.period_end,
             (x.metric = 'revenue' AND x.period_start IS NOT NULL
              AND ((x.value = 0 AND (x.large_filed >= x.filed OR x.small_filed > x.filed))
                   OR (x.tag = 'Revenues' AND x.value > 0 AND x.value < 0.2 * x.contract_value)
                   -- 064: net revenue under 20% of the gross line is a part too (Rectitude 0.2%)
                   OR (x.tag = 'RevenuesNetOfInterestExpense' AND x.value > 0
                       AND x.value < 0.2 * x.gross_value)))
                 IS TRUE,
             x.priority, x.filed DESC, x.accession_no DESC, x.cik
$$;
