-- Cross-company routes stay fast with the public catalog open (Query API `Readable.ids`).
--
-- The incident (hermes, 2026-11-02, ~6,033 companies readable): screener, /v1/filings/latest,
-- /v1/filing-index and /v1/search hit their statement timeouts. The id array wasn't the cost by
-- itself; each route made Postgres read and sort every row of every company before its LIMIT:
--   screener: gold.latest_financials, seq scans + a sort of 1.6M rows to pivot 6,000 companies
--     for a page of 50;
--   /v1/filings/latest: every section of gold.filing_sections grouped under RLS, then LIMIT 20;
--   /v1/filing-index: silver.filings has company_id only from a join inside each UNION branch, so
--     5.2M rows were joined and DISTINCT-ed, then LIMIT 50;
--   /v1/search: `@@` isn't leakproof, so under RLS it can't be an index condition: as query_api
--     the GIN index is unusable and a seq scan or the company btree reads every passage;
--   /v1/calendar/expected-filings?scope=readable (the home page): every 10-K/10-Q of all time.
--
-- With the catalog public the Query API now passes no id list (RLS bounds the rows) and these
-- objects give each route a form that stops early. Nothing here changes who can read what.
--
-- Locks on hermes: functions take none on the tables; CREATE OR REPLACE VIEW silver.filings takes a
-- brief ACCESS EXCLUSIVE on the view (no table is rewritten). No index is built. lock_timeout so
-- a long reader of the view fails this migration (the deploy can be retried) instead of queueing
-- every other reader behind it.

SET LOCAL lock_timeout = '30s';

-- The latest value of one company, span and metric: gold.latest_financials' rule (017) as a single
-- index lookup per table, so a screener page reads what it shows and a sort reads one row per
-- company. Same branches: quarterly rows are the 'quarter' span, and also 'ttm' for balance-sheet
-- (instant) metrics; gold.derived_financials holds the rest. SECURITY INVOKER: granted_financials
-- applies to both tables. A plain SQL single SELECT, so the planner inlines it into a LATERAL
-- (no SET, plpgsql or SECURITY DEFINER), and the instant test uses p_metric, not the row's metric,
-- so it is a one-time filter instead of a check on every quarter of the company.
CREATE FUNCTION gold.latest_value(p_company_id text, p_span text, p_metric text)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT x.period_end, x.value, x.accession_no, x.form, x.filed
    FROM (
        (SELECT q.period_end, q.value, q.accession_no, q.form, q.filed
         FROM gold.quarterly_financials q
         WHERE q.company_id = p_company_id AND q.metric = p_metric
           AND (p_span = 'quarter'
                OR (p_span = 'ttm' AND EXISTS (SELECT 1 FROM silver.metrics m
                                               WHERE m.metric = p_metric AND m.kind = 'instant')))
         ORDER BY q.period_end DESC LIMIT 1)
        UNION ALL
        (SELECT d.period_end, d.value, d.accession_no, d.form, d.filed
         FROM gold.derived_financials d
         WHERE d.company_id = p_company_id AND d.span = p_span AND d.metric = p_metric
         ORDER BY d.period_end DESC LIMIT 1)
    ) x
    ORDER BY x.period_end DESC LIMIT 1
$$;
GRANT EXECUTE ON FUNCTION gold.latest_value(text, text, text) TO query_api;

-- silver.filings gains filed_on: the row's date from its own base table, no join, so a filter on it
-- reaches both UNION branches (the bronze.filings branch otherwise joins all 64k rows to
-- bronze.submission_filings before filing_date can filter them). Never later than filing_date
-- by more than a long weekend after hours: always filter on filing_date too, with a margin on
-- filed_on (Store.FILED_ON_MARGIN_DAYS). 016's body otherwise unchanged; a new last column.
CREATE OR REPLACE VIEW silver.filings AS
SELECT f.accession_no,
       f.cik,
       k.company_id,
       f.form_type AS form,
       coalesce(h.filing_date, (f.filed_at AT TIME ZONE 'America/New_York')::date)
           AS filing_date,
       CASE WHEN coalesce(f.payload->>'source', 'feed') IN ('feed', 'seed') THEN f.filed_at
            ELSE h.accepted_at END AS accepted_at,
       h.report_date,
       h.primary_document,
       coalesce(f.payload->>'source', 'feed') AS source,
       (f.filed_at AT TIME ZONE 'America/New_York')::date AS filed_on
