-- Currencies, read where 056 left them read as dollars (F8b; F8a's review). After 056 about 700
-- companies' figures are in their own currency (yuan, euros, yen, won...). The places that still read
-- them as dollars, and what this does:
--
-- 1. gold.amount_currency(company): the currency of a company's amounts, its latest revenue row's
--    unit (else net income's, else USD). One currency per company since 056, so any amount's unit is
--    it; two PK probes. For readings whose rule needs it.
--
-- 2. Not meaningful (035) had two absolute bases in dollars: revenue under $100K (margins, revenue
--    growth) and growth over 1,000% from under $10M a year earlier (revenue and net income growth).
--    Against yen or won amounts they meant ¥100K ($650) and ₩100K ($70): never true, so the rules were
--    silently off. Choice: the absolute bases apply only to dollar amounts; every ratio bound stays
--    for every currency (a margin over 100% or over ten times revenue, growth below -100%, debt over
--    100 times equity...). Not per-currency equivalents: Tributary has no exchange rates (no paid
--    data), a fixed table would drift (the lira lost 80% in five years) and nobody would notice it was
--    stale; and the bases exist for shells and pre-revenue filers, which file 10-Ks in dollars, while
--    the companies in another currency are listed foreign filers far above $100K of revenue. So for a
--    euro company a 2,000% growth stays a figure, as Moderna's does in dollars. gold.not_meaningful
--    gains a fifth argument, p_currency; the four-argument form reads as dollars (an older Query API
--    during the deploy, and any caller that passes no currency). gold.latest_reading passes the
--    currency of the base's amounts (a PK probe, only for the six metrics with a base); the Query API
--    passes gold.amount_currency for /financials, recaps and formula points.
--
-- 3. gold.filing_figures (051, the thesis check's figures from one filing) returns each figure's unit:
--    the fact's own, and the filing's currency for net debt, cash and investments and free cash flow,
--    so the check writes "€2,097M", not "$2,097M". Its result gains a column: DROP and CREATE, with
--    051's grants. Still SECURITY DEFINER with 051's read rule (authz.readable_companies('financials')
--    in the caller's session) and search_path.
--
-- 4. ops.check_financials' debt_scale check (050) compared a debt fact with the same date's total
--    liabilities and total assets in silver without a unit: a 20-F filer's convenience dollar
--    translation of its liabilities (a seventh of the yuan figure) beside its yuan debt made a false
--    "debt over five times liabilities". Now both sides are in the debt's unit; parts_scale groups by
--    unit too. 050's body otherwise unchanged. Findings change at the next check of each company (the
--    nightly facts jobs and the financials-rebuild run it).
--
-- Nothing about who reads what changes: the new and replaced functions are SECURITY INVOKER except
-- gold.filing_figures, as before. Generated from 035's, 050's, 051's and 056's bodies by a script
-- asserting each edit's anchor once. A later rewrite of any of them keeps the currency rules.
--
-- Locks on hermes: CREATE FUNCTION / CREATE OR REPLACE / DROP and CREATE of functions only (catalog,
-- lock_timeout 10 s; a query planned before the commit finishes with the old body). No table is
-- touched and gold doesn't change: no rebuild. Backout: db/backout/currency_readings_057.sql.

SET LOCAL lock_timeout = '10s';

-- 1. The company's currency, for a rule that reads its amounts.
CREATE FUNCTION gold.amount_currency(p_company_id text) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT coalesce(
        (SELECT q.unit FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'revenue'
         ORDER BY q.period_end DESC LIMIT 1),
        (SELECT q.unit FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'net_income'
         ORDER BY q.period_end DESC LIMIT 1),
        'USD')
$$;
GRANT EXECUTE ON FUNCTION gold.amount_currency(text) TO query_api;

-- 2. Not meaningful, with the amounts' currency: 035's rules, the dollar bases only for dollars.
CREATE FUNCTION gold.not_meaningful(p_metric text, p_value numeric, p_inputs jsonb,
                                    p_equity numeric, p_currency text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN p_metric IN ('debt_to_equity', 'roe') AND p_equity <= 0
            THEN 'stockholders'' equity is negative'
        WHEN p_value IS NULL THEN NULL
        WHEN p_metric IN ('gross_margin', 'operating_margin', 'net_margin', 'fcf_margin') THEN CASE
            WHEN coalesce(p_currency, 'USD') = 'USD' AND (p_inputs->>'revenue')::numeric < 100000
                THEN 'revenue is under $100K, too small a base'
            WHEN p_metric = 'gross_margin' AND p_value > 1
                THEN 'gross profit is larger than revenue, so the two are on different bases'
            WHEN p_metric = 'operating_margin' AND p_value > 1
                THEN 'operating income is larger than revenue: a one-off gain, or revenue that '
                     'covers only part of the business'
            WHEN abs(p_value) > 10
                THEN 'the amount is more than ten times revenue'
        END
        WHEN p_metric = 'revenue_growth' THEN CASE
            WHEN coalesce(p_currency, 'USD') = 'USD'
                 AND (p_inputs->>'revenue_year_ago')::numeric < 100000
                THEN 'revenue a year earlier was under $100K, too small a base'
            WHEN p_value > 10 AND coalesce(p_currency, 'USD') = 'USD'
                 AND (p_inputs->>'revenue_year_ago')::numeric < 10000000
                THEN 'growth of over 1,000% from under $10M a year earlier'
            WHEN p_value < -1 THEN 'revenue is negative'
        END
        WHEN p_metric = 'net_income_growth' THEN CASE
            WHEN p_value < -1 THEN 'net income went from a profit to a loss'
            WHEN p_value > 10 AND coalesce(p_currency, 'USD') = 'USD'
                 AND (p_inputs->>'net_income_year_ago')::numeric < 10000000
                THEN 'growth of over 1,000% from under $10M a year earlier'
        END
        WHEN p_metric = 'eps_growth' THEN CASE
            WHEN p_value < -1 THEN 'earnings per share went from a profit to a loss'
            WHEN p_value > 10 THEN 'growth of over 1,000% from a very small base'
        END
        WHEN p_metric = 'debt_to_equity' THEN CASE
            WHEN p_value < 0 THEN 'debt is below zero, a sign error in the filing''s XBRL'
            WHEN p_value > 100 THEN 'debt is over 100 times equity, so equity is close to zero'
        END
        WHEN p_metric = 'roe' AND abs(p_value) > 10
            THEN 'net income is more than ten times equity, so equity is close to zero'
        WHEN p_metric = 'payout_ratio' AND p_value > 10
            THEN 'dividends are more than ten times net income, so net income is close to zero'
        WHEN p_metric = 'current_ratio' AND p_value > 100
            THEN 'current liabilities are close to zero'
    END
$$;


-- 035's four arguments read as dollars, as before (an older Query API, the other callers)
CREATE OR REPLACE FUNCTION gold.not_meaningful(p_metric text, p_value numeric, p_inputs jsonb,
                                               p_equity numeric DEFAULT NULL) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT gold.not_meaningful(p_metric, p_value, p_inputs, p_equity, 'USD')
$$;

-- 3. 056's gold.latest_reading with the currency of the reading's base (header, 2).
CREATE OR REPLACE FUNCTION gold.latest_reading(p_company_id text, p_span text, p_metric text, p_sic text,
                                    p_latest date)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date,
               status text, reason text, unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT x.period_end, x.value, x.accession_no, x.form, x.filed,
           CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'not_applicable'
                -- 056: an amount in another currency is never compared with dollars (header, 3)
                WHEN x.unit ~ '^[A-Z]{3}(/shares)?$' AND x.unit !~ '^USD'
                    THEN 'other_currency'
                WHEN gold.not_meaningful(p_metric, x.value, x.inputs, e.value, coalesce(cu.unit, 'USD')) IS NOT NULL
                    THEN 'not_meaningful'
                ELSE gold.period_status(x.period_end, p_latest) END,
           CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'this is a bank: deposits are its debt and lending its operations, so '
                         'the figure has no comparable meaning'
                WHEN x.unit ~ '^[A-Z]{3}(/shares)?$' AND x.unit !~ '^USD'
                    THEN 'reported in ' || split_part(x.unit, '/', 1)
                         || ', so it isn''t sorted, filtered or averaged with US-dollar figures'
                ELSE gold.not_meaningful(p_metric, x.value, x.inputs, e.value, coalesce(cu.unit, 'USD')) END,
           x.unit
    FROM (SELECT 1) one
    LEFT JOIN LATERAL (
        SELECT y.period_end, y.value, y.accession_no, y.form, y.filed, y.inputs, y.unit
        FROM (
            (SELECT q.period_end, q.value, q.accession_no, q.form, q.filed, NULL::jsonb AS inputs,
                    q.unit
             FROM gold.quarterly_financials q
             WHERE q.company_id = p_company_id AND q.metric = p_metric
               AND (p_span = 'quarter'
                    OR (p_span = 'ttm' AND EXISTS (SELECT 1 FROM silver.metrics m
                                                   WHERE m.metric = p_metric AND m.kind = 'instant')))
             ORDER BY q.period_end DESC LIMIT 1)
            UNION ALL
            (SELECT d.period_end, d.value, d.accession_no, d.form, d.filed, d.inputs, d.unit
             FROM gold.derived_financials d
             WHERE d.company_id = p_company_id AND d.span = p_span AND d.metric = p_metric
             ORDER BY d.period_end DESC LIMIT 1)
        ) y
        ORDER BY y.period_end DESC LIMIT 1
    ) x ON true
    LEFT JOIN LATERAL (
        SELECT q.value FROM gold.quarterly_financials q
        WHERE p_metric IN ('debt_to_equity', 'roe')
          AND q.company_id = p_company_id AND q.metric = 'stockholders_equity'
          AND q.period_end <= current_date
        ORDER BY q.period_end DESC LIMIT 1
    ) e ON true
    -- 057: the currency of the amounts a rule's base is in (one per company: 056), only for the
    -- readings that have a base; one PK probe
    LEFT JOIN LATERAL (
        SELECT q.unit FROM gold.quarterly_financials q
        WHERE p_metric IN ('gross_margin', 'operating_margin', 'net_margin', 'fcf_margin',
                           'revenue_growth', 'net_income_growth')
          AND q.company_id = p_company_id
          AND q.metric = CASE p_metric WHEN 'net_income_growth' THEN 'net_income' ELSE 'revenue' END
        ORDER BY q.period_end DESC LIMIT 1
    ) cu ON true
$$;

-- 4. 051's gold.filing_figures with each figure's unit (its result gains a column: DROP and CREATE).
DROP FUNCTION gold.filing_figures(text, text);
CREATE FUNCTION gold.filing_figures(p_company_id text, p_accession_no text)
RETURNS TABLE (metric text, period_start date, period_end date, value numeric, how text,
               source_tag text, inputs jsonb, unit text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    WITH facts AS MATERIALIZED (
        SELECT DISTINCT ON (u.metric, u.period_start, u.period_end)
               u.metric, u.period_start, u.period_end, u.value, u.taxonomy || ':' || u.tag AS tag,
               u.unit
        FROM gold.usable_facts(ARRAY[p_company_id]) u
        WHERE u.accession_no = p_accession_no
          AND p_company_id IN (SELECT r FROM authz.readable_companies('financials') r)
        ORDER BY u.metric, u.period_start, u.period_end, u.priority, u.tag
    ),
    -- 057: the computed amounts are in the filing's currency (its facts are in one: 056)
    currency AS (
        SELECT coalesce((SELECT f.unit FROM facts f WHERE f.unit ~ '^[A-Z]{3}$'
                         ORDER BY f.metric = 'cash' DESC, f.metric LIMIT 1), 'USD') AS unit
    ),
    instants AS (
        SELECT f.period_end,
               max(f.value) FILTER (WHERE f.metric = 'long_term_debt') AS long_term_debt,
               max(f.value) FILTER (WHERE f.metric = 'long_term_debt_current')
                   AS long_term_debt_current,
               max(f.value) FILTER (WHERE f.metric = 'cash') AS cash,
               max(f.value) FILTER (WHERE f.metric = 'short_term_investments')
                   AS short_term_investments,
               max(f.value) FILTER (WHERE f.metric = 'current_assets') AS current_assets,
               max(f.value) FILTER (WHERE f.metric = 'receivables') AS receivables,
               max(f.value) FILTER (WHERE f.metric = 'inventory') AS inventory
        FROM facts f
        WHERE f.period_start IS NULL
        GROUP BY f.period_end
    ),
    balances AS (  -- 040's in_cash: the investments don't fit the current assets beside cash
        SELECT i.*,
               coalesce(i.short_term_investments > (i.current_assets - i.cash
                                                    - coalesce(i.receivables, 0)
                                                    - coalesce(i.inventory, 0)) * 1.01,
                        false) AS in_cash
        FROM instants i
    ),
    flows AS (
        SELECT f.period_start, f.period_end,
               max(f.value) FILTER (WHERE f.metric = 'operating_cash_flow') AS operating_cash_flow,
               max(f.value) FILTER (WHERE f.metric = 'capex') AS capex
        FROM facts f
        WHERE f.period_start IS NOT NULL AND f.metric IN ('operating_cash_flow', 'capex')
        GROUP BY f.period_start, f.period_end
    )
    SELECT f.metric, f.period_start, f.period_end, f.value, 'reported', f.tag, NULL::jsonb, f.unit
    FROM facts f
    UNION ALL
    SELECT 'net_debt', NULL, b.period_end,
           b.long_term_debt + coalesce(b.long_term_debt_current, 0) - b.cash
           - CASE WHEN b.in_cash THEN 0 ELSE coalesce(b.short_term_investments, 0) END,
           'computed', NULL,
           jsonb_strip_nulls(jsonb_build_object(
               'long_term_debt', b.long_term_debt,
               'long_term_debt_current', b.long_term_debt_current, 'cash', b.cash,
               'short_term_investments',
               CASE WHEN b.in_cash THEN NULL ELSE b.short_term_investments END)),
           (SELECT unit FROM currency)
    FROM balances b
    WHERE b.long_term_debt IS NOT NULL AND b.cash IS NOT NULL
    UNION ALL
    SELECT 'cash_and_investments', NULL, b.period_end,
           b.cash + CASE WHEN b.in_cash THEN 0 ELSE coalesce(b.short_term_investments, 0) END,
           'computed', NULL,
           jsonb_strip_nulls(jsonb_build_object(
               'cash', b.cash, 'short_term_investments',
               CASE WHEN b.in_cash THEN NULL ELSE b.short_term_investments END)),
           (SELECT unit FROM currency)
    FROM balances b
    WHERE b.cash IS NOT NULL
    UNION ALL
    SELECT 'free_cash_flow', w.period_start, w.period_end, w.operating_cash_flow - w.capex,
           'computed', NULL,
           jsonb_build_object('operating_cash_flow', w.operating_cash_flow, 'capex', w.capex),
           (SELECT unit FROM currency)
    FROM flows w
    WHERE w.operating_cash_flow IS NOT NULL AND w.capex IS NOT NULL
$fn$;
REVOKE EXECUTE ON FUNCTION gold.filing_figures(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.filing_figures(text, text) TO query_api;

-- 5. 050's ops.check_financials with debt compared only with liabilities and assets in its unit.
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
