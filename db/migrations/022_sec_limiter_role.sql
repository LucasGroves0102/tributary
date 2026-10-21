-- Least-privilege login for SEC callers (libs/sec). Every `uses: sec` pod only needs
-- ops.sec_take_token / ops.sec_report_throttle (019), but used the full database-url login for
-- them. sec_limiter is a NOLOGIN group that may run those two functions and read nothing: the
-- functions become SECURITY DEFINER (they run as their owner, who can update
-- ops.sec_rate_limit) with a fixed search_path, and PUBLIC loses EXECUTE on them, so other
-- roles (query_api, ops_reader, ...) don't gain a way to drain or block the shared bucket.
--
-- The login role and its password are created on the host by the owner, not here
-- (deploy/helm/tributary/README.md, "SEC limiter login"):
--   CREATE ROLE tributary_sec LOGIN PASSWORD '...' IN ROLE sec_limiter CONNECTION LIMIT 20;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sec_limiter') THEN
        CREATE ROLE sec_limiter NOLOGIN;
    END IF;
END
$$;

-- Both bodies only name schema-qualified objects (ops.*) and pg_catalog built-ins.
ALTER FUNCTION ops.sec_take_token(text, text)
    SECURITY DEFINER SET search_path = pg_catalog, pg_temp;
ALTER FUNCTION ops.sec_report_throttle(text)
    SECURITY DEFINER SET search_path = pg_catalog, pg_temp;

REVOKE ALL ON FUNCTION ops.sec_take_token(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION ops.sec_report_throttle(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.sec_take_token(text, text) TO sec_limiter;
GRANT EXECUTE ON FUNCTION ops.sec_report_throttle(text) TO sec_limiter;

-- Resolving ops.<function> needs USAGE on the schema; it grants nothing on ops' tables or views.
GRANT USAGE ON SCHEMA ops TO sec_limiter;
