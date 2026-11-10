-- The alerts job's runs, so a user's alert_checks row needn't be rewritten every ten minutes.
--
-- Until now the job (query_api/alert_job.py) upserted every following user's alert_checks row on
-- every run, a new row version per user every ten minutes whether or not anything changed. Now it
-- writes a user's row only when something did (filings_through moved, notifications were made or
-- became ready) or the row is an hour old, and records each run once here. What the Monitor step,
-- the alerts page and the settings show as "last checked" is the newer of the user's row and the
-- last run, the run's only when it was complete (every planned user checked: none deferred to the
-- next run, none failed), so a user a run didn't reach never reads as checked by it.
--
-- One row (`run` is always true): when the last run started (`checked_at`, the same clock as
-- alert_checks.checked_at) and whether it was complete. Not per user and holds no user data: no
-- row-level security, like public_catalogs (008); not streamed (Debezium's include list names its
-- tables), REPLICA IDENTITY default.
--
-- Lock impact (hermes): CREATE TABLE only; no existing table is locked, read or rewritten.

SET LOCAL lock_timeout = '5s';

CREATE TABLE alert_runs (
    run        boolean     PRIMARY KEY DEFAULT true CHECK (run),
    checked_at timestamptz NOT NULL,
    complete   boolean     NOT NULL
);