FROM bronze.filings f
LEFT JOIN bronze.submission_filings h USING (accession_no, cik)
LEFT JOIN silver.company_ciks k ON k.cik = f.cik
UNION ALL
SELECT h.accession_no,
       h.cik,
       k.company_id,
       h.form,
       h.filing_date,
       h.accepted_at,
       h.report_date,
       h.primary_document,
       'submissions',
       h.filing_date
FROM bronze.submission_filings h
LEFT JOIN silver.company_ciks k ON k.cik = h.cik
WHERE NOT EXISTS (
    SELECT 1 FROM bronze.filings f WHERE f.accession_no = h.accession_no AND f.cik = h.cik
);

-- Full-text search's first stage: the best passage per section, ranked, as a page (offsets and
-- rank, no text). Why SECURITY DEFINER, unlike every other read of RLS tables:
--   `@@` (ts_match_vq) isn't leakproof and only a superuser can mark it so, so under
--   gold.section_search's policy the planner may not use it as an index condition. As query_api
--   the GIN index never runs: closed, the company btree reads all of a company's passages and
--   filters them; with ~6,000 companies, all 12M passages. As the table's owner the same query
--   does a BitmapAnd of GIN and the company btree: 221 ms for 30 days on hermes.
-- The read rule is still the only rule: authz.readable_companies('filings'), evaluated in the
-- caller's session (app.user_id set by Store from the JWT), intersected with p_companies, which
-- can only narrow. No app.user_id or no tier = nothing, before any scan. It returns no text and no
-- value; the snippet's text is read by the caller under RLS on silver.filing_sections.
-- plpgsql EXECUTE plans with the real values (a SQL function would plan generically, without
-- the array's size or the query's selectivity). search_path pinned, EXECUTE only for query_api,
-- as 022/026/028. A CREATE OR REPLACE must re-issue the REVOKE/GRANT.
CREATE FUNCTION gold.search_passages(p_q tsquery, p_companies text[], p_form text,
                                     p_section text, p_from date, p_to date,
                                     p_limit int, p_offset int)
