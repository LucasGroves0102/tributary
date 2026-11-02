-- Data-quality fixes found by ops.check_financials (014) on hermes, 2026-11-02 (4.97M gold rows):
--
--   1. Phantom quarters. Gold took every 'instant' fact as a quarter row, whatever the metric.
--      For flow metrics that is wrong: UnitedHealth tags each dividend payment at its payment
--      date, so next to each real quarter gold had a second dividends_paid row with no
--      period_start (1,838 such rows, 597 companies), double in charts and the main source of
--      overlapping_quarters findings. Instants now count only for non-additive (balance-sheet,
--      share count) metrics, as silver.metrics.additive already distinguishes.
--   2. Impossible derived quarters. A derived quarter is a year-to-date figure minus the previous
--      one; when either input is wrong, the difference can be impossible (Cytek's 2024 10-K
--      re-tagged its 2022 Revenues as 2,963,000, so Q4 2022 came out at -112,737,000). For
--      metrics that can't be negative, a negative derived quarter is now left out (11,320 rows,
--      3,047 companies): a gap the quality checks show, instead of a wrong number. Reported
--      negative values stay (EQT's 2022 Q1 revenue really was negative, from hedging losses).
--
-- gold.refresh_quarterly_financials: 024's body with those two filters; everything else
-- unchanged. Existing companies change on `facts-fetcher --rebuild` (make financials-rebuild).

CREATE OR REPLACE FUNCTION gold.refresh_quarterly_financials(p_company_ids text[])
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('gold.quarterly_financials'), hashtext(id))
    FROM (SELECT DISTINCT id FROM unnest(p_company_ids) id ORDER BY id) ids;

    DELETE FROM gold.quarterly_financials WHERE company_id = ANY(p_company_ids);

    WITH facts AS (
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
    )
    INSERT INTO gold.quarterly_financials
        (company_id, metric, period_start, period_end, value, unit, value_source, source_tag,
         accession_no, form, filed, source_accessions, ingested_at)
    SELECT DISTINCT ON (company_id, metric, period_end)
           company_id, metric, period_start, period_end, value, unit, value_source,
           source_tag, accession_no, form, filed, source_accessions, ingested_at
    FROM candidates
    ORDER BY company_id, metric, period_end, priority, value_source = 'reported' DESC,
             filed DESC, accession_no DESC, source_accessions DESC, period_start DESC,
             source_tag, value, ingested_at DESC;

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END
$$;
