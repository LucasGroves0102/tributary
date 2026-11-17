-- Backout of 069_readings_not_computed_stale.sql (never a migration: README.md): gold.latest_reading
-- back to 061's body, copied unchanged, and gold.not_computed dropped. Readings lose `not_computed`
-- (a gross margin Tributary doesn't compute reads a bare blank again) and `stale` (a company whose
-- latest period is long past sorts with current figures again). No rebuild, and the Query API needs no
-- other image: the signature is 061's and the web words an unknown status as a plain figure.
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as postgres,
-- a function it drops and re-creates would belong to postgres, and the non-superuser migrate login
-- could never replace it again. The guard below refuses any login that doesn't own the functions,
-- and any database where a newer backout hasn't run yet (README.md, Order).
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f readings_069.sql

BEGIN;
SET LOCAL lock_timeout = '10s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: revenue_fallbacks_080.sql,
-- screener_figures_078.sql, net_income_077.sql) and this one hasn't, and unless the login owns the
-- functions (the migrate login; header).
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
        ('gold.latest_reading(text,text,text,text,date)', '59c9079afdc0a3aea48c5a77e9bc526c'),
        ('gold.not_applicable_metrics(text,text)', '31aa1cba6092282684aaf6b699539359'),
        ('gold.not_computed(text,text)', 'a1d6b45f36a10d029335de6f30893f96'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', '472cb5abb2f6cb3ad0cbf64734468ee4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', '90812eb486b8ef14081bf2fea5ab1890'),
        ('gold.refresh_derived_financials(text[])', '7349566dfd5a8b5357be80633bcecc61'),
        ('gold.refresh_financials(text[])', '127cb472ccd41e6cc6d6768bfb1935a0'),
        ('gold.refresh_quarterly_financials(text[])', '84ce53d8fc3987c84012b4360b69fed4'),
        ('gold.screen_backlog()', '7d5ea26e61bff2e3c0c610a7f5148a6f'),
        ('gold.screen_company(text)', '476ff18b646bc1ce48eb45d1980cac51'),
        ('gold.screen_reading(text,text,text,text,date,boolean,numeric,text,boolean)', 'd89336966fbcc1d5b3b7ddafa8e5432c'),
        ('gold.usable_facts(text[])', '0b0c7a112a9761106084a4fd15dd2a56'),
        ('ops.check_financials(text[])', 'a8a94bf23f986a93f3f61c8fc5f35461'),
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
                'changed.', w.fn, 'revenue_fallbacks_080.sql, screener_figures_078.sql, net_income_077.sql');
        END IF;
    END LOOP;
END
$guard$;
-- end guard

-- 061's gold.latest_reading, copied unchanged
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

DROP FUNCTION gold.not_computed(text, text);

COMMIT;
