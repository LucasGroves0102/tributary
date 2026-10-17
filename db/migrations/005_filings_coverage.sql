-- How much of each day's filings the live poller caught before the daily-index backfill
-- filled the rest. Days are EDGAR dates (US Eastern). Filings accepted after 5:30pm ET carry
-- the next business day's date in the index but count on their acceptance day here, so a
-- day's total can differ slightly from the index row count.
CREATE VIEW bronze.filings_daily_coverage AS
SELECT (filed_at AT TIME ZONE 'America/New_York')::date AS day,
       count(*) AS total,
       count(*) FILTER (WHERE coalesce(payload->>'source', 'feed') = 'feed') AS via_feed,
       count(*) FILTER (WHERE payload->>'source' = 'daily-index') AS via_backfill,
       round(100.0 * count(*) FILTER (WHERE coalesce(payload->>'source', 'feed') = 'feed')
             / count(*), 1) AS live_coverage_pct
FROM bronze.filings
GROUP BY 1;
