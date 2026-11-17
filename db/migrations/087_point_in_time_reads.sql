-- Point-in-time data, part 3: reading gold as of a day (db/DESIGN.md "087").
--
-- 1. gold.financials_at(p_as_of): gold.financials' rows and labels as of a day, from
--    gold.financials_known (086): the versions believed then, with each version's known_from.
--    Inlined, security invoker: RLS (030's policy) as for gold.financials.
-- 2. gold.company_financials_at(company, as_of, span, metrics): one company's rows as of a day for
--    the Query API's single-company routes: from its history when this rules version built it, else
--    replayed at read time by gold's pipeline (gold.financial_rows: exact, 0.2-1 s; its citation is
--    the latest filing to state the figure by then, the history's the first). SECURITY DEFINER
--    (query_api reads no silver facts), authz.readable_companies('financials') its only rule
--    (figure_facts' pattern); search_path pinned, EXECUTE only query_api.
-- 3. The readings' probes as of a day: gold.latest_row, latest_equity, quarterly_recent,
--    amount_currency_of, latest_period and latest_reading gain p_as_of DEFAULT NULL. NULL reads gold
--    as before (the same probes); a date reads gold.financials_known's versions believed then, each
--    branch behind a one-time filter (UNION ALL / coalesce), so every function stays plain SQL, one
--    SELECT, inlined. gold.reading is unchanged: the rules are defined once; stale is against
--    tributary.today, which the Query API sets to the day in an as-of session; "today" in the
--    probes (latest period and equity to date) is the day. The stored screener figures (078) are
--    the NULL path's, unchanged: nothing to empty or refill.
-- 4. gold.annual_per_share(company, years, p_as_of DEFAULT NULL): the 10-K figures filed by the
--    day (036's rule, 056's currency read as of then).
-- 5. gold.restatements(company, p_as_of DEFAULT NULL): every change of a figure's value between
--    consecutive versions believed by the day (quarters and TTM sums; computed ratios are their
--    inputs' consequences): the original and restated values, each version's citation and first
--    day, and whether the line (XBRL tag) stayed the same. Security invoker (RLS). Which change is
--    a stock split is query_api.notes' one rule, applied by the route.
--
-- Locks on hermes (lock_timeout 3 s): DROP FUNCTION of the five readings, latest_reading and
-- annual_per_share and CREATE of their replacements in this transaction (catalog only; a running
-- query keeps the plan it started with; the next one resolves the new function); new functions. No
-- table is read, written or locked. Backout db/backout/point_in_time_085.sql.

SET LOCAL lock_timeout = '3s';

-- 1. Gold as of a day -----------------------------------------------------------------------------

-- 014's gold.financials over the versions believed on p_as_of (labels by the same rules).
CREATE FUNCTION gold.financials_at(p_as_of date)
RETURNS TABLE (company_id text, span text, metric text, period_start date, period_end date,
               fiscal_year integer, fiscal_quarter integer, calendar_year integer,
               calendar_quarter integer, calendar_primary boolean, value numeric, unit text,
               value_source text, source_tag text, inputs jsonb, accession_no text, form text,
               filed date, source_accessions text[], ingested_at timestamptz, known_from date)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    WITH known AS (
        SELECT k.* FROM gold.financials_known k
        WHERE k.known_from <= p_as_of AND (k.known_to IS NULL OR k.known_to > p_as_of)
    ),
    facts AS (
        SELECT company_id, span, metric, period_start, period_end, value, unit, value_source,
               source_tag, inputs, accession_no, form, filed, source_accessions, ingested_at,
               known_from
        FROM known
        UNION ALL
        SELECT g.company_id, 'ttm', g.metric, g.period_start, g.period_end, g.value, g.unit,
               g.value_source, g.source_tag, NULL, g.accession_no, g.form, g.filed,
               g.source_accessions, g.ingested_at, g.known_from
        FROM known g
        JOIN silver.metrics m ON m.metric = g.metric
        WHERE g.span = 'quarter' AND m.kind = 'instant'
    ),
    labeled AS (
        SELECT f.*,
               gold.fiscal_year_end(f.period_end, c.fiscal_year_end) AS fiscal_year_end,
               gold.calendar_quarter(f.period_end) AS calendar_start
        FROM facts f
        JOIN silver.companies c ON c.company_id = f.company_id
    )
    SELECT company_id, span, metric, period_start, period_end,
           extract(year FROM fiscal_year_end - 7)::int AS fiscal_year,
           CASE WHEN fiscal_year_end IS NOT NULL
                THEN greatest(1, least(4, 4 - round((fiscal_year_end - period_end) / 91.31)::int))
           END AS fiscal_quarter,
           extract(year FROM calendar_start)::int AS calendar_year,
           extract(quarter FROM calendar_start)::int AS calendar_quarter,
           row_number() OVER (
               PARTITION BY company_id, span, metric, calendar_start
               ORDER BY CASE WHEN period_start IS NOT NULL
                             THEN least(period_end, (calendar_start + interval '3 months')::date - 1)
                                  - greatest(period_start, calendar_start) END DESC NULLS LAST,
                        period_end DESC
           ) = 1 AS calendar_primary,
           value, unit, value_source, source_tag, inputs, accession_no, form, filed,
           source_accessions, ingested_at, known_from
    FROM labeled
$$;
GRANT EXECUTE ON FUNCTION gold.financials_at(date) TO query_api;

-- 2. One company as of a day, for the single-company routes (header, 2). known_from is NULL for
-- rows replayed at read time.
CREATE FUNCTION gold.company_financials_at(p_company_id text, p_as_of date,
                                           p_span text DEFAULT NULL, p_metrics text[] DEFAULT NULL)
RETURNS TABLE (company_id text, span text, metric text, period_start date, period_end date,
               fiscal_year integer, fiscal_quarter integer, calendar_year integer,
               calendar_quarter integer, calendar_primary boolean, value numeric, unit text,
               value_source text, source_tag text, inputs jsonb, accession_no text, form text,
               filed date, source_accessions text[], ingested_at timestamptz, known_from date)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    WITH gate AS MATERIALIZED (  -- readable, and whether this rules version built its history
        SELECT p_company_id IN (SELECT r FROM authz.readable_companies('financials') r) AS readable,
               EXISTS (SELECT 1 FROM ops.company_builds b
                       WHERE b.kind = 'known' AND b.company_id = p_company_id
                         AND b.version = gold.rules_version())
               AND EXISTS (SELECT 1 FROM gold.known_companies k
                           WHERE k.company_id = p_company_id) AS built
    ),
    rows AS (
        SELECT k.span, k.metric, k.period_start, k.period_end, k.value, k.unit, k.value_source,
               k.source_tag, k.inputs, k.accession_no, k.form, k.filed, k.source_accessions,
               k.ingested_at, k.known_from
        FROM gold.financials_known k
        WHERE (SELECT g.readable AND g.built FROM gate g)
          AND k.company_id = p_company_id
          AND (p_metrics IS NULL OR k.metric = ANY(p_metrics))
          AND k.known_from <= p_as_of AND (k.known_to IS NULL OR k.known_to > p_as_of)
        UNION ALL
        SELECT r.span, r.metric, r.period_start, r.period_end, r.value, r.unit, r.value_source,
               r.source_tag, r.inputs, r.accession_no, r.form, r.filed, r.source_accessions,
               r.ingested_at, NULL::date
        FROM gold.financial_rows(ARRAY[p_company_id], p_as_of) r
        WHERE (SELECT g.readable AND NOT g.built FROM gate g)
          AND (p_metrics IS NULL OR r.metric = ANY(p_metrics))
    ),
    facts AS (
        SELECT * FROM rows
        UNION ALL
        SELECT 'ttm', r.metric, r.period_start, r.period_end, r.value, r.unit, r.value_source,
               r.source_tag, NULL, r.accession_no, r.form, r.filed, r.source_accessions,
               r.ingested_at, r.known_from
        FROM rows r
        JOIN silver.metrics m ON m.metric = r.metric
        WHERE r.span = 'quarter' AND m.kind = 'instant'
    ),
    labeled AS (
        SELECT f.*,
               gold.fiscal_year_end(f.period_end, c.fiscal_year_end) AS fiscal_year_end,
               gold.calendar_quarter(f.period_end) AS calendar_start
        FROM facts f
        JOIN silver.companies c ON c.company_id = p_company_id
        WHERE p_span IS NULL OR f.span = p_span
    )
    SELECT p_company_id, span, metric, period_start, period_end,
           extract(year FROM fiscal_year_end - 7)::int,
           CASE WHEN fiscal_year_end IS NOT NULL
                THEN greatest(1, least(4, 4 - round((fiscal_year_end - period_end) / 91.31)::int))
           END,
           extract(year FROM calendar_start)::int,
           extract(quarter FROM calendar_start)::int,
           row_number() OVER (
               PARTITION BY span, metric, calendar_start
               ORDER BY CASE WHEN period_start IS NOT NULL
                             THEN least(period_end, (calendar_start + interval '3 months')::date - 1)
                                  - greatest(period_start, calendar_start) END DESC NULLS LAST,
                        period_end DESC
           ) = 1,
           value, unit, value_source, source_tag, inputs, accession_no, form, filed,
           source_accessions, ingested_at, known_from
    FROM labeled
$fn$;
REVOKE EXECUTE ON FUNCTION gold.company_financials_at(text, date, text, text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.company_financials_at(text, date, text, text[]) TO query_api;

-- 3. The readings' probes as of a day ---------------------------------------------------------------

DROP FUNCTION gold.latest_reading(text, text, text, text, date);
DROP FUNCTION gold.latest_row(text, text, text);
DROP FUNCTION gold.latest_equity(text);
DROP FUNCTION gold.quarterly_recent(text, date);
DROP FUNCTION gold.amount_currency_of(text);
DROP FUNCTION gold.latest_period(text);

-- 078's body, and as of p_as_of from the history (087).
CREATE FUNCTION gold.latest_row(p_company_id text, p_span text, p_metric text,
                                p_as_of date DEFAULT NULL)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date,
               inputs jsonb, unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT y.period_end, y.value, y.accession_no, y.form, y.filed, y.inputs, y.unit
    FROM (
        (SELECT q.period_end, q.value, q.accession_no, q.form, q.filed, NULL::jsonb AS inputs,
                q.unit
         FROM gold.quarterly_financials q
         WHERE p_as_of IS NULL
           AND q.company_id = p_company_id AND q.metric = p_metric
           AND (p_span = 'quarter'
                OR (p_span = 'ttm' AND EXISTS (SELECT 1 FROM silver.metrics m
                                               WHERE m.metric = p_metric AND m.kind = 'instant')))
         ORDER BY q.period_end DESC LIMIT 1)
        UNION ALL
        (SELECT d.period_end, d.value, d.accession_no, d.form, d.filed, d.inputs, d.unit
         FROM gold.derived_financials d
         WHERE p_as_of IS NULL
           AND d.company_id = p_company_id AND d.span = p_span AND d.metric = p_metric
         ORDER BY d.period_end DESC LIMIT 1)
        UNION ALL
        -- 087: the history's rows of the span (quarterly and derived alike)
        (SELECT k.period_end, k.value, k.accession_no, k.form, k.filed, k.inputs, k.unit
         FROM gold.financials_known k
         WHERE p_as_of IS NOT NULL
           AND k.company_id = p_company_id AND k.span = p_span AND k.metric = p_metric
           AND k.known_from <= p_as_of AND (k.known_to IS NULL OR k.known_to > p_as_of)
         ORDER BY k.period_end DESC LIMIT 1)
        UNION ALL
        -- 087: a balance's TTM is its quarter row
        (SELECT k.period_end, k.value, k.accession_no, k.form, k.filed, k.inputs, k.unit
         FROM gold.financials_known k
         WHERE p_as_of IS NOT NULL AND p_span = 'ttm'
           AND EXISTS (SELECT 1 FROM silver.metrics m
                       WHERE m.metric = p_metric AND m.kind = 'instant')
           AND k.company_id = p_company_id AND k.span = 'quarter' AND k.metric = p_metric
           AND k.known_from <= p_as_of AND (k.known_to IS NULL OR k.known_to > p_as_of)
         ORDER BY k.period_end DESC LIMIT 1)
    ) y
    ORDER BY y.period_end DESC LIMIT 1
$$;

-- 078's body, and as of p_as_of (087: equity to that day, from the history).
CREATE FUNCTION gold.latest_equity(p_company_id text, p_as_of date DEFAULT NULL)
RETURNS TABLE (value numeric)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT y.value
    FROM (
        (SELECT q.value, q.period_end FROM gold.quarterly_financials q
         WHERE p_as_of IS NULL
           AND q.company_id = p_company_id AND q.metric = 'stockholders_equity'
           AND q.period_end <= current_date
         ORDER BY q.period_end DESC LIMIT 1)
        UNION ALL
        (SELECT k.value, k.period_end FROM gold.financials_known k
         WHERE p_as_of IS NOT NULL
           AND k.company_id = p_company_id AND k.span = 'quarter'
           AND k.metric = 'stockholders_equity' AND k.period_end <= p_as_of
           AND k.known_from <= p_as_of AND (k.known_to IS NULL OR k.known_to > p_as_of)
         ORDER BY k.period_end DESC LIMIT 1)
    ) y
$$;

-- 078's body, and as of p_as_of (087).
CREATE FUNCTION gold.quarterly_recent(p_company_id text, p_latest date, p_as_of date DEFAULT NULL)
RETURNS TABLE (recent boolean)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT EXISTS (SELECT 1 FROM gold.quarterly_financials q
                   WHERE p_as_of IS NULL
                     AND q.company_id = p_company_id
                     AND q.metric IN ('revenue', 'net_income')
                     AND q.period_end > p_latest - 200
                     AND q.period_end <= p_latest)
        OR EXISTS (SELECT 1 FROM gold.financials_known k
                   WHERE p_as_of IS NOT NULL
                     AND k.company_id = p_company_id AND k.span = 'quarter'
                     AND k.metric IN ('revenue', 'net_income')
                     AND k.period_end > p_latest - 200
                     AND k.period_end <= p_latest
                     AND k.known_from <= p_as_of
                     AND (k.known_to IS NULL OR k.known_to > p_as_of))
$$;

-- 061's rule, and as of p_as_of (087): the latest quarterly revenue's unit, else net income's,
-- else the TTM rows', else USD.
CREATE FUNCTION gold.amount_currency_of(p_company_id text, p_as_of date DEFAULT NULL)
RETURNS TABLE (unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT coalesce(
        (SELECT q.unit FROM gold.quarterly_financials q
         WHERE p_as_of IS NULL AND q.company_id = p_company_id AND q.metric = 'revenue'
         ORDER BY q.period_end DESC LIMIT 1),
        (SELECT q.unit FROM gold.quarterly_financials q
         WHERE p_as_of IS NULL AND q.company_id = p_company_id AND q.metric = 'net_income'
         ORDER BY q.period_end DESC LIMIT 1),
        -- 060: an annual-only filer's (20-F, 40-F) revenue and net income are its stated years,
        -- kept only as TTM rows of gold.derived_financials (PK probes, like the two above)
        (SELECT d.unit FROM gold.derived_financials d
         WHERE p_as_of IS NULL AND d.company_id = p_company_id AND d.span = 'ttm'
           AND d.metric = 'revenue'
         ORDER BY d.period_end DESC LIMIT 1),
        (SELECT d.unit FROM gold.derived_financials d
         WHERE p_as_of IS NULL AND d.company_id = p_company_id AND d.span = 'ttm'
           AND d.metric = 'net_income'
         ORDER BY d.period_end DESC LIMIT 1),
        -- 087: the same four, from the versions believed on p_as_of
        (SELECT k.unit FROM gold.financials_known k
         WHERE p_as_of IS NOT NULL AND k.company_id = p_company_id AND k.span = 'quarter'
           AND k.metric = 'revenue'
           AND k.known_from <= p_as_of AND (k.known_to IS NULL OR k.known_to > p_as_of)
         ORDER BY k.period_end DESC LIMIT 1),
        (SELECT k.unit FROM gold.financials_known k
         WHERE p_as_of IS NOT NULL AND k.company_id = p_company_id AND k.span = 'quarter'
           AND k.metric = 'net_income'
           AND k.known_from <= p_as_of AND (k.known_to IS NULL OR k.known_to > p_as_of)
         ORDER BY k.period_end DESC LIMIT 1),
        (SELECT k.unit FROM gold.financials_known k
         WHERE p_as_of IS NOT NULL AND k.company_id = p_company_id AND k.span = 'ttm'
           AND k.metric = 'revenue'
           AND k.known_from <= p_as_of AND (k.known_to IS NULL OR k.known_to > p_as_of)
         ORDER BY k.period_end DESC LIMIT 1),
        (SELECT k.unit FROM gold.financials_known k
         WHERE p_as_of IS NOT NULL AND k.company_id = p_company_id AND k.span = 'ttm'
           AND k.metric = 'net_income'
           AND k.known_from <= p_as_of AND (k.known_to IS NULL OR k.known_to > p_as_of)
         ORDER BY k.period_end DESC LIMIT 1),
        'USD')
$$;

-- 035's body, and as of p_as_of (087: periods to that day, from the history).
CREATE FUNCTION gold.latest_period(p_company_id text, p_as_of date DEFAULT NULL)
RETURNS TABLE (latest date)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT max(x.period_end)
    FROM (
        (SELECT q.period_end FROM gold.quarterly_financials q
         WHERE p_as_of IS NULL AND q.company_id = p_company_id AND q.metric = 'revenue'
           AND q.period_end <= current_date
         ORDER BY q.period_end DESC LIMIT 1)
        UNION ALL
        (SELECT q.period_end FROM gold.quarterly_financials q
         WHERE p_as_of IS NULL AND q.company_id = p_company_id AND q.metric = 'net_income'
           AND q.period_end <= current_date
         ORDER BY q.period_end DESC LIMIT 1)
        UNION ALL
        (SELECT q.period_end FROM gold.quarterly_financials q
         WHERE p_as_of IS NULL AND q.company_id = p_company_id AND q.metric = 'total_assets'
           AND q.period_end <= current_date
         ORDER BY q.period_end DESC LIMIT 1)
        UNION ALL
        (SELECT k.period_end FROM gold.financials_known k
         WHERE p_as_of IS NOT NULL AND k.company_id = p_company_id AND k.span = 'quarter'
           AND k.metric = 'revenue' AND k.period_end <= p_as_of
           AND k.known_from <= p_as_of AND (k.known_to IS NULL OR k.known_to > p_as_of)
         ORDER BY k.period_end DESC LIMIT 1)
        UNION ALL
        (SELECT k.period_end FROM gold.financials_known k
         WHERE p_as_of IS NOT NULL AND k.company_id = p_company_id AND k.span = 'quarter'
           AND k.metric = 'net_income' AND k.period_end <= p_as_of
           AND k.known_from <= p_as_of AND (k.known_to IS NULL OR k.known_to > p_as_of)
         ORDER BY k.period_end DESC LIMIT 1)
        UNION ALL
        (SELECT k.period_end FROM gold.financials_known k
         WHERE p_as_of IS NOT NULL AND k.company_id = p_company_id AND k.span = 'quarter'
           AND k.metric = 'total_assets' AND k.period_end <= p_as_of
           AND k.known_from <= p_as_of AND (k.known_to IS NULL OR k.known_to > p_as_of)
         ORDER BY k.period_end DESC LIMIT 1)
    ) x
$$;

-- 083's body, and as of p_as_of (087): every probe passes the day on; equity to that day.
CREATE FUNCTION gold.latest_reading(p_company_id text, p_span text, p_metric text, p_sic text,
                                    p_latest date, p_as_of date DEFAULT NULL)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date,
               status text, reason text, unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT x.period_end, x.value, x.accession_no, x.form, x.filed, r.status, r.reason, x.unit
    FROM (SELECT 1) one
    LEFT JOIN LATERAL gold.latest_row(p_company_id, p_span, p_metric, p_as_of) x ON true  -- 087
    LEFT JOIN LATERAL (
        SELECT e.value FROM gold.latest_equity(p_company_id, p_as_of) e  -- 087
        WHERE p_metric IN ('debt_to_equity', 'roe')
        LIMIT 1
    ) e ON true
    -- 057/060/061: the currency of the amounts a rule's base is in, only for the readings with a base
    LEFT JOIN LATERAL (
        SELECT c.unit FROM gold.amount_currency_of(p_company_id, p_as_of) c  -- 087
        WHERE p_metric IN ('gross_margin', 'operating_margin', 'net_margin', 'fcf_margin',
                           'revenue_growth', 'net_income_growth')
        LIMIT 1
    ) cu ON true
    -- 083: cost of revenue at the revenue's period end, only where revenue can be gross sales
    LEFT JOIN LATERAL (
        SELECT c.value FROM gold.latest_row(p_company_id, p_span, 'cost_of_revenue', p_as_of) c
        WHERE p_metric = 'revenue' AND gold.gross_sales_sic(p_sic) AND c.period_end = x.period_end
        LIMIT 1
    ) gs ON true
    -- 069: the recent-quarters probe, only when the stale answer depends on it
    LEFT JOIN LATERAL (
        SELECT qr.recent FROM gold.quarterly_recent(p_company_id, p_latest, p_as_of) qr  -- 087
        WHERE gold.is_stale(p_latest, x.form, true) AND NOT gold.is_stale(p_latest, x.form, false)
        LIMIT 1
    ) st ON true
    CROSS JOIN LATERAL gold.reading(p_company_id, p_metric, p_sic, p_latest, x.period_end, x.value,
                                    gold.with_cost(x.inputs, gs.value),  -- 083
                                    x.unit, x.form, e.value, cu.unit, st.recent) r
$$;

GRANT EXECUTE ON FUNCTION gold.latest_row(text, text, text, date), gold.latest_equity(text, date),
    gold.quarterly_recent(text, date, date), gold.amount_currency_of(text, date),
    gold.latest_period(text, date), gold.latest_reading(text, text, text, text, date, date)
    TO query_api;

-- 4. The 10-K's per-share figures as of a day ---------------------------------------------------------

DROP FUNCTION gold.annual_per_share(text, int);

-- 056's body; facts filed by p_as_of, and the currency of the quarters believed then (087).
CREATE FUNCTION gold.annual_per_share(p_company_id text, p_years int, p_as_of date DEFAULT NULL)
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
                             WHERE p_as_of IS NULL
                               AND q.company_id = p_company_id AND q.unit ~ '^[A-Z]{3}$'
                             ORDER BY q.period_end DESC LIMIT 1),
                            -- 087
                            (SELECT q.unit FROM gold.financials_known q
                             WHERE p_as_of IS NOT NULL
                               AND q.company_id = p_company_id AND q.span = 'quarter'
                               AND q.unit ~ '^[A-Z]{3}$' AND q.value_source <> 'computed'
                               AND q.known_from <= p_as_of
                               AND (q.known_to IS NULL OR q.known_to > p_as_of)
                             ORDER BY q.period_end DESC LIMIT 1),
                            'USD') AS currency
        ) cu
        WHERE k.company_id = p_company_id
          AND k.company_id IN (SELECT r FROM authz.readable_companies('financials') r)
          AND f.metric IN ('eps_diluted', 'eps_basic', 'weighted_shares_diluted',
                           'weighted_shares_basic')
          AND f.unit = CASE m.unit WHEN 'USD/shares' THEN cu.currency || '/shares' ELSE m.unit END
          AND f.period_type = 'annual'
          AND coalesce(f.form, '') !~ '^(DEF|PRE)'
          AND (p_as_of IS NULL OR f.filed <= p_as_of)  -- 087
        ORDER BY f.metric, f.period_end, t.priority, f.filed DESC, f.accession_no DESC
    ) y
    WHERE y.recency <= p_years
$fn$;
REVOKE EXECUTE ON FUNCTION gold.annual_per_share(text, int, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.annual_per_share(text, int, date) TO query_api;

-- 5. Restatements ---------------------------------------------------------------------------------

-- Each change of a quarter's or TTM sum's value between two consecutive versions believed by
-- p_as_of (NULL: today): the original (the earlier version) and the restated one, each with its
-- citation and the day it was first believed; same_line when the XBRL tag stayed the same.
CREATE FUNCTION gold.restatements(p_company_id text, p_as_of date DEFAULT NULL)
RETURNS TABLE (span text, metric text, period_start date, period_end date, unit text,
               original numeric, original_tag text, original_accession text, original_form text,
               original_filed date, original_known_from date,
               restated numeric, restated_tag text, restated_accession text, restated_form text,
               restated_filed date, restated_known_from date, same_line boolean)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT v.span, v.metric, v.next_start, v.period_end, v.next_unit,
           v.value, v.source_tag, v.accession_no, v.form, v.filed, v.known_from,
           v.next_value, v.next_tag, v.next_accession, v.next_form, v.next_filed, v.next_from,
           v.source_tag IS NOT DISTINCT FROM v.next_tag
    FROM (
        SELECT k.span, k.metric, k.period_end, k.value, k.source_tag, k.accession_no, k.form,
               k.filed, k.known_from,
               lead(k.period_start) OVER w AS next_start, lead(k.unit) OVER w AS next_unit,
               lead(k.value) OVER w AS next_value, lead(k.source_tag) OVER w AS next_tag,
               lead(k.accession_no) OVER w AS next_accession, lead(k.form) OVER w AS next_form,
               lead(k.filed) OVER w AS next_filed, lead(k.known_from) OVER w AS next_from
        FROM gold.financials_known k
        WHERE k.company_id = p_company_id AND k.value_source <> 'computed'
          AND (p_as_of IS NULL OR k.known_from <= p_as_of)
        WINDOW w AS (PARTITION BY k.span, k.metric, k.period_end ORDER BY k.known_from)
    ) v
    WHERE v.next_from IS NOT NULL AND v.next_value <> v.value
$$;
GRANT EXECUTE ON FUNCTION gold.restatements(text, date) TO query_api;
