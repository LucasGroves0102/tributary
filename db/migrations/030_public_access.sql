-- Open access (Phase 9 A), layer 2: RLS also accepts companies in a public catalog.
--
-- Two tables, written only by authz-sync (same desired state as its OpenFGA tuples):
--   authz.public_access (company_id, tier): every catalog company x both tiers, while the app
--     database's public_catalogs row says public (app migration 008); catalog membership from
--     authz.catalog_companies(). Empty while the catalog is closed, so this migration changes
--     no one's access. ~12,000 rows, not users x companies.
--   authz.user_tiers (user_id, tier): the tiers of each user's plan (subscriptions x
--     plan_tiers), so a public company is readable only within the user's tiers, as in OpenFGA
--     (viewer AND subscriber). A user without a subscription reads nothing public.
--
-- The rule now lives in one function, authz.readable_companies(tier): the companies the
-- session's app.user_id may read for that tier = its authz.user_access rows (private grants,
-- tier already applied) plus authz.public_access if the user has that tier. No app.user_id
-- (unset or '') = nothing, so "public" means every signed-in user, never an anonymous session.
-- The nine policies (granted_financials x5, granted_filings x4) filter with
--
--     company_id IN (SELECT r FROM authz.readable_companies('<tier>') r)
--
-- Why a set and not a per-row boolean: Postgres never inlines a scalar SQL function whose body
-- has a subquery, so a per-row authz.can_read(company_id, tier) policy runs the function once
-- per row (measured on 1.2M rows: 2.4 s vs 0.05 s). The set-returning function is a single
-- SELECT, so the planner inlines it into the uncorrelated IN, which becomes a hashed SubPlan
-- built once per query (~6,000 ids: ~1-2 ms) and probed per row, parallel-safe. authz.can_read
-- is the same rule for one company (tests, ad hoc checks), defined on top of the set.
--
-- granted_filings on silver.filing_sections keeps 007's join: the section's document
-- (bronze.filing_documents by accession_no) → its filer CIK → silver.company_ciks → company.
--
-- Local EXPLAIN ANALYZE (6,000 companies, 1.2M gold rows, 240k sections; hermes to be measured):
-- one company's financials 0.2 → 2.3 ms with the catalog public (the hash of 6,000 ids), 0.5 ms
-- for a user with only private grants; one period across all companies 58 → 74-137 ms; one
-- company's sections 4.5 → 4.2 ms.

CREATE TABLE authz.public_access (
    company_id text        NOT NULL,
    tier       text        NOT NULL CHECK (tier IN ('financials', 'filings')),
    synced_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, tier)
);

-- Not per-user data (it is the catalog's company ids), but only signed-in sessions see it, so
-- nothing that reads it directly can treat a session without a user as public.
ALTER TABLE authz.public_access ENABLE ROW LEVEL SECURITY;
CREATE POLICY signed_in ON authz.public_access FOR SELECT TO query_api
    USING (coalesce(current_setting('app.user_id', true), '') <> '');
GRANT SELECT ON authz.public_access TO query_api;

CREATE TABLE authz.user_tiers (
    user_id   text        NOT NULL,
    tier      text        NOT NULL CHECK (tier IN ('financials', 'filings')),
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, tier)
);
ALTER TABLE authz.user_tiers ENABLE ROW LEVEL SECURITY;
CREATE POLICY own_rows ON authz.user_tiers FOR SELECT TO query_api
    USING (user_id = current_setting('app.user_id', true));
GRANT SELECT ON authz.user_tiers TO query_api;

-- The read rule. SECURITY INVOKER (the default, stated): the own_rows / signed_in policies of
-- the three authz tables still apply to query_api. Keep it a single SELECT (inlinable):
-- no SET options, no plpgsql, no SECURITY DEFINER, or every policy gets slow.
CREATE FUNCTION authz.readable_companies(tier text) RETURNS SETOF text
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT a.company_id
    FROM authz.user_access a
    WHERE coalesce(current_setting('app.user_id', true), '') <> ''
      AND a.user_id = current_setting('app.user_id', true)
      AND a.tier = readable_companies.tier
    UNION ALL
    SELECT p.company_id
    FROM authz.public_access p
    WHERE coalesce(current_setting('app.user_id', true), '') <> ''
      AND p.tier = readable_companies.tier
      AND EXISTS (SELECT 1 FROM authz.user_tiers t
                  WHERE t.user_id = current_setting('app.user_id', true)
                    AND t.tier = readable_companies.tier)
$$;

CREATE FUNCTION authz.can_read(company_id text, tier text) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT EXISTS (SELECT 1 FROM authz.readable_companies(can_read.tier) r
                   WHERE r = can_read.company_id)
$$;

-- Catalog membership for authz-sync: every company with gold financials or a stored filing
-- document (catalog:sec). Company ids only; authz-sync runs it every few minutes.
CREATE FUNCTION authz.catalog_companies() RETURNS TABLE (company_id text)
LANGUAGE sql STABLE AS $$
    SELECT company_id FROM gold.financials_coverage
    UNION
    SELECT k.company_id
    FROM (SELECT DISTINCT cik FROM bronze.filing_documents) d
    JOIN silver.company_ciks k ON k.cik = d.cik
$$;
REVOKE EXECUTE ON FUNCTION authz.catalog_companies() FROM PUBLIC;

-- The nine policies, same names, roles and commands; only the rule changes.
ALTER POLICY granted_financials ON gold.quarterly_financials
    USING (company_id IN (SELECT r FROM authz.readable_companies('financials') r));
ALTER POLICY granted_financials ON gold.derived_financials
    USING (company_id IN (SELECT r FROM authz.readable_companies('financials') r));
ALTER POLICY granted_financials ON gold.segment_financials
    USING (company_id IN (SELECT r FROM authz.readable_companies('financials') r));
ALTER POLICY granted_financials ON ops.quality_companies
    USING (company_id IN (SELECT r FROM authz.readable_companies('financials') r));
ALTER POLICY granted_financials ON ops.quality_findings
    USING (company_id IN (SELECT r FROM authz.readable_companies('financials') r));

-- A scalar subquery, not 007's EXISTS: inside an EXISTS the planner pulls the IN up into a
-- join it re-scans per section row (11 ms vs 4 ms for one company's sections in a local test);
-- at the top of the qual the IN stays a hashed SubPlan built once. Same rows: accession_no is
-- bronze.filing_documents' key and cik silver.company_ciks', so the subquery yields at most one
-- company (none: NULL IN (...) is not true, the row is hidden).
ALTER POLICY granted_filings ON silver.filing_sections
    USING ((SELECT k.company_id
            FROM bronze.filing_documents d
            JOIN silver.company_ciks k ON k.cik = d.cik
            WHERE d.accession_no = filing_sections.accession_no)
           IN (SELECT r FROM authz.readable_companies('filings') r));
ALTER POLICY granted_filings ON gold.section_search
    USING (company_id IN (SELECT r FROM authz.readable_companies('filings') r));
ALTER POLICY granted_filings ON gold.section_diffs
    USING (company_id IN (SELECT r FROM authz.readable_companies('filings') r));
ALTER POLICY granted_filings ON gold.section_changes
    USING (company_id IN (SELECT r FROM authz.readable_companies('filings') r));
