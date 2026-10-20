-- XBRL financials for every SEC filer: the nightly companyfacts.zip sweep next to the
-- per-company API fetch. Both write the same bronze rows and share set-based refreshes.

-- Bronze per distinct payload: payload holds only the facts of tags in silver.metric_tags
-- (what the transforms read); raw_gzip holds the whole payload as fetched, so a change to
-- metric_tags can be re-projected without the SEC. raw_gzip is kept for each CIK's latest
-- payload only; older versions keep their hash, timestamps and projected facts.
-- Rows from before this migration have the whole payload in payload and no raw_gzip.
ALTER TABLE bronze.company_facts
    ADD COLUMN raw_gzip   bytea,
    ADD COLUMN size_bytes bigint,  -- uncompressed size of the payload as fetched
    ADD COLUMN source     text NOT NULL DEFAULT 'api' CHECK (source IN ('api', 'bulk'));

-- latest payload per CIK: the one most recently seen
CREATE INDEX company_facts_latest ON bronze.company_facts (cik, last_checked_at DESC);

-- Progress of the bulk load, one row per source (the SEC URL, or file:// for a local zip).
-- A load of one version of the file (last_modified) spans several runs, each committing
-- batches of entries in name order; cursor is the last entry of the last committed batch.
-- completed_at is set when a run reaches the end, and only then is last_modified sent as
-- If-Modified-Since, so a partly loaded version is always downloaded again.
CREATE TABLE bronze.bulk_loads (
    source        text        PRIMARY KEY,
    last_modified text,                    -- version being loaded, or last loaded
    cursor        text,                    -- last committed entry; NULL = not started
    entries       integer     NOT NULL DEFAULT 0,  -- entries read in this version so far
    changed       integer     NOT NULL DEFAULT 0,  -- of those, with a new payload
    started_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    completed_at  timestamptz
);

-- Re-derive silver rows for many CIKs from each one's latest bronze payload. Idempotent.
CREATE FUNCTION silver.refresh_financial_facts(p_ciks text[]) RETURNS integer
LANGUAGE sql AS $$
  WITH latest AS (
    SELECT DISTINCT ON (cik) cik, payload FROM bronze.company_facts
    WHERE cik = ANY(p_ciks)
    ORDER BY cik, last_checked_at DESC, fetched_at DESC
  ),
  inserted AS (
    INSERT INTO silver.financial_facts
        (cik, metric, tag, unit, period_start, period_end, value, accession_no, form, filed, frame)
    SELECT l.cik, t.metric, t.tag, u.key,
           (r->>'start')::date, (r->>'end')::date, (r->>'val')::numeric,
           r->>'accn', r->>'form', (r->>'filed')::date, r->>'frame'
    FROM latest l
    CROSS JOIN silver.metric_tags t
    JOIN silver.metrics m ON m.metric = t.metric
    CROSS JOIN LATERAL jsonb_each(l.payload->'facts'->t.taxonomy->t.tag->'units') u
    CROSS JOIN LATERAL jsonb_array_elements(u.value) r
    WHERE u.key = m.unit
    ON CONFLICT DO NOTHING
    RETURNING 1
  )
  SELECT count(*)::int FROM inserted
$$;

CREATE OR REPLACE FUNCTION silver.refresh_financial_facts(p_cik text) RETURNS integer
LANGUAGE sql AS $$ SELECT silver.refresh_financial_facts(ARRAY[p_cik]) $$;

-- Rebuild many companies' gold rows from silver. Rules:
--   1. per period, take the highest-priority tag, then the most recently filed value
--      (restatements win; originals stay in silver)
--   2. reported quarters and instants are used as-is; instants are never derived
--   3. quarters of additive metrics are derived from consecutive year-to-date values that
--      share a start date, when they are a quarter (80-120 days) apart; a derived value
--      ranks as the worse of its two tags
--   4. per quarter: the preferred tag, then reported over derived, then the latest filing
-- Takes a per-company advisory lock, in company_id order, so the live fetch and the nightly
-- sweep never rebuild the same company at once.
CREATE FUNCTION gold.refresh_quarterly_financials(p_company_ids text[]) RETURNS integer
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
                 accession_no DESC
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
             filed DESC;

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END
$$;

CREATE OR REPLACE FUNCTION gold.refresh_quarterly_financials(p_company_id text)
RETURNS integer
LANGUAGE sql AS $$ SELECT gold.refresh_quarterly_financials(ARRAY[p_company_id]) $$;

-- Companies with silver facts but no gold rows: a CIK mapped to a company after its facts
-- were loaded. The nightly sweep refreshes these too.
CREATE VIEW gold.financials_pending AS
SELECT DISTINCT k.company_id
FROM silver.company_ciks k
WHERE EXISTS (SELECT 1 FROM silver.financial_facts f WHERE f.cik = k.cik)
  AND NOT EXISTS (SELECT 1 FROM gold.quarterly_financials g WHERE g.company_id = k.company_id);
