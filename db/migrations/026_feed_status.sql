-- ops.feed_status(): when the newest filing was accepted by EDGAR and when it reached bronze.
-- The Query API's public /v1/status serves these two timestamps so an outside uptime check
-- (.github/workflows/uptime.yml) can tell a stalled pipeline from a quiet EDGAR. query_api
-- can't read bronze.filings, and silver.filings (a UNION view) takes ~3.5 s for the max, so
-- this is a SECURITY DEFINER function returning only the two timestamps (no filing, company or
-- payload), the same pattern as 022.

CREATE FUNCTION ops.feed_status(OUT last_accepted_at timestamptz, OUT last_ingested_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$ SELECT max(filed_at), max(ingested_at) FROM bronze.filings $$;

REVOKE ALL ON FUNCTION ops.feed_status() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.feed_status() TO query_api;  -- USAGE on ops: 023
