-- One series of quarters per company and metric.
--
-- Gold kept one row per (company, metric, period_end), so a quarter reported with two different
-- (start, end) pairs survived twice. On hermes (2026-11-02, after 029 and a full rebuild)
-- ops.check_financials found 11,080 overlapping_quarters at 620 companies, three patterns:
--   1. The same quarter, end date off by a day: NVIDIA's Q2 FY2011 cost of revenue is
--      2010-05-03..07-31 in a 10-K and ..08-01 in the 10-Q, same value; Worthington's revenue
--      2023-12-01..2024-02-28 (derived) and ..02-29 (reported).
--   2. Two calendars interleaved: a company re-labels last year's quarters in this year's
--      comparatives. ATI has 2023-01-01..03-31 (its 10-Q) and 2023-01-02..04-02 (the next year's
--      10-Q), 04-01..06-30 and 04-03..07-02, ...; Dine Brands 2025-01-01..03-31 = 4,598,000
--      and 2024-12-30..2025-03-30 = 4,600,000.
--   3. Fiscal vs calendar-month quarters around a change of fiscal year: TranSuite.org
--      2023-12-01..2024-02-29 and 2024-01-01..03-31 (different values).
-- Both rows of a pair count in charts and break TTM sums (four contiguous quarters).
--
-- Rule (gold.refresh_quarterly_financials, duration rows only; instants are points and keep
-- 029's one-row-per-date choice):
--   a. Calendar votes. For each company and date, `instants` = how many metrics have a
--      balance-sheet (instant) fact on that date, `metrics` = how many metrics have any fact
--      ending on it (silver facts gold reads: mapped tags, no DEF/PRE forms). A candidate
--      quarter scores the votes of its last day plus those of the day before its first day:
--      a quarter on the company's own calendar starts and ends on dates the company closed its
--      books on, which its balance sheets carry. A re-labelled comparative never has a balance
--      sheet of its own.
--   b. Ranking, within (company, metric), best first: instant votes DESC, metric votes DESC, then
--      029's order (tag priority, reported before derived, filed DESC, accession DESC,
--      source accessions DESC, period_start DESC, source tag, value, ingested_at DESC), then
--      period_end DESC. Total, so refreshes stay byte-identical. The same order picks the one
--      row per period_end first (029's DISTINCT ON, now with the votes in front).
--   c. Conflict: two duration rows overlapping by more than 7 days. Quarters are 80-120 days,
--      so such rows can't both belong to one series; up to 7 days is tolerated, as
--      gold.refresh_derived_financials tolerates 0-7 day gaps (filers that tag a quarter's start
--      a day or two early, 2010-12-30..2011-03-31 after a quarter ending 12-31, are one series).
--   d. Greedy in rank order: a row is kept unless it conflicts with a row already kept. Only
--      rows with a conflict take part, so a clean company's gold is unchanged. Exact (no row is
--      lost to a chain A beats B beats C): a recursive CTE walks each (company, metric)'s
--      conflicting rows best first, carrying the kept periods.
-- Derived quarters never mix series: a year-to-date difference (and Q4 = FY - 9M) takes both
-- inputs from one period_start chain (029), and two calendars start on different days; the
-- start-day vote then prefers the derived Q4 of the chain whose earlier quarters it keeps (ATI
-- 2022: Q4 = 130.9M FY - 54.0M 9M, not the restated, re-labelled 10-03..2023-01-01 = 193.0M).
--
-- Measured on hermes before writing this (read-only simulation over the 620 companies' gold
-- rows): 11,041 overlaps → 1,686, all of 1-7 days (mis-tagged starts, kept on purpose), at 244
-- companies; ~6,400 rows dropped. All examples above resolve to the series with balance sheets.
-- Trade-off: when a company re-labels AND restates last year's quarters (ATI 2022-2023), gold
-- keeps the original filing's labels and values; a restatement that keeps the dates still wins
-- (latest filed), as before.
--
-- Everything else is 029's body (024's DEF/PRE filter, instants only for non-additive metrics,
-- no negative derived quarter for must-be-positive metrics). Existing companies change on
-- `facts-fetcher --rebuild` (make financials-rebuild), which production needs after deploy.

CREATE OR REPLACE FUNCTION gold.refresh_quarterly_financials(p_company_ids text[])
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('gold.quarterly_financials'), hashtext(id))
    FROM (SELECT DISTINCT id FROM unnest(p_company_ids) id ORDER BY id) ids;

    DELETE FROM gold.quarterly_financials WHERE company_id = ANY(p_company_ids);

    WITH RECURSIVE facts AS (
        SELECT k.company_id, f.*, t.taxonomy, t.priority, m.additive
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.metric = f.metric AND t.tag = f.tag
        JOIN silver.metrics m ON m.metric = f.metric AND m.unit = f.unit
        WHERE k.company_id = ANY(p_company_ids)
          -- proxy and information statements (DEF 14A, DEFA14A, DEFR14A, DEFM14A,
          -- DEF 14C, PRE 14A, ...): pay-versus-performance net income, rounded
          AND coalesce(f.form, '') !~ '^(DEF|PRE)'
    ),
    best AS (
        SELECT DISTINCT ON (company_id, metric, period_start, period_end) *
        FROM facts
        ORDER BY company_id, metric, period_start, period_end, priority, filed DESC,
                 accession_no DESC, cik
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
    candidates AS (
        SELECT company_id, metric, period_start, period_end, value, unit,
               'reported' AS value_source, taxonomy || ':' || tag AS source_tag, accession_no,
               form, filed, ARRAY[accession_no] AS source_accessions, ingested_at, priority
        FROM best
        -- instants are balance-sheet values; a flow metric tagged at an instant (UnitedHealth
        -- tags each dividend payment on its payment date) is not a quarter
        WHERE period_type = 'quarter' OR (period_type = 'instant' AND NOT additive)
        UNION ALL
        SELECT company_id, metric, prev_end + 1, period_end, value - prev_value, unit,
               'derived', taxonomy || ':' || tag, accession_no, form, filed,
               ARRAY[prev_accession, accession_no], greatest(ingested_at, prev_ingested_at),
               greatest(priority, prev_priority)
        FROM year_to_date
        WHERE additive AND period_end - prev_end BETWEEN 80 AND 120
          -- a derived quarter of a metric that can't be negative came out negative: one of its
          -- two inputs is wrong (Cytek's 2024 10-K re-tagged 2022 Revenues as 2,963,000 against
          -- 164,036,000 everywhere else). Leave the quarter missing rather than publish it.
          AND NOT (value - prev_value < 0 AND metric IN (
              'revenue', 'cost_of_revenue', 'research_and_development', 'sga', 'capex',
              'depreciation_amortization', 'stock_based_compensation', 'share_buybacks',
              'dividends_paid'))
    ),
    -- calendar votes per company and date: metrics with a balance-sheet value on it, and
    -- metrics with any fact ending on it
    votes AS (
        SELECT company_id, period_end AS day,
               count(DISTINCT metric) FILTER (WHERE period_type = 'instant') AS instants,
               count(DISTINCT metric) AS metrics
        FROM facts
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
    RETURN n;
END
$$;
