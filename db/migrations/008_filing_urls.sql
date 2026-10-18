-- Filer CIK per filing for SEC links in financial citations. Only the identifying columns are
-- granted to query_api, not values.

CREATE INDEX financial_facts_accession ON silver.financial_facts (accession_no);

GRANT SELECT (cik, accession_no) ON silver.financial_facts TO query_api;
