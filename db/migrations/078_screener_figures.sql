-- The screener's latest figures, kept by the gold build (Riley, test 6, #13/#67/#125; db/DESIGN.md
-- "078"). A screen sorted or filtered on a value column reads, for every company, its latest period
-- (three probes of gold.quarterly_financials), whether it has a value in any column (hide_blank: two or
-- three probes), and the column's latest reading (gold.latest_reading: two probes into 12 GB of gold
-- plus the currency and equity probes), so the first request after the pages leave memory reads tens
-- of thousands of pages from disk. Measured on hermes (2026-11-17, read-only, the owner login, no RLS):
-- the default screen 3.7 s on its first run, 0.67 s the second; sorted on operating margin 1.85 s /
-- 0.73 s; two filters and a growth sort 4.25 s / 3.42 s (64,429 pages read); debt/equity 2.15 s / 0.65 s.
--
-- 1. The readings family, split so a stored figure is read by the very rules gold.latest_reading uses
--    (one definition):
--      gold.latest_row(company, span, metric)   the latest figure (035's `x`: a quarterly row, also for
--                                               a balance's TTM, or a derived row, latest period first)
--      gold.latest_equity(company)              the latest stockholders' equity to date (035's `e`)
--      gold.quarterly_recent(company, latest)   quarterly revenue or net income within 200 days before
--                                               the latest period (069's annual-filer test)
--      gold.is_stale(latest, form, recent)      069's stale rule: 270 days past the latest period, 500
--                                               for an annual filer (20-F/40-F/6-K form, or no recent
--                                               quarters); "today" is tributary.today or current_date
--      gold.reading(...)                        status and reason from those inputs (069's order:
--                                               not_applicable, other_currency, not_computed,
--                                               not_meaningful, stale, old/behind)
--    gold.latest_reading keeps its signature, result and plan: the same probes (its `e` and `cu` laterals
--    unchanged, gated on the metric), the stale probe still only when the answer depends on it, the
--    rules through gold.reading. All plain SQL, STABLE, INVOKER, no SET: inlined
--    (test_latest_reading_stays_inlinable).
-- 2. gold.screen_companies (per company: latest period, latest equity, the amounts' currency, the recent
--    quarters flag, which metrics have rows) and gold.screen_figures (per company, span and metric:
--    gold.latest_row's figure). Only data: every reading is computed when read, with the reader's SIC,
--    today's date and RLS. What depends on the date is never stored: `until` is the first future period
--    end of the date-filtered probes (revenue, net income, total assets, equity: none on hermes today);
--    from that day the company is read live until refreshed. RLS: 030's financials policy on both; no
--    FK to silver.companies (fixtures TRUNCATE it).
--    screen_figures' primary key INCLUDEs every column a screen reads: a sort over one column reads its
--    ~6,600 index entries in order (an index-only scan, a few hundred pages), however the heap is laid
--    out; autovacuum runs at 1% so the visibility map keeps up.
-- 3. gold.refresh_screen(ids) rewrites both for these companies (a company with no gold gets an empty
--    row). gold.refresh_financials (071's body) calls it after the derived metrics, under the same
--    company locks, in the same transaction: a screen never reads figures older than gold's.
--    gold.refresh_screen_backlog(n) does n companies with no row (or past `until`) under
--    gold.lock_companies: the hourly catch-up (facts_fetcher/catchup.py) calls it first, so the tables
--    fill within the first hour after this deploy (hermes: ~10,000 companies; about 0.5 s per 100).
--    The tables start empty ON PURPOSE: a gold build of the previous body still running when this
--    commits writes no row, so nothing stored can be older than gold; a company without a row is
--    read live (below).
-- 4. The screener reads gold.screen_company(company) (latest period and the stored inputs, or live:
--    gold.latest_period when the company has no row or is past `until`) and gold.screen_reading(...)
--    (the stored figure through gold.reading, or gold.latest_reading live), and hide_blank tests the
--    stored metric lists (live probes without a row). Results equal the live path's: test_screen_db.py
--    compares every column of every fixture company at several dates.
-- 5. gold.financials_coverage (017) reads the stored lists first: a company with a row is covered when
--    it has quarterly rows; without a row, the probe as before. Still owner rights, company ids only.
--
-- A change to gold.latest_row, gold.latest_equity, gold.quarterly_recent, gold.amount_currency_of or
-- silver.metrics' kinds changes what is stored: that migration empties both tables (TRUNCATE; the
-- catch-up refills them within the hour, reads stay live meanwhile). Readings rules (gold.reading and
-- what it calls) are read time: no refill.
--
-- Locks on hermes: new tables and functions; CREATE OR REPLACE of gold.latest_reading,
-- gold.refresh_financials (catalog only; a running build keeps the body it started with) and the view
-- gold.financials_coverage (a moment's ACCESS EXCLUSIVE on the view; lock_timeout 3 s: re-run the hook if
-- a reader held it). No table is scanned or rewritten. Backout: db/backout/screener_figures_078.sql
-- (heads the chain).

SET LOCAL lock_timeout = '3s';

-- 1. The readings family's parts ------------------------------------------------------------------

-- 035's `x` (056's unit), as latest_reading has read it since 069.
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

-- 035's `e` without its metric gate (latest_reading keeps the gate: only debt/equity and ROE read it).
CREATE FUNCTION gold.latest_equity(p_company_id text)
RETURNS TABLE (value numeric)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT q.value FROM gold.quarterly_financials q
    WHERE q.company_id = p_company_id AND q.metric = 'stockholders_equity'
      AND q.period_end <= current_date
    ORDER BY q.period_end DESC LIMIT 1
$$;

-- 069's annual-filer test: quarterly revenue or net income within 200 days before the latest period.
CREATE FUNCTION gold.quarterly_recent(p_company_id text, p_latest date)
RETURNS TABLE (recent boolean)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT EXISTS (SELECT 1 FROM gold.quarterly_financials q
                   WHERE q.company_id = p_company_id
                     AND q.metric IN ('revenue', 'net_income')
                     AND q.period_end > p_latest - 200
                     AND q.period_end <= p_latest)
$$;

-- 069's stale rule (header, 1). p_recent matters only between 270 and 500 days for a quarterly form:
-- gold.latest_reading probes it only then (is_stale with true differs from is_stale with false).
CREATE FUNCTION gold.is_stale(p_latest date, p_form text, p_recent boolean) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN p_latest IS NULL
             OR coalesce(nullif(current_setting('tributary.today', true), '')::date, current_date)
                - p_latest <= 270 THEN false
        WHEN coalesce(nullif(current_setting('tributary.today', true), '')::date, current_date)
             - p_latest > 500 THEN true
        WHEN p_form ~ '^(20-F|40-F|6-K)' THEN false
        ELSE p_recent END
$$;

-- How a figure reads, from its inputs: 069's two CASEs (status, reason), unchanged in order and words.
-- p_equity is the company's latest equity, p_currency its amounts' currency (NULL: dollars), p_recent
-- gold.quarterly_recent's flag (NULL when it can't matter).
CREATE FUNCTION gold.reading(p_company_id text, p_metric text, p_sic text, p_latest date,
                             p_period_end date, p_value numeric, p_inputs jsonb, p_unit text,
                             p_form text, p_equity numeric, p_currency text, p_recent boolean)
