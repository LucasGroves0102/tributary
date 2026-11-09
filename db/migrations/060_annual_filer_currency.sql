-- An annual-only filer's currency (pre-ship review, 057's minor). 057's gold.amount_currency read the
-- currency of a company's amounts from its latest quarterly revenue or net income row, else USD; so
-- did gold.latest_reading's lookup of a reading's base currency. A 20-F or 40-F filer reports no
-- quarters: its revenue and net income are its stated years, kept only as TTM rows of
-- gold.derived_financials (040's stated-year TTM), so both read USD for Toyota's yen or a euro filer.
-- Where that mattered: /financials, recaps and formula points pass gold.amount_currency to
-- gold.not_meaningful, and the screener's readings go through gold.latest_reading, so 035's dollar
-- bases ($100K of revenue, $10M a year earlier: 057, header 2) were applied to yen and won amounts.
--
-- 1. gold.amount_currency: after 057's two quarterly probes, the latest TTM revenue row of
--    gold.derived_financials, then net income's; USD only when none has a row. Still STABLE,
--    SECURITY INVOKER, plain SQL; PK probes (company_id, span, metric, period_end DESC), and only
--    when the quarters gave nothing (COALESCE stops at the first currency).
-- 2. gold.latest_reading: 057's body, its `cu` lookup now gold.amount_currency (edit marked "060"),
--    still only for the six readings with a base. Still one SQL SELECT, STABLE, INVOKER, no SET:
--    inlinable (test_latest_reading_stays_inlinable).
--
-- Nothing about who reads what changes (both INVOKER, RLS as before; CREATE OR REPLACE keeps 057's
-- GRANT and the owner). Generated from 057's bodies by a script asserting each edit's anchor once.
--
-- Locks on hermes: CREATE OR REPLACE of two functions only, a catalog change (lock_timeout 10 s; a
-- query planned before the commit finishes with the old body). No table is locked, read or
-- rewritten, gold doesn't change: no rebuild. Backout: db/backout/annual_currency_060.sql.

SET LOCAL lock_timeout = '10s';

-- 1. 057's gold.amount_currency, with the annual-only filers' TTM rows after the quarters.
CREATE OR REPLACE FUNCTION gold.amount_currency(p_company_id text) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT coalesce(
        (SELECT q.unit FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'revenue'
         ORDER BY q.period_end DESC LIMIT 1),
        (SELECT q.unit FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'net_income'
         ORDER BY q.period_end DESC LIMIT 1),
        -- 060: an annual-only filer's (20-F, 40-F) revenue and net income are its stated years,
        -- kept only as TTM rows of gold.derived_financials (PK probes, like the two above)
        (SELECT d.unit FROM gold.derived_financials d
         WHERE d.company_id = p_company_id AND d.span = 'ttm' AND d.metric = 'revenue'
         ORDER BY d.period_end DESC LIMIT 1),
        (SELECT d.unit FROM gold.derived_financials d
         WHERE d.company_id = p_company_id AND d.span = 'ttm' AND d.metric = 'net_income'
         ORDER BY d.period_end DESC LIMIT 1),
        'USD')
$$;

-- 2. 057's gold.latest_reading, its base's currency from gold.amount_currency (edit marked "060").
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
    -- readings that have a base; 060: gold.amount_currency's rule, which also reads an
    -- annual-only filer's TTM rows (one PK probe for a company with quarterly revenue, as before)
    LEFT JOIN LATERAL (
        SELECT gold.amount_currency(p_company_id) AS unit
        WHERE p_metric IN ('gross_margin', 'operating_margin', 'net_margin', 'fcf_margin',
                           'revenue_growth', 'net_income_growth')
    ) cu ON true
$$;
