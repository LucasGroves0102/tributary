-- SQL workspace (B3a): the schema `workspace` that POST /v1/sql reads, and the role it runs as.
--
-- A user's SELECT (checked by services/query-api sql_guard.py: one SELECT naming only the views and
-- functions below and allowlisted built-ins) runs in a READ ONLY transaction as query_api with the
-- caller's app.user_id set by the server, then `SET LOCAL ROLE workspace_reader`:
--   * workspace_reader is a NOLOGIN group with no other membership: SELECT on the workspace views,
--     EXECUTE on the workspace functions, and the grants their bodies read with (security invoker,
--     so the caller's rights and row-level security apply). It holds EXECUTE on no SECURITY DEFINER
--     function (they are all REVOKEd from PUBLIC and granted to query_api or sec_limiter only;
--     test_sql_workspace_db checks every one). query_api's members may SET ROLE to it (INHERIT
--     FALSE: query_api gains none of its rights or policies).
--   * Row-level security exactly as today: the existing policies (030's financials and filings
--     tiers, authz's own rows, 100's owner-only userdata) name workspace_reader beside query_api,
--     ALTERed in place so the rule stays one expression. With the catalog closed only private
--     grants read; open, the catalog within the user's tiers; userdata only the caller's rows.
--   * Nothing here filters for security in a view's WHERE (a user's non-leakproof qual could run
--     before it); the base tables' policies are security-barrier quals and always run first.
--
-- The views (column comments are the schema browser's descriptions, GET /v1/sql/schema):
--   companies          identity (public metadata) with Tributary's sector grouping
--   metrics            what each metric is (silver.metrics + gold.derived_metrics)
--   financials         gold.financials (014): quarters, TTM and derived metrics (financials tier)
--   financials_history gold.financials_known (086): every version of a figure and when it was believed
--   latest_figures     the screener's stored latest figure per company, span and metric (078)
--   filings            the SEC filing index (silver.filings: public metadata)
--   sections           parsed sections of stored filings, no text (filings tier)
--   my_datasets        the caller's own datasets (B2, userdata: owner-only)
--   my_series          the caller's own values, one per company, day, field and version
-- and the functions financials_at(day) (the history's versions believed at the end of a day: 087's
-- rule, `known_from <= day AND (known_to IS NULL OR known_to > day)`, as one filter so a company or
-- metric filter reaches the history's primary key; gold.financials_at itself computes every
-- company's labels first: 3.5M rows on a catalog-sized copy, over the temp-file limit),
-- restatements(company, day) (gold.restatements) and reading(company, metric, span)
-- (gold.latest_reading's status and reason).
--
-- The role: hermes' migrate login is neither superuser nor CREATEROLE, so the owner creates the role
-- before this deploy (deploy/helm/tributary README "SQL workspace role"); this file creates it only
-- where the login may (compose, a superuser), and otherwise stops with the command to run. The same
-- for query_api's SET membership and for SET ON PARAMETER temp_file_limit (a superuser-only setting
-- the Query API lowers per query; PG15+ lets a superuser grant it).
--
-- Locks on hermes (lock_timeout 3 s): ALTER POLICY takes an ACCESS EXCLUSIVE lock on each of
-- gold.quarterly_financials, derived_financials, financials_known, screen_figures,
-- silver.filing_sections, authz.user_access, user_tiers, public_access and userdata.datasets / rows
-- for the instant of the catalog update; a long reader or the hourly gold build holding one makes
-- the hook fail after 3 s: re-run it. Everything else is new objects and grants. Nothing is scanned.

SET LOCAL lock_timeout = '3s';

-- 1. The role --------------------------------------------------------------------------------------

DO $$
DECLARE
    fix constant text := 'sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1'
        ' -c "CREATE ROLE workspace_reader NOLOGIN"'
        ' -c "GRANT workspace_reader TO query_api WITH INHERIT FALSE, SET TRUE"'
        ' -c "GRANT SET ON PARAMETER temp_file_limit TO query_api"';
    is_super boolean := (SELECT rolsuper FROM pg_roles WHERE rolname = current_user);
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'workspace_reader') THEN
        IF NOT (SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user) THEN
            RAISE EXCEPTION 'role workspace_reader does not exist and % may not create roles', current_user
                USING HINT = 'as the database superuser run, then re-run the migrations: ' || fix;
        END IF;
        CREATE ROLE workspace_reader NOLOGIN;
    END IF;
    IF (SELECT rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb
        FROM pg_roles WHERE rolname = 'workspace_reader') THEN
        RAISE EXCEPTION 'workspace_reader must be NOLOGIN, NOSUPERUSER, NOBYPASSRLS, NOCREATEROLE, NOCREATEDB';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member = 'workspace_reader'::regrole) THEN
        RAISE EXCEPTION 'workspace_reader must be a member of no role (it would gain that role''s rights)';
    END IF;
    IF NOT pg_has_role('query_api', 'workspace_reader', 'SET') THEN
        IF is_super THEN
            GRANT workspace_reader TO query_api WITH INHERIT FALSE, SET TRUE;
        ELSE
            RAISE EXCEPTION 'query_api may not SET ROLE workspace_reader'
                USING HINT = 'as the database superuser run, then re-run the migrations: ' || fix;
        END IF;
    END IF;
    IF pg_has_role('query_api', 'workspace_reader', 'USAGE') THEN
        RAISE EXCEPTION 'query_api must not inherit workspace_reader (GRANT ... WITH INHERIT FALSE)';
    END IF;
    IF NOT has_parameter_privilege('query_api', 'temp_file_limit', 'SET') THEN
        IF is_super THEN
            GRANT SET ON PARAMETER temp_file_limit TO query_api;
        ELSE
            RAISE EXCEPTION 'query_api may not SET temp_file_limit'
                USING HINT = 'as the database superuser run, then re-run the migrations: ' || fix;
        END IF;
    END IF;
