-- Deterministic gold refreshes: candidates that tie on the 011 rules (e.g. two year-to-date
-- chains deriving the same quarter from one filing) are ordered by accession, then the rest of
-- the row, so repeated refreshes pick the same row. Rules otherwise unchanged from 011:
--   1. per period, take the highest-priority tag, then the most recently filed value
--      (restatements win; originals stay in silver)
--   2. reported quarters and instants are used as-is; instants are never derived
--   3. quarters of additive metrics are derived from consecutive year-to-date values that
--      share a start date, when they are a quarter (80-120 days) apart; a derived value
--      ranks as the worse of its two tags
--   4. per quarter: the preferred tag, then reported over derived, then the latest filing
--   5. remaining ties: the higher accession number, then the higher source accessions, the
--      later start and the tag; per period, the CIK (co-registrants file one accession)
-- gold.refresh_derived_financials (014) only reads gold.quarterly_financials through windows
-- ordered by keys unique per (company, metric), so it is deterministic once this is.
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
