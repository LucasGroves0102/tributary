-- Backout of 058_bank_holding_companies.sql (never a migration: README.md): gold.not_applicable_metrics
-- back to 049's body, copied unchanged, so Charles Schwab, American Express and Synchrony read as the
-- brokers and card lenders their SICs say again (Ally stays a bank by its SIC, 6022). No rebuild is
-- needed for the readings; a financials-rebuild puts the fallback debt tags back into those three
-- companies' gold (gold.usable_facts drops them at a bank). Deploy the previous query-api and web images
-- with it only if their bank wording matters; they read the bank rule from this function.
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as postgres,
-- a function it drops and re-creates would belong to postgres, and the non-superuser migrate login
-- could never replace it again. The guard below refuses any login that doesn't own the functions,
-- and any database where a newer backout hasn't run yet (README.md, Order).
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f banks_058.sql

BEGIN;
SET LOCAL lock_timeout = '30s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: release_figures_165.sql,
-- analyst_fixes_155.sql, point_in_time_085.sql, revenue_fallbacks_080.sql,
-- screener_figures_078.sql, net_income_077.sql, readings_069.sql, revenue_part_064.sql,
-- rebuild_speed_063.sql, currency_speed_061.sql, annual_currency_060.sql) and this one hasn't, and
-- unless the login owns the functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('gold.amount_currency(text)', '84308633b72bc99f52616793528f7ebd'),
        ('gold.amount_currency_of(text)', NULL),
        ('gold.amount_currency_of(text,date)', NULL),
        ('gold.annual_per_share(text,integer)', '663430e80a6341f8a0e4227e1491e657'),
        ('gold.annual_per_share(text,integer,date)', NULL),
        ('gold.best_facts(text[])', '62873ad55ef4c2f947442d18f9604f27'),
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
        ('gold.latest_reading(text,text,text,text,date)', 'e8ecccdc7e77be5929165e9f36767827'),
        ('gold.latest_reading(text,text,text,text,date,date)', NULL),
        ('gold.latest_row(text,text,text)', '3863618d66983ddd14bf8fa00e2f62c8'),
        ('gold.latest_row(text,text,text,date)', NULL),
        ('gold.not_applicable_metrics(text,text)', '31aa1cba6092282684aaf6b699539359'),
        ('gold.not_computed(text,text)', NULL),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', '472cb5abb2f6cb3ad0cbf64734468ee4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', '9f4c2cd4aeaefa663263efb806700f5d'),
        ('gold.not_meaningful_for(text,text)', NULL),
        ('gold.quarterly_recent(text,date)', '91ece4790c2b876518c6d2a4cf060286'),
        ('gold.quarterly_recent(text,date,date)', NULL),
        ('gold.quarterly_rows(text[],date)', NULL),
        ('gold.reading(text,text,text,date,date,numeric,jsonb,text,text,numeric,text,boolean)', '419a0e26e3d341c59e38aa3b5be6dbc7'),
        ('gold.rebuild_known(text[])', NULL),
        ('gold.refresh_derived_financials(text[])', 'daf9fa4950728307efdbdefa793ec6d0'),
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
        ('ops.release_backlog(text)', NULL),
        ('silver.refresh_filing_facts(text[])', '3b400f0ebef7ea4ae47aeac60b50cca6'),
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
                'changed.', w.fn, 'release_figures_165.sql, analyst_fixes_155.sql, point_in_time_085.sql, revenue_fallbacks_080.sql, screener_figures_078.sql, net_income_077.sql, readings_069.sql, revenue_part_064.sql, rebuild_speed_063.sql, currency_speed_061.sql, annual_currency_060.sql');
        END IF;
    END LOOP;
END
$guard$;
-- end guard

CREATE OR REPLACE FUNCTION gold.not_applicable_metrics(p_company_id text, p_sic text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN p_sic IN ('6021', '6022', '6029', '6035', '6036', '6099')
                     OR p_company_id IN ('0000886982', '0000895421')  -- GS, MS (SIC 6211)
                THEN ARRAY['gross_margin', 'operating_margin', 'free_cash_flow', 'fcf_margin',
                           'net_debt', 'debt_to_equity', 'current_ratio',
                           'cash_and_investments']  -- 049
                ELSE ARRAY[]::text[] END
$$;

COMMIT;
