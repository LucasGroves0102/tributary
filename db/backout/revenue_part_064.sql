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
-- nothing, unless every newer backout has run (newest first: point_in_time_085.sql,
-- revenue_fallbacks_080.sql, screener_figures_078.sql, net_income_077.sql, readings_069.sql) and
-- this one hasn't, and unless the login owns the functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('gold.amount_currency(text)', 'd59a928b3b91fb9a6da98fc15e72a536'),
        ('gold.amount_currency_of(text)', '6d18237414dd02bfe4078cac3f7b429e'),
        ('gold.amount_currency_of(text,date)', NULL),
        ('gold.annual_per_share(text,integer)', '663430e80a6341f8a0e4227e1491e657'),
        ('gold.annual_per_share(text,integer,date)', NULL),
        ('gold.best_facts(text[])', 'd95fe7b45b1f9db2e37c7e01b500e5c0'),
        ('gold.best_facts(text[],date)', NULL),
        ('gold.companies_for_ciks(text[])', '5f02b9f1e395d7f743635d0a399f8f6c'),
        ('gold.company_confidence(text,text,date)', '139f9512b8629850a2e0f66333e44d37'),
        ('gold.company_financials_at(text,date,text,text[])', NULL),
        ('gold.derived_rows(text[],date,gold.quarter_row[])', NULL),
        ('gold.figure_facts(text,text,date)', '6789892e97611dd6c0260a25f695d7fc'),
        ('gold.filing_figures(text,text)', '51376508ac8a1a66d7e42cc92852346f'),
        ('gold.financial_rows(text[],date)', NULL),
        ('gold.financials_at(date)', NULL),
        ('gold.input_values(jsonb)', NULL),
        ('gold.knowledge_facts(text)', NULL),
        ('gold.known_changed_from(text)', NULL),
        ('gold.latest_equity(text)', '2dbdc33d9580044bb743cbaf5a98f252'),
        ('gold.latest_equity(text,date)', NULL),
        ('gold.latest_period(text)', '3c5a3d7158d43ad4e5cb09aaac1af74d'),
        ('gold.latest_period(text,date)', NULL),
        ('gold.latest_reading(text,text,text,text,date)', 'ec605b7822cdddde37d4ce22273cb4e0'),
        ('gold.latest_reading(text,text,text,text,date,date)', NULL),
        ('gold.latest_row(text,text,text)', '3863618d66983ddd14bf8fa00e2f62c8'),
        ('gold.latest_row(text,text,text,date)', NULL),
        ('gold.not_applicable_metrics(text,text)', '31aa1cba6092282684aaf6b699539359'),
        ('gold.not_computed(text,text)', NULL),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', '472cb5abb2f6cb3ad0cbf64734468ee4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', '9f4c2cd4aeaefa663263efb806700f5d'),
        ('gold.quarterly_recent(text,date)', '91ece4790c2b876518c6d2a4cf060286'),
        ('gold.quarterly_recent(text,date,date)', NULL),
        ('gold.quarterly_rows(text[],date)', NULL),
        ('gold.rebuild_known(text[])', NULL),
        ('gold.refresh_derived_financials(text[])', '6e52ee6444dc46cca8bbd52de1a6983e'),
        ('gold.refresh_financials(text[])', '127cb472ccd41e6cc6d6768bfb1935a0'),
        ('gold.refresh_known(text[])', NULL),
        ('gold.refresh_quarterly_financials(text[])', '84ce53d8fc3987c84012b4360b69fed4'),
        ('gold.replay_companies(text[],boolean)', NULL),
        ('gold.replay_known(text,date)', NULL),
        ('gold.restatements(text,date)', NULL),
        ('gold.screen_backlog()', '7d5ea26e61bff2e3c0c610a7f5148a6f'),
        ('gold.screen_company(text)', '476ff18b646bc1ce48eb45d1980cac51'),
        ('gold.screen_reading(text,text,text,text,date,boolean,numeric,text,boolean)', 'd89336966fbcc1d5b3b7ddafa8e5432c'),
        ('gold.usable_facts(text[])', '0b0c7a112a9761106084a4fd15dd2a56'),
        ('gold.usable_facts(text[],date)', NULL),
        ('ops.check_financials(text[])', 'a8a94bf23f986a93f3f61c8fc5f35461'),
        ('ops.company_backlog(text)', '97bd3cf942ea7f5c87ed866c0ba563d8'),
        ('ops.company_rule_version(text)', '60744ba7ead2e38d3dc3791e5630df38'),
        ('ops.rebuild_soon(text[])', 'c5a3427c2fac8f4cfdddb3ad14021fd7'),
        ('silver.refresh_filing_facts(text[])', '3b400f0ebef7ea4ae47aeac60b50cca6'),
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
                'changed.', w.fn, 'point_in_time_085.sql, revenue_fallbacks_080.sql, screener_figures_078.sql, net_income_077.sql, readings_069.sql');
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
