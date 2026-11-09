-- Figures the testers asked for that filings tag in us-gaap but gold didn't map (F5, H11, #111): a
-- backlog proxy, customer advances, accounts payable, and the cash flow statement's subtotals and
-- change in cash, so it can be tied to the balance sheet. Plus one standardised line, cash and
-- short-term investments, computed by net debt's own rule so the two always agree.
--
-- Tags measured on hermes (2026-11-09, read only, one connection): the raw companyfacts of 599
-- companies drawn at random from those with revenue in gold for a period ending on or after
-- 2025-06-30. Count = companies with a 10-K/10-Q fact under the tag for such a period (in a 10-Q):
--   investing_cash_flow     NetCashProvidedByUsedInInvestingActivities 573 (570), its
--                           ...ContinuingOperations twin 30 (a filer with discontinued operations)
--   financing_cash_flow     NetCashProvidedByUsedInFinancingActivities 591 (585), ...ContinuingOperations 25
--   change_in_cash          CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncrease-
--                           DecreaseIncludingExchangeRateEffect 503 (500), ...ExcludingExchangeRateEffect
--                           104; before 2018 (ASU 2016-18 brought restricted cash in):
--                           CashAndCashEquivalentsPeriodIncreaseDecrease (403 of the 599 ever),
--                           ...ExcludingExchangeRateEffect (20), CashPeriodIncreaseDecrease (29)
--   cash_and_restricted_cash  the cash flow statement's ending cash, what change_in_cash reconciles to:
--                           ...IncludingDisposalGroupAndDiscontinuedOperations 170 (the statement's
--                           total where a business is held for sale), CashCashEquivalentsRestricted-
--                           CashAndRestrictedCashEquivalents 486, and CashAndCashEquivalentsAtCarrying-
--                           Value 505 (before 2018, or no restricted cash). The `cash` metric stays the
--                           balance sheet's line; this one is only the statement's.
--   contract_liabilities_current  ContractWithCustomerLiabilityCurrent 258 (242), DeferredRevenueCurrent
--                           66 (the pre-2018 name some filers kept), CustomerAdvancesCurrent 3 (a filer
--                           whose only such line is advances). Customer advances and billings ahead of
--                           revenue, due within a year: Vertiv's 1,131.9M (2025-09-30) to 4,120.5M
--                           (2026-09-30) beside current liabilities of 3,576.8M to 8,710.7M.
--   contract_liabilities    the total: ContractWithCustomerLiability 158, else current + noncurrent
--                           (ContractWithCustomerLiabilityNoncurrent 114; 038's sum: both parts in one
--                           filing for one date), else DeferredRevenue 41, else its two parts
--   remaining_performance_obligations  RevenueRemainingPerformanceObligation 126 (119): revenue under
--                           contract not yet recognised (ASC 606), the nearest us-gaap element to a
--                           backlog. Vertiv tags none (its backlog is MD&A text: F8); Caterpillar
--                           49.4B, Boeing 745.4B, NVIDIA 3.2B at their latest quarters.
--   accounts_payable        AccountsPayableCurrent 403 (394), AccountsPayableTradeCurrent 30. With
--                           receivables and inventory, the working capital a cash flow moves with.
--                           Not AccountsPayableAndAccruedLiabilitiesCurrent (80): a different, wider line.
-- Left out: ContractWithCustomerAssetNet (contract assets, 72) and inventory's parts (raw materials,
-- work in process, finished goods, about 100 each): narrower, and the statements have no line for them.
--
-- Signs are the filing's: the subtotals and the change in cash are negative for an outflow. capex,
-- dividends and buybacks stay positive amounts paid in gold (029's must-be-positive rule); the web
-- shows every outflow line negative on the cash flow statement (services/web README, "Cash flow").
-- None of the new duration metrics joins 029's must-be-positive list: they can be negative.
--
-- cash_and_investments (derived, quarter and TTM span like net_debt): cash plus short-term investments,
-- except when the investments are inside cash (040's in_cash), and no figure when short-term
-- investments were reported in the last 730 days, are at least 1% of assets and fit the current
-- assets left (040's "unknown" without its debt test, which is about net debt's size): a missing
-- figure isn't a zero. So net_debt = debt - cash_and_investments wherever both exist. To compute it
-- for a company with no debt, the last-reported-investments probe now also runs where cash exists
-- (it ran only where debt did); net_debt reads it only with debt, so net_debt doesn't change. Not
-- applicable to a bank, like net debt (gold.not_applicable_metrics, 035, re-created with it).
--
-- gold.refresh_derived_financials below is 048's body with three edits marked "049" (the probe's
-- condition, the investments test, the VALUES row), generated by a script that asserts each anchor
-- occurs once. A later rewrite keeps 040, 048 and these.
--
-- gold.figure_facts: the facts behind one figure, for the "open components" view (F5): every mapped
-- tag's facts for that company, metric and period end, the latest filing of each (tag, start), with
-- the tag's priority and whether it is a line or only part of a sum. SECURITY DEFINER for the reason
-- 036 gives (query_api has two columns of silver.financial_facts); its only read rule is
-- authz.readable_companies('financials'), in the caller's session. search_path pinned, EXECUTE only for
-- query_api; a CREATE OR REPLACE re-issues the REVOKE and GRANT. Index probes on
-- financial_facts_identity (cik, tag, unit, ...), one company and metric per call.
--
-- Golden figures don't move: no golden metric's tags change and the golden fixtures carry none of the
-- new tags. Gold of the existing metrics was compared row for row on the 613-company sample rebuilt
-- before and after this migration (docs/analyst-test/b6-runbook.md, F5).
--
-- Locks on hermes: INSERTs into silver.metrics, silver.metric_tags, silver.metric_sums and
-- gold.derived_metrics (small tables, row locks; facts-fetcher reads metric_tags at the start of a
-- run), CREATE OR REPLACE FUNCTION twice and CREATE FUNCTION once (catalog only, no table lock).
-- Nothing is rewritten: gold has the new metrics after `facts-fetcher --rebuild`, or company by
-- company as new filings arrive.

SET LOCAL lock_timeout = '30s';

INSERT INTO silver.metrics VALUES
    ('investing_cash_flow',               'duration', 'USD', true),
    ('financing_cash_flow',               'duration', 'USD', true),
    ('change_in_cash',                    'duration', 'USD', true),
    ('cash_and_restricted_cash',          'instant',  'USD', false),
    ('contract_liabilities_current',      'instant',  'USD', false),
    ('contract_liabilities',              'instant',  'USD', false),
    ('remaining_performance_obligations', 'instant',  'USD', false),
    ('accounts_payable',                  'instant',  'USD', false);

INSERT INTO silver.metric_tags (metric, tag, priority) VALUES
    ('investing_cash_flow', 'NetCashProvidedByUsedInInvestingActivities', 1),
    ('investing_cash_flow', 'NetCashProvidedByUsedInInvestingActivitiesContinuingOperations', 2),
    ('financing_cash_flow', 'NetCashProvidedByUsedInFinancingActivities', 1),
    ('financing_cash_flow', 'NetCashProvidedByUsedInFinancingActivitiesContinuingOperations', 2),
    ('change_in_cash', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect', 1),
    ('change_in_cash', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect', 2),
    ('change_in_cash', 'CashAndCashEquivalentsPeriodIncreaseDecrease', 3),
    ('change_in_cash', 'CashAndCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect', 4),
    ('change_in_cash', 'CashPeriodIncreaseDecrease', 5),
    ('cash_and_restricted_cash', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations', 1),
    ('cash_and_restricted_cash', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents', 2),
    ('cash_and_restricted_cash', 'CashAndCashEquivalentsAtCarryingValue', 3),
    ('contract_liabilities_current', 'ContractWithCustomerLiabilityCurrent', 1),
    ('contract_liabilities_current', 'DeferredRevenueCurrent', 2),
    ('contract_liabilities_current', 'CustomerAdvancesCurrent', 3),
    ('contract_liabilities', 'ContractWithCustomerLiability', 1),
    ('contract_liabilities', 'DeferredRevenue', 2),
    ('contract_liabilities', 'ContractWithCustomerLiabilityCurrent', 3),
    ('contract_liabilities', 'ContractWithCustomerLiabilityNoncurrent', 4),
    ('contract_liabilities', 'DeferredRevenueCurrent', 5),
    ('contract_liabilities', 'DeferredRevenueNoncurrent', 6),
    ('remaining_performance_obligations', 'RevenueRemainingPerformanceObligation', 1),
    ('accounts_payable', 'AccountsPayableCurrent', 1),
    ('accounts_payable', 'AccountsPayableTradeCurrent', 2);

-- the total's parts are never the total on their own
UPDATE silver.metric_tags SET gold_line = false
WHERE metric = 'contract_liabilities'
  AND tag IN ('ContractWithCustomerLiabilityCurrent', 'ContractWithCustomerLiabilityNoncurrent',
              'DeferredRevenueCurrent', 'DeferredRevenueNoncurrent');

-- current + noncurrent: after the tagged total, before the old name's total
INSERT INTO silver.metric_sums (metric, priority, tags) VALUES
    ('contract_liabilities', 1.5, ARRAY['ContractWithCustomerLiabilityCurrent', 'ContractWithCustomerLiabilityNoncurrent']),
    ('contract_liabilities', 2.5, ARRAY['DeferredRevenueCurrent', 'DeferredRevenueNoncurrent']);

INSERT INTO gold.derived_metrics VALUES
    ('cash_and_investments', 'USD', '{quarter,ttm}',
     'cash + short_term_investments; the investments left out when inside cash, no figure when they are missing but were reported in the last 730 days (net debt''s rule)');

CREATE OR REPLACE FUNCTION gold.not_applicable_metrics(p_company_id text, p_sic text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN p_sic IN ('6021', '6022', '6029', '6035', '6036', '6099')
                     OR p_company_id IN ('0000886982', '0000895421')  -- GS, MS (SIC 6211)
                THEN ARRAY['gross_margin', 'operating_margin', 'free_cash_flow', 'fcf_margin',
                           'net_debt', 'debt_to_equity', 'current_ratio',
                           'cash_and_investments']  -- 049
                ELSE ARRAY[]::text[] END
$$;

CREATE FUNCTION gold.figure_facts(p_company_id text, p_metric text, p_period_end date)
RETURNS TABLE (tag text, taxonomy text, priority int, gold_line boolean, period_start date,
               period_end date, value numeric, unit text, accession_no text, filer_cik text,
               form text, filed date, filings int)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    SELECT DISTINCT ON (t.tag, f.period_start)
           t.tag, t.taxonomy, t.priority, t.gold_line, f.period_start, f.period_end, f.value, f.unit,
           f.accession_no, f.cik, f.form, f.filed,
           (count(*) OVER (PARTITION BY t.tag, f.period_start))::int
    FROM silver.company_ciks k
    JOIN silver.metric_tags t ON t.metric = p_metric
    JOIN silver.metrics m ON m.metric = t.metric
    JOIN silver.financial_facts f ON f.cik = k.cik AND f.tag = t.tag AND f.unit = m.unit
                                 AND f.period_end = p_period_end
    WHERE k.company_id = p_company_id
      AND k.company_id IN (SELECT r FROM authz.readable_companies('financials') r)
      -- what gold reads (039's usable_facts): no proxy statement, no period after its filing
      AND coalesce(f.form, '') !~ '^(DEF|PRE)'
      AND f.period_end <= f.filed + 7
    ORDER BY t.tag, f.period_start, f.filed DESC, f.accession_no DESC
$fn$;
REVOKE EXECUTE ON FUNCTION gold.figure_facts(text, text, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.figure_facts(text, text, date) TO query_api;

CREATE OR REPLACE FUNCTION gold.refresh_derived_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('gold.quarterly_financials'), hashtext(id))
    FROM (SELECT DISTINCT id FROM unnest(p_company_ids) id ORDER BY id) ids;

    DELETE FROM gold.derived_financials WHERE company_id = ANY(p_company_ids);

    WITH quarters AS (
        SELECT g.*,
               coalesce(g.period_start - lag(g.period_end) OVER w BETWEEN 0 AND 7, false)
                   AS follows
        FROM gold.quarterly_financials g
        JOIN silver.metrics m ON m.metric = g.metric
        WHERE g.company_id = ANY(p_company_ids) AND m.kind = 'duration' AND m.additive
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
    )
    INSERT INTO gold.derived_financials
        (company_id, span, metric, period_start, period_end, value, unit, value_source,
         source_tag, inputs, accession_no, form, filed, source_accessions, ingested_at)
    SELECT company_id, 'ttm', metric, ttm_start, period_end, ttm_value, unit, 'ttm',
           source_tag, jsonb_build_object('quarters', parts), accession_no, form, filed,
           ARRAY(SELECT DISTINCT a FROM unnest(accessions) a ORDER BY 1), ttm_ingested_at
    FROM windows
    WHERE n_quarters = 4 AND contiguous AND period_end - ttm_start + 1 BETWEEN 350 AND 380;

    -- a year the 10-K states, where four quarters don't exist to sum (header). Not the quarters'
    -- gross_profit: a filing that printed one has it here, and revenue less cost is a quarter's rule.
    -- Ends within 7 days are one period (031's rule): ATI's FY2021 is tagged ending 2021-12-31 in one
    -- 10-K and 2022-01-02 in a later one, and was in gold twice.
    WITH years AS (
        SELECT y.*,
               EXISTS (SELECT 1 FROM gold.quarterly_financials q
                       WHERE q.company_id = y.company_id AND q.metric = y.metric
                         AND q.period_end = y.period_end) AS in_series
        FROM (
            -- one per period end: two annual facts can end on one day (a re-labelled start)
            SELECT DISTINCT ON (f.company_id, f.metric, f.period_end) f.*
            FROM gold.best_facts(p_company_ids) f
            JOIN silver.metrics m ON m.metric = f.metric AND m.kind = 'duration'
            WHERE f.period_type = 'annual' AND f.additive
            ORDER BY f.company_id, f.metric, f.period_end, f.priority, f.filed DESC,
                     f.accession_no DESC, f.period_start DESC
        ) y
        -- a year that can't hold the quarters gold has for it is another tag's part, not the year
        -- (Clover Health's 2022 revenue at 1,097 beside quarters of about 800 each; Peoples
        -- Bancorp's 2018 at -127): only for the metrics that can't be negative (029's list)
        WHERE NOT (y.metric IN ('revenue', 'cost_of_revenue', 'research_and_development', 'sga',
                                'capex', 'depreciation_amortization', 'stock_based_compensation',
                                'share_buybacks', 'dividends_paid')
                   AND (y.value < 0 OR y.value < 0.99 * (
                       SELECT coalesce(sum(q.value), 0) FROM gold.quarterly_financials q
                       WHERE q.company_id = y.company_id AND q.metric = y.metric
                         AND q.period_start >= y.period_start - 7
                         AND q.period_end <= y.period_end + 7)))
    )
    INSERT INTO gold.derived_financials
        (company_id, span, metric, period_start, period_end, value, unit, value_source,
         source_tag, inputs, accession_no, form, filed, source_accessions, ingested_at)
    SELECT b.company_id, 'ttm', b.metric, b.period_start, b.period_end, b.value, b.unit, 'ttm',
           b.taxonomy || ':' || b.tag,
           jsonb_build_object('quarters', jsonb_build_array(jsonb_build_object(
               'period_start', b.period_start, 'period_end', b.period_end, 'value', b.value,
               'accession_no', b.accession_no))),
           b.accession_no, b.form, b.filed, ARRAY[b.accession_no], b.ingested_at
    FROM years b
    WHERE NOT EXISTS (
        SELECT 1 FROM gold.derived_financials d
        WHERE d.company_id = b.company_id AND d.span = 'ttm' AND d.metric = b.metric
          AND d.period_end BETWEEN b.period_end - 7 AND b.period_end + 7)
      -- of two stated years ending within 7 days, one: the end gold's quarters use, then the
      -- later filing, then the later end
      AND NOT EXISTS (
        SELECT 1 FROM years o
        WHERE o.company_id = b.company_id AND o.metric = b.metric
          AND o.period_end <> b.period_end
          AND o.period_end BETWEEN b.period_end - 7 AND b.period_end + 7
          AND (o.in_series, o.filed, o.period_end) > (b.in_series, b.filed, b.period_end));

    WITH facts AS (
        SELECT 'quarter' AS span, company_id, metric, period_start, period_end, value,
               accession_no, form, filed, source_accessions, ingested_at
        FROM gold.quarterly_financials
        WHERE company_id = ANY(p_company_ids)
        UNION ALL
        SELECT 'ttm', company_id, metric, period_start, period_end, value,
               accession_no, form, filed, source_accessions, ingested_at
        FROM gold.derived_financials
        WHERE company_id = ANY(p_company_ids)  -- the TTM sums just inserted
        UNION ALL
        SELECT 'ttm', g.company_id, g.metric, g.period_start, g.period_end, g.value,
               g.accession_no, g.form, g.filed, g.source_accessions, g.ingested_at
        FROM gold.quarterly_financials g
        JOIN silver.metrics m ON m.metric = g.metric
        WHERE g.company_id = ANY(p_company_ids) AND m.kind = 'instant'
    ),
    base AS (  -- the inputs of derived metrics
        SELECT * FROM facts
        WHERE metric IN ('revenue', 'gross_profit', 'operating_income', 'net_income',
                         'eps_diluted', 'operating_cash_flow', 'capex', 'dividends_paid',
                         'long_term_debt', 'long_term_debt_current', 'cash',
                         'short_term_investments', 'current_assets', 'current_liabilities',
                         'stockholders_equity', 'total_assets', 'receivables', 'inventory',
                         -- 048: the efficiency ratio's inputs
                         'net_interest_income', 'noninterest_income', 'noninterest_expense')
    ),
    year_ago AS (
        SELECT b.*,
               last_value(value) OVER ya AS ya_value,
               last_value(source_accessions) OVER ya AS ya_accessions
        FROM base b
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
               max(ya_value) FILTER (WHERE metric = 'revenue') AS revenue_year_ago,
               max(ya_value) FILTER (WHERE metric = 'net_income') AS net_income_year_ago,
               max(ya_value) FILTER (WHERE metric = 'eps_diluted') AS eps_diluted_year_ago,
               max(ya_value) FILTER (WHERE metric = 'stockholders_equity')
                   AS stockholders_equity_year_ago,
               max(ya_value) FILTER (WHERE metric = 'long_term_debt') AS long_term_debt_year_ago,
               jsonb_object_agg(metric, jsonb_build_object(
                   'period_start', period_start, 'accession_no', accession_no, 'form', form,
                   'filed', filed, 'accessions', source_accessions,
                   'ingested_at', ingested_at, 'year_ago_accessions', ya_accessions)) AS prov
        FROM year_ago
        GROUP BY span, company_id, period_end
    ),
    debt AS (  -- the net debt rule's reading of each balance sheet (header)
        SELECT p.*,
               coalesce(p.short_term_investments, 0) AS investments_now,
               p.current_assets - p.cash - coalesce(p.receivables, 0) - coalesce(p.inventory, 0)
                   AS room,
               x.value AS last_investments,
               x.period_end AS last_investments_end,
               t.value AS interest
        FROM periods p
        LEFT JOIN LATERAL (
            SELECT q.value, q.period_end
            FROM gold.quarterly_financials q
            -- 049: also where there is cash and no debt, for cash_and_investments (header);
            -- net_debt reads it only with debt, so it doesn't change
            WHERE p.short_term_investments IS NULL
              AND (p.long_term_debt IS NOT NULL OR p.cash IS NOT NULL)
              AND q.company_id = p.company_id AND q.metric = 'short_term_investments'
              AND q.period_end < p.period_end AND q.period_end >= p.period_end - 730 AND q.value > 0
            ORDER BY q.period_end DESC LIMIT 1
        ) x ON true
        LEFT JOIN LATERAL (
            SELECT d.value
            FROM gold.derived_financials d
            WHERE p.long_term_debt IS NOT NULL
              AND d.company_id = p.company_id AND d.span = 'ttm' AND d.metric = 'interest_expense'
              AND d.period_end = p.period_end
        ) t ON true
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
            ('gross_margin',
             CASE WHEN p.revenue > 0 THEN p.gross_profit / p.revenue END,
             jsonb_build_object('gross_profit', p.gross_profit, 'revenue', p.revenue)),
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
    INSERT INTO gold.derived_financials
        (company_id, span, metric, period_start, period_end, value, unit, value_source,
         inputs, accession_no, form, filed, source_accessions, ingested_at)
    SELECT d.company_id, d.span, d.metric, c.period_start, d.period_end, d.value, d.unit,
           'computed', d.inputs, c.accession_no, c.form, c.filed, s.accessions, c.ingested_at
    FROM derived d
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
    ) s;

    SELECT count(*) INTO n FROM gold.derived_financials WHERE company_id = ANY(p_company_ids);
    RETURN n;
END
$$;
