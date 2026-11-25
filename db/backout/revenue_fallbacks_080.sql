-- Backout of 080_revenue_industry_tags.sql (never a migration: README.md): gold.best_facts back to
-- 077's body, copied unchanged, so a revenue figure under a fifth of an industry total is believed
-- again (Conagra's FY2012-2016 Revenues of $1.6-1.7B, Materion's 2012-2016 quarters of $1.5-21M);
-- and the three revenue industry tags and total assets' LiabilitiesAndStockholdersEquity removed
-- with their silver facts (the 639 companies' revenue history starts late again; SEI Investments
-- without total assets after June 2025). silver.industry_total_exclusions stays (077's rules never
-- read it; 081's coverage sweep does). Then run the financials-rebuild job, which puts the old rows
-- back; the rules version stays 80 (db/CLAUDE.md: a backout leaves the versions).
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as
-- postgres, a function it drops and re-creates would belong to postgres, and the non-superuser
-- migrate login could never replace it again. The guard below refuses any login that doesn't own
-- the functions, and any database where a newer backout hasn't run yet (README.md, Order).
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f revenue_fallbacks_080.sql

BEGIN;
SET LOCAL lock_timeout = '10s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: release_figures_165.sql,
-- analyst_fixes_155.sql, point_in_time_085.sql) and this one hasn't, and unless the login owns the
-- functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('gold.amount_currency(text)', 'd59a928b3b91fb9a6da98fc15e72a536'),
        ('gold.amount_currency_of(text)', '6d18237414dd02bfe4078cac3f7b429e'),
        ('gold.amount_currency_of(text,date)', NULL),
        ('gold.annual_per_share(text,integer)', '663430e80a6341f8a0e4227e1491e657'),
        ('gold.annual_per_share(text,integer,date)', NULL),
        ('gold.best_facts(text[])', '805280aad1147c74d415ea8cc33ba846'),
        ('gold.best_facts(text[],date)', NULL),
        ('gold.companies_for_ciks(text[])', '5f02b9f1e395d7f743635d0a399f8f6c'),
        ('gold.company_confidence(text,text,date)', '139f9512b8629850a2e0f66333e44d37'),
        ('gold.company_financials_at(text,date,text,text[])', NULL),
        ('gold.derived_rows(text[],date,gold.quarter_row[])', NULL),
        ('gold.figure_facts(text,text,date)', '6789892e97611dd6c0260a25f695d7fc'),
        ('gold.filing_figures(text,text)', '51376508ac8a1a66d7e42cc92852346f'),
        ('gold.financial_rows(text[],date)', NULL),
        ('gold.financials_at(date)', NULL),
        ('gold.input_values(jsonb)', NULL),
        ('gold.knowledge_facts(text)', NULL),
        ('gold.known_changed_from(text)', NULL),
        ('gold.latest_equity(text)', '2dbdc33d9580044bb743cbaf5a98f252'),
        ('gold.latest_equity(text,date)', NULL),
        ('gold.latest_period(text)', '3c5a3d7158d43ad4e5cb09aaac1af74d'),
        ('gold.latest_period(text,date)', NULL),
        ('gold.latest_reading(text,text,text,text,date)', '097ab17641e6893f4284b9d08c621637'),
        ('gold.latest_reading(text,text,text,text,date,date)', NULL),
        ('gold.latest_row(text,text,text)', '3863618d66983ddd14bf8fa00e2f62c8'),
        ('gold.latest_row(text,text,text,date)', NULL),
        ('gold.not_applicable_metrics(text,text)', '31aa1cba6092282684aaf6b699539359'),
        ('gold.not_computed(text,text)', 'a1d6b45f36a10d029335de6f30893f96'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', '472cb5abb2f6cb3ad0cbf64734468ee4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', '9f4c2cd4aeaefa663263efb806700f5d'),
        ('gold.not_meaningful_for(text,text)', NULL),
        ('gold.quarterly_recent(text,date)', '91ece4790c2b876518c6d2a4cf060286'),
        ('gold.quarterly_recent(text,date,date)', NULL),
        ('gold.quarterly_rows(text[],date)', NULL),
        ('gold.reading(text,text,text,date,date,numeric,jsonb,text,text,numeric,text,boolean)', '419a0e26e3d341c59e38aa3b5be6dbc7'),
        ('gold.rebuild_known(text[])', NULL),
        ('gold.refresh_derived_financials(text[])', '6e52ee6444dc46cca8bbd52de1a6983e'),
        ('gold.refresh_financials(text[])', '4bd8545d1e9cf94fa665cdee21294039'),
        ('gold.refresh_known(text[])', NULL),
        ('gold.refresh_quarterly_financials(text[])', '84ce53d8fc3987c84012b4360b69fed4'),
        ('gold.replay_companies(text[],boolean)', NULL),
        ('gold.replay_known(text,date)', NULL),
        ('gold.restatements(text,date)', NULL),
        ('gold.screen_backlog()', '972441f449f03e3d8fa36a9748610e85'),
        ('gold.screen_company(text)', 'd0ab770fb304683145115aeb8dcee30d'),
        ('gold.screen_reading(text,text,text,text,date,boolean,numeric,text,boolean)', 'f110e0894f0dff22b6fb8fe0423313fe'),
        ('gold.usable_facts(text[])', '0b0c7a112a9761106084a4fd15dd2a56'),
        ('gold.usable_facts(text[],date)', NULL),
        ('ops.check_financials(text[])', 'a8a94bf23f986a93f3f61c8fc5f35461'),
        ('ops.company_backlog(text)', '97bd3cf942ea7f5c87ed866c0ba563d8'),
        ('ops.company_rule_version(text)', '60744ba7ead2e38d3dc3791e5630df38'),
        ('ops.rebuild_soon(text[])', 'c5a3427c2fac8f4cfdddb3ad14021fd7'),
        ('ops.release_backlog(text)', NULL),
        ('silver.refresh_filing_facts(text[])', '3b400f0ebef7ea4ae47aeac60b50cca6'),
        ('silver.refresh_financial_facts(text[])', '813785ee2a47de9ea6e3a86f6176fef2')
    ) v(fn, want) LOOP
        SELECT pg_get_userbyid(p.proowner) INTO fn_owner FROM pg_proc p WHERE p.oid = w.id;
        IF fn_owner IS NOT NULL AND fn_owner <> current_user THEN
            RAISE EXCEPTION USING MESSAGE = format('run this as %s, the owner of %s (the '
                'migrate login), not as %s: see the header. Nothing was changed.', fn_owner, w.fn,
                current_user);
        END IF;
        IF w.want IS DISTINCT FROM (SELECT md5(p.prosrc) FROM pg_proc p WHERE p.oid = w.id) THEN
            RAISE EXCEPTION USING MESSAGE = format('%s is not where this backout starts: run '
                'the newer backouts first, newest first (%s), and this one only once. Nothing was '
                'changed.', w.fn, 'release_figures_165.sql, analyst_fixes_155.sql, point_in_time_085.sql');
        END IF;
    END LOOP;
END
$guard$;
-- end guard

-- 077's gold.best_facts, copied unchanged
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
                    THEN 0 ELSE t.priority END AS rank
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
             x.rank, x.filed DESC, x.accession_no DESC, x.cik  -- 077: rank, not priority
$$;

DELETE FROM silver.financial_facts
WHERE (metric = 'revenue'
       AND tag IN ('RealEstateRevenueNet', 'SalesRevenueGoodsNet', 'SalesRevenueServicesNet'))
   OR (metric = 'total_assets' AND tag = 'LiabilitiesAndStockholdersEquity');
DELETE FROM silver.metric_tags
WHERE (metric = 'revenue'
       AND tag IN ('RealEstateRevenueNet', 'SalesRevenueGoodsNet', 'SalesRevenueServicesNet'))
   OR (metric = 'total_assets' AND tag = 'LiabilitiesAndStockholdersEquity');

COMMIT;
