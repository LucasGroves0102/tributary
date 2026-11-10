-- Screener sorts on margins and growth timed out (production, 2026-11-10 00:15 UTC, after the full
-- financials rebuild): GET /v1/screener?sort=-gross_margin_ttm (also -operating_margin_ttm,
-- -revenue_growth_ttm) answered 503 after the 5 s statement timeout; other value sorts took 0.7-2.4 s.
--
-- Cause: 060 made gold.latest_reading's `cu` lateral (the currency of a reading's base) call
-- gold.amount_currency. Its body is a SELECT of sub-selects, which the planner never inlines into a
-- caller, so it ran as a query of its own once per company and column; and as query_api each run
-- built the RLS policy's hashed subplan again (authz.readable_companies: with the catalog open, the
-- catalog's ~6,000 ids from authz.public_access) for gold.quarterly_financials, 1.5 ms a call against
-- 0.02 ms as the owner. 057's `cu` was a lateral inside the inlined body, under the subplan the outer
-- query builds once. A sort on one of the six readings with a base read it for every company (6,594
-- calls, 10 s); and in a generic plan (the Query API's pool prepares a statement after its fifth
-- run, and a screen's SQL is the same text whatever the column) the metric test became a join filter
-- applied after the call, so every value sort paid it (10.5 s for revenue).
--
-- 1. gold.amount_currency_of(company) RETURNS TABLE (unit text): 060's rule, the same four PK probes
--    and USD, copied unchanged, as a one-row set-returning SQL function, which the planner inlines
--    in FROM (STABLE, INVOKER, no SET, one SELECT).
-- 2. gold.amount_currency(company): that row's unit, so the rule stays in one place; its callers
--    (/financials, recaps, formula points) get the same answer at the same cost (it never inlined).
-- 3. gold.latest_reading: 060's body with `cu` reading gold.amount_currency_of in its FROM, LIMIT 1
--    (edit marked "061"), so the probes join the outer query's plan under its RLS subplans and the
--    metric test stays the subquery's one-time filter in a generic plan. Still one SQL SELECT,
--    STABLE, INVOKER, no SET: inlinable (test_latest_reading_stays_inlinable).
--
-- Measured as query_api with the catalog open on a catalog-sized copy (production's 8,081 companies;
-- each of the 6,594 with gold given the full gold rows of one of 529 sampled companies, stratified by
-- currency and annual-only filing: 6.62M quarterly and 7.24M derived rows), 126 sorts (every column
-- and metadata sort, 50 rows, the default columns): see db/README.md "061". Same rows, values,
-- readings and order for every sort, before and after.
--
-- Nothing about who reads what changes (all three INVOKER, RLS as before; CREATE OR REPLACE keeps
-- 060's grants and owner; the new function is granted to query_api like amount_currency). Generated
-- from 060's bodies by a script asserting each anchor once.
--
-- Locks on hermes: CREATE FUNCTION and CREATE OR REPLACE of two functions, catalog only
-- (lock_timeout 10 s; a query planned before the commit finishes with the old body). No table is
-- locked, read or rewritten and gold doesn't change: no rebuild. Backout:
-- db/backout/currency_speed_061.sql.

SET LOCAL lock_timeout = '10s';

-- 1.
CREATE FUNCTION gold.amount_currency_of(p_company_id text) RETURNS TABLE (unit text)
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
GRANT EXECUTE ON FUNCTION gold.amount_currency_of(text) TO query_api;

-- 2.
CREATE OR REPLACE FUNCTION gold.amount_currency(p_company_id text) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT c.unit FROM gold.amount_currency_of(p_company_id) c  -- 061
$$;

-- 3.
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
    -- annual-only filer's TTM rows (one PK probe for a company with quarterly revenue, as before).
    -- 061: the rule's set-returning form, which the planner inlines, so its probes run in this
    -- query under the RLS subplans it already built; LIMIT 1 (it is one row) keeps this a subquery
    -- of its own, so the metric test stays its one-time filter in a generic plan too (header)
    LEFT JOIN LATERAL (
        SELECT c.unit FROM gold.amount_currency_of(p_company_id) c
        WHERE p_metric IN ('gross_margin', 'operating_margin', 'net_margin', 'fcf_margin',
                           'revenue_growth', 'net_income_growth')
        LIMIT 1
    ) cu ON true
$$;
