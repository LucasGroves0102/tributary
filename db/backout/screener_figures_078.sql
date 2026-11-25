-- Backout of 078_screener_figures.sql (never a migration: README.md): the screener reads live
-- again. gold.latest_reading back to 069's body and gold.refresh_financials to 071's (copied
-- unchanged); gold.screen_company and gold.screen_reading
-- read live only (the Query API from 078 keeps calling them: no other image needed), the backlog
-- is empty (the hourly catch-up stores nothing) and both tables are emptied. The readings are the
-- same; the screener's cold first request is as slow as before 078. 078's other functions stay,
-- unused. Run it before readings_069.sql: that one drops gold.not_computed, which gold.reading
-- (078) reads.
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as postgres,
-- a function it re-creates would belong to postgres, and the non-superuser migrate login could never
-- replace it again. The guard below refuses any login that doesn't own the functions, and any
-- database where a newer backout hasn't run yet (README.md, Order).
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f screener_figures_078.sql

BEGIN;
SET LOCAL lock_timeout = '10s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: release_figures_165.sql,
-- analyst_fixes_155.sql, point_in_time_085.sql, revenue_fallbacks_080.sql) and this one hasn't, and
-- unless the login owns the functions (the migrate login; header).
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
        ('gold.best_facts(text[])', '21ac196fb91af417d9f27ff079a35882'),
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
        ('gold.latest_reading(text,text,text,text,date)', '097ab17641e6893f4284b9d08c621637'),
        ('gold.latest_reading(text,text,text,text,date,date)', NULL),
        ('gold.latest_row(text,text,text)', '3863618d66983ddd14bf8fa00e2f62c8'),
        ('gold.latest_row(text,text,text,date)', NULL),
        ('gold.not_applicable_metrics(text,text)', '31aa1cba6092282684aaf6b699539359'),
        ('gold.not_computed(text,text)', 'a1d6b45f36a10d029335de6f30893f96'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', '472cb5abb2f6cb3ad0cbf64734468ee4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', '9f4c2cd4aeaefa663263efb806700f5d'),
        ('gold.not_meaningful_for(text,text)', NULL),
        ('gold.quarterly_recent(text,date)', '91ece4790c2b876518c6d2a4cf060286'),
        ('gold.quarterly_recent(text,date,date)', NULL),
        ('gold.quarterly_rows(text[],date)', NULL),
        ('gold.reading(text,text,text,date,date,numeric,jsonb,text,text,numeric,text,boolean)', '419a0e26e3d341c59e38aa3b5be6dbc7'),
        ('gold.rebuild_known(text[])', NULL),
        ('gold.refresh_derived_financials(text[])', '6e52ee6444dc46cca8bbd52de1a6983e'),
        ('gold.refresh_financials(text[])', '4bd8545d1e9cf94fa665cdee21294039'),
        ('gold.refresh_known(text[])', NULL),
        ('gold.refresh_quarterly_financials(text[])', '84ce53d8fc3987c84012b4360b69fed4'),
        ('gold.replay_companies(text[],boolean)', NULL),
        ('gold.replay_known(text,date)', NULL),
        ('gold.restatements(text,date)', NULL),
        ('gold.screen_backlog()', '972441f449f03e3d8fa36a9748610e85'),
        ('gold.screen_company(text)', 'd0ab770fb304683145115aeb8dcee30d'),
        ('gold.screen_reading(text,text,text,text,date,boolean,numeric,text,boolean)', 'f110e0894f0dff22b6fb8fe0423313fe'),
        ('gold.usable_facts(text[])', '0b0c7a112a9761106084a4fd15dd2a56'),
        ('gold.usable_facts(text[],date)', NULL),
        ('ops.check_financials(text[])', 'a8a94bf23f986a93f3f61c8fc5f35461'),
        ('ops.company_backlog(text)', '97bd3cf942ea7f5c87ed866c0ba563d8'),
        ('ops.company_rule_version(text)', '60744ba7ead2e38d3dc3791e5630df38'),
        ('ops.rebuild_soon(text[])', 'c5a3427c2fac8f4cfdddb3ad14021fd7'),
        ('ops.release_backlog(text)', NULL),
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
                'changed.', w.fn, 'release_figures_165.sql, analyst_fixes_155.sql, point_in_time_085.sql, revenue_fallbacks_080.sql');
        END IF;
    END LOOP;
END
$guard$;
-- end guard

