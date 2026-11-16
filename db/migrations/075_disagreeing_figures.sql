-- The two figures behind a tag_disagreement note, read when the note is read (owner decision,
-- 2026-11-16; db/DESIGN.md "075").
--
-- 072's tag_disagreement finding names the filing, the period, the two tags and their ratio, never the
-- figures (014's rule: finding detail holds no values, so ops views and the public status page can't
-- leak one). A reader of the company's figures should still see them: "This 10-Q gives cash as
-- $205,134 in one place and $205,134,000 in another". gold.disagreeing_figures reads them from silver
-- at read time, for the Query API's data notes (query_api/notes.py), after require(financials) and as
-- the user: SECURITY DEFINER because query_api has no grant on silver.financial_facts' values (030's
-- column grant is cik and accession_no), with authz.readable_companies('financials') as its only rule
-- (p_company_ids only narrows: figure_facts' and filing_figures' pattern). One row per finding and tag
-- that silver still holds: the finding's own filing, tag and period (start and end), a tag's latest
-- fact of them; a tag with no row was corrected since (a restated or reloaded filing) and the note
-- says so. Storage stays figure-free: findings, ops views and the status page are unchanged.
--
-- Locks on hermes: a new function, catalog only.

SET LOCAL lock_timeout = '3s';

CREATE FUNCTION gold.disagreeing_figures(p_company_ids text[])
RETURNS TABLE (company_id text, metric text, period_end date, accession_no text, tag text,
               value numeric, unit text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    SELECT f.company_id, f.metric, f.period_end, f.detail->>'accession_no', x.tag, x.value, x.unit
    FROM ops.quality_findings f
    CROSS JOIN LATERAL (
        SELECT DISTINCT ON (s.tag) s.tag, s.value, s.unit
        FROM silver.company_ciks k
        JOIN silver.financial_facts s ON s.cik = k.cik
        WHERE k.company_id = f.company_id
          AND s.accession_no = f.detail->>'accession_no'
          AND s.tag IN (f.detail->>'tag', f.detail->>'other_tag')
          AND s.period_end = f.period_end
          AND s.period_start IS NOT DISTINCT FROM (f.detail->>'period_start')::date
        ORDER BY s.tag, s.filed DESC, s.fact_id DESC
    ) x
    WHERE f.company_id = ANY(p_company_ids)
      AND f.company_id IN (SELECT r FROM authz.readable_companies('financials') r)
      AND f.check_name = 'tag_disagreement'
$fn$;
REVOKE EXECUTE ON FUNCTION gold.disagreeing_figures(text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.disagreeing_figures(text[]) TO query_api;
