-- Revenue under industry tags, and total assets as the balance sheet's other total (coverage, part 2;
-- db/DESIGN.md "080"). Measured 2026-11-16 on hermes' stored companyfacts, read-only, over four
-- samples: the 639 listed companies whose revenue starts a year or more after their net income
-- (076's history_short revenue_tag), 500 companies with figures drawn at random, the 124 with a core
-- metric missing lately (076's metric_missing) and 91 partnerships and LLCs.
--
-- 1. Three industry totals for revenue, read only where no other revenue tag gives the period
--    (priority 11-13, after every revenue tag before them): RealEstateRevenueNet (REITs' and real
--    estate companies' total before 2018), SalesRevenueGoodsNet (manufacturers' "Net sales"),
--    SalesRevenueServicesNet (service companies' total). 038's method: where a filing without the
--    tag gives the same period under a tag gold trusts (the switch to ASC 606's tags in 2018 and
--    later comparatives), is the tag's value that total?
--      SalesRevenueGoodsNet     1,149 of 1,242 periods equal within 0.5% (92.5%), within 5% 93.9%;
--                               under 80% of it (a part) 0.9%; companies with an equal period 169,
--                               with a miss 26
--      SalesRevenueServicesNet    483 of 562 (85.9%), within 5% 90.9%; a part 2.7%; 64 / 22
--      RealEstateRevenueNet       249 of 274 (90.9%), within 5% 92.3%; a part 2.9%; 30 / 7
--      SalesRevenueNet (trusted since 003, the baseline) 861 of 963 (89.4%), within 5% 93.9%; a
--                               part 1.2%; 125 / 27
--    The misses above 5% are restatements (discontinued operations: Campbell, Autoliv, Aptiv) and
--    gross sales before excise taxes (Brown-Forman, Constellation: the tag holds "Sales", net sales
--    is the company's own tag). Checked again against the printed statements (074's statement
--    lines, the filings read so far): goods 79 of 83 titled revenue lines equal, services 17 of 18,
--    real estate 5 of 5. Beside a total in the same filing the tags are parts (goods equal to it in
--    19-54% of periods, 076's 41 of 178): never read there, the total is.
--    Not mapped, measured: ContractsRevenue (79% equal, 10% a part), LicensesRevenue (77%, 20%),
--    OilAndGasRevenue (63%), HealthCareOrganizationRevenue (55%), OilAndGasSalesRevenue (50%),
--    ElectricUtilityRevenue (54%), RoyaltyRevenue (79%, 21%), RevenueMineralSales (74%),
--    RevenueFromLeasedAndOwnedHotels (84%, 57 periods), OperatingLeaseLeaseIncome (a REIT's only
--    line since 2019 at American Homes 4 Rent, ILPT, One Liberty; 21 of 28 switch periods, beside a
--    total 10% equal), InterestAndDividendIncomeOperating (a bank's gross interest income: never
--    its revenue), and every tag with under 50 periods to measure.
--
-- 2. gold.best_facts (077's body, edits marked "080"), three rules for the industry totals:
--    a. One company's own filings decide: an industry total is never read at a company whose
--       filings give it, for some period, under 80% of another revenue tag's value for the same
--       period (it is a part there: Ultragenyx's product sales beside collaboration revenue,
--       Tiptree's real estate line beside an insurer's revenues, Workday's services beside
--       subscriptions; a figure in thousands, Green Brick's, is one too). Over 125% is no part:
--       restatements and gross sales.
--    b. Two of them in one period (goods and services) are parts of a total no tag gives: neither
--       is read. 49 periods at 6 companies of the samples.
--    c. Another revenue tag under 20% of the period's industry total is a part, as 039's Revenues
--       under contract revenue: Conagra's Revenues of $1.6-1.7B beside $13.5B of net sales
--       (FY2012-2016, gold's revenue was the small line), Materion's $1.5-21M quarters beside
--       $250-300M (2012-2016), Camden's $1.8M contract revenue beside $237M of real estate revenue
--       (Q2 2018), Caleres, Timken, Clarus, Centerspace, Patrick, Dycom, TrueBlue: 124 periods at
--       14 companies of the 639, 27 at 3 of the random 500.
--    Zero or negative industry totals are not read.
--
-- 3. LiabilitiesAndStockholdersEquity for total assets (priority 3, after us-gaap and IFRS Assets):
--    the balance sheet's other total. Beside Assets in one filing equal in 59,838 of 59,863 dates
--    (99.96%, at 1,266 companies; the misses typing slips). Fills 228 dates at 32 companies of the
--    samples, SEI Investments' since July 2025 among them (it stopped tagging Assets).
--
-- Effect, on a throwaway database: the samples' stored companyfacts (1,289 CIKs) rebuilt at 077 and
-- at 080. Gold at 077 equals hermes' for the 639 (revenue 20,033 quarters, total assets 32,023, none
-- different; 25 and 24 more on hermes from 055's filing facts).
--    Of the 639, 240 get their revenue history back (goods 149, services 58, real estate 33; 5,979
--    quarters): Consumer Staples 31 of 37, Consumer Discretionary 45 of 60, Industrials 53 of 93,
--    Real Estate 33 of 56, Materials 27 of 46, Technology 24 of 65, Health Care 16 of 191,
--    Financials 6 of 41, Energy 2 of 22, Utilities 1 of 16, Communications 2 of 9. Left, 399: no
--    revenue line at all in those years 139 (biotechs before a first sale), an industry tag not
--    mapped 118, revenue in the 10-Ks only 99 (081's revenue_yearly), held back by rules a-b 27
--    and by 4's exclusions 3, other 13. Church & Dwight's revenue now starts in 2009 (FY2009 $2.52B), Coca-Cola's in 2008,
--    Camden's in 2009, Hub Group's in 2008.
--    Existing figures: 95 revenue quarters change at 10 of the 639, each a zero or a part replaced
--    (Churchill Downs' quarters of 0 in 2012-2016 now $148-409M, Cooper-Standard's 0 now
--    $748-879M, Caleres, Materion, Timken, Clarus, Camden, Hudson Pacific, Compass Minerals,
--    Transcontinental Realty); 2 move to another series (031's rule) at Aytu (a shell's
--    $12,500-21,429 quarters). The random 500: 70 quarters added at 8 companies, none changed. Total assets: 205 dates added at
--    32 companies, none changed. No other quarterly metric moves; margins and growth follow
--    revenue. Gold's rebuild time unchanged (three workers, 508-527 s at 080).
--    Known: Camden's Q3 2018 tags only a part (no industry total): its 2018 TTM stays low.
--
-- 4. silver.industry_total_exclusions: an industry total gold never reads at a company (the owner's
--    call, 2026-11-16), a part in best_facts' rule a; 081's sweep words why revenue starts late:
--      excise       Brown-Forman, Constellation Brands: their pre-2017 goods tag holds sales before
--                   excise taxes (net sales were their own tag): tag less ExciseAndSalesTaxes equals
--                   the later net sales in 7 periods each, so those years read 10-28% high. Of the
--                   25 companies whose industry total exceeds the later total by over 5%, these two
--                   are the only excise cases; the rest are restatements (discontinued operations).
--                   No rule instead: ExciseAndSalesTaxes isn't stored in silver, and Kforce and
--                   MercadoLibre tag it in other periods. Their pre-2017 revenue stays blank.
--      predecessor  Skyline Champion: the registrant is legacy Skyline, Champion the accounting
--                   acquirer (2018); legacy Skyline's goods tag would replace Champion's 2017
--                   comparatives with Skyline's own quarters ($58M against $244-294M). Excluded,
--                   gold reads what it read before 080.
--
-- Not mapped, coverage notes stay (076's sweep): partners' and members' capital for equity
-- (PartnersCapital equal to StockholdersEquity in 9 of 21 switch periods, to the balance sheet's
-- own arithmetic in 61-63%: preferred units held outside equity), commodity trusts' net assets
-- (SIC 6221: no balance sheet totals or cash flow statement), Vale's cash generated by operations
-- (before interest and taxes), an extension tag (Acadian), a combined filing's dimensioned facts
-- (Cinemark since 2022: 073's member picks would read them).
--
-- gold.rules_version() 77 -> 80: the hourly catch-up (071) re-projects every company's stored
-- companyfacts with the new tags and rebuilds its gold, oldest build first, with 078's screener
-- figures in the same build; nothing by hand. About 550 companies a run: every company in ~14 hours.
-- The backouts' guards pin best_facts' new body (scripts/backout_guards.py).
--
-- Locks on hermes (lock_timeout 3 s): one new table; four rows into silver.metric_tags (read by
-- the hourly jobs: ROW EXCLUSIVE), CREATE OR REPLACE of gold.best_facts and gold.rules_version
-- (catalog only; a running caller keeps the body it started with). Nothing is rebuilt here. Undo:
-- db/backout/revenue_fallbacks_080.sql (077's best_facts, the tags and their silver facts gone; the
-- exclusions table stays, unused by 077's rules, read by 081's sweep), then the financials-rebuild
-- job.

SET LOCAL lock_timeout = '3s';

INSERT INTO silver.metric_tags (metric, tag, priority) VALUES
    ('revenue', 'RealEstateRevenueNet', 11),
    ('revenue', 'SalesRevenueGoodsNet', 12),
    ('revenue', 'SalesRevenueServicesNet', 13),
    ('total_assets', 'LiabilitiesAndStockholdersEquity', 3);

-- 4. Industry totals never read at a company (header). No FK: fixtures TRUNCATE silver.companies.
CREATE TABLE silver.industry_total_exclusions (
    company_id text NOT NULL,
    tag        text NOT NULL,
    cause      text NOT NULL CHECK (cause IN ('excise', 'predecessor')),
    PRIMARY KEY (company_id, tag)
);
INSERT INTO silver.industry_total_exclusions (company_id, tag, cause) VALUES
    ('0000014693', 'SalesRevenueGoodsNet', 'excise'),       -- Brown-Forman
    ('0000016918', 'SalesRevenueGoodsNet', 'excise'),       -- Constellation Brands
    ('0000090896', 'SalesRevenueGoodsNet', 'predecessor');  -- Skyline Champion

CREATE OR REPLACE FUNCTION gold.best_facts(p_company_ids text[])
RETURNS TABLE (company_id text, metric text, tag text, taxonomy text, unit text, period_start date,
               period_end date, period_type text, value numeric, accession_no text, form text,
               filed date, ingested_at timestamptz, cik text, priority numeric, additive boolean)
LANGUAGE sql STABLE AS $$
    WITH tag_latest AS (
        SELECT DISTINCT ON (u.company_id, u.metric, u.tag, u.period_start, u.period_end) u.*,
               -- 077: when the tag first gave this period (a later filing's comparative or not)
               min(u.filed) OVER (PARTITION BY u.company_id, u.metric, u.tag, u.period_start,
                                               u.period_end) AS first_filed
        FROM gold.usable_facts(p_company_ids) u
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

CREATE OR REPLACE FUNCTION gold.rules_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 80';
