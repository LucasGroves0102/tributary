-- Freshness (Avery's systems review, batch 3): the hourly recheck (poller-recheck) and what it
-- means for the freshness figures.
--
-- Measured on hermes (bronze.filings, 2026-10-20 to 2026-11-27): the live feed caught 95,639 of
-- 102,398 filings (93.4%); the rest came only from the nightly daily-index check, up to a day
-- later. The misses are timing, not form types (no form is always missed): bursts of more than
-- one feed page between two polls (2026-11-16, the 13F and 10-Q deadline: 2,797 of 7,869
-- filings), stalls (2026-11-19, 11:20 am-2 pm ET: up to an hour behind), and filings accepted
-- 5-10 pm ET that EDGAR lists only the next morning, below entries already seen (about 100-270
-- rows a normal weekday). The poller now pages; the recheck walks the same feed back to the
-- start of the previous filing day every hour and republishes what bronze lacks with
-- `source: recheck` (the feed's exact acceptance time and role).
--
--   ops.filing_rechecks       one row per hourly run: how far back the feed listed, rows
--                             listed, missing, republished (counts only; ops_reader reads the
--                             view, as 027's)
--   ops.public_catch()        the status page's split: filings caught by the live poller,
--                             found by the hourly recheck, found only by the nightly check
--   the coverage views        a via_recheck column (last), so total = feed + recheck +
--                             backfill again
--   silver.filings            recheck rows' acceptance time is exact, as feed and seed rows'
--
-- Only `feed` rows count in latency and live-coverage figures (ops.filing_latency,
-- ops.public_ingest: unchanged). Nothing stored is re-processed; no version is bumped.
--
-- Locks on hermes: CREATE OR REPLACE VIEW takes a brief ACCESS EXCLUSIVE on each view (no table
-- is rewritten, no index built); lock_timeout so a long reader of silver.filings fails this
-- migration (the deploy can be retried) instead of queueing every other reader behind it.

SET LOCAL lock_timeout = '30s';

CREATE TABLE ops.filing_rechecks (
    checked_at      timestamptz PRIMARY KEY,
    window_start    timestamptz NOT NULL,  -- midnight ET of the previous filing day
    reached_back_to timestamptz,           -- the oldest acceptance time the feed listed
    complete        boolean     NOT NULL,  -- the feed listed back to window_start
    pages           integer     NOT NULL,  -- feed pages read (SEC requests)
    listed          integer     NOT NULL,  -- (accession, filer) rows in the window
    missing         integer     NOT NULL,  -- accessions with a filer absent from bronze
    republished     integer     NOT NULL   -- (accession, filer) events republished
);

CREATE VIEW ops.filing_recheck_status AS
SELECT checked_at, window_start, reached_back_to, complete, pages, listed, missing, republished
FROM ops.filing_rechecks;

GRANT SELECT ON ops.filing_recheck_status TO ops_reader;

-- Per accession accepted in the last 7 days (public_ingest's window and count): caught live if
-- any filer row came from the feed, else by the recheck if one did, else by the nightly
-- daily-index check. Seed rows are none of these (filings - the three). The recheck's lag is
-- exact (the feed's acceptance time); the nightly's isn't (the index has days only).
CREATE FUNCTION ops.public_catch(
    OUT window_start timestamptz,
    OUT filings bigint,
    OUT via_feed bigint,
    OUT via_recheck bigint,
    OUT via_index bigint,
    OUT recheck_p50_seconds float8,   -- EDGAR acceptance → bronze, recheck-only filings
    OUT recheck_p95_seconds float8,
    OUT last_recheck_at timestamptz)  -- the latest hourly run
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
    WITH a AS (
        SELECT f.accession_no,
               bool_or(f.payload->>'source' = 'feed') AS live,
               bool_or(f.payload->>'source' = 'recheck') AS recheck,
               bool_or(f.payload->>'source' = 'daily-index') AS nightly,
               min(extract(epoch FROM f.ingested_at - f.filed_at)::float8)
                   FILTER (WHERE f.payload->>'source' = 'recheck') AS recheck_lag
        FROM bronze.filings f
        WHERE f.filed_at >= now() - interval '7 days'
        GROUP BY f.accession_no
    )
    SELECT now() - interval '7 days',
           count(*),
           count(*) FILTER (WHERE live),
           count(*) FILTER (WHERE recheck AND NOT live),
           count(*) FILTER (WHERE nightly AND NOT live AND NOT recheck),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY recheck_lag) FILTER (WHERE NOT live),
           percentile_cont(0.95) WITHIN GROUP (ORDER BY recheck_lag) FILTER (WHERE NOT live),
           (SELECT max(r.checked_at) FROM ops.filing_rechecks r)
    FROM a
$$;

REVOKE ALL ON FUNCTION ops.public_catch() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.public_catch() TO query_api;  -- USAGE on ops: 023

-- 016's body with a via_recheck column (last: CREATE OR REPLACE may only add columns).
CREATE OR REPLACE VIEW bronze.filings_daily_coverage AS
SELECT (filed_at AT TIME ZONE 'America/New_York')::date AS day,
       count(*) AS total,
       count(*) FILTER (WHERE coalesce(payload->>'source', 'feed') = 'feed') AS via_feed,
       count(*) FILTER (WHERE payload->>'source' = 'daily-index') AS via_backfill,
       round(100.0 * count(*) FILTER (WHERE coalesce(payload->>'source', 'feed') = 'feed')
             / count(*), 1) AS live_coverage_pct,
       count(*) FILTER (WHERE payload->>'source' = 'recheck') AS via_recheck
FROM bronze.filings
WHERE coalesce(payload->>'source', 'feed') <> 'seed'
GROUP BY 1;

CREATE OR REPLACE VIEW ops.filings_daily_coverage AS
SELECT day, total, via_feed, via_backfill, live_coverage_pct, via_recheck
FROM bronze.filings_daily_coverage;

-- 032's body; recheck rows join feed and seed rows in trusting their own acceptance time.
CREATE OR REPLACE VIEW silver.filings AS
SELECT f.accession_no,
       f.cik,
       k.company_id,
       f.form_type AS form,
       coalesce(h.filing_date, (f.filed_at AT TIME ZONE 'America/New_York')::date)
           AS filing_date,
       CASE WHEN coalesce(f.payload->>'source', 'feed') IN ('feed', 'seed', 'recheck')
            THEN f.filed_at
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
