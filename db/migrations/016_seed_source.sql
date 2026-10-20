-- doc-fetcher-seed events now carry source = 'seed' and the corrected acceptance time
-- (submissions' acceptanceDateTime is labelled UTC but runs ahead of it by the US Eastern
-- offset). Seed rows are history loads: not live feed, not daily-index backfill.

-- Earlier seed events had no source and the uncorrected time. They are the rows without a
-- source whose filed_at is exactly the Eastern offset (4 or 5 hours) after the bulk history's
-- corrected time; feed rows match it exactly. Mark them and correct filed_at (column and
-- payload). Rows without bulk history to compare against are left as they are.
UPDATE bronze.filings f
SET filed_at = h.accepted_at,
    payload = f.payload || jsonb_build_object(
        'source', 'seed',
        'filed_at', to_char(h.accepted_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'))
FROM bronze.submission_filings h
WHERE h.accession_no = f.accession_no AND h.cik = f.cik
  AND NOT f.payload ? 'source'
  AND f.payload->>'role' = 'Filer'
  AND f.filed_at - h.accepted_at IN (interval '4 hours', interval '5 hours');

-- How much of each day's filings the live poller caught before the daily-index backfill
-- filled the rest. Seed rows are neither, so they're not counted.
CREATE OR REPLACE VIEW bronze.filings_daily_coverage AS
SELECT (filed_at AT TIME ZONE 'America/New_York')::date AS day,
       count(*) AS total,
       count(*) FILTER (WHERE coalesce(payload->>'source', 'feed') = 'feed') AS via_feed,
       count(*) FILTER (WHERE payload->>'source' = 'daily-index') AS via_backfill,
       round(100.0 * count(*) FILTER (WHERE coalesce(payload->>'source', 'feed') = 'feed')
             / count(*), 1) AS live_coverage_pct
FROM bronze.filings
WHERE coalesce(payload->>'source', 'feed') <> 'seed'
GROUP BY 1;

-- As in 013, with seed rows' acceptance time treated as exact too. ops.filing_latency (010)
-- already counts only source = 'feed'.
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
       coalesce(f.payload->>'source', 'feed') AS source
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
       'submissions'
FROM bronze.submission_filings h
LEFT JOIN silver.company_ciks k ON k.cik = h.cik
WHERE NOT EXISTS (
    SELECT 1 FROM bronze.filings f WHERE f.accession_no = h.accession_no AND f.cik = h.cik
);
