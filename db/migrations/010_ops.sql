-- FR11: freshness and pipeline-health views for the Grafana dashboard and the ops exporter.
--
-- Readers get the ops_reader group role: SELECT on these views only, not on the tables behind
-- them (views run with their owner's privileges). They expose timestamps, counts and filing
-- identifiers, never document text or financial values. Login roles are created on the host.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_reader') THEN
        CREATE ROLE ops_reader NOLOGIN;
    END IF;
END
$$;

CREATE SCHEMA ops;

-- One row per filing seen on the live feed, with when it reached each stage. Daily-index
-- (backfill) and history-load rows have no exact acceptance time to measure from, so only
-- source = 'feed' counts. Stage times are NULL until reached, and stay NULL for filings the
-- pipeline doesn't fetch (untracked companies, other forms).
--   document_at: primary document stored (tracked 10-K / 10-Q / 8-K)
--   sections_at: parsed sections queryable (gold.filing_sections); a re-parse moves it
--   facts_at:    XBRL facts from this filing in silver (companyfacts API, 10-K / 10-Q)
CREATE VIEW ops.filing_latency AS
WITH filings AS (
    SELECT f.accession_no,
           f.form_type AS form,
           f.filed_at AS accepted_at,
           min(f.ingested_at) AS bronze_at,
           min(c.company_id) FILTER (WHERE c.tracked) AS company_id
    FROM bronze.filings f
    LEFT JOIN silver.company_ciks k ON k.cik = f.cik
    LEFT JOIN silver.companies c ON c.company_id = k.company_id
    WHERE f.payload->>'source' = 'feed'
    GROUP BY f.accession_no, f.form_type, f.filed_at
)
SELECT f.accession_no,
       f.form,
       f.company_id,
       c.name AS company,
       f.accepted_at,
       f.bronze_at,
       d.fetched_at AS document_at,
       s.sections_at,
       x.facts_at,
       least(s.sections_at, x.facts_at) AS queryable_at,
       extract(epoch FROM f.bronze_at - f.accepted_at)::float8 AS bronze_lag_s,
       extract(epoch FROM s.sections_at - f.accepted_at)::float8 AS sections_lag_s,
       extract(epoch FROM x.facts_at - f.accepted_at)::float8 AS facts_lag_s,
       extract(epoch FROM least(s.sections_at, x.facts_at) - f.accepted_at)::float8
           AS queryable_lag_s
FROM filings f
LEFT JOIN silver.companies c ON c.company_id = f.company_id
LEFT JOIN bronze.filing_documents d ON d.accession_no = f.accession_no
LEFT JOIN LATERAL (
    SELECT min(parsed_at) AS sections_at
    FROM silver.filing_sections
    WHERE accession_no = f.accession_no AND f.company_id IS NOT NULL
) s ON true
LEFT JOIN LATERAL (
    SELECT min(ingested_at) AS facts_at
    FROM silver.financial_facts
    WHERE accession_no = f.accession_no AND f.company_id IS NOT NULL
) x ON true;

-- Distinct filings per EDGAR day (US Eastern) and form, all sources.
CREATE VIEW ops.filings_daily AS
SELECT (f.filed_at AT TIME ZONE 'America/New_York')::date AS day,
       f.form_type AS form,
       count(DISTINCT f.accession_no) AS filings,
       count(DISTINCT f.accession_no) FILTER (WHERE c.tracked) AS tracked_filings
FROM bronze.filings f
LEFT JOIN silver.company_ciks k ON k.cik = f.cik
LEFT JOIN silver.companies c ON c.company_id = k.company_id
GROUP BY 1, 2;

CREATE VIEW ops.filings_daily_coverage AS
SELECT day, total, via_feed, via_backfill, live_coverage_pct
FROM bronze.filings_daily_coverage;

-- One row per tracked company: 10-K / 10-Q filings on EDGAR (bronze.filings, any source)
-- that the companyfacts API doesn't reflect yet. A filing counts as missing when none of our
-- facts cite its accession number and it was accepted on or after the latest 10-K / 10-Q
-- filing date in our facts. behind_seconds is the age of the oldest missing filing, 0 when up to date.
-- companyfacts_checked_at after the missing filing's acceptance means the API lags EDGAR,
-- not our fetch.
CREATE VIEW ops.companyfacts_lag AS
WITH facts AS (
    SELECT DISTINCT ON (k.company_id)
           k.company_id, f.accession_no AS latest_accession, f.form AS latest_form,
           f.filed AS latest_filed
    FROM silver.financial_facts f
    JOIN silver.company_ciks k ON k.cik = f.cik
    WHERE f.form IN ('10-K', '10-Q', '10-K/A', '10-Q/A')  -- proxies (DEF 14A) carry XBRL too
    ORDER BY k.company_id, f.filed DESC, f.accession_no DESC
),
checked AS (
    SELECT k.company_id, max(b.last_checked_at) AS checked_at
    FROM bronze.company_facts b
    JOIN silver.company_ciks k ON k.cik = b.cik
    GROUP BY k.company_id
),
edgar AS (
    SELECT k.company_id, f.accession_no, f.form_type AS form, min(f.filed_at) AS accepted_at
    FROM bronze.filings f
    JOIN silver.company_ciks k ON k.cik = f.cik
    WHERE f.form_type IN ('10-K', '10-Q')
    GROUP BY k.company_id, f.accession_no, f.form_type
),
missing AS (
    SELECT e.company_id,
           count(*) AS missing_filings,
           min(e.accepted_at) AS oldest_missing_at,
           (array_agg(e.accession_no ORDER BY e.accepted_at DESC))[1] AS newest_missing_accession,
           (array_agg(e.form ORDER BY e.accepted_at DESC))[1] AS newest_missing_form
    FROM edgar e
    LEFT JOIN facts fa ON fa.company_id = e.company_id
    WHERE (e.accepted_at AT TIME ZONE 'America/New_York')::date
              >= coalesce(fa.latest_filed, '-infinity'::date)
      AND NOT EXISTS (
          SELECT 1 FROM silver.financial_facts x WHERE x.accession_no = e.accession_no
      )
    GROUP BY e.company_id
)
SELECT c.company_id,
       c.name AS company,
       fa.latest_accession AS facts_latest_accession,
       fa.latest_form AS facts_latest_form,
       fa.latest_filed AS facts_latest_filed,
       ch.checked_at AS companyfacts_checked_at,
       coalesce(m.missing_filings, 0) AS missing_filings,
       m.newest_missing_accession,
       m.newest_missing_form,
       m.oldest_missing_at,
       coalesce(extract(epoch FROM now() - m.oldest_missing_at), 0)::float8 AS behind_seconds
FROM silver.companies c
LEFT JOIN facts fa ON fa.company_id = c.company_id
LEFT JOIN checked ch ON ch.company_id = c.company_id
LEFT JOIN missing m ON m.company_id = c.company_id
WHERE c.tracked;

GRANT USAGE ON SCHEMA ops TO ops_reader;
GRANT SELECT ON ops.filing_latency, ops.filings_daily, ops.filings_daily_coverage,
    ops.companyfacts_lag TO ops_reader;
