-- ops.filing_completeness: how complete the filing index is against EDGAR's own daily master
-- indexes. poller-backfill (nightly, a rolling window of days) compares every (accession, filer)
-- row of each day's index with bronze.filings and bronze.submission_filings, republishes the
-- missing ones to filings.raw and records the day here. A day is checked every night while it is
-- in the window, so `missing` is what that night's check found: after a republish the next check
-- shows 0. Counts only, no filing or company data, so ops_reader may read it.

CREATE TABLE ops.filing_completeness (
    day         date PRIMARY KEY,         -- the master index's date
    indexed     integer NOT NULL,         -- distinct accessions in that day's index
    missing     integer NOT NULL,         -- of those, accessions with a filer absent from bronze
    republished integer NOT NULL,         -- (accession, filer) events republished to filings.raw
    checked_at  timestamptz NOT NULL DEFAULT now()
);

CREATE VIEW ops.filing_completeness_status AS
SELECT day, indexed, missing, republished, checked_at FROM ops.filing_completeness;

GRANT SELECT ON ops.filing_completeness_status TO ops_reader;