END
$$;

-- 2. Policies: the same rule now also binds workspace_reader -------------------------------------

ALTER POLICY granted_financials ON gold.quarterly_financials TO query_api, workspace_reader;
ALTER POLICY granted_financials ON gold.derived_financials TO query_api, workspace_reader;
ALTER POLICY granted_financials ON gold.financials_known TO query_api, workspace_reader;
ALTER POLICY granted_financials ON gold.screen_figures TO query_api, workspace_reader;
ALTER POLICY granted_filings ON silver.filing_sections TO query_api, workspace_reader;
ALTER POLICY own_rows ON authz.user_access TO query_api, workspace_reader;
ALTER POLICY own_rows ON authz.user_tiers TO query_api, workspace_reader;
ALTER POLICY signed_in ON authz.public_access TO query_api, workspace_reader;
ALTER POLICY owner ON userdata.datasets TO query_api, workspace_reader;
ALTER POLICY owner ON userdata.rows TO query_api, workspace_reader;

-- 3. What the views' and functions' bodies read with (security invoker) ---------------------------
-- USAGE: SQL function bodies (authz.readable_companies in every policy, the gold readings) are
-- parsed as the caller. sql_guard.py lets a query name only `workspace`; these grants never widen
-- what RLS lets through.
GRANT USAGE ON SCHEMA gold, silver, authz, userdata TO workspace_reader;
GRANT SELECT ON gold.quarterly_financials, gold.derived_financials, gold.financials_known,
    gold.screen_figures, gold.derived_metrics, gold.financials, gold.filing_sections,
    silver.metrics, silver.companies, silver.company_ciks, silver.company_sectors,
    silver.filings, silver.filing_sections,
    authz.user_access, authz.user_tiers, authz.public_access,
    userdata.datasets, userdata.rows, userdata.series
    TO workspace_reader;
-- document metadata only, as query_api (007): never content_gzip
GRANT SELECT (accession_no, cik, form, filed, period_of_report, document_name, url, fetched_at)
    ON bronze.filing_documents TO workspace_reader;
GRANT SELECT (accession_no, exhibit_type, description, document_name, url, fetched_at)
    ON bronze.filing_exhibits TO workspace_reader;

-- 4. The schema ----------------------------------------------------------------------------------

CREATE SCHEMA workspace;
GRANT USAGE ON SCHEMA workspace TO workspace_reader;
COMMENT ON SCHEMA workspace IS 'What POST /v1/sql reads (B3a): views and functions over gold, the '
    'filing index and the caller''s own data, read as workspace_reader under row-level security.';

CREATE VIEW workspace.companies WITH (security_invoker = true) AS
SELECT c.company_id, c.name, c.current_cik AS cik, c.tickers, c.exchanges, c.sic,
       c.sic_description, s.sector, s.industry, c.fiscal_year_end, c.entity_type,
       c.state_of_incorporation, c.filer_categories
