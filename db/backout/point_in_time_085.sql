-- Backout of 085_point_in_time_pipeline.sql, 086_financials_known.sql and
-- 087_point_in_time_reads.sql (never a migration: README.md): everything point-in-time undone,
-- newest first: 087's reads (gold.financials_at, company_financials_at, restatements dropped; the
-- readings' probes back to one signature each: gold.latest_row, latest_equity and quarterly_recent
-- 078's, amount_currency_of 061's, latest_period 035's, latest_reading 083's; gold.annual_per_share
-- 056's two-argument form; gold.company_confidence 074's), then 086's history
-- (gold.financials_known, gold.known_companies and their functions dropped; gold.refresh_financials
-- back to 078's body; the 'known' catch-up kind and its stamps removed, ops.company_rule_version,
-- ops.company_backlog and ops.rebuild_soon back to 071's, 074's and 076's), then 085's pipeline
-- (gold.usable_facts and gold.best_facts with one argument, 056's and 080's; the writers 043's and
-- 084's; the pure functions and their types dropped). Deploy the previous query-api image first (it
-- calls the as-of forms). Gold's own rows are the same either way: nothing to rebuild.
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as
-- postgres, a function it drops and re-creates would belong to postgres, and the non-superuser
-- migrate login could never replace it again. The guard below refuses any login that doesn't own
-- the functions, and any database where a newer backout hasn't run yet (README.md, Order).
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f point_in_time_085.sql

BEGIN;
SET LOCAL lock_timeout = '10s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: analyst_fixes_155.sql) and this one
-- hasn't, and unless the login owns the functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('gold.amount_currency(text)', 'd59a928b3b91fb9a6da98fc15e72a536'),
        ('gold.amount_currency_of(text)', NULL),
        ('gold.amount_currency_of(text,date)', '7ef505eacda72c0de066a26d5f3303ed'),
        ('gold.annual_per_share(text,integer)', NULL),
        ('gold.annual_per_share(text,integer,date)', 'c27e50f38738222c370bb9b834620cab'),
        ('gold.best_facts(text[])', NULL),
        ('gold.best_facts(text[],date)', '6950247dc6c9f51d99de4b12e906dc5a'),
        ('gold.companies_for_ciks(text[])', '5f02b9f1e395d7f743635d0a399f8f6c'),
        ('gold.company_confidence(text,text,date)', '91c5d249e662b77610e084d4a0620ddb'),
        ('gold.company_financials_at(text,date,text,text[])', '63de4900f9b4285f61d220d0141ceda7'),
        ('gold.derived_rows(text[],date,gold.quarter_row[])', 'd22751c4c57103d6fda7665e6477a179'),
        ('gold.figure_facts(text,text,date)', '6789892e97611dd6c0260a25f695d7fc'),
        ('gold.filing_figures(text,text)', '51376508ac8a1a66d7e42cc92852346f'),
        ('gold.financial_rows(text[],date)', '341a1dec1ff52a3b51a05312c7e0de16'),
        ('gold.financials_at(date)', '9bdd938e555828de0b4eed294e0dad5d'),
        ('gold.input_values(jsonb)', '06de5fab08b2d846d578a006a9efc859'),
        ('gold.knowledge_facts(text)', '18e26ac36e3c21f7ce905a870362750c'),
        ('gold.known_changed_from(text)', '61c37386d5747694eadbb421824e579f'),
        ('gold.latest_equity(text)', NULL),
        ('gold.latest_equity(text,date)', '1890ba1959496c3ea2c74996620e23c1'),
        ('gold.latest_period(text)', NULL),
        ('gold.latest_period(text,date)', 'a666140990b5da7e3a53846f08a39bf6'),
        ('gold.latest_reading(text,text,text,text,date)', NULL),
        ('gold.latest_reading(text,text,text,text,date,date)', '57d405f2fb1fd44c33a77abaa962cd08'),
        ('gold.latest_row(text,text,text)', NULL),
        ('gold.latest_row(text,text,text,date)', '78b74abf479901f860df8e016ef725fb'),
        ('gold.not_applicable_metrics(text,text)', '31aa1cba6092282684aaf6b699539359'),
        ('gold.not_computed(text,text)', 'a1d6b45f36a10d029335de6f30893f96'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', '472cb5abb2f6cb3ad0cbf64734468ee4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', '9f4c2cd4aeaefa663263efb806700f5d'),
        ('gold.not_meaningful_for(text,text)', NULL),
        ('gold.quarterly_recent(text,date)', NULL),
        ('gold.quarterly_recent(text,date,date)', 'b7e20593a6dc7b936c9a138a15fb0951'),
        ('gold.quarterly_rows(text[],date)', '58f05bc5ff7d5f1f6bce48ec05d1201a'),
        ('gold.reading(text,text,text,date,date,numeric,jsonb,text,text,numeric,text,boolean)', '419a0e26e3d341c59e38aa3b5be6dbc7'),
        ('gold.rebuild_known(text[])', '7c0e1472047486fb812194cfe5b5919b'),
        ('gold.refresh_derived_financials(text[])', '6e9debd1c4fec0936d3498b7a9e4c147'),
        ('gold.refresh_financials(text[])', 'b3f53c9f634d1d1383ec72d363982a61'),
        ('gold.refresh_known(text[])', '4e601c1d2eab3b6f283829ea2c4774fc'),
        ('gold.refresh_quarterly_financials(text[])', '4cf929c3db5e1b7830220b9b3574bfe2'),
        ('gold.replay_companies(text[],boolean)', 'a88eba6b35d5504daf597ad643fe35a0'),
        ('gold.replay_known(text,date)', '39c0339c527f6718a084b2bc4c656f29'),
        ('gold.restatements(text,date)', '7097e219996f52c772e678e98917ef29'),
        ('gold.screen_backlog()', '972441f449f03e3d8fa36a9748610e85'),
        ('gold.screen_company(text)', 'd0ab770fb304683145115aeb8dcee30d'),
        ('gold.screen_reading(text,text,text,text,date,boolean,numeric,text,boolean)', 'f110e0894f0dff22b6fb8fe0423313fe'),
        ('gold.usable_facts(text[])', NULL),
        ('gold.usable_facts(text[],date)', 'fe486d44c294971b08f43188f6822765'),
        ('ops.check_financials(text[])', 'a8a94bf23f986a93f3f61c8fc5f35461'),
        ('ops.company_backlog(text)', 'c57e6d676904010c5295c661689911ca'),
        ('ops.company_rule_version(text)', 'c42c4ab6b3199df8659901cfcff470c4'),
        ('ops.rebuild_soon(text[])', '9426fba21e20547353faa6ed8bc0824e'),
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
                'changed.', w.fn, 'analyst_fixes_155.sql');
        END IF;
    END LOOP;
END
$guard$;
-- end guard

-- 087: the reads as of a day
DROP FUNCTION gold.restatements(text, date);
DROP FUNCTION gold.company_financials_at(text, date, text, text[]);
DROP FUNCTION gold.financials_at(date);
DROP FUNCTION gold.latest_reading(text, text, text, text, date, date);
DROP FUNCTION gold.latest_row(text, text, text, date);
DROP FUNCTION gold.latest_equity(text, date);
DROP FUNCTION gold.quarterly_recent(text, date, date);
DROP FUNCTION gold.amount_currency_of(text, date);
DROP FUNCTION gold.latest_period(text, date);
DROP FUNCTION gold.annual_per_share(text, int, date);

-- 078's gold.latest_row, latest_equity and quarterly_recent, copied unchanged
CREATE FUNCTION gold.latest_row(p_company_id text, p_span text, p_metric text)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date,
               inputs jsonb, unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
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
$$;
CREATE FUNCTION gold.latest_equity(p_company_id text)
RETURNS TABLE (value numeric)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT q.value FROM gold.quarterly_financials q
    WHERE q.company_id = p_company_id AND q.metric = 'stockholders_equity'
      AND q.period_end <= current_date
    ORDER BY q.period_end DESC LIMIT 1
$$;
CREATE FUNCTION gold.quarterly_recent(p_company_id text, p_latest date)
RETURNS TABLE (recent boolean)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT EXISTS (SELECT 1 FROM gold.quarterly_financials q
                   WHERE q.company_id = p_company_id
                     AND q.metric IN ('revenue', 'net_income')
                     AND q.period_end > p_latest - 200
                     AND q.period_end <= p_latest)
$$;

-- 061's gold.amount_currency_of, copied unchanged
CREATE FUNCTION gold.amount_currency_of(p_company_id text) RETURNS TABLE (unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT coalesce(
        (SELECT q.unit FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'revenue'
         ORDER BY q.period_end DESC LIMIT 1),
        (SELECT q.unit FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'net_income'
         ORDER BY q.period_end DESC LIMIT 1),
        -- 060: an annual-only filer's (20-F, 40-F) revenue and net income are its stated years,
        -- kept only as TTM rows of gold.derived_financials (PK probes, like the two above)
        (SELECT d.unit FROM gold.derived_financials d
         WHERE d.company_id = p_company_id AND d.span = 'ttm' AND d.metric = 'revenue'
         ORDER BY d.period_end DESC LIMIT 1),
        (SELECT d.unit FROM gold.derived_financials d
         WHERE d.company_id = p_company_id AND d.span = 'ttm' AND d.metric = 'net_income'
         ORDER BY d.period_end DESC LIMIT 1),
        'USD')
$$;

-- 035's gold.latest_period, copied unchanged
CREATE FUNCTION gold.latest_period(p_company_id text)
RETURNS TABLE (latest date)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT max(x.period_end)
    FROM (
        (SELECT q.period_end FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'revenue'
           AND q.period_end <= current_date
         ORDER BY q.period_end DESC LIMIT 1)
        UNION ALL
        (SELECT q.period_end FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'net_income'
           AND q.period_end <= current_date
         ORDER BY q.period_end DESC LIMIT 1)
        UNION ALL
        (SELECT q.period_end FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = 'total_assets'
           AND q.period_end <= current_date
         ORDER BY q.period_end DESC LIMIT 1)
    ) x
$$;

-- 083's gold.latest_reading, copied unchanged
CREATE FUNCTION gold.latest_reading(p_company_id text, p_span text, p_metric text, p_sic text,
                                    p_latest date)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date,
               status text, reason text, unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT x.period_end, x.value, x.accession_no, x.form, x.filed, r.status, r.reason, x.unit
    FROM (SELECT 1) one
    LEFT JOIN LATERAL gold.latest_row(p_company_id, p_span, p_metric) x ON true
    LEFT JOIN LATERAL (
        SELECT q.value FROM gold.quarterly_financials q
        WHERE p_metric IN ('debt_to_equity', 'roe')
          AND q.company_id = p_company_id AND q.metric = 'stockholders_equity'
          AND q.period_end <= current_date
        ORDER BY q.period_end DESC LIMIT 1
    ) e ON true
    -- 057/060/061: the currency of the amounts a rule's base is in, only for the readings with a base
    LEFT JOIN LATERAL (
        SELECT c.unit FROM gold.amount_currency_of(p_company_id) c
        WHERE p_metric IN ('gross_margin', 'operating_margin', 'net_margin', 'fcf_margin',
                           'revenue_growth', 'net_income_growth')
        LIMIT 1
    ) cu ON true
    -- 083: cost of revenue at the revenue's period end, only where revenue can be gross sales
    LEFT JOIN LATERAL (
        SELECT c.value FROM gold.latest_row(p_company_id, p_span, 'cost_of_revenue') c
        WHERE p_metric = 'revenue' AND gold.gross_sales_sic(p_sic) AND c.period_end = x.period_end
        LIMIT 1
    ) gs ON true
    -- 069: the recent-quarters probe, only when the stale answer depends on it
    LEFT JOIN LATERAL (
        SELECT qr.recent FROM gold.quarterly_recent(p_company_id, p_latest) qr
        WHERE gold.is_stale(p_latest, x.form, true) AND NOT gold.is_stale(p_latest, x.form, false)
        LIMIT 1
    ) st ON true
    CROSS JOIN LATERAL gold.reading(p_company_id, p_metric, p_sic, p_latest, x.period_end, x.value,
                                    gold.with_cost(x.inputs, gs.value),  -- 083
                                    x.unit, x.form, e.value, cu.unit, st.recent) r
$$;

GRANT EXECUTE ON FUNCTION gold.latest_row(text, text, text), gold.latest_equity(text),
    gold.quarterly_recent(text, date), gold.amount_currency_of(text), gold.latest_period(text),
    gold.latest_reading(text, text, text, text, date) TO query_api;

-- 056's gold.annual_per_share, copied unchanged
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
                                 AND t.taxonomy = f.taxonomy AND t.company_id IS NULL
        JOIN silver.metrics m ON m.metric = f.metric
        CROSS JOIN LATERAL (
            SELECT coalesce((SELECT q.unit FROM gold.quarterly_financials q
                             WHERE q.company_id = p_company_id AND q.unit ~ '^[A-Z]{3}$'
                             ORDER BY q.period_end DESC LIMIT 1), 'USD') AS currency
        ) cu
        WHERE k.company_id = p_company_id
          AND k.company_id IN (SELECT r FROM authz.readable_companies('financials') r)
          AND f.metric IN ('eps_diluted', 'eps_basic', 'weighted_shares_diluted',
                           'weighted_shares_basic')
          AND f.unit = CASE m.unit WHEN 'USD/shares' THEN cu.currency || '/shares' ELSE m.unit END
          AND f.period_type = 'annual'
          AND coalesce(f.form, '') !~ '^(DEF|PRE)'
        ORDER BY f.metric, f.period_end, t.priority, f.filed DESC, f.accession_no DESC
    ) y
    WHERE y.recency <= p_years
$fn$;
REVOKE EXECUTE ON FUNCTION gold.annual_per_share(text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.annual_per_share(text, int) TO query_api;

-- 074's gold.company_confidence, copied unchanged
CREATE OR REPLACE FUNCTION gold.company_confidence(p_company_id text, p_sic text, p_latest date)
RETURNS TABLE (level text, reasons text[])
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT CASE WHEN q.company_id IS NULL AND p_latest IS NULL THEN NULL
                WHEN cardinality(x.low) > 0 THEN 'low'
                WHEN cardinality(x.medium) > 0 THEN 'medium'
                ELSE 'high' END,
           CASE WHEN q.company_id IS NULL AND p_latest IS NULL THEN NULL
                WHEN cardinality(x.low) > 0 THEN x.low
                WHEN cardinality(x.medium) > 0 THEN x.medium
                ELSE x.high END
    FROM (SELECT 1) one
    LEFT JOIN ops.quality_companies q ON q.company_id = p_company_id
    LEFT JOIN LATERAL gold.amount_currency_of(p_company_id) cu ON true
    CROSS JOIN LATERAL (
        SELECT coalesce(current_setting('tributary.today', true)::date, current_date) - p_latest
                   AS age,
               CASE WHEN q.figures > 0 THEN 1000.0 * q.errors / q.figures END AS errors_k,
               CASE WHEN q.figures > 0 THEN 1000.0 * q.warnings / q.figures END AS warnings_k
    ) t
    -- 069's annual filer (no quarterly revenue or net income in the 200 days before its latest
    -- period), probed only for a company past 270 days
    LEFT JOIN LATERAL (
        SELECT NOT EXISTS (
                   SELECT 1 FROM gold.quarterly_financials g
                   WHERE g.company_id = p_company_id AND g.metric IN ('revenue', 'net_income')
                     AND g.period_end BETWEEN p_latest - 200 AND p_latest
                     AND g.period_end - g.period_start BETWEEN 80 AND 120) AS annual
        WHERE t.age > 270
    ) a ON true
    CROSS JOIN LATERAL (
        SELECT array_remove(ARRAY[
                   CASE q.entity
                       WHEN 'shell' THEN 'a blank-check company with no business yet, so its '
                                         'figures say little about one'
                       WHEN 'commodity_pool' THEN 'a commodity pool or trust: its results are gains '
                                         'on what it holds, not sales and profits'
                       WHEN 'royalty_trust' THEN 'a royalty trust: it passes royalties through, so '
                                         'most operating figures don''t apply'
                       WHEN 'bdc' THEN 'a business development company: it reports investment '
                                         'income and net assets, which the standard lines read '
                                         'only in part' END,
                   CASE WHEN t.errors_k >= 2
                        THEN format('%s %s a reader could see in its %s figures', q.errors,
                                    CASE WHEN q.errors = 1 THEN 'error' ELSE 'errors' END,
                                    to_char(q.figures, 'FM999,999,999')) END,
                   CASE WHEN t.warnings_k >= 25
                        THEN format('%s %s to double-check in its %s', q.warnings,
                                    CASE WHEN q.warnings = 1 THEN 'figure' ELSE 'figures' END,
                                    to_char(q.figures, 'FM999,999,999')) END,
                   CASE WHEN t.age > 500
                        THEN 'no report since the period ended '
                             || to_char(p_latest, 'FMMon FMDD, YYYY') END,
                   CASE WHEN q.core_figures <= 2
                        THEN format('only %s of the six core figures for its latest period',
                                    q.core_figures) END
               ], NULL) AS low,
               array_remove(ARRAY[
                   CASE WHEN q.company_id IS NULL THEN 'not checked yet' END,
                   CASE WHEN q.errors > 0
                        THEN format('%s %s a reader could see', q.errors,
                                    CASE WHEN q.errors = 1 THEN 'error' ELSE 'errors' END) END,
                   CASE WHEN t.warnings_k >= 10
                        THEN format('%s %s to double-check in its %s', q.warnings,
                                    CASE WHEN q.warnings = 1 THEN 'figure' ELSE 'figures' END,
                                    to_char(q.figures, 'FM999,999,999')) END,
                   CASE WHEN q.statements_differ_at IS NOT NULL
                        THEN 'a filing''s own statement prints another headline figure than '
                             'the one here' END,
                   CASE WHEN cu.unit IS NOT NULL AND cu.unit <> 'USD'
                        THEN 'reported in ' || cu.unit || ': fewer of its lines map to the '
                             'standard ones, and nothing is converted' END,
                   CASE WHEN t.age > 270 AND t.age <= 500 AND NOT a.annual
                        THEN 'its latest figures are for the period ended '
                             || to_char(p_latest, 'FMMon FMDD, YYYY') END,
                   CASE WHEN q.core_figures IN (3, 4)
                        THEN format('%s of the six core figures for its latest period',
                                    q.core_figures) END
               ], NULL) AS medium,
               array_remove(ARRAY[
                   CASE WHEN q.figures > 0
                        THEN format('no error found in its %s figures',
                                    to_char(q.figures, 'FM999,999,999')) END,
                   CASE WHEN q.statements_agreed > 0
                        THEN format('%s %s from its filings'' own statements %s the figures here',
                                    q.statements_agreed,
                                    CASE WHEN q.statements_agreed = 1 THEN 'line' ELSE 'lines' END,
                                    CASE WHEN q.statements_agreed = 1 THEN 'agrees with'
                                         ELSE 'agree with' END) END
               ], NULL) AS high
    ) x
$$;

-- 086: the history
-- 078's gold.refresh_financials, copied unchanged
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
    PERFORM gold.refresh_screen(p_company_ids);  -- 078
    PERFORM ops.check_financials(p_company_ids);
    PERFORM ops.stamp_builds('gold', p_company_ids, rules);
    PERFORM ops.stamp_builds('check_financials', p_company_ids, checks);
    RETURN n;
END
$$;

DROP FUNCTION gold.refresh_known(text[]);
DROP FUNCTION gold.rebuild_known(text[]);
DROP FUNCTION gold.replay_companies(text[], boolean);
DROP FUNCTION gold.known_changed_from(text);
DROP FUNCTION gold.replay_known(text, date);
DROP FUNCTION gold.knowledge_facts(text);
DROP FUNCTION gold.financial_rows(text[], date);
DROP FUNCTION gold.input_values(jsonb);
DROP TABLE gold.known_companies;
DROP TABLE gold.financials_known;

-- the 'known' kind: its stamps and runs, then its row and the constraints as 074 left them
DELETE FROM ops.company_builds WHERE kind = 'known';
DELETE FROM ops.catchup_runs WHERE kind = 'known';
DELETE FROM ops.catchup WHERE kind = 'known';
ALTER TABLE ops.company_builds DROP CONSTRAINT company_builds_kind_check;
ALTER TABLE ops.company_builds ADD CONSTRAINT company_builds_kind_check
    CHECK (kind IN ('gold', 'check_financials', 'check_segments'));
ALTER TABLE ops.catchup DROP CONSTRAINT catchup_kind_check;
ALTER TABLE ops.catchup ADD CONSTRAINT catchup_kind_check
    CHECK (kind IN ('parser', 'passages', 'diffs', 'gold', 'check_financials', 'check_segments',
                    'check_sections', 'statements'));

-- 071's ops.company_rule_version, 074's ops.company_backlog, 076's ops.rebuild_soon, copied
-- unchanged
CREATE OR REPLACE FUNCTION ops.company_rule_version(p_kind text) RETURNS integer
LANGUAGE sql STABLE AS $$
    SELECT CASE p_kind WHEN 'gold' THEN gold.rules_version()
                       WHEN 'check_financials' THEN ops.check_financials_version()
                       WHEN 'check_segments' THEN ops.check_segments_version() END
$$;
CREATE OR REPLACE FUNCTION ops.company_backlog(p_kind text)
RETURNS TABLE (company_id text, built_at timestamptz, failed boolean)
LANGUAGE sql STABLE AS $$
    SELECT c.company_id, b.built_at,
           b.failed_version IS NOT DISTINCT FROM ops.company_rule_version(p_kind)
    FROM silver.companies c
    JOIN ops.catchup k ON k.kind = p_kind
    LEFT JOIN ops.company_builds b ON b.kind = p_kind AND b.company_id = c.company_id
    WHERE (coalesce(b.version, k.baseline) < ops.company_rule_version(p_kind)
           -- 074
           OR (p_kind = 'check_financials' AND EXISTS (
                  SELECT 1 FROM silver.company_ciks x
                  JOIN silver.statement_reads r ON r.cik = x.cik
                  WHERE x.company_id = c.company_id
                    AND r.read_at > coalesce(b.built_at, '-infinity'::timestamptz))))
      AND (p_kind <> 'check_financials' OR c.company_id NOT IN (
              SELECT g.company_id FROM ops.company_backlog('gold') g WHERE NOT g.failed))
$$;
CREATE OR REPLACE FUNCTION ops.rebuild_soon(p_company_ids text[]) RETURNS integer
LANGUAGE sql AS $$
    WITH due AS (
        INSERT INTO ops.company_builds AS b (kind, company_id, version, built_at)
        SELECT DISTINCT 'gold', id, 0, 'epoch'::timestamptz
        FROM unnest(p_company_ids) id WHERE id IS NOT NULL
        ON CONFLICT (kind, company_id) DO UPDATE
        SET version = 0, built_at = 'epoch', failed_version = NULL, failed_at = NULL,
            error = NULL
        RETURNING 1
    )
    SELECT count(*)::integer FROM due
$$;

-- 085: the pipeline
-- 043's gold.refresh_quarterly_financials, copied unchanged
CREATE OR REPLACE FUNCTION gold.refresh_quarterly_financials(p_company_ids text[])
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('gold.quarterly_financials'), hashtext(id))
    FROM (SELECT DISTINCT id FROM unnest(p_company_ids) id ORDER BY id) ids;

    DELETE FROM gold.quarterly_financials WHERE company_id = ANY(p_company_ids);

    WITH RECURSIVE best AS MATERIALIZED (
        SELECT * FROM gold.best_facts(p_company_ids)
    ),
    year_to_date AS (
        SELECT b.*,
               lag(value)        OVER w AS prev_value,
               lag(period_end)   OVER w AS prev_end,
               lag(accession_no) OVER w AS prev_accession,
               lag(ingested_at)  OVER w AS prev_ingested_at,
               lag(priority)     OVER w AS prev_priority
        FROM best b
        WHERE period_type <> 'instant'
        WINDOW w AS (PARTITION BY company_id, metric, period_start ORDER BY period_end)
    ),
    reported AS (
        SELECT company_id, metric, period_start, period_end, value, unit,
               'reported' AS value_source, taxonomy || ':' || tag AS source_tag, accession_no,
               form, filed, ARRAY[accession_no] AS source_accessions, ingested_at, priority
        FROM best
        -- instants are balance-sheet values; a flow metric tagged at an instant (UnitedHealth
        -- tags each dividend payment on its payment date) is not a quarter
        WHERE period_type = 'quarter' OR (period_type = 'instant' AND NOT additive)
    ),
    derived AS (
        SELECT company_id, metric, prev_end + 1 AS period_start, period_end, value - prev_value AS value,
               unit, 'derived' AS value_source, taxonomy || ':' || tag AS source_tag, accession_no,
               form, filed, ARRAY[prev_accession, accession_no] AS source_accessions,
               greatest(ingested_at, prev_ingested_at) AS ingested_at,
               greatest(priority, prev_priority) AS priority
        FROM year_to_date
        WHERE additive AND period_end - prev_end BETWEEN 80 AND 120
          -- a derived quarter of a metric that can't be negative came out negative: one of its
          -- two inputs is wrong (Cytek's 2024 10-K re-tagged 2022 Revenues as 2,963,000 against
          -- 164,036,000 everywhere else). Leave the quarter missing rather than publish it.
          AND NOT (value - prev_value < 0 AND metric IN (
              'revenue', 'cost_of_revenue', 'research_and_development', 'sga', 'capex',
              'depreciation_amortization', 'stock_based_compensation', 'share_buybacks',
              'dividends_paid', 'interest_expense'))  -- interest_expense since 043
    ),
    both_kinds AS (
        SELECT * FROM reported UNION ALL SELECT * FROM derived
    ),
    compared AS (
        SELECT b.*, max(b.value) FILTER (WHERE b.value_source = 'derived') OVER (
                   PARTITION BY b.company_id, b.metric, b.period_end, b.accession_no, b.source_tag
               ) AS difference
        FROM both_kinds b
    ),
    candidates AS (
        SELECT company_id, metric, period_start, period_end, value, unit, value_source, source_tag,
               accession_no, form, filed, source_accessions, ingested_at, priority
        FROM compared c
        -- a figure rounded for a sentence ("we paid $6.0 billion"), tagged in the same filing as an
        -- exact year to date: the difference is the exact quarter (NVIDIA's Q2 FY2027 dividends,
        -- 6,290 - 243 = 6,047, not 6,000; Cisco's "$1.5 billion" against 1,486; American Eagle's
        -- "$9.3 million" of stock compensation against 9.257). The reported figure has at most two
        -- significant digits, the difference at least three that aren't zero, and they're 0.2-2%
        -- apart. Tried on the sample and narrowed: three digits also took IBM's 5,260 of SG&A, which a
        -- filer in millions can state exactly while an earlier quarter was restated; and a difference
        -- like Lowe's 449 against 450 is that filer's own rounding of each year-to-date figure.
        WHERE NOT (c.value_source = 'reported' AND c.difference IS NOT NULL AND c.value <> 0
                   AND abs(c.value - c.difference) BETWEEN 0.002 * abs(c.difference)
                                                       AND 0.02 * abs(c.difference)
                   AND mod(c.value, power(10::numeric, floor(log(nullif(abs(c.value), 0))) - 1)) = 0
                   AND mod(c.difference, power(10::numeric, floor(log(nullif(abs(c.value), 0))) - 2)) <> 0)
    ),
    -- calendar votes per company and date: metrics with a balance-sheet value on it, and
    -- metrics with any fact ending on it
    votes AS (
        SELECT company_id, period_end AS day,
               count(DISTINCT metric) FILTER (WHERE period_type = 'instant') AS instants,
               count(DISTINCT metric) AS metrics
        FROM best
        GROUP BY company_id, period_end
    ),
    scored AS (  -- a quarter's votes: its last day's plus the day before its first day's
        SELECT c.*,
               coalesce(e.instants, 0) + coalesce(s.instants, 0) AS calendar_instants,
               coalesce(e.metrics, 0) + coalesce(s.metrics, 0) AS calendar_metrics
        FROM candidates c
        LEFT JOIN votes e ON e.company_id = c.company_id AND e.day = c.period_end
        LEFT JOIN votes s ON s.company_id = c.company_id AND s.day = c.period_start - 1
    ),
    per_end AS (  -- one row per period end
        SELECT DISTINCT ON (company_id, metric, period_end) *
        FROM scored
        ORDER BY company_id, metric, period_end, calendar_instants DESC, calendar_metrics DESC,
                 priority, value_source = 'reported' DESC, filed DESC, accession_no DESC,
                 source_accessions DESC, period_start DESC, source_tag, value,
                 ingested_at DESC
    ),
    ranked AS MATERIALIZED (  -- the same order across period ends: rnk 1 is the best row
        SELECT p.*, daterange(period_start, period_end, '[]') AS span,
               row_number() OVER (
                   PARTITION BY company_id, metric
                   ORDER BY calendar_instants DESC, calendar_metrics DESC, priority,
                            value_source = 'reported' DESC, filed DESC, accession_no DESC,
                            source_accessions DESC, period_start DESC, source_tag, value,
                            ingested_at DESC, period_end DESC) AS rnk
        FROM per_end p
    ),
    clashing AS (  -- duration rows overlapping another of their metric by more than 7 days
        SELECT DISTINCT a.company_id, a.metric, a.rnk, a.span
        FROM ranked a
        JOIN ranked b ON b.company_id = a.company_id AND b.metric = a.metric AND b.rnk <> a.rnk
        WHERE a.period_start IS NOT NULL AND b.period_start IS NOT NULL
          AND upper(a.span * b.span) - lower(a.span * b.span) > 7
    ),
    queue AS MATERIALIZED (
        SELECT c.*, row_number() OVER (PARTITION BY company_id, metric ORDER BY rnk) AS i
        FROM clashing c
    ),
    greedy AS (  -- best first: keep a row unless it overlaps one already kept by > 7 days
        SELECT company_id, metric, i, ARRAY[span] AS kept, ARRAY[rnk] AS kept_ranks
        FROM queue
        WHERE i = 1
        UNION ALL
        SELECT q.company_id, q.metric, q.i,
               CASE WHEN x.clash THEN g.kept ELSE g.kept || q.span END,
               CASE WHEN x.clash THEN g.kept_ranks ELSE g.kept_ranks || q.rnk END
        FROM greedy g
        JOIN queue q ON q.company_id = g.company_id AND q.metric = g.metric AND q.i = g.i + 1
        CROSS JOIN LATERAL (
            SELECT EXISTS (SELECT 1 FROM unnest(g.kept) k
                           WHERE upper(k * q.span) - lower(k * q.span) > 7) AS clash
        ) x
    ),
    dropped AS (
        SELECT q.company_id, q.metric, q.rnk
        FROM queue q
        JOIN (SELECT DISTINCT ON (company_id, metric) company_id, metric, kept_ranks
              FROM greedy
              ORDER BY company_id, metric, i DESC) g
          ON g.company_id = q.company_id AND g.metric = q.metric
        WHERE q.rnk <> ALL (g.kept_ranks)
    )
    INSERT INTO gold.quarterly_financials
        (company_id, metric, period_start, period_end, value, unit, value_source, source_tag,
         accession_no, form, filed, source_accessions, ingested_at)
    SELECT r.company_id, r.metric, r.period_start, r.period_end, r.value, r.unit,
           r.value_source, r.source_tag, r.accession_no, r.form, r.filed, r.source_accessions,
           r.ingested_at
    FROM ranked r
    LEFT JOIN dropped d
      ON d.company_id = r.company_id AND d.metric = r.metric AND d.rnk = r.rnk
    WHERE d.rnk IS NULL
    ORDER BY r.company_id, r.metric, r.period_end;

    GET DIAGNOSTICS n = ROW_COUNT;

    -- revenue less cost, where the filing prints no gross profit (header)
    WITH pairs AS (
        SELECT r.company_id, r.period_start, r.period_end, r.value AS revenue, c.value AS cost,
               o.value AS operating_income, s.value AS sga, d.value AS rnd, r.unit,
               (r.value - c.value) / r.value AS margin,
               CASE WHEN c.filed > r.filed THEN c.accession_no ELSE r.accession_no END AS accession_no,
               CASE WHEN c.filed > r.filed THEN c.form ELSE r.form END AS form,
               greatest(c.filed, r.filed) AS filed,
               ARRAY(SELECT DISTINCT a FROM unnest(r.source_accessions || c.source_accessions) a
                     ORDER BY 1) AS source_accessions,
               greatest(c.ingested_at, r.ingested_at) AS ingested_at
        FROM gold.quarterly_financials r
        JOIN silver.companies co ON co.company_id = r.company_id
        JOIN gold.quarterly_financials c
          ON c.company_id = r.company_id AND c.metric = 'cost_of_revenue'
         AND c.period_end = r.period_end AND abs(c.period_start - r.period_start) <= 7
        LEFT JOIN gold.quarterly_financials o
          ON o.company_id = r.company_id AND o.metric = 'operating_income' AND o.period_end = r.period_end
        LEFT JOIN gold.quarterly_financials s
          ON s.company_id = r.company_id AND s.metric = 'sga' AND s.period_end = r.period_end
        LEFT JOIN gold.quarterly_financials d
          ON d.company_id = r.company_id AND d.metric = 'research_and_development'
         AND d.period_end = r.period_end
        WHERE r.company_id = ANY(p_company_ids) AND r.metric = 'revenue' AND r.value > 0
          AND r.period_end >= DATE '2019-01-01'
          AND co.sic IS NOT NULL
          AND co.sic !~ '^(6[0-7]|13|48|58)' AND co.sic !~ '^(4911|493.|2911|5331|5912)$'
          AND c.value >= 0.15 * r.value
          AND (o.value IS NULL OR r.value - c.value >= o.value)
          AND NOT (o.value IS NOT NULL AND s.value IS NOT NULL
                   AND r.value - c.value - o.value - s.value - coalesce(d.value, 0) > 0.25 * r.value)
          AND NOT EXISTS (
              SELECT 1 FROM gold.quarterly_financials g
              WHERE g.company_id = r.company_id AND g.metric = 'gross_profit'
                AND g.period_start <= r.period_end - 7 AND g.period_end >= r.period_start + 7)
    ),
    usual AS (
        SELECT company_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY margin)::numeric AS margin
        FROM pairs GROUP BY company_id
    )
    INSERT INTO gold.quarterly_financials
        (company_id, metric, period_start, period_end, value, unit, value_source, source_tag,
         accession_no, form, filed, source_accessions, ingested_at)
    SELECT p.company_id, 'gross_profit', p.period_start, p.period_end, p.revenue - p.cost, p.unit,
           'derived', 'derived:revenue-cost_of_revenue', p.accession_no, p.form, p.filed,
           p.source_accessions, p.ingested_at
    FROM pairs p
    JOIN usual u USING (company_id)
    WHERE abs(p.margin - u.margin) <= 0.08
    ORDER BY p.company_id, p.period_end;

    RETURN n;
