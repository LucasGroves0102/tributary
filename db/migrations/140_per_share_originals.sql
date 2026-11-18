-- Per-share figures as first filed (Riley, test 10, finding 4): the Statements tab's "As filed" read
-- gold's quarters, which hold the LATEST filing to state a quarter. A quarter a later filing
-- restated for a stock split then read on the new basis beside quarters only ever filed on the old
-- one: NVIDIA's Jul 2023 quarter read $0.25 (the Aug 2024 10-Q's comparative, after the 10-for-1)
-- next to $0.82 for Apr 2023, a fake 70% fall. As filed is each filing's own figure for its period.
--
-- gold.per_share_originals(company, as_of): for the company's EPS and weighted share counts
-- (diluted and basic), each quarter's and each fiscal year's figure from the FIRST periodic filing
-- (10-K, 10-Q, 20-F, 40-F; amendments and other forms left out) to state it, filed by the end of
-- as_of (NULL: today), with its tag, filing and filer CIK; within that filing the metric's best tag
-- (silver.metric_tags priority). query_api.per_share swaps these in for gold's rows when asked for
-- `as_filed` (only for a period gold has, in the same unit); split-adjusted figures are unchanged
-- (gold's latest restated figure, then the splits after it). Gold keeps every figure as it chose it.
--
-- SECURITY DEFINER because query_api has no grant on silver.financial_facts' values (036's
-- gold.annual_per_share pattern): authz.readable_companies('financials') its only rule, search_path
-- pinned, EXECUTE only query_api, the company's own per-share facts only. One company per call; the
-- financial_facts_cik_metric index serves it (NVIDIA: 320 rows in 4 ms on hermes, 2026-11-18).
--
-- Locks on hermes (lock_timeout 3 s): CREATE FUNCTION only; no table is locked, read or rewritten.

SET LOCAL lock_timeout = '3s';

CREATE FUNCTION gold.per_share_originals(p_company_id text, p_as_of date DEFAULT NULL)
RETURNS TABLE (metric text, span text, period_start date, period_end date, value numeric,
               unit text, source_tag text, accession_no text, filer_cik text, form text,
               filed date)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    SELECT DISTINCT ON (f.metric, f.period_type, f.period_end)
           f.metric, CASE f.period_type WHEN 'quarter' THEN 'quarter' ELSE 'annual' END,
           f.period_start, f.period_end, f.value, f.unit, f.tag, f.accession_no, f.cik, f.form,
           f.filed
    FROM silver.company_ciks k
    JOIN silver.financial_facts f ON f.cik = k.cik
    JOIN silver.metric_tags t ON t.metric = f.metric AND t.tag = f.tag
                             AND t.taxonomy = f.taxonomy AND t.company_id IS NULL
    WHERE k.company_id = p_company_id
      AND k.company_id IN (SELECT r FROM authz.readable_companies('financials') r)
      AND f.metric IN ('eps_diluted', 'eps_basic', 'weighted_shares_diluted',
                       'weighted_shares_basic')
      AND f.period_type IN ('quarter', 'annual')
      AND coalesce(f.form, '') ~ '^(10-K|10-Q|20-F|40-F)'
      AND coalesce(f.form, '') !~ '/A$'
      AND (p_as_of IS NULL OR f.filed <= p_as_of)
    ORDER BY f.metric, f.period_type, f.period_end, f.filed, t.priority, f.accession_no
$fn$;
REVOKE EXECUTE ON FUNCTION gold.per_share_originals(text, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.per_share_originals(text, date) TO query_api;