FROM silver.companies c
LEFT JOIN silver.company_sectors s ON s.company_id = c.company_id;
COMMENT ON VIEW workspace.companies IS 'Every company Tributary knows: identity from the SEC, '
    'and Tributary''s own sector grouping of SIC codes. Public: no tier needed.';
COMMENT ON COLUMN workspace.companies.company_id IS 'Tributary''s id: the first CIK of the company''s lineage (10 digits). Join every other view on it.';
COMMENT ON COLUMN workspace.companies.name IS 'The SEC''s name for the company';
COMMENT ON COLUMN workspace.companies.cik IS 'The CIK the company files under now';
COMMENT ON COLUMN workspace.companies.tickers IS 'Current tickers, primary first';
COMMENT ON COLUMN workspace.companies.exchanges IS 'Exchanges of those tickers';
COMMENT ON COLUMN workspace.companies.sic IS 'SEC Standard Industrial Classification code';
COMMENT ON COLUMN workspace.companies.sic_description IS 'The SIC code''s name';
COMMENT ON COLUMN workspace.companies.sector IS 'Tributary''s sector for the SIC code (not GICS)';
COMMENT ON COLUMN workspace.companies.industry IS 'Tributary''s industry within the sector';
COMMENT ON COLUMN workspace.companies.fiscal_year_end IS 'Fiscal year end as MMDD, e.g. 0930';
COMMENT ON COLUMN workspace.companies.entity_type IS 'The SEC''s entity type (operating, other...)';
COMMENT ON COLUMN workspace.companies.state_of_incorporation IS 'State or country of incorporation';
COMMENT ON COLUMN workspace.companies.filer_categories IS 'The SEC''s filer categories (large accelerated filer...)';

CREATE VIEW workspace.metrics WITH (security_invoker = true) AS
SELECT m.metric, m.kind, m.unit, m.additive, false AS derived, NULL::text AS definition
FROM silver.metrics m
UNION ALL
SELECT d.metric, 'derived', d.unit, false, true, d.definition
FROM gold.derived_metrics d;
COMMENT ON VIEW workspace.metrics IS 'Every metric in financials: reported ones (from XBRL tags) and '
    'derived ones (ratios, margins, growth, TTM sums Tributary computes).';
COMMENT ON COLUMN workspace.metrics.metric IS 'The name financials.metric uses';
COMMENT ON COLUMN workspace.metrics.kind IS 'duration (a flow over a period), instant (a balance on a day) or derived';
COMMENT ON COLUMN workspace.metrics.unit IS 'USD means money in the company''s own currency (see financials.unit); also ratio, pure, shares, USD/shares';
COMMENT ON COLUMN workspace.metrics.additive IS 'Whether quarters add up to a year';
COMMENT ON COLUMN workspace.metrics.derived IS 'Computed by Tributary rather than reported';
COMMENT ON COLUMN workspace.metrics.definition IS 'How a derived metric is computed';

CREATE VIEW workspace.financials WITH (security_invoker = true) AS
SELECT f.company_id, f.span, f.metric, f.period_start, f.period_end, f.fiscal_year,
       f.fiscal_quarter, f.calendar_year, f.calendar_quarter, f.calendar_primary, f.value, f.unit,
       f.value_source, f.source_tag, f.accession_no, f.form, f.filed, f.source_accessions
FROM gold.financials f;
COMMENT ON VIEW workspace.financials IS 'Tributary''s figures as they stand today: one row per company, '
    'span, metric and period, each citing the filing it came from. Needs the financials tier. '
    'Amounts are in each company''s own currency (unit): filter on unit before comparing companies.';
