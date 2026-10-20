-- Data for the research workspace UI (ROADMAP Phase 6): screener, watchlist, expected filing
-- dates, filing recaps and peer suggestions. Read by the Query API as query_api.
--
-- Public vs permissioned (services/query-api/README.md, "Public metadata"):
--   * company identity and metadata (silver.companies) and the SEC filing index
--     (silver.filings: accession, form, dates, period) are public SEC metadata;
--   * financial values (gold.*financials) and section text (gold.filing_sections) stay behind
--     the RLS policies of 007/014: everything below that reads them is SECURITY INVOKER or
--     security_invoker, so the caller's policies apply.

-- Company lookups by company_id (silver.filings, peers, watchlists).
CREATE INDEX company_ciks_company ON silver.company_ciks (company_id);

-- Which companies have any gold financials: membership only, no values. A plain view runs with
-- its owner's privileges, so it lists every covered company regardless of the caller's grants
-- (having XBRL financials at the SEC is public); the values stay behind RLS.
CREATE VIEW gold.financials_coverage AS
SELECT c.company_id
FROM silver.companies c
WHERE EXISTS (SELECT 1 FROM gold.quarterly_financials q WHERE q.company_id = c.company_id);

-- The latest value of every metric and span per company (span ttm includes balance-sheet
-- instants, as in gold.financials). Each value keeps its own period: a metric a company stopped
-- reporting keeps an old period_end, which the API returns with the value.
CREATE VIEW gold.latest_financials WITH (security_invoker = true) AS
SELECT DISTINCT ON (company_id, span, metric)
       company_id, span, metric, period_start, period_end, value, unit, value_source,
       accession_no, form, filed, ingested_at
FROM (
    SELECT company_id, 'quarter' AS span, metric, period_start, period_end, value, unit,
           value_source, accession_no, form, filed, ingested_at
    FROM gold.quarterly_financials
    UNION ALL
    SELECT g.company_id, 'ttm', g.metric, g.period_start, g.period_end, g.value, g.unit,
           g.value_source, g.accession_no, g.form, g.filed, g.ingested_at
    FROM gold.quarterly_financials g
    JOIN silver.metrics m ON m.metric = g.metric
    WHERE m.kind = 'instant'
    UNION ALL
    SELECT company_id, span, metric, period_start, period_end, value, unit, value_source,
           accession_no, form, filed, ingested_at
    FROM gold.derived_financials
) x
ORDER BY company_id, span, metric, period_end DESC;

-- SEC filer category (submissions "category", e.g. {Non-accelerated filer, Smaller reporting
-- company}) as large_accelerated | accelerated | non_accelerated; NULL when unknown. A smaller
-- reporting company with no accelerated status listed is non-accelerated.
CREATE FUNCTION gold.filer_category(categories text[]) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN EXISTS (SELECT 1 FROM unnest(categories) c WHERE c ILIKE 'large accelerated%')
            THEN 'large_accelerated'
        WHEN EXISTS (SELECT 1 FROM unnest(categories) c WHERE c ILIKE 'accelerated%')
            THEN 'accelerated'
        WHEN EXISTS (SELECT 1 FROM unnest(categories) c
                     WHERE c ILIKE 'non-accelerated%' OR c ILIKE 'smaller reporting%')
            THEN 'non_accelerated'
    END
$$;

-- Revenue size band: 0 under $100M, 1 $100M-$1B, 2 $1B-$10B, 3 $10B-$100B, 4 $100B and up;
-- NULL for no or non-positive revenue.
CREATE FUNCTION gold.revenue_band(revenue numeric) RETURNS int
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN revenue IS NULL OR revenue <= 0 THEN NULL
        WHEN revenue < 1e8 THEN 0
        WHEN revenue < 1e9 THEN 1
        WHEN revenue < 1e10 THEN 2
        WHEN revenue < 1e11 THEN 3
        ELSE 4
    END
$$;

GRANT SELECT ON gold.financials_coverage, gold.latest_financials TO query_api;

-- Expected filing dates ------------------------------------------------------------------------

-- The SEC filing index (silver.filings: form, dates, period of report) is public metadata. The
-- view runs with its owner's privileges, so query_api needs no grant on the bronze tables.
GRANT SELECT ON silver.filings TO query_api;

