-- Golden 10-K figures (tests/golden) found two gaps in gold.quarterly_financials:
--
--   1. Proxy statements override the 10-K. Since 2023 a DEF 14A carries pay-versus-performance
--      XBRL: net income for five fiscal years, rounded (JPMorgan 57.0 billion for 2025 against
--      57,048 million in its 10-K; Home Depot 14.16 billion against 14,156 million). Filed
--      weeks after the 10-K, it won rule 1 ("most recently filed"), so the derived fiscal Q4
--      (year minus nine months) and every TTM built on it were off by the rounding. On hermes
--      6,387 gold rows of 1,669 companies cited a DEF 14A. Gold now ignores facts from proxy
--      and information statements (forms starting DEF or PRE); silver keeps them, as it keeps
--      every reported value. The annual_sum reference in ops.check_financials (023) may still
--      pick a proxy figure; its 1% tolerance is far above the proxy rounding, so it raises no
--      false findings and is left as it is.
--   2. Costco tags pre-tax income in its 10-Qs only as IncomeLossAttributableToParent
--      ("Income (Loss) Attributable to Parent, before Tax"), so no quarter, and no TTM, had
--      pretax_income. Added as the third pretax_income tag (includes discontinued operations,
--      so it ranks after both continuing-operations tags).
--
-- gold.refresh_quarterly_financials: 015's body with the form filter in `facts`; rules
-- otherwise unchanged. Existing companies change on `facts-fetcher --rebuild`
-- (make financials-rebuild), which re-projects bronze for the new tag and rebuilds gold.

INSERT INTO silver.metric_tags (metric, tag, priority) VALUES
    ('pretax_income', 'IncomeLossAttributableToParent', 3);

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
        WHERE period_type IN ('quarter', 'instant')
        UNION ALL
        SELECT company_id, metric, prev_end + 1, period_end, value - prev_value, unit,
               'derived', taxonomy || ':' || tag, accession_no, form, filed,
               ARRAY[prev_accession, accession_no], greatest(ingested_at, prev_ingested_at),
               greatest(priority, prev_priority)
        FROM year_to_date
        WHERE additive AND period_end - prev_end BETWEEN 80 AND 120
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
