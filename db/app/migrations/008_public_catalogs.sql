-- Open access (Phase 9 A): the switch that opens every company's data to every signed-in user.
--
-- A catalog is a set of companies authz-sync keeps in OpenFGA (company:<id>#catalog@catalog:<id>;
-- catalog:sec = every company with gold financials or parsed documents). While a catalog's row
-- here says public, authz-sync writes catalog:<id>#reader@user:* (layer 1) and authz.public_access
-- rows for its companies (layer 2, RLS), so every user with a session reads them, within their
-- plan's tiers. Per-company grants (company_grants) keep working for anything outside it.
--
-- Streamed by Debezium like every table that affects access, so the switch is replayed and
-- audited like any grant; never write the tuple by hand. It ships OFF: deploying this changes
-- nothing until the row is updated (db/app/README.md, "Open access"):
--
--   UPDATE public_catalogs SET public = true  WHERE catalog_id = 'sec';  -- open
--   UPDATE public_catalogs SET public = false WHERE catalog_id = 'sec';  -- close again
--
-- Plan tiers are not changed here: the free plan's filings tier ships with the flip (same
-- README section), so a deploy alone changes no one's access.

CREATE TABLE public_catalogs (
    catalog_id text    PRIMARY KEY CHECK (catalog_id ~ '^[a-z][a-z0-9_-]{0,31}$'),
    public     boolean NOT NULL
);

-- CDC: an update or delete event must carry the whole old row.
ALTER TABLE public_catalogs REPLICA IDENTITY FULL;

INSERT INTO public_catalogs (catalog_id, public) VALUES ('sec', false);
