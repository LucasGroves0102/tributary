-- Backout of 060_annual_filer_currency.sql (never a migration: README.md): gold.amount_currency and
-- gold.latest_reading back to 057's bodies, copied unchanged, so an annual-only filer's currency
-- reads USD again (its TTM rows aren't read). No rebuild: 060 changes how figures are read, not
-- gold's rows. The Query API needs no other image: the functions' signatures are 057's.
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as postgres,
-- a function it drops and re-creates would belong to postgres, and the non-superuser migrate login
-- could never replace it again. The guard below refuses any login that doesn't own the functions,
-- and any database where a newer backout hasn't run yet (README.md, Order).
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f annual_currency_060.sql

BEGIN;
SET LOCAL lock_timeout = '10s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: revenue_part_064.sql,
-- rebuild_speed_063.sql, currency_speed_061.sql) and this one hasn't, and unless the login owns the
-- functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('gold.amount_currency(text)', '6d18237414dd02bfe4078cac3f7b429e'),
        ('gold.amount_currency_of(text)', NULL),
        ('gold.annual_per_share(text,integer)', '663430e80a6341f8a0e4227e1491e657'),
        ('gold.best_facts(text[])', '62873ad55ef4c2f947442d18f9604f27'),
        ('gold.companies_for_ciks(text[])', '5f02b9f1e395d7f743635d0a399f8f6c'),
        ('gold.figure_facts(text,text,date)', '6789892e97611dd6c0260a25f695d7fc'),
        ('gold.filing_figures(text,text)', '51376508ac8a1a66d7e42cc92852346f'),
        ('gold.latest_reading(text,text,text,text,date)', '4cb30a4c5f1405288ad29ed8be42acd4'),
        ('gold.not_applicable_metrics(text,text)', '31aa1cba6092282684aaf6b699539359'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', '472cb5abb2f6cb3ad0cbf64734468ee4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', '90812eb486b8ef14081bf2fea5ab1890'),
        ('gold.refresh_derived_financials(text[])', 'daf9fa4950728307efdbdefa793ec6d0'),
        ('gold.refresh_quarterly_financials(text[])', '84ce53d8fc3987c84012b4360b69fed4'),
        ('gold.usable_facts(text[])', '0b0c7a112a9761106084a4fd15dd2a56'),
        ('ops.check_financials(text[])', '510eaedc79ece7e71f7b8b0fb0b6a339'),
        ('silver.refresh_filing_facts(text[])', '566a561f85a949551ee2cdef2cf576f3'),
        ('silver.refresh_financial_facts(text[])', '3b47cf049eeb9714470d25212df6ec05')
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
                'changed.', w.fn, 'revenue_part_064.sql, rebuild_speed_063.sql, currency_speed_061.sql');
        END IF;
    END LOOP;
END
$guard$;
-- end guard

-- 057's gold.amount_currency, copied unchanged
CREATE OR REPLACE FUNCTION gold.amount_currency(p_company_id text) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT coalesce(
        (SELECT q.unit FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'revenue'
         ORDER BY q.period_end DESC LIMIT 1),
        (SELECT q.unit FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'net_income'
         ORDER BY q.period_end DESC LIMIT 1),
        'USD')
$$;

-- 057's gold.latest_reading, copied unchanged
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
    -- readings that have a base; one PK probe
    LEFT JOIN LATERAL (
        SELECT q.unit FROM gold.quarterly_financials q
        WHERE p_metric IN ('gross_margin', 'operating_margin', 'net_margin', 'fcf_margin',
                           'revenue_growth', 'net_income_growth')
          AND q.company_id = p_company_id
          AND q.metric = CASE p_metric WHEN 'net_income_growth' THEN 'net_income' ELSE 'revenue' END
        ORDER BY q.period_end DESC LIMIT 1
    ) cu ON true
$$;

COMMIT;