COMMENT ON COLUMN workspace.financials.company_id IS 'The company (companies.company_id)';
COMMENT ON COLUMN workspace.financials.span IS 'quarter (a fiscal quarter, or a balance on its last day) or ttm (trailing twelve months)';
COMMENT ON COLUMN workspace.financials.metric IS 'The metric (metrics.metric), e.g. revenue, net_income, gross_margin';
COMMENT ON COLUMN workspace.financials.period_start IS 'First day of the period (empty for a balance)';
COMMENT ON COLUMN workspace.financials.period_end IS 'Last day of the period';
COMMENT ON COLUMN workspace.financials.fiscal_year IS 'The company''s fiscal year the period ends in';
COMMENT ON COLUMN workspace.financials.fiscal_quarter IS 'Fiscal quarter, 1 to 4';
COMMENT ON COLUMN workspace.financials.calendar_year IS 'Calendar year of the calendar quarter the period falls in most';
COMMENT ON COLUMN workspace.financials.calendar_quarter IS 'That calendar quarter, 1 to 4';
COMMENT ON COLUMN workspace.financials.calendar_primary IS 'True for the one row of each calendar quarter to compare companies by';
COMMENT ON COLUMN workspace.financials.value IS 'The figure as filed (ratios as fractions: 0.42 is 42%)';
COMMENT ON COLUMN workspace.financials.unit IS 'Its unit: an ISO currency (USD, EUR...), shares, ratio or pure';
COMMENT ON COLUMN workspace.financials.value_source IS 'reported, derived (from other quarters) or computed (a ratio or sum)';
COMMENT ON COLUMN workspace.financials.source_tag IS 'The XBRL tag it was read from';
COMMENT ON COLUMN workspace.financials.accession_no IS 'The filing it cites';
COMMENT ON COLUMN workspace.financials.form IS 'That filing''s form (10-K, 10-Q...)';
COMMENT ON COLUMN workspace.financials.filed IS 'The day that filing was filed';
COMMENT ON COLUMN workspace.financials.source_accessions IS 'Every filing the figure was computed from';

CREATE VIEW workspace.financials_history WITH (security_invoker = true) AS
SELECT k.company_id, k.span, k.metric, k.period_start, k.period_end, k.value, k.unit,
       k.value_source, k.source_tag, k.accession_no, k.form, k.filed, k.known_from, k.known_to
FROM gold.financials_known k;
COMMENT ON VIEW workspace.financials_history IS 'Point in time: every version of every quarterly and TTM '
    'figure, from the day it was first believed (known_from) to the day a later filing changed it '
    '(known_to). A restatement is two versions of one period. Needs the financials tier.';
COMMENT ON COLUMN workspace.financials_history.company_id IS 'The company (companies.company_id)';
COMMENT ON COLUMN workspace.financials_history.span IS 'quarter or ttm';
COMMENT ON COLUMN workspace.financials_history.metric IS 'The metric (metrics.metric)';
COMMENT ON COLUMN workspace.financials_history.period_start IS 'First day of the period';
COMMENT ON COLUMN workspace.financials_history.period_end IS 'Last day of the period';
COMMENT ON COLUMN workspace.financials_history.value IS 'The figure in this version';
COMMENT ON COLUMN workspace.financials_history.unit IS 'Its unit (an ISO currency, shares, ratio...)';
COMMENT ON COLUMN workspace.financials_history.value_source IS 'reported, derived or computed';
COMMENT ON COLUMN workspace.financials_history.source_tag IS 'The XBRL tag this version was read from';
COMMENT ON COLUMN workspace.financials_history.accession_no IS 'The filing that first stated this version';
COMMENT ON COLUMN workspace.financials_history.form IS 'That filing''s form';
COMMENT ON COLUMN workspace.financials_history.filed IS 'The day that filing was filed';
COMMENT ON COLUMN workspace.financials_history.known_from IS 'First day this version was believed (end of day)';
COMMENT ON COLUMN workspace.financials_history.known_to IS 'Day a later version replaced it (empty: still believed)';

CREATE VIEW workspace.latest_figures WITH (security_invoker = true) AS
SELECT f.company_id, f.span, f.metric, f.period_end, f.value, f.unit, f.accession_no, f.form,
       f.filed
FROM gold.screen_figures f;
COMMENT ON VIEW workspace.latest_figures IS 'Each company''s latest figure of each metric and span, as '
    'the screener stores them (kept by every gold build; a company being rebuilt may be missing for '
    'up to an hour). Figures as filed: the screener''s readings (not meaningful, stale, another '
    'currency) are not applied; reading(company_id, metric, span) gives them. Needs the financials tier.';
COMMENT ON COLUMN workspace.latest_figures.company_id IS 'The company (companies.company_id)';
COMMENT ON COLUMN workspace.latest_figures.span IS 'quarter or ttm';
COMMENT ON COLUMN workspace.latest_figures.metric IS 'The metric (metrics.metric)';
COMMENT ON COLUMN workspace.latest_figures.period_end IS 'Last day of the latest period with a figure';
COMMENT ON COLUMN workspace.latest_figures.value IS 'The figure';
COMMENT ON COLUMN workspace.latest_figures.unit IS 'Its unit: compare amounts only within one currency';
COMMENT ON COLUMN workspace.latest_figures.accession_no IS 'The filing it cites';
COMMENT ON COLUMN workspace.latest_figures.form IS 'That filing''s form';
COMMENT ON COLUMN workspace.latest_figures.filed IS 'The day that filing was filed';

