-- Backout of 064_revenue_net_of_interest_part.sql (never a migration: README.md): gold.best_facts
-- back to 039's body, copied unchanged, so RevenuesNetOfInterestExpense under 20% of the period's
-- gross revenue is believed again (Rectitude's FY2025 S$74,328, StoneX's net operating revenues),
-- and so are revenue figures 1,000 times off another tag of their period (Tigo's $50B contract tag,
-- Plexus' quarters in thousands). Then run the financials-rebuild job for the same CIKs 064's
-- rebuild took (or for all), which puts the old rows back.
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as
-- postgres, a function it drops and re-creates would belong to postgres, and the non-superuser
-- migrate login could never replace it again. The guard below refuses any login that doesn't own
-- the functions, and any database where a newer backout hasn't run yet (README.md, Order).
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f revenue_part_064.sql

BEGIN;
SET LOCAL lock_timeout = '10s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: none, this is the newest) and this one
-- hasn't, and unless the login owns the functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('gold.amount_currency(text)', 'd59a928b3b91fb9a6da98fc15e72a536'),
        ('gold.amount_currency_of(text)', '6d18237414dd02bfe4078cac3f7b429e'),
        ('gold.annual_per_share(text,integer)', '663430e80a6341f8a0e4227e1491e657'),
        ('gold.best_facts(text[])', 'd95fe7b45b1f9db2e37c7e01b500e5c0'),
        ('gold.companies_for_ciks(text[])', '5f02b9f1e395d7f743635d0a399f8f6c'),
        ('gold.figure_facts(text,text,date)', '6789892e97611dd6c0260a25f695d7fc'),
        ('gold.filing_figures(text,text)', '51376508ac8a1a66d7e42cc92852346f'),
        ('gold.latest_reading(text,text,text,text,date)', 'ec605b7822cdddde37d4ce22273cb4e0'),
        ('gold.not_applicable_metrics(text,text)', '31aa1cba6092282684aaf6b699539359'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', '472cb5abb2f6cb3ad0cbf64734468ee4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', '90812eb486b8ef14081bf2fea5ab1890'),
        ('gold.refresh_derived_financials(text[])', '7349566dfd5a8b5357be80633bcecc61'),
        ('gold.refresh_quarterly_financials(text[])', '84ce53d8fc3987c84012b4360b69fed4'),
        ('gold.usable_facts(text[])', '0b0c7a112a9761106084a4fd15dd2a56'),
        ('ops.check_financials(text[])', '510eaedc79ece7e71f7b8b0fb0b6a339'),
        ('silver.refresh_filing_facts(text[])', '566a561f85a949551ee2cdef2cf576f3'),
        ('silver.refresh_financial_facts(text[])', '813785ee2a47de9ea6e3a86f6176fef2')
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
                'changed.', w.fn, 'none, this is the newest');
        END IF;
    END LOOP;
END
$guard$;
-- end guard

-- 039's gold.best_facts, copied unchanged
CREATE OR REPLACE FUNCTION gold.best_facts(p_company_ids text[])
RETURNS TABLE (company_id text, metric text, tag text, taxonomy text, unit text, period_start date,
               period_end date, period_type text, value numeric, accession_no text, form text,
               filed date, ingested_at timestamptz, cik text, priority numeric, additive boolean)
LANGUAGE sql STABLE AS $$
    WITH tag_latest AS (
        SELECT DISTINCT ON (u.company_id, u.metric, u.tag, u.period_start, u.period_end) u.*
        FROM gold.usable_facts(p_company_ids) u
        ORDER BY u.company_id, u.metric, u.tag, u.period_start, u.period_end, u.filed DESC,
                 u.accession_no DESC, u.cik
    ),
    weighed AS (  -- revenue's two rules (header) read the period's other tags
        SELECT t.*,
               max(t.filed) FILTER (WHERE t.value >= 1000000) OVER w AS large_filed,
               max(t.filed) FILTER (WHERE t.value > 0 AND t.value < 1000000) OVER w AS small_filed,
               max(t.value) FILTER (WHERE t.tag IN (
                   'RevenueFromContractWithCustomerExcludingAssessedTax',
                   'RevenueFromContractWithCustomerIncludingAssessedTax')) OVER w AS contract_value
        FROM tag_latest t
        WINDOW w AS (PARTITION BY t.company_id, t.metric, t.period_start, t.period_end)
    )
    SELECT DISTINCT ON (x.company_id, x.metric, x.period_start, x.period_end)
           x.company_id, x.metric, x.tag, x.taxonomy, x.unit, x.period_start, x.period_end,
           x.period_type, x.value, x.accession_no, x.form, x.filed, x.ingested_at, x.cik, x.priority,
           x.additive
    FROM weighed x
    ORDER BY x.company_id, x.metric, x.period_start, x.period_end,
             (x.metric = 'revenue' AND x.period_start IS NOT NULL
              AND ((x.value = 0 AND (x.large_filed >= x.filed OR x.small_filed > x.filed))
                   OR (x.tag = 'Revenues' AND x.value > 0 AND x.value < 0.2 * x.contract_value)))
                 IS TRUE,
             x.priority, x.filed DESC, x.accession_no DESC, x.cik
$$;

COMMIT;
