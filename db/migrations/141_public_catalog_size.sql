-- One company count everywhere (Riley, test 10, finding 36): the front door and Data status said
-- "6,625 companies" (with quarterly financials, 028's ops.public_coverage) while the sidebar said
-- 6,630 (the companies a user can read: the public catalog, every company with gold financials,
-- quarterly or annual, or a stored filing's text). The status page now says the catalog's size too,
-- so the front door, Data status and the sidebar can show the same number.
--
-- ops.public_catalog_size(): how many companies the public catalog holds (authz.public_access,
-- written by authz-sync from authz.catalog_companies(); 0 while the catalog is closed). Aggregate
-- only, like the other ops.public_* functions: DEFINER because query_api reads public_access only in
-- a session with a user (its policy), one count and nothing else, search_path pinned, EXECUTE only
-- query_api. The primary key (company_id, tier) serves it: 2 ms on hermes (6,630, 2026-11-18).
--
-- Locks on hermes (lock_timeout 3 s): CREATE FUNCTION only; no table is locked, read or rewritten.

SET LOCAL lock_timeout = '3s';

CREATE FUNCTION ops.public_catalog_size() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
    SELECT count(DISTINCT p.company_id) FROM authz.public_access p
$$;
REVOKE ALL ON FUNCTION ops.public_catalog_size() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.public_catalog_size() TO query_api;