CREATE VIEW workspace.filings WITH (security_invoker = true) AS
SELECT f.accession_no, f.cik, f.company_id, f.form, f.filing_date, f.accepted_at, f.report_date,
       f.primary_document, f.source
FROM silver.filings f;
COMMENT ON VIEW workspace.filings IS 'The SEC filing index: every filing of every filer since 2001, '
    'stored or not. Public: no tier needed. Filter on company_id or filing_date: it is large.';
COMMENT ON COLUMN workspace.filings.accession_no IS 'The filing''s accession number';
COMMENT ON COLUMN workspace.filings.cik IS 'The filer''s CIK';
COMMENT ON COLUMN workspace.filings.company_id IS 'The company (empty for a filer Tributary doesn''t track)';
COMMENT ON COLUMN workspace.filings.form IS 'Form type (10-K, 10-Q, 8-K, 4...)';
COMMENT ON COLUMN workspace.filings.filing_date IS 'The day it was filed';
COMMENT ON COLUMN workspace.filings.accepted_at IS 'When EDGAR accepted it';
COMMENT ON COLUMN workspace.filings.report_date IS 'The period it reports on';
COMMENT ON COLUMN workspace.filings.primary_document IS 'The main document''s file name';
COMMENT ON COLUMN workspace.filings.source IS 'How Tributary learned of it: feed, daily-index, seed or submissions';

CREATE VIEW workspace.sections WITH (security_invoker = true) AS
SELECT s.company_id, s.accession_no, s.form, s.filed, s.period_of_report, s.section_key, s.part,
       s.item, s.title, s.char_end - s.char_start AS chars, s.parser_version, s.parsed_at
FROM gold.filing_sections s;
COMMENT ON VIEW workspace.sections IS 'The sections Tributary parsed from stored 10-K, 10-Q, 20-F and '
    '8-K filings (and their press releases): what, where and how long. No text. Needs the filings tier.';
COMMENT ON COLUMN workspace.sections.company_id IS 'The company (companies.company_id)';
COMMENT ON COLUMN workspace.sections.accession_no IS 'The filing';
COMMENT ON COLUMN workspace.sections.form IS 'Its form';
COMMENT ON COLUMN workspace.sections.filed IS 'The day it was filed';
COMMENT ON COLUMN workspace.sections.period_of_report IS 'The period it reports on';
COMMENT ON COLUMN workspace.sections.section_key IS 'Section: 10-K 1A (risk factors), 7 (MD&A); 10-Q II-1A; 8-K 2.02; EX-99.1...';
COMMENT ON COLUMN workspace.sections.part IS 'Part of the form (I, II...)';
COMMENT ON COLUMN workspace.sections.item IS 'Item number';
COMMENT ON COLUMN workspace.sections.title IS 'The section''s heading as filed';
COMMENT ON COLUMN workspace.sections.chars IS 'Its length in characters';
COMMENT ON COLUMN workspace.sections.parser_version IS 'The parser version that read it';
COMMENT ON COLUMN workspace.sections.parsed_at IS 'When it was parsed';

CREATE VIEW workspace.my_datasets WITH (security_invoker = true) AS
SELECT d.dataset_id, d.name, d.title, d.fields, d.units, d.currency, d.known_from, d.row_count,
       d.company_count, d.first_day, d.last_day, d.uploaded_at
FROM userdata.datasets d;
COMMENT ON VIEW workspace.my_datasets IS 'Your own datasets (Your data): only yours, never anyone else''s.';
COMMENT ON COLUMN workspace.my_datasets.dataset_id IS 'The dataset''s id';
COMMENT ON COLUMN workspace.my_datasets.name IS 'Its name, as formulas read it (mine.<name>.<field>)';
COMMENT ON COLUMN workspace.my_datasets.title IS 'Its title';
COMMENT ON COLUMN workspace.my_datasets.fields IS 'Its value columns';
COMMENT ON COLUMN workspace.my_datasets.units IS 'How each field reads: number, ratio, money, shares, per_share';
COMMENT ON COLUMN workspace.my_datasets.currency IS 'The currency of money and per-share fields';
COMMENT ON COLUMN workspace.my_datasets.known_from IS 'column: rows say when each value was known; upload: known from the upload day';
COMMENT ON COLUMN workspace.my_datasets.row_count IS 'Rows stored';
COMMENT ON COLUMN workspace.my_datasets.company_count IS 'Companies with rows';
COMMENT ON COLUMN workspace.my_datasets.first_day IS 'Earliest day with a row';
COMMENT ON COLUMN workspace.my_datasets.last_day IS 'Latest day with a row';
COMMENT ON COLUMN workspace.my_datasets.uploaded_at IS 'When it was last uploaded';

