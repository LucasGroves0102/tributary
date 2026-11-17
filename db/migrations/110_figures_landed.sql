-- When the nightly figures last landed, for saved screens that alert (Query API
-- query_api/watches.py, app migration 024).
--
-- A watched screen is evaluated once after each night's gold build, not every ten minutes: the
-- alerts job (query-api-alerts) asks this function and evaluates a watch whose last check is older
-- than its answer. The nightly financials-bulk job's last step is the coverage report
-- (facts_fetcher/bulk.py: extremes, then ops.refresh_coverage, 076), which runs once gold is
-- current for the night, changed zip or not; its newest ops.coverage_runs row is therefore "the
-- figures landed". NULL before any run (a new database, CI): the job then falls back to a check a
-- day (watches.FALLBACK).
--
-- DEFINER because query_api has no grant on ops.coverage_runs; it returns one timestamp and
-- nothing else (aggregate-only, like ops.public_coverage_gaps), search_path pinned, EXECUTE only
-- query_api. run_at is the table's primary key: max() is one index probe.
--
-- Locks on hermes (lock_timeout 3 s): CREATE FUNCTION only; no table is locked, read or rewritten.

SET LOCAL lock_timeout = '3s';

CREATE FUNCTION ops.figures_landed_at() RETURNS timestamptz
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
    SELECT max(r.run_at) FROM ops.coverage_runs r
$$;
REVOKE ALL ON FUNCTION ops.figures_landed_at() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.figures_landed_at() TO query_api;
