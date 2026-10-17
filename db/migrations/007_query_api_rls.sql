-- FR8 layer 2: Postgres row-level security for the Query API.
--
-- The Query API checks OpenFGA first (layer 1). Its queries then run as the restricted
-- query_api role, with the caller's user ID in the transaction-local setting app.user_id:
--
--   SET LOCAL ROLE query_api;
--   SELECT set_config('app.user_id', '<user>', true);
--
-- Policies only return rows the user can read according to authz.user_access (kept in sync
-- by authz-sync). No user set means no rows. Table owners (the pipeline jobs) bypass RLS.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'query_api') THEN
        CREATE ROLE query_api NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA bronze, silver, gold, authz TO query_api;

-- Company identity and metric definitions are public metadata.
GRANT SELECT ON silver.companies, silver.company_ciks, silver.company_aliases, silver.metrics
    TO query_api;

-- Data the policies below filter.
GRANT SELECT ON gold.quarterly_financials, silver.filing_sections, gold.filing_sections,
    authz.user_access TO query_api;

-- Document metadata only: the stored HTML (content_gzip) is deliberately not granted.
GRANT SELECT (accession_no, cik, form, filed, period_of_report, document_name, url, fetched_at)
    ON bronze.filing_documents TO query_api;

-- Make the view apply the caller's privileges and policies, not its owner's.
ALTER VIEW gold.filing_sections SET (security_invoker = true);

ALTER TABLE authz.user_access ENABLE ROW LEVEL SECURITY;
CREATE POLICY own_rows ON authz.user_access FOR SELECT TO query_api
    USING (user_id = current_setting('app.user_id', true));

ALTER TABLE gold.quarterly_financials ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_financials ON gold.quarterly_financials FOR SELECT TO query_api
    USING (EXISTS (
        SELECT 1 FROM authz.user_access a
        WHERE a.user_id = current_setting('app.user_id', true)
          AND a.company_id = quarterly_financials.company_id
          AND a.tier = 'financials'
    ));

ALTER TABLE silver.filing_sections ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON silver.filing_sections FOR SELECT TO query_api
    USING (EXISTS (
        SELECT 1
        FROM bronze.filing_documents d
        JOIN silver.company_ciks k ON k.cik = d.cik
        JOIN authz.user_access a ON a.company_id = k.company_id
        WHERE d.accession_no = filing_sections.accession_no
          AND a.user_id = current_setting('app.user_id', true)
          AND a.tier = 'filings'
    ));