RETURNS TABLE (company_id text, accession_no text, section_key text, passage int, form text,
               filed date, parser text, start_char int, end_char int, rank real,
               passages_matched bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
DECLARE
    readable text[];
BEGIN
    readable := ARRAY(SELECT r FROM authz.readable_companies('filings') r
                      WHERE p_companies IS NULL OR r = ANY(p_companies));
    IF cardinality(readable) = 0 THEN
        RETURN;
    END IF;
    RETURN QUERY EXECUTE $sql$
        WITH matches AS (
            SELECT p.company_id, p.accession_no, p.section_key, p.passage, p.form, p.filed,
                   p.parser, p.start_char, p.end_char, ts_rank_cd(p.tsv, $1) AS rank,
                   count(*) OVER (PARTITION BY p.accession_no, p.section_key) AS passages_matched
            FROM gold.section_search p
            WHERE p.tsv @@ $1 AND p.company_id = ANY($2)
              AND ($3::text IS NULL OR p.form = $3)
              AND ($4::text IS NULL OR p.section_key = $4)
              AND ($5::date IS NULL OR p.filed >= $5)
              AND ($6::date IS NULL OR p.filed <= $6)
        ),
        best AS (
            SELECT DISTINCT ON (accession_no, section_key) *
            FROM matches
            ORDER BY accession_no, section_key, rank DESC, passage
        )
        SELECT * FROM best
        ORDER BY rank DESC, filed DESC, accession_no, section_key
        LIMIT $7 OFFSET $8
    $sql$ USING p_q, readable, p_form, p_section, p_from, p_to, p_limit, p_offset;
END
$fn$;
REVOKE EXECUTE ON FUNCTION gold.search_passages(tsquery, text[], text, text, date, date, int, int)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.search_passages(tsquery, text[], text, text, date, date, int, int)
    TO query_api;

-- gold.expected_filings for every company the caller can read, without an id list: 017's rules over
-- the 10-K/10-Qs of the last 1,500 days instead of all time (hermes: 103k rows instead of 960k).
-- The history a company's estimate uses is its last 8 10-Qs and 4 10-Ks, and a company whose last
-- report is over 400 days old gets none, so the window only changes the estimate of a company
-- with long gaps (fewer lag samples). The home page calls this on every load with the catalog
-- open. Below `reports` the body is 017's: change both together.
CREATE FUNCTION gold.expected_filings_recent(p_as_of date DEFAULT current_date,
                                             p_periods int DEFAULT 2)
RETURNS TABLE (company_id text, form text, period_end date, expected_date date, deadline date,
               deadline_days int, filer_category text, basis text, typical_lag_days int,
               lag_samples int, last_period_end date, last_filed date, status text)
LANGUAGE sql STABLE AS $$
WITH reports AS (  -- originals: the first filing per form and period
    SELECT r.company_id, r.form, r.period_end, min(r.filing_date) AS filing_date
    FROM (
        SELECT DISTINCT ON (f.company_id, f.accession_no)
               f.company_id, f.form, f.filing_date,
               coalesce(f.report_date, d.period_of_report) AS period_end
        FROM silver.filings f
        LEFT JOIN bronze.filing_documents d ON d.accession_no = f.accession_no
        WHERE f.form IN ('10-K', '10-Q')
          AND f.filing_date BETWEEN p_as_of - 1500 AND p_as_of
          AND f.filed_on >= p_as_of - 1507
          AND f.company_id IN (SELECT x FROM authz.readable_companies('financials') x
                               UNION SELECT x FROM authz.readable_companies('filings') x)
        ORDER BY f.company_id, f.accession_no, f.report_date NULLS LAST
    ) r
    WHERE r.period_end IS NOT NULL
    GROUP BY r.company_id, r.form, r.period_end
),
latest AS (
    SELECT r.company_id, max(r.period_end) AS last_period_end, max(r.filing_date) AS last_filed
    FROM reports r
    GROUP BY r.company_id
),
lags AS (
    SELECT x.company_id, x.form,
           percentile_disc(0.5) WITHIN GROUP (ORDER BY x.lag) AS lag,
           count(*)::int AS samples
    FROM (
        SELECT r.company_id, r.form, r.filing_date - r.period_end AS lag,
               row_number() OVER (PARTITION BY r.company_id, r.form
                                  ORDER BY r.period_end DESC) AS n
        FROM reports r
        WHERE r.filing_date - r.period_end BETWEEN 1 AND 180
    ) x
    WHERE x.n <= CASE x.form WHEN '10-K' THEN 4 ELSE 8 END
    GROUP BY x.company_id, x.form
),
periods AS (
    SELECT l.company_id, l.last_period_end, l.last_filed,
           CASE WHEN l.last_period_end
                     = (date_trunc('month', l.last_period_end) + interval '1 month - 1 day')::date
                THEN (date_trunc('month', l.last_period_end + make_interval(months => 3 * k.k))
                      + interval '1 month - 1 day')::date
                ELSE l.last_period_end + 91 * k.k
           END AS period_end
    FROM latest l
    CROSS JOIN generate_series(1, p_periods) AS k(k)
    WHERE l.last_filed >= p_as_of - 400
),
typed AS (
    SELECT p.*, gold.filer_category(c.filer_categories) AS category,
           CASE
               WHEN c.fiscal_year_end IS NOT NULL THEN
                   CASE WHEN gold.fiscal_year_end(p.period_end, c.fiscal_year_end) - p.period_end
                             <= 20
                        THEN '10-K' ELSE '10-Q' END
               WHEN EXISTS (SELECT 1 FROM reports r
                            WHERE r.company_id = p.company_id AND r.form = '10-K'
                              AND r.period_end BETWEEN p.period_end - 380 AND p.period_end - 350)
                   THEN '10-K'
               ELSE '10-Q'
           END AS form
    FROM periods p
    JOIN silver.companies c ON c.company_id = p.company_id
),
dated AS (
    SELECT t.*, g.lag, coalesce(g.samples, 0) AS samples,
           gold.filing_deadline_days(t.category, t.form) AS deadline_days,
           gold.next_business_day(t.period_end + gold.filing_deadline_days(t.category, t.form))
               AS deadline,
           CASE WHEN g.samples >= 2 THEN gold.next_business_day(t.period_end + g.lag) END
               AS from_history
    FROM typed t
    LEFT JOIN lags g ON g.company_id = t.company_id AND g.form = t.form
)
SELECT d.company_id, d.form, d.period_end,
       coalesce(least(d.from_history, d.deadline), d.from_history, d.deadline) AS expected_date,
       d.deadline, d.deadline_days, d.category,
       CASE WHEN d.from_history > d.deadline THEN 'history_capped'
            WHEN d.from_history IS NOT NULL THEN 'history'
            WHEN d.deadline IS NOT NULL THEN 'deadline'
       END AS basis,
       d.lag, d.samples, d.last_period_end, d.last_filed,
       CASE WHEN p_as_of <= d.period_end THEN 'upcoming'
            WHEN p_as_of <= coalesce(d.deadline, d.from_history, p_as_of) THEN 'due'
            ELSE 'overdue'
       END AS status
FROM dated d
ORDER BY d.company_id, d.period_end
$$;
GRANT EXECUTE ON FUNCTION gold.expected_filings_recent(date, int) TO query_api;
