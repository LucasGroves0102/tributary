-- What the 10-K itself states for a fiscal year's EPS and weighted shares. Gold's quarters can't give
-- these: per-share amounts and average share counts don't add across quarters (the count changes),
-- so gold derives neither a TTM nor a fiscal Q4, and the Statements tab's Annual and TTM lost both
-- rows. The annual fact is in silver, picked as ops.check_financials picks annual values: the
-- highest-priority mapped tag, then the latest filing (so a split-adjusted comparative replaces the
-- original), never a proxy statement's figure (024).
--
-- SECURITY DEFINER because query_api has no grant on silver.financial_facts' values (008 gives it two
-- columns on purpose). As in 032 the read rule stays the only rule: authz.readable_companies
-- ('financials'), evaluated in the caller's session, so no app.user_id or no tier means no rows; the
-- route checks OpenFGA first. Returns one company's own reported figures, nothing else. search_path
-- pinned, EXECUTE only for query_api. A CREATE OR REPLACE must re-issue the REVOKE and GRANT.
--
-- Locks on hermes: a new function, nothing on any table. Reads the facts table's cik index, per request.

SET LOCAL lock_timeout = '30s';

CREATE FUNCTION gold.annual_per_share(p_company_id text, p_years int)
RETURNS TABLE (metric text, period_start date, period_end date, value numeric, unit text,
               source_tag text, accession_no text, filer_cik text, form text, filed date,
               ingested_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    SELECT y.metric, y.period_start, y.period_end, y.value, y.unit, y.source_tag, y.accession_no,
           y.cik, y.form, y.filed, y.ingested_at
    FROM (
        SELECT DISTINCT ON (f.metric, f.period_end)
               f.metric, f.period_start, f.period_end, f.value, f.unit, f.tag AS source_tag,
               f.accession_no, f.cik, f.form, f.filed, f.ingested_at,
               dense_rank() OVER (PARTITION BY f.metric ORDER BY f.period_end DESC) AS recency
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.metric = f.metric AND t.tag = f.tag
        JOIN silver.metrics m ON m.metric = f.metric AND m.unit = f.unit
        WHERE k.company_id = p_company_id
          AND k.company_id IN (SELECT r FROM authz.readable_companies('financials') r)
          AND f.metric IN ('eps_diluted', 'eps_basic', 'weighted_shares_diluted',
                           'weighted_shares_basic')
          AND f.period_type = 'annual'
          AND coalesce(f.form, '') !~ '^(DEF|PRE)'
        ORDER BY f.metric, f.period_end, t.priority, f.filed DESC, f.accession_no DESC
    ) y
    WHERE y.recency <= p_years
$fn$;
REVOKE EXECUTE ON FUNCTION gold.annual_per_share(text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.annual_per_share(text, int) TO query_api;
