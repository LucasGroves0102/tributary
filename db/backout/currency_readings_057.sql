-- Backout of 057_currency_readings.sql (never a migration: backout/README.md). Run by hand; no
-- rebuild needed (057 changes how figures are read, not gold's rows), but run `SELECT
-- ops.check_financials(array_agg(DISTINCT company_id)) FROM gold.quarterly_financials` after it to
-- bring back 050's debt findings. Puts back gold.latest_reading (056's body), gold.not_meaningful
-- (035's four-argument body; the five-argument one is dropped), gold.filing_figures (051's, no
-- `unit`: DROP and CREATE) and ops.check_financials (050's), and drops gold.amount_currency.
-- Generated from those migrations by a script. Deploy the previous query-api image with it: from
-- 057 on the Query API calls the five-argument gold.not_meaningful, gold.amount_currency and reads
-- `unit` from gold.filing_figures.
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as postgres,
-- a function it drops and re-creates would belong to postgres, and the non-superuser migrate login
-- could never replace it again. The guard below refuses any login that doesn't own the functions,
-- and any database where a newer backout hasn't run yet (README.md, Order).
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f currency_readings_057.sql

BEGIN;
SET LOCAL lock_timeout = '10s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: banks_058.sql) and this one hasn't,
-- and unless the login owns the functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('gold.amount_currency(text)', '84308633b72bc99f52616793528f7ebd'),
        ('gold.annual_per_share(text,integer)', '663430e80a6341f8a0e4227e1491e657'),
        ('gold.companies_for_ciks(text[])', '5f02b9f1e395d7f743635d0a399f8f6c'),
        ('gold.figure_facts(text,text,date)', '6789892e97611dd6c0260a25f695d7fc'),
        ('gold.filing_figures(text,text)', '51376508ac8a1a66d7e42cc92852346f'),
        ('gold.latest_reading(text,text,text,text,date)', 'e8ecccdc7e77be5929165e9f36767827'),
        ('gold.not_applicable_metrics(text,text)', 'b0145e9727c704f359b95047071fe193'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', '472cb5abb2f6cb3ad0cbf64734468ee4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', '90812eb486b8ef14081bf2fea5ab1890'),
        ('gold.refresh_derived_financials(text[])', 'daf9fa4950728307efdbdefa793ec6d0'),
        ('gold.refresh_quarterly_financials(text[])', '84ce53d8fc3987c84012b4360b69fed4'),
        ('gold.usable_facts(text[])', '0b0c7a112a9761106084a4fd15dd2a56'),
        ('ops.check_financials(text[])', '510eaedc79ece7e71f7b8b0fb0b6a339'),
        ('silver.refresh_filing_facts(text[])', '566a561f85a949551ee2cdef2cf576f3'),
        ('silver.refresh_financial_facts(text[])', '3b47cf049eeb9714470d25212df6ec05')
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
                'changed.', w.fn, 'banks_058.sql');
        END IF;
    END LOOP;
END
$guard$;
-- end guard

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
                WHEN gold.not_meaningful(p_metric, x.value, x.inputs, e.value) IS NOT NULL
                    THEN 'not_meaningful'
                ELSE gold.period_status(x.period_end, p_latest) END,
           CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'this is a bank: deposits are its debt and lending its operations, so '
                         'the figure has no comparable meaning'
                WHEN x.unit ~ '^[A-Z]{3}(/shares)?$' AND x.unit !~ '^USD'
                    THEN 'reported in ' || split_part(x.unit, '/', 1)
                         || ', so it isn''t sorted, filtered or averaged with US-dollar figures'
                ELSE gold.not_meaningful(p_metric, x.value, x.inputs, e.value) END,
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
$$;

DROP FUNCTION gold.not_meaningful(text, numeric, jsonb, numeric, text);
CREATE OR REPLACE FUNCTION gold.not_meaningful(p_metric text, p_value numeric, p_inputs jsonb,
                                    p_equity numeric DEFAULT NULL) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN p_metric IN ('debt_to_equity', 'roe') AND p_equity <= 0
            THEN 'stockholders'' equity is negative'
        WHEN p_value IS NULL THEN NULL
        WHEN p_metric IN ('gross_margin', 'operating_margin', 'net_margin', 'fcf_margin') THEN CASE
            WHEN (p_inputs->>'revenue')::numeric < 100000
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
            WHEN (p_inputs->>'revenue_year_ago')::numeric < 100000
                THEN 'revenue a year earlier was under $100K, too small a base'
            WHEN p_value > 10 AND (p_inputs->>'revenue_year_ago')::numeric < 10000000
                THEN 'growth of over 1,000% from under $10M a year earlier'
            WHEN p_value < -1 THEN 'revenue is negative'
        END
        WHEN p_metric = 'net_income_growth' THEN CASE
            WHEN p_value < -1 THEN 'net income went from a profit to a loss'
            WHEN p_value > 10 AND (p_inputs->>'net_income_year_ago')::numeric < 10000000
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


DROP FUNCTION gold.filing_figures(text, text);
CREATE FUNCTION gold.filing_figures(p_company_id text, p_accession_no text)
RETURNS TABLE (metric text, period_start date, period_end date, value numeric, how text,
               source_tag text, inputs jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    WITH facts AS MATERIALIZED (
        SELECT DISTINCT ON (u.metric, u.period_start, u.period_end)
               u.metric, u.period_start, u.period_end, u.value, u.taxonomy || ':' || u.tag AS tag
        FROM gold.usable_facts(ARRAY[p_company_id]) u
        WHERE u.accession_no = p_accession_no
          AND p_company_id IN (SELECT r FROM authz.readable_companies('financials') r)
        ORDER BY u.metric, u.period_start, u.period_end, u.priority, u.tag
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
    SELECT f.metric, f.period_start, f.period_end, f.value, 'reported', f.tag, NULL::jsonb
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
               CASE WHEN b.in_cash THEN NULL ELSE b.short_term_investments END))
    FROM balances b
    WHERE b.long_term_debt IS NOT NULL AND b.cash IS NOT NULL
    UNION ALL
    SELECT 'cash_and_investments', NULL, b.period_end,
           b.cash + CASE WHEN b.in_cash THEN 0 ELSE coalesce(b.short_term_investments, 0) END,
           'computed', NULL,
           jsonb_strip_nulls(jsonb_build_object(
               'cash', b.cash, 'short_term_investments',
               CASE WHEN b.in_cash THEN NULL ELSE b.short_term_investments END))
    FROM balances b
    WHERE b.cash IS NOT NULL
    UNION ALL
    SELECT 'free_cash_flow', w.period_start, w.period_end, w.operating_cash_flow - w.capex,
           'computed', NULL,
           jsonb_build_object('operating_cash_flow', w.operating_cash_flow, 'capex', w.capex)
    FROM flows w
    WHERE w.operating_cash_flow IS NOT NULL AND w.capex IS NOT NULL
$fn$;
REVOKE EXECUTE ON FUNCTION gold.filing_figures(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.filing_figures(text, text) TO query_api;

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

DROP FUNCTION gold.amount_currency(text);

COMMIT;