-- Periodic reports (10-K, 10-Q, transition reports and amendments) of these companies, with
-- the period they cover: the submissions' report date, else the fetched document's period.
CREATE FUNCTION silver.company_reports(p_company_ids text[])
RETURNS TABLE (company_id text, accession_no text, form text, filing_date date, period_end date)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (f.company_id, f.accession_no)
           f.company_id, f.accession_no, f.form, f.filing_date,
           coalesce(f.report_date, d.period_of_report)
    FROM silver.filings f
    LEFT JOIN bronze.filing_documents d ON d.accession_no = f.accession_no
    WHERE f.company_id = ANY(p_company_ids)
      AND f.form IN ('10-K', '10-Q', '10-K/A', '10-Q/A', '10-KT', '10-QT')
    ORDER BY f.company_id, f.accession_no, f.report_date NULLS LAST
$$;

-- SEC deadline in days after the period end (Form 10-K and 10-Q General Instruction A):
-- 10-K 60 / 75 / 90 days and 10-Q 40 / 40 / 45 days for large accelerated / accelerated /
-- non-accelerated filers. NULL when the category is unknown.
CREATE FUNCTION gold.filing_deadline_days(category text, form text) RETURNS int
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE form
        WHEN '10-K' THEN CASE category WHEN 'large_accelerated' THEN 60
                                       WHEN 'accelerated' THEN 75
                                       WHEN 'non_accelerated' THEN 90 END
        WHEN '10-Q' THEN CASE category WHEN 'large_accelerated' THEN 40
                                       WHEN 'accelerated' THEN 40
                                       WHEN 'non_accelerated' THEN 45 END
    END
$$;

-- A due date on a Saturday or Sunday moves to the Monday (Rule 0-3). Holidays are not modelled.
CREATE FUNCTION gold.next_business_day(d date) RETURNS date
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT d + CASE extract(isodow FROM d) WHEN 6 THEN 2 WHEN 7 THEN 1 ELSE 0 END
$$;

-- Estimated next 10-Q / 10-K filings: the p_periods fiscal periods after the latest one a
-- company has reported, as of p_as_of. Estimates, never facts:
--   period_end: the last reported period end plus 3 months (snapped to the month end when the
--     last one ended on a month end), else plus 91 days per quarter (52/53-week years)
--   form: 10-K when the period ends within 20 days of the fiscal year end (silver.companies,
--     MMDD), 10-Q otherwise; without a fiscal year end, 10-K when a 10-K covered the period a
--     year earlier
--   deadline: SEC deadline for the filer category, moved off weekends
--   expected_date: period_end + the median filing lag of the company's last 8 original 10-Qs
--     (4 10-Ks) of that form, when there are at least 2, capped at the deadline; else the
--     deadline. basis says which: history, history_capped (history later than the deadline),
--     deadline, or NULL (no history and no filer category: no estimate)
--   status: upcoming (period not over), due (over, deadline not passed), overdue
-- Companies whose last periodic report is over 400 days old get no estimate.
CREATE FUNCTION gold.expected_filings(p_company_ids text[], p_as_of date DEFAULT current_date,
                                      p_periods int DEFAULT 2)
RETURNS TABLE (company_id text, form text, period_end date, expected_date date, deadline date,
               deadline_days int, filer_category text, basis text, typical_lag_days int,
               lag_samples int, last_period_end date, last_filed date, status text)
