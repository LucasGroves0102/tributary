-- What one filing itself says (H2, Riley #2): the thesis check marked "Net debt was $10.92B" supported,
-- citing NVIDIA's 10-Q whose balance sheet shows $99.37B of marketable securities against $33.37B of
-- debt (net cash of about $66B). The claim matched Tributary's computed net debt, which then left the
-- securities out, and the check certified agreement with the app's own figure. The agent now compares a
-- claimed computed figure with the same figure computed from the cited filing's own facts
-- (services/agent thesis_check.py, query_api.thesis_checks CheckResult).
--
-- gold.filing_figures(company, accession): the filing's facts as gold reads them (gold.usable_facts:
-- signs, sums, a total's current portion, a co-registrant's filing, 050), the best tag per metric and
-- period of that filing only, plus the computed figures gold would make from them alone, by gold's own
-- rules: net_debt and cash_and_investments at each balance-sheet date (040: debt + current debt - cash -
-- short-term investments, which are inside cash when they don't fit the current assets beside it) and
-- free_cash_flow for each period the cash flow statement covers (operating cash flow - capex). No
-- sliver or "unknown investments" test: those need other filings, and a figure from this filing alone is
-- the point. A filing that tags no debt or no cash has no net debt here.
--
-- SECURITY DEFINER for the reason 036 and 049 give (query_api reads two columns of
-- silver.financial_facts); the only read rule is authz.readable_companies('financials') in the caller's
-- session (app.user_id set by Store from the JWT): a company the caller can't read gives no rows, even
-- past require(). search_path pinned, EXECUTE only for query_api; a CREATE OR REPLACE re-issues the
-- REVOKE and GRANT. Cost: gold.usable_facts for one company (NVIDIA's ~10k facts, about 100-200 ms), one
-- call per cited filing of a claimed computed figure.
--
-- Locks on hermes: CREATE FUNCTION, catalog only. No rebuild: nothing stored changes.

SET LOCAL lock_timeout = '30s';

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