END
$$;

-- 084's gold.refresh_derived_financials, copied unchanged
CREATE OR REPLACE FUNCTION gold.refresh_derived_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('gold.quarterly_financials'), hashtext(id))
    FROM (SELECT DISTINCT id FROM unnest(p_company_ids) id ORDER BY id) ids;

    DELETE FROM gold.derived_financials WHERE company_id = ANY(p_company_ids);

    WITH quarters AS (
        SELECT g.*,
               coalesce(g.period_start - lag(g.period_end) OVER w BETWEEN 0 AND 7, false)
                   AS follows
        FROM gold.quarterly_financials g
        JOIN silver.metrics m ON m.metric = g.metric
        WHERE g.company_id = ANY(p_company_ids) AND m.kind = 'duration' AND m.additive
        WINDOW w AS (PARTITION BY g.company_id, g.metric ORDER BY g.period_end)
    ),
    windows AS (
        SELECT q.*,
               count(*) OVER w4 AS n_quarters,
               bool_and(follows) OVER w3 AS contiguous,
               first_value(period_start) OVER w4 AS ttm_start,
               sum(value) OVER w4 AS ttm_value,
               max(ingested_at) OVER w4 AS ttm_ingested_at,
               jsonb_agg(jsonb_build_object('period_start', period_start,
                                            'period_end', period_end, 'value', value,
                                            'accession_no', accession_no)) OVER w4 AS parts,
               source_accessions || lag(source_accessions, 1) OVER w
                   || lag(source_accessions, 2) OVER w || lag(source_accessions, 3) OVER w
                   AS accessions
        FROM quarters q
        WINDOW w AS (PARTITION BY company_id, metric ORDER BY period_end),
               w4 AS (w ROWS 3 PRECEDING),
               w3 AS (w ROWS 2 PRECEDING)
    )
    INSERT INTO gold.derived_financials
        (company_id, span, metric, period_start, period_end, value, unit, value_source,
         source_tag, inputs, accession_no, form, filed, source_accessions, ingested_at)
    SELECT company_id, 'ttm', metric, ttm_start, period_end, ttm_value, unit, 'ttm',
           source_tag, jsonb_build_object('quarters', parts), accession_no, form, filed,
           ARRAY(SELECT DISTINCT a FROM unnest(accessions) a ORDER BY 1), ttm_ingested_at
    FROM windows
    WHERE n_quarters = 4 AND contiguous AND period_end - ttm_start + 1 BETWEEN 350 AND 380;

    -- a year the 10-K states, where four quarters don't exist to sum (header). Not the quarters'
    -- gross_profit: a filing that printed one has it here, and revenue less cost is a quarter's rule.
    -- Ends within 7 days are one period (031's rule): ATI's FY2021 is tagged ending 2021-12-31 in one
    -- 10-K and 2022-01-02 in a later one, and was in gold twice.
    WITH years AS (
        SELECT y.*,
               EXISTS (SELECT 1 FROM gold.quarterly_financials q
                       WHERE q.company_id = y.company_id AND q.metric = y.metric
                         AND q.period_end = y.period_end) AS in_series
        FROM (
            -- one per period end: two annual facts can end on one day (a re-labelled start)
            SELECT DISTINCT ON (f.company_id, f.metric, f.period_end) f.*
            FROM gold.best_facts(p_company_ids) f
            JOIN silver.metrics m ON m.metric = f.metric AND m.kind = 'duration'
            WHERE f.period_type = 'annual' AND f.additive
            ORDER BY f.company_id, f.metric, f.period_end, f.priority, f.filed DESC,
                     f.accession_no DESC, f.period_start DESC
        ) y
        -- a year that can't hold the quarters gold has for it is another tag's part, not the year
        -- (Clover Health's 2022 revenue at 1,097 beside quarters of about 800 each; Peoples
        -- Bancorp's 2018 at -127): only for the metrics that can't be negative (029's list)
        WHERE NOT (y.metric IN ('revenue', 'cost_of_revenue', 'research_and_development', 'sga',
                                'capex', 'depreciation_amortization', 'stock_based_compensation',
                                'share_buybacks', 'dividends_paid')
                   AND (y.value < 0 OR y.value < 0.99 * (
                       SELECT coalesce(sum(q.value), 0) FROM gold.quarterly_financials q
                       WHERE q.company_id = y.company_id AND q.metric = y.metric
                         AND q.period_start >= y.period_start - 7
                         AND q.period_end <= y.period_end + 7)))
    )
    INSERT INTO gold.derived_financials
        (company_id, span, metric, period_start, period_end, value, unit, value_source,
         source_tag, inputs, accession_no, form, filed, source_accessions, ingested_at)
    SELECT b.company_id, 'ttm', b.metric, b.period_start, b.period_end, b.value, b.unit, 'ttm',
           b.taxonomy || ':' || b.tag,
           jsonb_build_object('quarters', jsonb_build_array(jsonb_build_object(
               'period_start', b.period_start, 'period_end', b.period_end, 'value', b.value,
               'accession_no', b.accession_no))),
           b.accession_no, b.form, b.filed, ARRAY[b.accession_no], b.ingested_at
    FROM (
        -- 063: 040's NOT EXISTS over `years` (a nested loop over every year of the call per year)
        -- as a window: the greatest (in_series, filed, period_end) among the years ending within 7
        -- days of this one, itself included, as text that sorts like the row (one year per end)
        SELECT k.*,
               max(k.rank_key) OVER (PARTITION BY k.company_id, k.metric ORDER BY k.period_end
                                     RANGE BETWEEN '7 days' PRECEDING AND '7 days' FOLLOWING)
                   AS best_key
        FROM (SELECT y.*, y.in_series::int::text || to_char(y.filed, 'YYYYMMDD')
                              || to_char(y.period_end, 'YYYYMMDD') AS rank_key
              FROM years y) k
    ) b
    WHERE NOT EXISTS (
        SELECT 1 FROM gold.derived_financials d
        WHERE d.company_id = b.company_id AND d.span = 'ttm' AND d.metric = b.metric
          AND d.period_end BETWEEN b.period_end - 7 AND b.period_end + 7)
      -- of two stated years ending within 7 days, one: the end gold's quarters use, then the
      -- later filing, then the later end
      AND b.rank_key = b.best_key;  -- 063

    -- 056: a computed amount (free cash flow, net debt, cash and investments) is in the currency of
    -- its inputs: the company's, which every monetary row of its gold shares (gold.usable_facts)
    WITH currency AS (
        SELECT DISTINCT ON (q.company_id) q.company_id, q.unit
        FROM gold.quarterly_financials q
        WHERE q.company_id = ANY(p_company_ids) AND q.unit ~ '^[A-Z]{3}$'
        ORDER BY q.company_id, q.period_end DESC
    ),
    facts AS (
        SELECT 'quarter' AS span, company_id, metric, period_start, period_end, value,
               accession_no, form, filed, source_accessions, ingested_at
        FROM gold.quarterly_financials
        WHERE company_id = ANY(p_company_ids)
        UNION ALL
        SELECT 'ttm', company_id, metric, period_start, period_end, value,
               accession_no, form, filed, source_accessions, ingested_at
        FROM gold.derived_financials
        WHERE company_id = ANY(p_company_ids)  -- the TTM sums just inserted
        UNION ALL
        SELECT 'ttm', g.company_id, g.metric, g.period_start, g.period_end, g.value,
               g.accession_no, g.form, g.filed, g.source_accessions, g.ingested_at
        FROM gold.quarterly_financials g
        JOIN silver.metrics m ON m.metric = g.metric
        WHERE g.company_id = ANY(p_company_ids) AND m.kind = 'instant'
    ),
    base AS (  -- the inputs of derived metrics
        SELECT * FROM facts
        WHERE metric IN ('revenue', 'gross_profit', 'operating_income', 'net_income',
                         'eps_diluted', 'operating_cash_flow', 'capex', 'dividends_paid',
                         'long_term_debt', 'long_term_debt_current', 'cash',
                         'short_term_investments', 'current_assets', 'current_liabilities',
                         'stockholders_equity', 'total_assets', 'receivables', 'inventory',
                         -- 048: the efficiency ratio's inputs
                         'net_interest_income', 'noninterest_income', 'noninterest_expense',
                         -- 084: gross margin on net sales
                         'net_sales', 'cost_of_revenue')
    ),
    year_ago AS (
        SELECT b.*,
               last_value(value) OVER ya AS ya_value,
               last_value(source_accessions) OVER ya AS ya_accessions
        FROM base b
        WINDOW ya AS (PARTITION BY span, company_id, metric ORDER BY period_end
                      RANGE BETWEEN '380 days' PRECEDING AND '350 days' PRECEDING)
    ),
    periods AS (  -- one row per (span, company, period end), with each input metric's value
        SELECT span, company_id, period_end,
               max(value) FILTER (WHERE metric = 'revenue') AS revenue,
               max(value) FILTER (WHERE metric = 'gross_profit') AS gross_profit,
               max(value) FILTER (WHERE metric = 'operating_income') AS operating_income,
               max(value) FILTER (WHERE metric = 'net_income') AS net_income,
               max(value) FILTER (WHERE metric = 'eps_diluted') AS eps_diluted,
               max(value) FILTER (WHERE metric = 'operating_cash_flow') AS operating_cash_flow,
               max(value) FILTER (WHERE metric = 'capex') AS capex,
               max(value) FILTER (WHERE metric = 'dividends_paid') AS dividends_paid,
               max(value) FILTER (WHERE metric = 'long_term_debt') AS long_term_debt,
               max(value) FILTER (WHERE metric = 'long_term_debt_current')
                   AS long_term_debt_current,
               max(value) FILTER (WHERE metric = 'cash') AS cash,
               max(value) FILTER (WHERE metric = 'short_term_investments')
                   AS short_term_investments,
               max(value) FILTER (WHERE metric = 'current_assets') AS current_assets,
               max(value) FILTER (WHERE metric = 'current_liabilities') AS current_liabilities,
               max(value) FILTER (WHERE metric = 'stockholders_equity') AS stockholders_equity,
               max(value) FILTER (WHERE metric = 'total_assets') AS total_assets,
               max(value) FILTER (WHERE metric = 'receivables') AS receivables,
               max(value) FILTER (WHERE metric = 'inventory') AS inventory,
               -- 048
               max(value) FILTER (WHERE metric = 'net_interest_income') AS net_interest_income,
               max(value) FILTER (WHERE metric = 'noninterest_income') AS noninterest_income,
               max(value) FILTER (WHERE metric = 'noninterest_expense') AS noninterest_expense,
               -- 084
               max(value) FILTER (WHERE metric = 'net_sales') AS net_sales,
               max(value) FILTER (WHERE metric = 'cost_of_revenue') AS cost_of_revenue,
               max(ya_value) FILTER (WHERE metric = 'revenue') AS revenue_year_ago,
               max(ya_value) FILTER (WHERE metric = 'net_income') AS net_income_year_ago,
               max(ya_value) FILTER (WHERE metric = 'eps_diluted') AS eps_diluted_year_ago,
               max(ya_value) FILTER (WHERE metric = 'stockholders_equity')
                   AS stockholders_equity_year_ago,
               max(ya_value) FILTER (WHERE metric = 'long_term_debt') AS long_term_debt_year_ago,
               jsonb_object_agg(metric, jsonb_build_object(
                   'period_start', period_start, 'accession_no', accession_no, 'form', form,
                   'filed', filed, 'accessions', source_accessions,
                   'ingested_at', ingested_at, 'year_ago_accessions', ya_accessions)) AS prov
        FROM year_ago
        GROUP BY span, company_id, period_end
    ),
    debt AS (  -- the net debt rule's reading of each balance sheet (header)
        SELECT p.*,
               coalesce(p.short_term_investments, 0) AS investments_now,
               p.current_assets - p.cash - coalesce(p.receivables, 0) - coalesce(p.inventory, 0)
                   AS room,
               x.value AS last_investments,
               x.period_end AS last_investments_end,
               t.value AS interest
        FROM periods p
        LEFT JOIN LATERAL (
            SELECT q.value, q.period_end
            FROM gold.quarterly_financials q
            -- 049: also where there is cash and no debt, for cash_and_investments (header);
            -- net_debt reads it only with debt, so it doesn't change
            WHERE p.short_term_investments IS NULL
              AND (p.long_term_debt IS NOT NULL OR p.cash IS NOT NULL)
              AND q.company_id = p.company_id AND q.metric = 'short_term_investments'
              AND q.period_end < p.period_end AND q.period_end >= p.period_end - 730 AND q.value > 0
            ORDER BY q.period_end DESC LIMIT 1
        ) x ON true
        LEFT JOIN LATERAL (
            SELECT d.value
            FROM gold.derived_financials d
            WHERE p.long_term_debt IS NOT NULL
              AND d.company_id = p.company_id AND d.span = 'ttm' AND d.metric = 'interest_expense'
              AND d.period_end = p.period_end
        ) t ON true
    ),
    derived AS (
        SELECT p.span, p.company_id, p.period_end, p.prov, d.metric,
               CASE WHEN dm.unit = 'ratio' THEN round(d.value, 6) ELSE d.value END AS value,
               dm.unit, jsonb_strip_nulls(d.inputs) AS inputs
        FROM debt p
        CROSS JOIN LATERAL (
            SELECT p.long_term_debt + coalesce(p.long_term_debt_current, 0) AS total,
                   p.long_term_debt + coalesce(p.long_term_debt_current, 0) > 0
                   AND p.interest > 2 * (p.long_term_debt + coalesce(p.long_term_debt_current, 0))
                   AND coalesce(p.long_term_debt_year_ago <= 2 * (p.long_term_debt
                                + coalesce(p.long_term_debt_current, 0)), true) AS sliver,
                   p.short_term_investments > p.room * 1.01 AS in_cash,
                   p.last_investments >= 0.01 * p.total_assets
                   AND p.last_investments >= 0.10 * abs(p.long_term_debt
                                                        + coalesce(p.long_term_debt_current, 0) - p.cash)
                   AND (p.room IS NULL OR p.last_investments <= p.room) AS unknown,
                   -- 049: investments missing here but reported lately: unknown, not zero
                   -- (unknown's test without the part about net debt's size)
                   p.last_investments >= 0.01 * p.total_assets
                   AND (p.room IS NULL OR p.last_investments <= p.room) AS investments_unknown
        ) r
        CROSS JOIN LATERAL (VALUES
            -- 084: on net sales where gold reads a company's net sales apart from its revenue
            -- (membership and other income: Walmart, Costco), for every period that has both
            ('gross_margin',
             CASE WHEN p.net_sales > 0 AND p.cost_of_revenue IS NOT NULL
                  THEN (p.net_sales - p.cost_of_revenue) / p.net_sales
                  WHEN p.revenue > 0 THEN p.gross_profit / p.revenue END,
             CASE WHEN p.net_sales > 0 AND p.cost_of_revenue IS NOT NULL
                  THEN jsonb_build_object('net_sales', p.net_sales,
                                          'cost_of_revenue', p.cost_of_revenue)
                  ELSE jsonb_build_object('gross_profit', p.gross_profit, 'revenue', p.revenue)
             END),
            ('operating_margin',
             CASE WHEN p.revenue > 0 THEN p.operating_income / p.revenue END,
             jsonb_build_object('operating_income', p.operating_income, 'revenue', p.revenue)),
            ('net_margin',
             CASE WHEN p.revenue > 0 THEN p.net_income / p.revenue END,
             jsonb_build_object('net_income', p.net_income, 'revenue', p.revenue)),
            ('free_cash_flow',
             p.operating_cash_flow - p.capex,
             jsonb_build_object('operating_cash_flow', p.operating_cash_flow,
                                'capex', p.capex)),
            ('fcf_margin',
             CASE WHEN p.revenue > 0 THEN (p.operating_cash_flow - p.capex) / p.revenue END,
             jsonb_build_object('operating_cash_flow', p.operating_cash_flow,
                                'capex', p.capex, 'revenue', p.revenue)),
            ('revenue_growth',
             CASE WHEN p.revenue_year_ago > 0 THEN p.revenue / p.revenue_year_ago - 1 END,
             jsonb_build_object('revenue', p.revenue,
                                'revenue_year_ago', p.revenue_year_ago)),
            ('net_income_growth',
             CASE WHEN p.net_income_year_ago > 0
                  THEN p.net_income / p.net_income_year_ago - 1 END,
             jsonb_build_object('net_income', p.net_income,
                                'net_income_year_ago', p.net_income_year_ago)),
            ('eps_growth',
             CASE WHEN p.eps_diluted_year_ago > 0
                  THEN p.eps_diluted / p.eps_diluted_year_ago - 1 END,
             jsonb_build_object('eps_diluted', p.eps_diluted,
                                'eps_diluted_year_ago', p.eps_diluted_year_ago)),
            ('payout_ratio',
             CASE WHEN p.net_income > 0 THEN p.dividends_paid / p.net_income END,
             jsonb_build_object('dividends_paid', p.dividends_paid,
                                'net_income', p.net_income)),
            ('debt_to_equity',
             CASE WHEN p.stockholders_equity > 0 AND r.sliver IS NOT TRUE
                  THEN r.total / p.stockholders_equity END,
             jsonb_build_object('long_term_debt', p.long_term_debt,
                                'long_term_debt_current', p.long_term_debt_current,
                                'stockholders_equity', p.stockholders_equity)),
            ('net_debt',
             CASE WHEN r.sliver IS NOT TRUE AND r.unknown IS NOT TRUE
                  THEN r.total - p.cash
                       - CASE WHEN r.in_cash THEN 0 ELSE p.investments_now END END,
             jsonb_build_object('long_term_debt', p.long_term_debt,
                                'long_term_debt_current', p.long_term_debt_current,
                                'cash', p.cash,
                                'short_term_investments',
                                CASE WHEN r.in_cash IS NOT TRUE THEN p.short_term_investments END)),
            ('current_ratio',
             CASE WHEN p.current_liabilities > 0
                  THEN p.current_assets / p.current_liabilities END,
             jsonb_build_object('current_assets', p.current_assets,
                                'current_liabilities', p.current_liabilities)),
            ('roe',
             CASE WHEN p.span = 'ttm'
                       AND p.stockholders_equity + p.stockholders_equity_year_ago > 0
                  THEN p.net_income
                       / ((p.stockholders_equity + p.stockholders_equity_year_ago) / 2) END,
             jsonb_build_object('net_income', p.net_income,
                                'stockholders_equity', p.stockholders_equity,
                                'stockholders_equity_year_ago',
                                p.stockholders_equity_year_ago)),
            -- 048: a bank's cost per dollar of revenue (header)
            ('efficiency_ratio',
             CASE WHEN p.net_interest_income + p.noninterest_income > 0
                  THEN p.noninterest_expense / (p.net_interest_income + p.noninterest_income) END,
             jsonb_build_object('noninterest_expense', p.noninterest_expense,
                                'net_interest_income', p.net_interest_income,
                                'noninterest_income', p.noninterest_income)),
            -- 049: cash and short-term investments, by net debt's reading of them (header)
            ('cash_and_investments',
             CASE WHEN r.investments_unknown IS NOT TRUE
                  THEN p.cash + CASE WHEN r.in_cash THEN 0 ELSE p.investments_now END END,
             jsonb_build_object('cash', p.cash,
                                'short_term_investments',
                                CASE WHEN r.in_cash IS NOT TRUE THEN p.short_term_investments END))
        ) d(metric, value, inputs)
        JOIN gold.derived_metrics dm ON dm.metric = d.metric
        WHERE d.value IS NOT NULL
    )
    INSERT INTO gold.derived_financials
        (company_id, span, metric, period_start, period_end, value, unit, value_source,
         inputs, accession_no, form, filed, source_accessions, ingested_at)
    SELECT d.company_id, d.span, d.metric, c.period_start, d.period_end, d.value,
           CASE WHEN d.unit = 'USD' THEN coalesce(cu.unit, 'USD') ELSE d.unit END,  -- 056
           'computed', d.inputs, c.accession_no, c.form, c.filed, s.accessions, c.ingested_at
    FROM derived d
    LEFT JOIN currency cu ON cu.company_id = d.company_id  -- 056
    -- provenance of the inputs at this period end: latest filing, and the earliest start
    -- (NULL when all are instants)
    CROSS JOIN LATERAL (
        SELECT (array_agg(j->>'accession_no' ORDER BY j->>'filed' DESC,
                          j->>'accession_no' DESC))[1] AS accession_no,
               (array_agg(j->>'form' ORDER BY j->>'filed' DESC,
                          j->>'accession_no' DESC))[1] AS form,
               max((j->>'filed')::date) AS filed,
               min((j->>'period_start')::date) AS period_start,
               max((j->>'ingested_at')::timestamptz) AS ingested_at
        FROM jsonb_object_keys(d.inputs) k
        CROSS JOIN LATERAL (SELECT d.prov->k) x(j)
        WHERE k NOT LIKE '%\_year\_ago'
    ) c
    -- every filing behind the inputs, year-ago ones included
    CROSS JOIN LATERAL (
        SELECT array_agg(DISTINCT a ORDER BY a) AS accessions
        FROM jsonb_object_keys(d.inputs) k
        CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE WHEN k LIKE '%\_year\_ago' THEN d.prov->left(k, -9)->'year_ago_accessions'
                 ELSE d.prov->k->'accessions' END) a
    ) s;

    SELECT count(*) INTO n FROM gold.derived_financials WHERE company_id = ANY(p_company_ids);
    RETURN n;
END
$$;

DROP FUNCTION gold.derived_rows(text[], date, gold.quarter_row[]);
DROP FUNCTION gold.quarterly_rows(text[], date);
DROP FUNCTION gold.best_facts(text[], date);
DROP FUNCTION gold.usable_facts(text[], date);
DROP TYPE gold.derived_row;
DROP TYPE gold.quarter_row;

-- 056's gold.usable_facts, copied unchanged
CREATE FUNCTION gold.usable_facts(p_company_ids text[])
RETURNS TABLE (company_id text, metric text, tag text, taxonomy text, unit text, period_start date,
               period_end date, period_type text, value numeric, accession_no text, form text,
               filed date, ingested_at timestamptz, cik text, priority numeric, additive boolean)
LANGUAGE sql STABLE AS $$
    WITH own AS (
        SELECT k.company_id, k.cik FROM silver.company_ciks k WHERE k.company_id = ANY(p_company_ids)
    ),
    -- 050: a filing's facts belong to the filer its XBRL names (header). lent: (filing, CIK) of
    -- these companies' facts that are another company's filing; borrowed: facts under another
    -- filer's CIK of a filing whose XBRL names one of these companies' CIKs, when none of the
    -- company's own CIKs has the filing's facts.
    lent AS MATERIALIZED (
        SELECT DISTINCT a.accession_no, a.cik
        FROM (SELECT DISTINCT f.accession_no, f.cik
              FROM own o JOIN silver.financial_facts f ON f.cik = o.cik) a
        JOIN bronze.filing_xbrl x ON x.accession_no = a.accession_no AND x.entity_cik <> a.cik
        JOIN silver.company_ciks e ON e.cik = x.entity_cik
        JOIN own o ON o.cik = a.cik
        WHERE e.company_id <> o.company_id
          AND gold.filed_by(a.accession_no, x.entity_cik)
    ),
    borrowed AS MATERIALIZED (
        SELECT DISTINCT o.company_id, f.accession_no, f.cik
        FROM own o
        JOIN bronze.filing_xbrl x ON x.entity_cik = o.cik
        JOIN silver.financial_facts f ON f.accession_no = x.accession_no AND f.cik <> x.entity_cik
        WHERE NOT EXISTS (SELECT 1 FROM silver.company_ciks k
                          WHERE k.cik = f.cik AND k.company_id = o.company_id)
          AND NOT EXISTS (SELECT 1 FROM own m
                          JOIN silver.financial_facts g
                            ON g.cik = m.cik AND g.accession_no = x.accession_no
                          WHERE m.company_id = o.company_id)
          AND gold.filed_by(x.accession_no, x.entity_cik)
    ),
    facts AS (
        SELECT o.company_id, f.*
        FROM own o
        JOIN silver.financial_facts f ON f.cik = o.cik
        WHERE NOT EXISTS (SELECT 1 FROM lent l
                          WHERE l.accession_no = f.accession_no AND l.cik = f.cik)
        UNION ALL
        SELECT b.company_id, f.*
        FROM borrowed b
        JOIN silver.financial_facts f ON f.accession_no = b.accession_no AND f.cik = b.cik
    ),
    -- 056: each company's reporting currency: the currency most of the monetary facts of its latest
    -- annual or quarterly report are in (a 20-F's convenience translation of one year into USD is
    -- the minority). One series never mixes currencies.
    report AS (
        SELECT DISTINCT ON (f.company_id) f.company_id, f.accession_no
        FROM facts f
        WHERE f.unit ~ '^[A-Z]{3}$' AND coalesce(f.form, '') ~ '^(10-K|10-Q|20-F|40-F|6-K)'
        ORDER BY f.company_id, f.filed DESC, f.accession_no DESC
    ),
    currency AS (
        SELECT DISTINCT ON (c.company_id) c.company_id, c.unit
        FROM (
            SELECT f.company_id, f.unit, count(*) AS n
            FROM facts f
            JOIN report r ON r.company_id = f.company_id AND r.accession_no = f.accession_no
            WHERE f.unit ~ '^[A-Z]{3}$'
            GROUP BY f.company_id, f.unit
        ) c
        ORDER BY c.company_id, c.n DESC, c.unit = 'USD' DESC, c.unit
    ),
    tagged AS (
        -- by tag, not by silver's metric column: its identity index has no metric, so a tag two metrics
        -- map (EarningsPerShareBasicAndDiluted) is stored once, under one of them
        SELECT f.company_id, t.metric, f.tag, t.taxonomy, f.unit, f.period_start, f.period_end,
               f.period_type,
               -- 043: interest expense is a cost, its sign in a filing is presentation (header);
               -- 050: so is a debt balance's, which can't be below zero
               CASE WHEN t.metric IN ('interest_expense', 'long_term_debt', 'long_term_debt_current')
                    THEN abs(f.value) ELSE f.value END AS value,
               f.accession_no, f.form, f.filed, f.ingested_at, f.cik,
               t.priority::numeric AS priority, m.additive, t.gold_line
        FROM facts f
        -- 055: and by taxonomy (an ifrs-full tag can share a us-gaap tag's name), and a company's own
        -- tags (an extension, a sum of members) only for that company
        JOIN silver.metric_tags t ON t.tag = f.tag AND t.taxonomy = f.taxonomy
                                 AND (t.company_id IS NULL OR t.company_id = f.company_id)
        -- 056: a currency for a monetary metric (silver.unit_fits), the company's own (header)
        JOIN silver.metrics m ON m.metric = t.metric AND silver.unit_fits(m.unit, f.unit)
        LEFT JOIN currency cu ON cu.company_id = f.company_id
        WHERE (m.unit NOT IN ('USD', 'USD/shares')
               OR f.unit = coalesce(cu.unit, 'USD')
                           || CASE WHEN m.unit = 'USD/shares' THEN '/shares' ELSE '' END)
          -- proxy and information statements (DEF 14A, DEFA14A, DEFR14A, DEFM14A,
          -- DEF 14C, PRE 14A, ...): pay-versus-performance net income, rounded
          AND coalesce(f.form, '') !~ '^(DEF|PRE)'
          AND f.period_end <= f.filed + 7
    ),
    summed AS (
        SELECT x.company_id, s.metric, array_to_string(s.tags, '+') AS tag, x.taxonomy, x.unit,
               x.period_start, x.period_end, x.period_type, sum(x.value) AS value, x.accession_no,
               x.form, x.filed, max(x.ingested_at) AS ingested_at, min(x.cik) AS cik, s.priority,
               x.additive, true AS gold_line
        FROM silver.metric_sums s
        JOIN tagged x ON x.metric = s.metric AND x.tag = ANY(s.tags)
        GROUP BY x.company_id, s.metric, s.tags, x.taxonomy, x.unit, x.period_start, x.period_end,
                 x.period_type, x.accession_no, x.form, x.filed, s.priority, x.additive
        -- every part present, and none a rounding of the other: State Street's 2019 10-Qs tag
        -- NoninterestIncome as -1 and 0 (it's a securities gain), beside 613 of interest income
        HAVING count(DISTINCT x.tag) = cardinality(s.tags)
           AND min(abs(x.value)) >= 0.01 * max(abs(x.value))
    ),
    loss_eps AS (  -- a loss quarter's diluted EPS is its basic EPS; ranks after the two diluted tags
        SELECT x.company_id, 'eps_diluted' AS metric, x.tag, x.taxonomy, x.unit, x.period_start,
               x.period_end, x.period_type, x.value, x.accession_no, x.form, x.filed,
               x.ingested_at, x.cik, 3::numeric AS priority, x.additive, true AS gold_line
        FROM tagged x
        WHERE x.metric = 'eps_basic' AND x.tag = 'EarningsPerShareBasic' AND x.value < 0
    ),
    lines AS (
        SELECT * FROM tagged WHERE gold_line
        UNION ALL SELECT * FROM summed
        UNION ALL SELECT * FROM loss_eps
    ),
    -- 050: debt parts adding up to 1,000 or 1,000,000 times the same filing's total for the date
    -- are a scale slip in the parts (Bank First's 2019 10-K: 1,400,000,000 + 38,400,000,000 beside a
    -- LongTermDebt of 39,800,000)
    slipped AS (
        SELECT p.company_id, p.accession_no, p.period_end
        FROM (
            SELECT x.company_id, x.accession_no, x.period_end,
                   sum(x.value) FILTER (WHERE x.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent'))
                       AS parts,
                   max(x.value) FILTER (WHERE x.tag = 'LongTermDebt') AS total
            FROM tagged x
            WHERE x.period_start IS NULL
              AND x.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent', 'LongTermDebt')
            GROUP BY x.company_id, x.accession_no, x.period_end
        ) p
        WHERE p.total > 0
          AND (abs(p.parts / p.total - 1000) <= 5 OR abs(p.parts / p.total - 1000000) <= 5000)
    ),
    balance AS (  -- what the debt tests read at each date: each tag's latest filing
        SELECT y.company_id, y.period_end,
               max(y.value) FILTER (WHERE y.tag = 'Liabilities') AS liabilities,
               max(y.value) FILTER (WHERE y.tag = 'LiabilitiesCurrent') AS liabilities_current,
               max(y.value) FILTER (WHERE y.tag = 'Assets') AS assets,
               max(y.value) FILTER (WHERE y.metric = 'long_term_debt_current'
                                      AND y.rnk = 1) AS current_debt
        FROM (
            SELECT DISTINCT ON (x.company_id, x.tag, x.period_end) x.*,
                   dense_rank() OVER (PARTITION BY x.company_id, x.metric, x.period_end
                                      ORDER BY x.priority) AS rnk
            FROM tagged x
            WHERE x.period_start IS NULL
              AND (x.tag IN ('Liabilities', 'LiabilitiesCurrent', 'Assets')
                   OR x.metric = 'long_term_debt_current')
              AND NOT (x.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent')  -- 050
                       AND EXISTS (SELECT 1 FROM slipped s
                                   WHERE s.company_id = x.company_id
                                     AND s.accession_no = x.accession_no
                                     AND s.period_end = x.period_end))
            ORDER BY x.company_id, x.tag, x.period_end, x.filed DESC, x.accession_no DESC
        ) y
        GROUP BY y.company_id, y.period_end
    )
    SELECT l.company_id, l.metric,
           CASE WHEN c.total THEN l.tag || '-' || 'CurrentPortion' ELSE l.tag END, l.taxonomy, l.unit,
           l.period_start, l.period_end, l.period_type,
           CASE WHEN c.total THEN l.value - b.current_debt ELSE l.value END,
           l.accession_no, l.form, l.filed, l.ingested_at, l.cik, l.priority, l.additive
    FROM lines l
    LEFT JOIN balance b ON (l.metric IN ('long_term_debt', 'long_term_debt_current') OR l.tag = 'Cash')
                       AND b.company_id = l.company_id AND b.period_end = l.period_end
    LEFT JOIN silver.companies co ON l.tag IN ('LongTermDebt', 'LongTermNotesPayable',
                                               'LongTermNotesAndLoans')
                                 AND co.company_id = l.company_id
    CROSS JOIN LATERAL (
        SELECT l.tag IN ('LongTermDebt', 'LongTermNotesPayable', 'LongTermNotesAndLoans')
               AND b.current_debt > 0
               -- a total holds its current portion: equal to it (all of the debt is current), or
               -- above it and above all noncurrent liabilities; below it, it's the noncurrent line
               AND (l.value = b.current_debt
                    OR (l.value > b.current_debt
                        AND l.value > (b.liabilities - b.liabilities_current) * 1.01)) AS total
    ) c
    WHERE NOT (l.metric IN ('long_term_debt', 'long_term_debt_current')
               AND l.value > coalesce(b.liabilities * 5, 'Infinity')
               AND l.value > coalesce(b.assets * 5, 'Infinity'))
      -- Cash only on a balance sheet's date: elsewhere it's a note (cash paid in a deal, Boston
      -- Scientific on 2023-02-20)
      AND NOT (l.tag = 'Cash' AND b.assets IS NULL)
      -- a negative "investment" under the tags 038 added is a mis-signed note (HCA's 2012 10-Q)
      AND NOT (l.metric = 'short_term_investments' AND l.priority > 4 AND l.value < 0)
      AND NOT (l.tag IN ('LongTermDebt', 'LongTermNotesPayable', 'LongTermNotesAndLoans')
               AND (co.sic IS NULL OR 'net_debt' = ANY(gold.not_applicable_metrics(l.company_id, co.sic))))
      -- 050: debt parts that are a scale slip of the filing's own total
      AND NOT (l.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent') AND l.period_start IS NULL
               AND EXISTS (SELECT 1 FROM slipped s
                           WHERE s.company_id = l.company_id AND s.accession_no = l.accession_no
                             AND s.period_end = l.period_end))
$$;

-- 080's gold.best_facts, copied unchanged
CREATE FUNCTION gold.best_facts(p_company_ids text[])
RETURNS TABLE (company_id text, metric text, tag text, taxonomy text, unit text, period_start date,
               period_end date, period_type text, value numeric, accession_no text, form text,
               filed date, ingested_at timestamptz, cik text, priority numeric, additive boolean)
LANGUAGE sql STABLE AS $$
    WITH tag_latest AS (
        SELECT DISTINCT ON (u.company_id, u.metric, u.tag, u.period_start, u.period_end) u.*,
               -- 077: when the tag first gave this period (a later filing's comparative or not)
               min(u.filed) OVER (PARTITION BY u.company_id, u.metric, u.tag, u.period_start,
                                               u.period_end) AS first_filed
        FROM gold.usable_facts(p_company_ids) u
        ORDER BY u.company_id, u.metric, u.tag, u.period_start, u.period_end, u.filed DESC,
                 u.accession_no DESC, u.cik
    ),
    -- 077: a year whose net income the company's own filings of the year tag ProfitLoss only (the
    -- annual figure ProfitLoss, never NetIncomeLoss; a year: 350-380 days), NetIncomeLoss for its
    -- periods coming only from later filings' comparatives (first filed 100+ days after the year
    -- ended). Inside it ProfitLoss reads first, so the year's quarters, its year-to-date figures and
    -- the fiscal Q4 derived from them are one line. PNC's 2025: ProfitLoss in every filing of the
    -- year, NetIncomeLoss only in two 2026 10-Qs' comparatives (Q2, Q3, six and nine months): gold
    -- read Q1 ProfitLoss, Q2-Q3 NetIncomeLoss and a Q4 of the year's ProfitLoss less nine months of
    -- NetIncomeLoss.
    profit_loss_years AS MATERIALIZED (
        SELECT y.company_id, y.period_start, y.period_end
        FROM (SELECT t.company_id, t.period_start, t.period_end
              FROM tag_latest t
              WHERE t.metric = 'net_income' AND t.taxonomy = 'us-gaap'
                AND t.tag IN ('NetIncomeLoss', 'ProfitLoss')
                AND t.period_end - t.period_start BETWEEN 350 AND 380
              GROUP BY t.company_id, t.period_start, t.period_end
              HAVING NOT bool_or(t.tag = 'NetIncomeLoss')) y
        WHERE NOT EXISTS (SELECT 1 FROM tag_latest n
                          WHERE n.company_id = y.company_id AND n.metric = 'net_income'
                            AND n.taxonomy = 'us-gaap' AND n.tag = 'NetIncomeLoss'
                            AND n.period_start >= y.period_start
                            AND n.period_end <= y.period_end
                            AND n.first_filed <= y.period_end + 100)
    ),
    ranked AS (  -- 077: the order tags are believed in; priority, but for that rule
        SELECT t.*,
               CASE WHEN t.metric = 'net_income' AND t.taxonomy = 'us-gaap' AND t.tag = 'ProfitLoss'
                         AND EXISTS (SELECT 1 FROM profit_loss_years y
                                     WHERE y.company_id = t.company_id
                                       AND t.period_start >= y.period_start
                                       AND t.period_end <= y.period_end)
                    THEN 0 ELSE t.priority END AS rank,
               -- 080: an industry total, read only where no other revenue tag gives the period
               (t.metric = 'revenue' AND t.taxonomy = 'us-gaap'
                AND t.tag IN ('RealEstateRevenueNet', 'SalesRevenueGoodsNet',
                              'SalesRevenueServicesNet')) AS fallback
        FROM tag_latest t
    ),
    -- 064: the period's positive revenue values, and the largest of its other money figures (any
    -- other metric with the same start and end), for the scale test below
    scales AS (
        SELECT t.*,
               array_agg(t.value) FILTER (WHERE t.metric = 'revenue' AND t.value > 0) OVER wm
                   AS revenue_values,
               max(abs(t.value)) FILTER (WHERE t.metric <> 'revenue' AND t.unit ~ '^[A-Z]{3}$')
                   OVER wp AS other_max
        FROM ranked t  -- 077
        WINDOW wp AS (PARTITION BY t.company_id, t.period_start, t.period_end),
               wm AS (PARTITION BY t.company_id, t.period_start, t.period_end, t.metric)
    ),
    -- 064: two revenue tags of a period 1,000 or 1,000,000 times apart (within 0.5%) are one figure
    -- in two scales. The larger is the slip when it is over 100 times every other money figure of
    -- the period, else the smaller is (also when the period has no other figure). A slip is no
    -- evidence for revenue's rules below and sorts after every other tag.
    slips AS (
        SELECT s.*,
               CASE WHEN s.metric = 'revenue' AND s.value > 0 THEN EXISTS (
                   SELECT 1 FROM unnest(s.revenue_values) v
                   WHERE ((s.value BETWEEN v * 995 AND v * 1005
                           OR s.value BETWEEN v * 995000 AND v * 1005000)
                          AND s.value > 100 * s.other_max)
                      OR ((v BETWEEN s.value * 995 AND s.value * 1005
                           OR v BETWEEN s.value * 995000 AND s.value * 1005000)
                          AND (s.other_max IS NULL OR v <= 100 * s.other_max)))
               ELSE false END AS slipped
        FROM scales s
    ),
    weighed AS (  -- revenue's rules (039's and 064's headers) read the period's other tags
        SELECT t.*,
               -- 064: a slip counts for none of these
               max(t.filed) FILTER (WHERE t.value >= 1000000 AND NOT t.slipped) OVER w AS large_filed,
               max(t.filed) FILTER (WHERE t.value > 0 AND t.value < 1000000 AND NOT t.slipped) OVER w
                   AS small_filed,
               max(t.value) FILTER (WHERE NOT t.slipped AND t.tag IN (
                   'RevenueFromContractWithCustomerExcludingAssessedTax',
                   'RevenueFromContractWithCustomerIncludingAssessedTax')) OVER w AS contract_value,
               -- 064: the gross line a net revenue figure is compared with
               max(t.value) FILTER (WHERE NOT t.slipped AND t.tag IN ('Revenues',
                   'RevenueFromContractWithCustomerExcludingAssessedTax',
                   'RevenueFromContractWithCustomerIncludingAssessedTax')) OVER w AS gross_value,
               -- 080: the period's industry totals (the first and last of their tags) and the
               -- largest of its other revenue values; positive, neither a slip
               min(t.tag) FILTER (WHERE t.fallback AND t.value > 0) OVER w AS fallback_tag,
               max(t.tag) FILTER (WHERE t.fallback AND t.value > 0) OVER w AS fallback_tag_last,
               max(t.value) FILTER (WHERE t.fallback AND t.value > 0 AND NOT t.slipped) OVER w
                   AS fallback_value,
               max(t.value) FILTER (WHERE t.metric = 'revenue' AND NOT t.fallback AND t.value > 0
                                      AND NOT t.slipped) OVER w AS trusted_value
        FROM slips t  -- 064
        WINDOW w AS (PARTITION BY t.company_id, t.metric, t.period_start, t.period_end)
    ),
    -- 080: an industry total the company's own filings give as a part: under 80% of another
    -- revenue tag's value for the same period, in any filing (Ultragenyx's product sales beside
    -- its total, Tiptree's real estate line beside an insurer's revenues). Never read there; nor
    -- where silver.industry_total_exclusions says so (sales before excise taxes, a predecessor).
    fallback_parts AS MATERIALIZED (
        SELECT x.company_id, x.tag
        FROM weighed x
        WHERE x.fallback AND x.value < 0.8 * x.trusted_value
        UNION
        SELECT e.company_id, e.tag
        FROM silver.industry_total_exclusions e
        WHERE e.company_id = ANY(p_company_ids)
    ),
    judged AS (
        SELECT x.*,
               -- 080: the period's one industry total, at a company that never gives it as a part
               -- (two of them in one period are parts of a total no tag gives: neither is read)
               CASE WHEN x.fallback_tag IS NULL OR x.fallback_tag <> x.fallback_tag_last THEN false
                    ELSE NOT EXISTS (SELECT 1 FROM fallback_parts p
                                     WHERE p.company_id = x.company_id
                                       AND p.tag = x.fallback_tag) END AS fallback_readable
        FROM weighed x
    )
    SELECT DISTINCT ON (x.company_id, x.metric, x.period_start, x.period_end)
           x.company_id, x.metric, x.tag, x.taxonomy, x.unit, x.period_start, x.period_end,
           x.period_type, x.value, x.accession_no, x.form, x.filed, x.ingested_at, x.cik, x.priority,
           x.additive
    FROM judged x  -- 080
    WHERE NOT (x.fallback AND (x.value <= 0 OR NOT x.fallback_readable))  -- 080
    ORDER BY x.company_id, x.metric, x.period_start, x.period_end,
             x.slipped,  -- 064: a scale slip after every other tag
             (x.metric = 'revenue' AND x.period_start IS NOT NULL
              AND ((x.value = 0 AND (x.large_filed >= x.filed OR x.small_filed > x.filed))
                   OR (x.tag = 'Revenues' AND x.value > 0 AND x.value < 0.2 * x.contract_value)
                   -- 064: net revenue under 20% of the gross line is a part too (Rectitude 0.2%)
                   OR (x.tag = 'RevenuesNetOfInterestExpense' AND x.value > 0
                       AND x.value < 0.2 * x.gross_value)
                   -- 080: any other revenue tag under 20% of the period's industry total is a part
                   -- (Conagra's Revenues of $1.6-1.7B beside $13B of net sales, FY2012-2016)
                   OR (NOT x.fallback AND x.fallback_readable AND x.value > 0
                       AND x.value < 0.2 * x.fallback_value)))
                 IS TRUE,
             x.rank, x.filed DESC, x.accession_no DESC, x.cik  -- 077: rank, not priority
$$;

COMMIT;