LANGUAGE sql STABLE AS $$
WITH reports AS (  -- originals: the first filing per form and period
    SELECT r.company_id, r.form, r.period_end, min(r.filing_date) AS filing_date
    FROM silver.company_reports(p_company_ids) r
    WHERE r.form IN ('10-K', '10-Q') AND r.period_end IS NOT NULL AND r.filing_date <= p_as_of
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

-- Filing recaps --------------------------------------------------------------------------------

-- A 10-Q/10-K's metrics against the prior quarter and the year-ago quarter, as currently in gold
-- (restatements win, so a value may cite a later filing than the one recapped). Rows:
--   current: per metric and span (quarter, ttm), the period ending within 7 days of
--     p_period_end (the filing's period of report), closest first
--   prior_quarter: the same metric and span ending 80-120 days earlier (latest first)
--   year_ago: the same metric and span ending 350-380 days earlier (closest to 364 first)
-- Reads gold.financials (security_invoker), so RLS applies: no rows without a financials grant.
CREATE FUNCTION gold.filing_recap(p_company_id text, p_period_end date, p_metrics text[])
RETURNS TABLE (comparison text, metric text, span text, period_start date, period_end date,
               fiscal_year int, fiscal_quarter int, calendar_year int, calendar_quarter int,
               value numeric, unit text, value_source text, source_tag text,
               accession_no text, form text, filed date, source_accessions text[],
               ingested_at timestamptz)
LANGUAGE sql STABLE AS $$
WITH cur AS (
    SELECT DISTINCT ON (f.span, f.metric) f.*
    FROM gold.financials f
    WHERE f.company_id = p_company_id AND f.metric = ANY(p_metrics)
      AND f.period_end BETWEEN p_period_end - 7 AND p_period_end + 7
    ORDER BY f.span, f.metric, abs(f.period_end - p_period_end), f.period_end DESC
),
matched AS (
    SELECT 'current' AS comparison, c.* FROM cur c
    UNION ALL
    SELECT 'prior_quarter', p.*
    FROM cur c
    CROSS JOIN LATERAL (
        SELECT f.* FROM gold.financials f
        WHERE f.company_id = c.company_id AND f.span = c.span AND f.metric = c.metric
          AND f.period_end BETWEEN c.period_end - 120 AND c.period_end - 80
        ORDER BY f.period_end DESC
        LIMIT 1
    ) p
    UNION ALL
    SELECT 'year_ago', y.*
    FROM cur c
    CROSS JOIN LATERAL (
        SELECT f.* FROM gold.financials f
        WHERE f.company_id = c.company_id AND f.span = c.span AND f.metric = c.metric
          AND f.period_end BETWEEN c.period_end - 380 AND c.period_end - 350
        ORDER BY abs(c.period_end - f.period_end - 364), f.period_end DESC
        LIMIT 1
    ) y
)
SELECT m.comparison, m.metric, m.span, m.period_start, m.period_end, m.fiscal_year,
       m.fiscal_quarter, m.calendar_year, m.calendar_quarter, m.value, m.unit, m.value_source,
       m.source_tag, m.accession_no, m.form, m.filed, m.source_accessions, m.ingested_at
FROM matched m
ORDER BY m.metric, m.span, m.comparison
$$;

-- One-time-item mentions in a filing's MD&A (10-K Item 7, 10-Q Part I Item 2) and press-release
-- exhibits (EX-99.x): a keyword pass, not a judgement. One row per flag and section, citing the
-- first match (char_start: offset into the parser's document text, like the section's) with a
-- snippet around it. Reads gold.filing_sections (security_invoker): RLS needs the filings tier.
CREATE FUNCTION gold.filing_flags(p_accession_no text)
RETURNS TABLE (flag text, section_key text, title text, matches int, char_start int,
               snippet text, document_url text, form text, filed date)
LANGUAGE sql STABLE AS $$
    SELECT k.flag, s.section_key, s.title,
           regexp_count(s.text, k.pattern, 1, 'i'),
           s.char_start + regexp_instr(s.text, k.pattern, 1, 1, 0, 'i') - 1,
           btrim(substr(s.text, greatest(1, regexp_instr(s.text, k.pattern, 1, 1, 0, 'i') - 160),
                        360)),
           s.document_url, s.form, s.filed
    FROM gold.filing_sections s
    CROSS JOIN (VALUES
        ('impairment',    '\mimpairments?\M'),
        ('restructuring', '\mrestructuring\M'),
        ('one_time',      '\m(one-time|non-?recurring)\M'),
        ('gain_on_sale',  '\mgains? on (the )?(sale|disposal|divestiture)'),
        ('loss_on_sale',  '\mloss(es)? on (the )?(sale|disposal|divestiture|extinguishment)'),
        ('write_down',    '\mwrite-?(downs?|offs?)\M'),
        ('litigation',    '\m(litigation|legal) (settlements?|charges?|reserves?)\M')
    ) AS k(flag, pattern)
    WHERE s.accession_no = p_accession_no
      AND (s.section_key IN ('7', 'I-2') OR (s.form LIKE '10-Q%' AND s.section_key = '2')
           OR s.section_key LIKE 'EX-99%')
      AND s.text ~* k.pattern
    ORDER BY s.char_start, k.flag
$$;
