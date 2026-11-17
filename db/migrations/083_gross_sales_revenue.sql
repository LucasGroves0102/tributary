-- Gross sales read as revenue (Riley, test 6, #42): Bullish (SIC 6199) books every crypto asset it
-- sells as revenue, $244.81B for 2025 against a cost of revenue of $244.73B, and ranked among the
-- largest US companies in the screener, unflagged. Its revenue now reads not meaningful, with words,
-- like any figure whose base misleads (035), and so leaves sorts, filters, size bands, medians and
-- peers' sizes; the figure itself stays on the company's statements.
--
-- The rule, in gold.not_meaningful (057's body, one branch marked "083"): revenue whose cost of
-- revenue at the same period end is over 95% of it and not over 100% (a gross margin under 5%, not a
-- gross loss), at SIC 6199 (finance services) or 6200-6299 (security and commodity brokers, dealers
-- and exchanges): gold.gross_sales_sic. Measured on hermes (2026-11-17, read-only, the screener's
-- stored TTM figures; 269 companies with revenue at those SICs): over 95% and at most 100%, Bullish
-- 99.97%, SOS 98.5%, The9 98.3% (CNY), eToro 98.2%, Coincheck 97.1% (JPY) and Cango 95.9% (a
-- bitcoin miner in its first year: the one that reads as a business margin, kept as approved);
-- Coincheck's quarters 95.9-97.3%. Fifteen more at those SICs have cost over 100% of revenue,
-- miners and micro-caps at a gross loss (BGIN 212%, Keel 195%, BTC Digital 123%): their revenue is
-- what they earn, so the rule stops at 100%. Not at other financial SICs: real estate's home
-- sellers (Opendoor 92.5%, Linkhome 96.0%) and insurance agents (Cheche 94.7%, in CNY) sell what
-- they own or earn a commission; banks carry no cost of revenue. The two in CNY and JPY read
-- other_currency first (gold.reading's order), as before.
--
-- Where the cost comes from: gold.reading takes inputs, not probes (078), so its two readers add the
-- cost of revenue to the revenue's inputs (gold.with_cost) before calling it, only for revenue at
-- those SICs (a one-time filter: every other column's plan is unchanged):
--   gold.latest_reading  gold.latest_row's cost_of_revenue for the span, at the revenue's period end;
--   gold.screen_reading  the stored figure for the same span and metric (078's gold.screen_figures
--                        holds every span and metric a company has rows of, cost_of_revenue
--                        among them), at the same period end: the same value.
-- Stored data doesn't change (078's tables hold values; readings are read time): nothing refills.
-- The Query API's peers count a not-meaningful revenue as an unknown size, like another currency's
-- (query_api/peers.py). /financials rows and recaps call gold.not_meaningful with the row's own
-- inputs, which have no cost of revenue: the statements show the revenue as filed.
--
-- Locks on hermes (lock_timeout 3 s): two new functions; CREATE OR REPLACE of gold.not_meaningful,
-- gold.latest_reading and gold.screen_reading (catalog only; a running query keeps the body it
-- planned with). No table is touched; nothing to rebuild. The backouts' guards pin the new bodies
-- (scripts/backout_guards.py). Undo: 057's gold.not_meaningful and 078's gold.latest_reading and
-- gold.screen_reading bodies, then drop gold.with_cost and gold.gross_sales_sic.

SET LOCAL lock_timeout = '3s';

-- Where revenue can be gross sales of the assets traded (header): finance services and brokers,
-- dealers and exchanges.
CREATE FUNCTION gold.gross_sales_sic(p_sic text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT p_sic ~ '^(6199|62[0-9][0-9])$'
$$;

-- A reading's inputs with the cost of revenue beside them (none: the inputs unchanged). STABLE as
-- jsonb_build_object is: an IMMUTABLE function over a STABLE one is never inlined.
CREATE FUNCTION gold.with_cost(p_inputs jsonb, p_cost numeric) RETURNS jsonb
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN p_cost IS NULL THEN p_inputs
                ELSE coalesce(p_inputs, '{}'::jsonb) || jsonb_build_object('cost_of_revenue', p_cost)
           END
$$;

GRANT EXECUTE ON FUNCTION gold.gross_sales_sic(text), gold.with_cost(jsonb, numeric) TO query_api;

CREATE OR REPLACE FUNCTION gold.not_meaningful(p_metric text, p_value numeric, p_inputs jsonb,
                                               p_equity numeric, p_currency text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN p_metric IN ('debt_to_equity', 'roe') AND p_equity <= 0
            THEN 'stockholders'' equity is negative'
        WHEN p_value IS NULL THEN NULL
        -- 083: gross sales of traded assets; cost_of_revenue is in the inputs only where revenue
        -- can be that (gold.gross_sales_sic), from the readers (header)
        WHEN p_metric = 'revenue' THEN CASE
            WHEN p_value > 0 AND (p_inputs->>'cost_of_revenue')::numeric > 0.95 * p_value
                 AND (p_inputs->>'cost_of_revenue')::numeric <= p_value
                THEN 'cost of revenue is over 95% of revenue: a trading venue or dealer counts the '
                     'full value of the assets it sells as revenue, not what it earns on them, so '
                     'it isn''t compared with other companies'' revenue'
        END
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

CREATE OR REPLACE FUNCTION gold.latest_reading(p_company_id text, p_span text, p_metric text, p_sic text,
                                    p_latest date)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date,
               status text, reason text, unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT x.period_end, x.value, x.accession_no, x.form, x.filed, r.status, r.reason, x.unit
    FROM (SELECT 1) one
    LEFT JOIN LATERAL gold.latest_row(p_company_id, p_span, p_metric) x ON true
    LEFT JOIN LATERAL (
        SELECT q.value FROM gold.quarterly_financials q
        WHERE p_metric IN ('debt_to_equity', 'roe')
          AND q.company_id = p_company_id AND q.metric = 'stockholders_equity'
          AND q.period_end <= current_date
        ORDER BY q.period_end DESC LIMIT 1
    ) e ON true
    -- 057/060/061: the currency of the amounts a rule's base is in, only for the readings with a base
    LEFT JOIN LATERAL (
        SELECT c.unit FROM gold.amount_currency_of(p_company_id) c
        WHERE p_metric IN ('gross_margin', 'operating_margin', 'net_margin', 'fcf_margin',
                           'revenue_growth', 'net_income_growth')
        LIMIT 1
    ) cu ON true
    -- 083: cost of revenue at the revenue's period end, only where revenue can be gross sales
    LEFT JOIN LATERAL (
        SELECT c.value FROM gold.latest_row(p_company_id, p_span, 'cost_of_revenue') c
        WHERE p_metric = 'revenue' AND gold.gross_sales_sic(p_sic) AND c.period_end = x.period_end
        LIMIT 1
    ) gs ON true
    -- 069: the recent-quarters probe, only when the stale answer depends on it
    LEFT JOIN LATERAL (
        SELECT qr.recent FROM gold.quarterly_recent(p_company_id, p_latest) qr
        WHERE gold.is_stale(p_latest, x.form, true) AND NOT gold.is_stale(p_latest, x.form, false)
        LIMIT 1
    ) st ON true
    CROSS JOIN LATERAL gold.reading(p_company_id, p_metric, p_sic, p_latest, x.period_end, x.value,
                                    gold.with_cost(x.inputs, gs.value),  -- 083
                                    x.unit, x.form, e.value, cu.unit, st.recent) r
$$;

CREATE OR REPLACE FUNCTION gold.screen_reading(p_company_id text, p_span text, p_metric text,
                                               p_sic text, p_latest date, p_cached boolean,
                                               p_equity numeric, p_currency text, p_recent boolean)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date,
               status text, reason text, unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT f.period_end, f.value, f.accession_no, f.form, f.filed, r.status, r.reason, f.unit
    FROM (SELECT 1) one
    LEFT JOIN LATERAL (
        SELECT f.period_end, f.value, f.accession_no, f.form, f.filed, f.inputs, f.unit
        FROM gold.screen_figures f
        WHERE f.span = p_span AND f.metric = p_metric AND f.company_id = p_company_id
    ) f ON true
    -- 083: the stored cost of revenue of the same span and period end, as gold.latest_reading's
    LEFT JOIN LATERAL (
        SELECT g.value FROM gold.screen_figures g
        WHERE p_metric = 'revenue' AND gold.gross_sales_sic(p_sic)
          AND g.span = p_span AND g.metric = 'cost_of_revenue' AND g.company_id = p_company_id
          AND g.period_end = f.period_end
        LIMIT 1
    ) gs ON true
    CROSS JOIN LATERAL gold.reading(p_company_id, p_metric, p_sic, p_latest, f.period_end, f.value,
                                    gold.with_cost(f.inputs, gs.value),  -- 083
                                    f.unit, f.form, p_equity, p_currency, p_recent) r
    WHERE p_cached
    UNION ALL
    SELECT x.* FROM gold.latest_reading(p_company_id, p_span, p_metric, p_sic, p_latest) x
    WHERE NOT p_cached
$$;