CREATE VIEW workspace.my_series WITH (security_invoker = true) AS
SELECT d.name AS dataset, s.company_id, s.day, s.field, s.value, s.known_at
FROM userdata.series s
JOIN userdata.datasets d ON d.dataset_id = s.dataset_id;
COMMENT ON VIEW workspace.my_series IS 'Your own values, one row per dataset, company, day, field and '
    'version: a later known_at for the same day is a revision. Only yours.';
COMMENT ON COLUMN workspace.my_series.dataset IS 'The dataset''s name (my_datasets.name)';
COMMENT ON COLUMN workspace.my_series.company_id IS 'The company (companies.company_id)';
COMMENT ON COLUMN workspace.my_series.day IS 'The day the value is for';
COMMENT ON COLUMN workspace.my_series.field IS 'The field (one of my_datasets.fields)';
COMMENT ON COLUMN workspace.my_series.value IS 'The value';
COMMENT ON COLUMN workspace.my_series.known_at IS 'The day this version was known';

-- Functions: plain SQL, security invoker, one SELECT each (inlined into the caller's plan).
CREATE FUNCTION workspace.financials_at(day date)
RETURNS TABLE (company_id text, span text, metric text, period_start date, period_end date,
               value numeric, unit text, value_source text, source_tag text, accession_no text,
               form text, filed date, known_from date, known_to date)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT k.company_id, k.span, k.metric, k.period_start, k.period_end, k.value, k.unit,
           k.value_source, k.source_tag, k.accession_no, k.form, k.filed, k.known_from,
           k.known_to
    FROM gold.financials_known k
    WHERE k.known_from <= day AND (k.known_to IS NULL OR k.known_to > day)  -- 087's rule
$$;
COMMENT ON FUNCTION workspace.financials_at(date) IS 'Quarterly and TTM figures as they stood at '
    'the end of a past day: the version of each believed then, only what had been filed by then (no '
    'look-ahead). Filter on company_id and metric: it is every company''s history. Needs the '
    'financials tier.';

CREATE FUNCTION workspace.restatements(company_id text, as_of date DEFAULT NULL)
RETURNS TABLE (span text, metric text, period_start date, period_end date, unit text,
               original numeric, original_tag text, original_accession text, original_form text,
               original_filed date, original_known_from date,
               restated numeric, restated_tag text, restated_accession text, restated_form text,
               restated_filed date, restated_known_from date, same_line boolean)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT r.* FROM gold.restatements(company_id, as_of) r
$$;
COMMENT ON FUNCTION workspace.restatements(text, date) IS 'Every change of one company''s quarterly '
    'or TTM figure between two consecutive versions (by as_of, or today): the original and the '
    'restated value, each with its filing; same_line when the XBRL tag stayed the same (a stock split '
    'restates per-share figures too). Needs the financials tier.';

CREATE FUNCTION workspace.reading(company_id text, metric text, span text DEFAULT 'ttm')
RETURNS TABLE (period_end date, value numeric, unit text, status text, reason text,
               accession_no text, form text, filed date)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT r.period_end, r.value, r.unit, r.status, r.reason, r.accession_no, r.form, r.filed
    FROM silver.companies c
    CROSS JOIN LATERAL gold.latest_period(c.company_id) p
    CROSS JOIN LATERAL gold.latest_reading(c.company_id, reading.span, reading.metric, c.sic,
                                           p.latest) r
    WHERE c.company_id = reading.company_id
$$;
COMMENT ON FUNCTION workspace.reading(text, text, text) IS 'One company''s latest figure of a metric '
    'with Tributary''s reading of it, as the screener shows it: status ok, or why not to compare it '
    '(not_meaningful, not_applicable, not_computed, other_currency, stale, old) with the reason. '
    'Needs the financials tier.';

REVOKE ALL ON FUNCTION workspace.financials_at(date), workspace.restatements(text, date),
    workspace.reading(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION workspace.financials_at(date), workspace.restatements(text, date),
    workspace.reading(text, text, text) TO workspace_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA workspace TO workspace_reader;
