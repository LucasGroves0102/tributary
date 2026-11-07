-- How a figure may be read: the one definition of "not meaningful", "not applicable" and "old" that
-- the Query API's sorts, filters and tables all use. A value that fails stays exact and reachable
-- (the API still returns it, with the reason); it just can't win a sort or pass a range filter.
--
-- Thresholds come from the latest values of every company on hermes (2026-11-06, TTM unless noted):
--   gross margin    p99 100.0%, then 23 companies above 100%, all gross profit > revenue (Hammer
--                   Technology 209,828% on $420 of revenue, StoneX 198.6%, NiSource 104.7%); 57 sit
--                   at exactly 100%, which is real (no cost of revenue).
--   operating margin p99 75.5%. AppLovin 77.8%, Texas Pacific Land 75.5%, Gaming & Leisure 79.1% are
--                   genuine; the 23 above 100% are one-off gains or a revenue tag covering part of the
--                   business (Verra Mobility 111.8% on $114M of its ~$900M, Triton 1,047%).
--   net margin      125 above 100%, some genuine (TriMas 136% on a sale, Rigel 107% on a tax asset), so
--                   only the 10x bound applies. Below: 10% of companies under -1,000%, and a real
--                   -350% (Lucid) stays.
--   base            revenue under $100K: 2-6% of companies; a margin or growth rate on it says nothing.
--   revenue growth  p95 146%, p99 1,497%. Moderna +2,200% on $803M and Liquidia +2,234% on $19M are
--                   real; +278,712% is not, so the bound needs the base: over 1,000% from under $10M.
--   debt / equity   p95 13x, p99 115x. Colgate 24.9x, Clorox 44x, Gartner 47x, Amneal 78x are genuine:
--                   equity really is small and still a number. Above 100x equity is under 1% of debt
--                   and is the ratio's whole story (Kinetik 230,770x, GoDaddy 563x, Iron Mountain 686x).
--   ROE             ten times equity: 319 of 4,608 beyond it, mostly SPACs; Nutanix 37,460% on an
--                   average of negative and positive equity. Payout over ten times net income, current
--                   ratio over 100x (the Invesco CurrencyShares trusts at 2,951x) the same way.
-- Negative equity: gold has no debt/equity or ROE there (014's guard), so "latest" reached back to
-- the last positive quarter: HCA's 125.8x from 2021, Lowe's 52.2x from 2021, Philip Morris 149.6x
-- from 2012. It now reads not meaningful, with that reason, whatever the old row says.
--
-- Stale: a value more than 300 days before the company's latest period is old. A figure only the
-- 10-K tags is at most three quarters (about 275 days) behind, so 300 isn't explained by the
-- reporting calendar: the tag stopped. 2,835 companies have a net debt, and 749 of them are
-- old by this rule, most by over two years. Over 45 days is merely a different quarter: the date
-- shows, the value still counts ("behind").
--
-- Banks: margins before interest, free cash flow, net debt and the balance-sheet ratios don't describe
-- a bank (deposits are its debt, lending its operations), whether or not a value exists. The old
-- rule (web, on empty cells only) split the banks by whether they happen to tag capex: JPM, BAC and
-- WFC read n/a while Citi and 306 others of 410 printed a free cash flow (median FCF margin 156%).
-- A bank here is the SEC's depository SICs (6021, 6022, 6029, 6035, 6036, 6099), plus Goldman Sachs
-- and Morgan Stanley: bank holding companies the SEC files under broker-dealers (6211).
--
-- Nothing changes who can read what: gold.latest_reading is SECURITY INVOKER (RLS on both gold tables
-- applies) and inlines into the screener's LATERAL like 032's gold.latest_value. No comparison here is
-- an index condition: the PK probes are text and date comparisons (texteq, date_le are leakproof) and
-- the rest is evaluated on rows RLS has already let through.
--
-- Locks on hermes: new functions only, so nothing on any table; it doesn't touch
-- gold.refresh_quarterly_financials or gold.latest_value. No rebuild needed.

SET LOCAL lock_timeout = '30s';

-- Metrics that don't apply to this company (today: only banks). Empty for every other company.
CREATE FUNCTION gold.not_applicable_metrics(p_company_id text, p_sic text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN p_sic IN ('6021', '6022', '6029', '6035', '6036', '6099')
                     OR p_company_id IN ('0000886982', '0000895421')  -- GS, MS (SIC 6211)
                THEN ARRAY['gross_margin', 'operating_margin', 'free_cash_flow', 'fcf_margin',
                           'net_debt', 'debt_to_equity', 'current_ratio']
                ELSE ARRAY[]::text[] END
$$;

-- Why a figure is not meaningful, or NULL. p_inputs is the row's own gold.derived_financials inputs
-- (014). p_equity is the company's latest stockholders' equity, for the ratios gold can't compute
-- once it is negative. The words reach the user as they are: keep them a clause of a sentence.
CREATE FUNCTION gold.not_meaningful(p_metric text, p_value numeric, p_inputs jsonb,
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

-- "Stale", defined once. p_latest: gold.latest_period. Old values drop out of sorts, range filters
-- and medians; a value merely behind shows its date and still counts.
CREATE FUNCTION gold.period_status(p_period_end date, p_latest date) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN p_latest - p_period_end > 300 THEN 'old'
                WHEN p_latest - p_period_end > 45 THEN 'behind' END
$$;

-- The company's latest period: the newest of its revenue, net income and total assets, as index
-- lookups (the three agree with every metric's newest at 5,974 of 5,993 companies and cost a
-- screener sort three probes per company, not a read of each company's rows). Never a future
-- date: two filers tagged 2029 and 2033 (M10), and those must not make everything else look old.
CREATE FUNCTION gold.latest_period(p_company_id text)
RETURNS TABLE (latest date)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT max(x.period_end)
    FROM (
        (SELECT q.period_end FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'revenue'
           AND q.period_end <= current_date
         ORDER BY q.period_end DESC LIMIT 1)
        UNION ALL
        (SELECT q.period_end FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'net_income'
           AND q.period_end <= current_date
         ORDER BY q.period_end DESC LIMIT 1)
        UNION ALL
        (SELECT q.period_end FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'total_assets'
           AND q.period_end <= current_date
         ORDER BY q.period_end DESC LIMIT 1)
    ) x
$$;
GRANT EXECUTE ON FUNCTION gold.latest_period(text) TO query_api;

-- gold.latest_value (032) plus how to read it. Always one row, so a figure with no value can still
-- be not applicable (JPM has no free cash flow) or not meaningful (negative equity): period_end and
-- value are NULL then. status: not_applicable | not_meaningful | old | behind | NULL (nothing to
-- say); reason says why for the first two. The equity probe is a one-time filter on p_metric, so
-- it costs other metrics nothing.
CREATE FUNCTION gold.latest_reading(p_company_id text, p_span text, p_metric text, p_sic text,
                                    p_latest date)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date,
               status text, reason text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT x.period_end, x.value, x.accession_no, x.form, x.filed,
           CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'not_applicable'
                WHEN gold.not_meaningful(p_metric, x.value, x.inputs, e.value) IS NOT NULL
                    THEN 'not_meaningful'
                ELSE gold.period_status(x.period_end, p_latest) END,
           CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'this is a bank: deposits are its debt and lending its operations, so '
                         'the figure has no comparable meaning'
                ELSE gold.not_meaningful(p_metric, x.value, x.inputs, e.value) END
    FROM (SELECT 1) one
    LEFT JOIN LATERAL (
        SELECT y.period_end, y.value, y.accession_no, y.form, y.filed, y.inputs
        FROM (
            (SELECT q.period_end, q.value, q.accession_no, q.form, q.filed, NULL::jsonb AS inputs
             FROM gold.quarterly_financials q
             WHERE q.company_id = p_company_id AND q.metric = p_metric
               AND (p_span = 'quarter'
                    OR (p_span = 'ttm' AND EXISTS (SELECT 1 FROM silver.metrics m
                                                   WHERE m.metric = p_metric AND m.kind = 'instant')))
             ORDER BY q.period_end DESC LIMIT 1)
            UNION ALL
            (SELECT d.period_end, d.value, d.accession_no, d.form, d.filed, d.inputs
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
GRANT EXECUTE ON FUNCTION gold.latest_reading(text, text, text, text, date) TO query_api;