RETURNS TABLE (status text, reason text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'not_applicable'
                -- 056: an amount in another currency is never compared with dollars
                WHEN p_unit ~ '^[A-Z]{3}(/shares)?$' AND p_unit !~ '^USD'
                    THEN 'other_currency'
                -- 069: no figure (or only an old one) because Tributary computes none here
                WHEN (p_value IS NULL OR gold.period_status(p_period_end, p_latest) = 'old')
                     AND p_latest IS NOT NULL AND gold.not_computed(p_metric, p_sic) IS NOT NULL
                    THEN 'not_computed'
                WHEN gold.not_meaningful(p_metric, p_value, p_inputs, p_equity,
                                         coalesce(p_currency, 'USD')) IS NOT NULL
                    THEN 'not_meaningful'
                -- 069: the company's latest period is long past; an old value stays old
                WHEN gold.is_stale(p_latest, p_form, p_recent) AND p_value IS NOT NULL
                     AND gold.period_status(p_period_end, p_latest) IS DISTINCT FROM 'old'
                    THEN 'stale'
                ELSE gold.period_status(p_period_end, p_latest) END,
           CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'this is a bank: deposits are its debt and lending its operations, so '
                         'the figure has no comparable meaning'
                WHEN p_unit ~ '^[A-Z]{3}(/shares)?$' AND p_unit !~ '^USD'
                    THEN 'reported in ' || split_part(p_unit, '/', 1)
                         || ', so it isn''t sorted, filtered or averaged with US-dollar figures'
                WHEN (p_value IS NULL OR gold.period_status(p_period_end, p_latest) = 'old')
                     AND p_latest IS NOT NULL AND gold.not_computed(p_metric, p_sic) IS NOT NULL
                    THEN gold.not_computed(p_metric, p_sic)
                WHEN gold.not_meaningful(p_metric, p_value, p_inputs, p_equity,
                                         coalesce(p_currency, 'USD')) IS NOT NULL
                    THEN gold.not_meaningful(p_metric, p_value, p_inputs, p_equity,
                                             coalesce(p_currency, 'USD'))
                WHEN gold.is_stale(p_latest, p_form, p_recent) AND p_value IS NOT NULL
                     AND gold.period_status(p_period_end, p_latest) IS DISTINCT FROM 'old'
                    THEN 'this company''s latest figures here are for the period ended '
                         || to_char(p_latest, 'FMMon FMDD, YYYY') || ', and no report since has '
                         'reached Tributary, so they aren''t compared with current figures' END
$$;

-- 069's body with its parts above (078): the same probes, gates and result.
CREATE OR REPLACE FUNCTION gold.latest_reading(p_company_id text, p_span text, p_metric text, p_sic text,
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
    -- 069: the recent-quarters probe, only when the stale answer depends on it
    LEFT JOIN LATERAL (
        SELECT qr.recent FROM gold.quarterly_recent(p_company_id, p_latest) qr
        WHERE gold.is_stale(p_latest, x.form, true) AND NOT gold.is_stale(p_latest, x.form, false)
        LIMIT 1
    ) st ON true
    CROSS JOIN LATERAL gold.reading(p_company_id, p_metric, p_sic, p_latest, x.period_end, x.value,
                                    x.inputs, x.unit, x.form, e.value, cu.unit, st.recent) r
$$;

GRANT EXECUTE ON FUNCTION gold.latest_row(text, text, text), gold.latest_equity(text),
    gold.quarterly_recent(text, date), gold.is_stale(date, text, boolean),
    gold.reading(text, text, text, date, date, numeric, jsonb, text, text, numeric, text, boolean)
    TO query_api;

-- 2. The stored inputs ----------------------------------------------------------------------------

CREATE TABLE gold.screen_companies (
    company_id       text        PRIMARY KEY,
    latest           date,                  -- gold.latest_period
    equity           numeric,               -- gold.latest_equity
    currency         text        NOT NULL,  -- gold.amount_currency_of
    recent           boolean     NOT NULL,  -- gold.quarterly_recent(company, latest)
    until            date,                  -- read live from this day (header, 2)
    quarter_metrics  text[]      NOT NULL,  -- derived metrics with a quarter row
    ttm_metrics      text[]      NOT NULL,  -- derived metrics with a TTM row
    reported_metrics text[]      NOT NULL,  -- metrics with a quarterly_financials row
    refreshed_at     timestamptz NOT NULL
) WITH (autovacuum_vacuum_scale_factor = 0.01, autovacuum_vacuum_insert_scale_factor = 0.01);

CREATE TABLE gold.screen_figures (
    span         text    NOT NULL,
    metric       text    NOT NULL,
    company_id   text    NOT NULL,
    period_end   date    NOT NULL,
    value        numeric NOT NULL,
    accession_no text    NOT NULL,
    form         text,
    filed        date    NOT NULL,
    inputs       jsonb,
    unit         text    NOT NULL,
    PRIMARY KEY (span, metric, company_id)
        INCLUDE (period_end, value, accession_no, form, filed, inputs, unit)
) WITH (autovacuum_vacuum_scale_factor = 0.01, autovacuum_vacuum_insert_scale_factor = 0.01);
CREATE INDEX screen_figures_company ON gold.screen_figures (company_id);

GRANT SELECT ON gold.screen_companies, gold.screen_figures TO query_api;
ALTER TABLE gold.screen_companies ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_financials ON gold.screen_companies FOR SELECT TO query_api
    USING (company_id IN (SELECT r FROM authz.readable_companies('financials') r));
ALTER TABLE gold.screen_figures ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_financials ON gold.screen_figures FOR SELECT TO query_api
    USING (company_id IN (SELECT r FROM authz.readable_companies('financials') r));

-- 3. Kept by the gold build -----------------------------------------------------------------------

-- Rewrite both tables for these companies from gold (the caller holds their gold.lock_companies).
-- Unchanged figures aren't rewritten (fewer dead rows, the visibility map stays set). Returns the
-- companies written.
CREATE FUNCTION gold.refresh_screen(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    -- every span and metric the company has rows of (a balance's quarter rows serve its TTM too),
    -- each with gold.latest_row's figure; rows of a key that has none left go
    WITH fresh AS MATERIALIZED (
        SELECT k.span, k.metric, k.company_id, x.period_end, x.value, x.accession_no, x.form,
               x.filed, x.inputs, x.unit
        FROM (
            SELECT DISTINCT q.company_id, s.span, q.metric
            FROM gold.quarterly_financials q
            JOIN silver.metrics m ON m.metric = q.metric
            CROSS JOIN LATERAL unnest(CASE WHEN m.kind = 'instant' THEN ARRAY['quarter', 'ttm']
                                           ELSE ARRAY['quarter'] END) s(span)
            WHERE q.company_id = ANY(p_company_ids)
            UNION
            SELECT DISTINCT d.company_id, d.span, d.metric
            FROM gold.derived_financials d
            WHERE d.company_id = ANY(p_company_ids)
        ) k
        CROSS JOIN LATERAL gold.latest_row(k.company_id, k.span, k.metric) x
    ), gone AS (
        DELETE FROM gold.screen_figures f
        WHERE f.company_id = ANY(p_company_ids)
          AND NOT EXISTS (SELECT 1 FROM fresh n WHERE n.span = f.span AND n.metric = f.metric
                                                  AND n.company_id = f.company_id)
    )
    INSERT INTO gold.screen_figures AS f (span, metric, company_id, period_end, value, accession_no,
                                          form, filed, inputs, unit)
    SELECT * FROM fresh
    ON CONFLICT (span, metric, company_id) DO UPDATE
    SET period_end = EXCLUDED.period_end, value = EXCLUDED.value,
        accession_no = EXCLUDED.accession_no, form = EXCLUDED.form, filed = EXCLUDED.filed,
        inputs = EXCLUDED.inputs, unit = EXCLUDED.unit
    WHERE (f.period_end, f.value, f.accession_no, f.form, f.filed, f.inputs, f.unit)
          IS DISTINCT FROM (EXCLUDED.period_end, EXCLUDED.value, EXCLUDED.accession_no,
                            EXCLUDED.form, EXCLUDED.filed, EXCLUDED.inputs, EXCLUDED.unit);

    INSERT INTO gold.screen_companies AS s (company_id, latest, equity, currency, recent, until,
                                            quarter_metrics, ttm_metrics, reported_metrics,
                                            refreshed_at)
    SELECT c.id, p.latest, e.value, cu.unit, coalesce(r.recent, false),
           (SELECT min(q.period_end) FROM gold.quarterly_financials q
            WHERE q.company_id = c.id
              AND q.metric IN ('revenue', 'net_income', 'total_assets', 'stockholders_equity')
              AND q.period_end > current_date),
           ARRAY(SELECT DISTINCT d.metric FROM gold.derived_financials d
                 WHERE d.company_id = c.id AND d.span = 'quarter' ORDER BY 1),
           ARRAY(SELECT DISTINCT d.metric FROM gold.derived_financials d
                 WHERE d.company_id = c.id AND d.span = 'ttm' ORDER BY 1),
           ARRAY(SELECT DISTINCT q.metric FROM gold.quarterly_financials q
                 WHERE q.company_id = c.id ORDER BY 1),
           now()
    FROM (SELECT DISTINCT id FROM unnest(p_company_ids) id WHERE id IS NOT NULL) c
    LEFT JOIN LATERAL gold.latest_period(c.id) p ON true
    LEFT JOIN LATERAL gold.latest_equity(c.id) e ON true
    LEFT JOIN LATERAL gold.amount_currency_of(c.id) cu ON true
    LEFT JOIN LATERAL gold.quarterly_recent(c.id, p.latest) r ON true
    ON CONFLICT (company_id) DO UPDATE
    SET latest = EXCLUDED.latest, equity = EXCLUDED.equity, currency = EXCLUDED.currency,
        recent = EXCLUDED.recent, until = EXCLUDED.until,
        quarter_metrics = EXCLUDED.quarter_metrics, ttm_metrics = EXCLUDED.ttm_metrics,
        reported_metrics = EXCLUDED.reported_metrics, refreshed_at = EXCLUDED.refreshed_at;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END
$$;

-- Companies with no stored row, or read live since `until`: the catch-up's work (header, 3).
CREATE FUNCTION gold.screen_backlog() RETURNS TABLE (company_id text)
LANGUAGE sql STABLE AS $$
    SELECT c.company_id FROM silver.companies c
    WHERE NOT EXISTS (SELECT 1 FROM gold.screen_companies s
                      WHERE s.company_id = c.company_id
                        AND (s.until IS NULL OR s.until > current_date))
$$;

-- Up to p_limit companies of the backlog, under their gold locks. Returns how many.
CREATE FUNCTION gold.refresh_screen_backlog(p_limit integer) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    ids text[];
BEGIN
    ids := ARRAY(SELECT b.company_id FROM gold.screen_backlog() b ORDER BY 1 LIMIT p_limit);
    IF cardinality(ids) = 0 THEN
        RETURN 0;
    END IF;
    PERFORM gold.lock_companies(ids);
    PERFORM gold.refresh_screen(ids);
    RETURN cardinality(ids);
END
$$;

-- 071's body + the screen (078).
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

-- 4. What the screener reads ----------------------------------------------------------------------

-- The company's latest period and stored inputs (cached), or its latest period read live
-- (gold.latest_period) when it has no row or is past `until`. One row.
CREATE FUNCTION gold.screen_company(p_company_id text)
RETURNS TABLE (latest date, equity numeric, currency text, recent boolean, cached boolean)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT CASE WHEN s.company_id IS NOT NULL THEN s.latest ELSE p.latest END,
           s.equity, s.currency, s.recent, s.company_id IS NOT NULL
    FROM (SELECT 1) one
    LEFT JOIN LATERAL (
        SELECT s.company_id, s.latest, s.equity, s.currency, s.recent
        FROM gold.screen_companies s
        WHERE s.company_id = p_company_id AND (s.until IS NULL OR s.until > current_date)
    ) s ON true
    LEFT JOIN LATERAL (
        SELECT l.latest FROM gold.latest_period(p_company_id) l WHERE s.company_id IS NULL
    ) p ON true
$$;

-- gold.latest_reading's row: from the stored figure through gold.reading when the company is cached
-- (gold.screen_company), else gold.latest_reading itself.
CREATE FUNCTION gold.screen_reading(p_company_id text, p_span text, p_metric text, p_sic text,
                                    p_latest date, p_cached boolean, p_equity numeric,
                                    p_currency text, p_recent boolean)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date,
               status text, reason text, unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT f.period_end, f.value, f.accession_no, f.form, f.filed, r.status, r.reason, f.unit
    FROM (SELECT 1) one
    LEFT JOIN LATERAL (
        SELECT f.period_end, f.value, f.accession_no, f.form, f.filed, f.inputs, f.unit
        FROM gold.screen_figures f
        WHERE f.span = p_span AND f.metric = p_metric AND f.company_id = p_company_id
    ) f ON true
    CROSS JOIN LATERAL gold.reading(p_company_id, p_metric, p_sic, p_latest, f.period_end, f.value,
                                    f.inputs, f.unit, f.form, p_equity, p_currency, p_recent) r
    WHERE p_cached
    UNION ALL
    SELECT x.* FROM gold.latest_reading(p_company_id, p_span, p_metric, p_sic, p_latest) x
    WHERE NOT p_cached
$$;

GRANT EXECUTE ON FUNCTION gold.screen_company(text),
    gold.screen_reading(text, text, text, text, date, boolean, numeric, text, boolean) TO query_api;

-- 5. Coverage reads the stored lists first (header, 5): 017's view otherwise. Scalar subqueries with
-- a LIMIT, not EXISTS: inside coalesce an EXISTS may become a hashed subplan, a seq scan of all of
-- gold.quarterly_financials for the first company without a row (the screener's _has_value, 061).
CREATE OR REPLACE VIEW gold.financials_coverage AS
SELECT c.company_id
FROM silver.companies c
WHERE coalesce((SELECT s.reported_metrics <> '{}' FROM gold.screen_companies s
                WHERE s.company_id = c.company_id),
               (SELECT true FROM gold.quarterly_financials q WHERE q.company_id = c.company_id
                LIMIT 1),
               false);