-- 069's gold.latest_reading, copied unchanged
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
                -- 069: no figure (or only an old one) because Tributary computes none here
                WHEN (x.value IS NULL OR gold.period_status(x.period_end, p_latest) = 'old')
                     AND p_latest IS NOT NULL AND gold.not_computed(p_metric, p_sic) IS NOT NULL
                    THEN 'not_computed'
                WHEN gold.not_meaningful(p_metric, x.value, x.inputs, e.value, coalesce(cu.unit, 'USD')) IS NOT NULL
                    THEN 'not_meaningful'
                -- 069: the company's latest period is long past (header, 2); an old value stays old
                WHEN st.stale AND x.value IS NOT NULL
                     AND gold.period_status(x.period_end, p_latest) IS DISTINCT FROM 'old'
                    THEN 'stale'
                ELSE gold.period_status(x.period_end, p_latest) END,
           CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'this is a bank: deposits are its debt and lending its operations, so '
                         'the figure has no comparable meaning'
                WHEN x.unit ~ '^[A-Z]{3}(/shares)?$' AND x.unit !~ '^USD'
                    THEN 'reported in ' || split_part(x.unit, '/', 1)
                         || ', so it isn''t sorted, filtered or averaged with US-dollar figures'
                -- 069
                WHEN (x.value IS NULL OR gold.period_status(x.period_end, p_latest) = 'old')
                     AND p_latest IS NOT NULL AND gold.not_computed(p_metric, p_sic) IS NOT NULL
                    THEN gold.not_computed(p_metric, p_sic)
                WHEN gold.not_meaningful(p_metric, x.value, x.inputs, e.value, coalesce(cu.unit, 'USD')) IS NOT NULL
                    THEN gold.not_meaningful(p_metric, x.value, x.inputs, e.value, coalesce(cu.unit, 'USD'))
                -- 069
                WHEN st.stale AND x.value IS NOT NULL
                     AND gold.period_status(x.period_end, p_latest) IS DISTINCT FROM 'old'
                    THEN 'this company''s latest figures here are for the period ended '
                         || to_char(p_latest, 'FMMon FMDD, YYYY') || ', and no report since has '
                         'reached Tributary, so they aren''t compared with current figures' END,
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
    -- 069: stale (header, 2). Only past 270 days (the gate); then 500 for an annual filer, by the
    -- reading's form or by no quarterly revenue or net income within 200 days of the latest period
    LEFT JOIN LATERAL (
        SELECT CASE WHEN t.today - p_latest > 500 THEN true
                    WHEN x.form ~ '^(20-F|40-F|6-K)' THEN false
                    ELSE EXISTS (SELECT 1 FROM gold.quarterly_financials q
                                 WHERE q.company_id = p_company_id
                                   AND q.metric IN ('revenue', 'net_income')
                                   AND q.period_end > p_latest - 200
                                   AND q.period_end <= p_latest) END AS stale
        FROM (SELECT coalesce(nullif(current_setting('tributary.today', true), '')::date,
                              current_date) AS today) t
        WHERE t.today - p_latest > 270
        LIMIT 1
    ) st ON true
$$;

-- 071's gold.refresh_financials, copied unchanged
CREATE OR REPLACE FUNCTION gold.refresh_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
    rules integer := gold.rules_version();
    checks integer := ops.check_financials_version();
BEGIN
    PERFORM gold.lock_companies(p_company_ids);
    n := gold.refresh_quarterly_financials(p_company_ids);
    PERFORM gold.refresh_derived_financials(p_company_ids);
    PERFORM ops.check_financials(p_company_ids);
    PERFORM ops.stamp_builds('gold', p_company_ids, rules);
    PERFORM ops.stamp_builds('check_financials', p_company_ids, checks);
    RETURN n;
END
$$;

-- live only: the latest period, nothing stored
CREATE OR REPLACE FUNCTION gold.screen_company(p_company_id text)
RETURNS TABLE (latest date, equity numeric, currency text, recent boolean, cached boolean)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT l.latest, NULL::numeric, NULL::text, NULL::boolean, false
    FROM gold.latest_period(p_company_id) l
$$;

CREATE OR REPLACE FUNCTION gold.screen_reading(p_company_id text, p_span text, p_metric text,
                                               p_sic text, p_latest date, p_cached boolean,
                                               p_equity numeric, p_currency text,
                                               p_recent boolean)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date,
               status text, reason text, unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT x.* FROM gold.latest_reading(p_company_id, p_span, p_metric, p_sic, p_latest) x
$$;

CREATE OR REPLACE FUNCTION gold.screen_backlog() RETURNS TABLE (company_id text)
LANGUAGE sql STABLE AS $$
    SELECT NULL::text WHERE false
$$;

TRUNCATE gold.screen_companies, gold.screen_figures;

COMMIT;
