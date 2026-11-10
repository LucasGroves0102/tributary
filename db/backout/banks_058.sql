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
-- nothing, unless every newer backout has run (newest first: revenue_part_064.sql,
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
        ('gold.annual_per_share(text,integer)', '663430e80a6341f8a0e4227e1491e657'),
        ('gold.best_facts(text[])', '62873ad55ef4c2f947442d18f9604f27'),
        ('gold.companies_for_ciks(text[])', '5f02b9f1e395d7f743635d0a399f8f6c'),
        ('gold.figure_facts(text,text,date)', '6789892e97611dd6c0260a25f695d7fc'),
        ('gold.filing_figures(text,text)', '51376508ac8a1a66d7e42cc92852346f'),
        ('gold.latest_reading(text,text,text,text,date)', 'e8ecccdc7e77be5929165e9f36767827'),
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
                'changed.', w.fn, 'revenue_part_064.sql, rebuild_speed_063.sql, currency_speed_061.sql, annual_currency_060.sql');
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
