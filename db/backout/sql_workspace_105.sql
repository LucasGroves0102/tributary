-- Backout of 105_sql_workspace.sql (never a migration: README.md): the SQL workspace removed. The
-- schema `workspace` and everything in it dropped; the policies 105 extended back to query_api
-- alone; workspace_reader's grants revoked (the role itself stays: only a superuser drops it, and
-- it can then do nothing). Deploy the previous query-api image first (POST /v1/sql and
-- GET /v1/sql/schema read the schema; with it gone they fail). No data is touched: nothing to
-- rebuild. Notebooks stay saved in the app database (their kind is app 023's).
--
-- It heads the chain: the workspace views depend on 085-087's gold.financials_known and 078's
-- gold.screen_figures, so every older backout runs after this one.
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below), like every
-- backout (README.md); then, if wanted, as the superuser:
--   sudo -u postgres psql -d tributary -c 'REVOKE SET ON PARAMETER temp_file_limit FROM query_api' -c 'DROP ROLE workspace_reader'
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f sql_workspace_105.sql

BEGIN;
SET LOCAL lock_timeout = '3s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: none, this is the newest) and this one
-- hasn't, and unless the login owns the functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('gold.amount_currency(text)', 'd59a928b3b91fb9a6da98fc15e72a536'),
        ('gold.amount_currency_of(text)', NULL),
        ('gold.amount_currency_of(text,date)', '7ef505eacda72c0de066a26d5f3303ed'),
        ('gold.annual_per_share(text,integer)', NULL),
        ('gold.annual_per_share(text,integer,date)', 'c27e50f38738222c370bb9b834620cab'),
        ('gold.best_facts(text[])', NULL),
        ('gold.best_facts(text[],date)', '6950247dc6c9f51d99de4b12e906dc5a'),
        ('gold.companies_for_ciks(text[])', '5f02b9f1e395d7f743635d0a399f8f6c'),
        ('gold.company_confidence(text,text,date)', '91c5d249e662b77610e084d4a0620ddb'),
        ('gold.company_financials_at(text,date,text,text[])', '63de4900f9b4285f61d220d0141ceda7'),
        ('gold.derived_rows(text[],date,gold.quarter_row[])', 'd22751c4c57103d6fda7665e6477a179'),
        ('gold.figure_facts(text,text,date)', '6789892e97611dd6c0260a25f695d7fc'),
        ('gold.filing_figures(text,text)', '51376508ac8a1a66d7e42cc92852346f'),
        ('gold.financial_rows(text[],date)', '861bf05e3f8d8f5743e946c9761e291d'),
        ('gold.financials_at(date)', '9bdd938e555828de0b4eed294e0dad5d'),
        ('gold.input_values(jsonb)', '06de5fab08b2d846d578a006a9efc859'),
        ('gold.knowledge_facts(text)', '18e26ac36e3c21f7ce905a870362750c'),
        ('gold.known_changed_from(text)', '61c37386d5747694eadbb421824e579f'),
        ('gold.latest_equity(text)', NULL),
        ('gold.latest_equity(text,date)', '1890ba1959496c3ea2c74996620e23c1'),
        ('gold.latest_period(text)', NULL),
        ('gold.latest_period(text,date)', 'a666140990b5da7e3a53846f08a39bf6'),
        ('gold.latest_reading(text,text,text,text,date)', NULL),
        ('gold.latest_reading(text,text,text,text,date,date)', '57d405f2fb1fd44c33a77abaa962cd08'),
        ('gold.latest_row(text,text,text)', NULL),
        ('gold.latest_row(text,text,text,date)', '78b74abf479901f860df8e016ef725fb'),
        ('gold.not_applicable_metrics(text,text)', '31aa1cba6092282684aaf6b699539359'),
        ('gold.not_computed(text,text)', 'a1d6b45f36a10d029335de6f30893f96'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', '472cb5abb2f6cb3ad0cbf64734468ee4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', '9f4c2cd4aeaefa663263efb806700f5d'),
        ('gold.quarterly_recent(text,date)', NULL),
        ('gold.quarterly_recent(text,date,date)', 'b7e20593a6dc7b936c9a138a15fb0951'),
        ('gold.quarterly_rows(text[],date)', '58f05bc5ff7d5f1f6bce48ec05d1201a'),
        ('gold.rebuild_known(text[])', '7c0e1472047486fb812194cfe5b5919b'),
        ('gold.refresh_derived_financials(text[])', '6e9debd1c4fec0936d3498b7a9e4c147'),
        ('gold.refresh_financials(text[])', 'b3f53c9f634d1d1383ec72d363982a61'),
        ('gold.refresh_known(text[])', '4e601c1d2eab3b6f283829ea2c4774fc'),
        ('gold.refresh_quarterly_financials(text[])', '4cf929c3db5e1b7830220b9b3574bfe2'),
        ('gold.replay_companies(text[],boolean)', 'a88eba6b35d5504daf597ad643fe35a0'),
        ('gold.replay_known(text,date)', '39c0339c527f6718a084b2bc4c656f29'),
        ('gold.restatements(text,date)', '7097e219996f52c772e678e98917ef29'),
        ('gold.screen_backlog()', '972441f449f03e3d8fa36a9748610e85'),
        ('gold.screen_company(text)', 'd0ab770fb304683145115aeb8dcee30d'),
        ('gold.screen_reading(text,text,text,text,date,boolean,numeric,text,boolean)', 'f110e0894f0dff22b6fb8fe0423313fe'),
        ('gold.usable_facts(text[])', NULL),
        ('gold.usable_facts(text[],date)', 'fe486d44c294971b08f43188f6822765'),
        ('ops.check_financials(text[])', 'a8a94bf23f986a93f3f61c8fc5f35461'),
        ('ops.company_backlog(text)', 'c57e6d676904010c5295c661689911ca'),
        ('ops.company_rule_version(text)', 'c42c4ab6b3199df8659901cfcff470c4'),
        ('ops.rebuild_soon(text[])', '9426fba21e20547353faa6ed8bc0824e'),
        ('silver.refresh_filing_facts(text[])', '3b400f0ebef7ea4ae47aeac60b50cca6'),
        ('silver.refresh_financial_facts(text[])', '813785ee2a47de9ea6e3a86f6176fef2'),
        ('workspace.financials_at(date)', '3c16973fde3710d0441ceab369e19f96'),
        ('workspace.reading(text,text,text)', 'ff6c7f835bf71c4b91b4ca8c38b1b881'),
        ('workspace.restatements(text,date)', '7af65209befe6bbdaf630a7d594ae20f')
    ) v(fn, want) LOOP
        SELECT pg_get_userbyid(p.proowner) INTO fn_owner FROM pg_proc p WHERE p.oid = w.id;
        IF fn_owner IS NOT NULL AND fn_owner <> current_user THEN
            RAISE EXCEPTION USING MESSAGE = format('run this as %s, the owner of %s (the '
                'migrate login), not as %s: see the header. Nothing was changed.', fn_owner, w.fn,
                current_user);
        END IF;
        IF w.want IS DISTINCT FROM (SELECT md5(p.prosrc) FROM pg_proc p WHERE p.oid = w.id) THEN
            RAISE EXCEPTION USING MESSAGE = format('%s is not where this backout starts: run '
                'the newer backouts first, newest first (%s), and this one only once. Nothing was '
                'changed.', w.fn, 'none, this is the newest');
        END IF;
    END LOOP;
END
$guard$;
-- end guard

DROP SCHEMA workspace CASCADE;

ALTER POLICY granted_financials ON gold.quarterly_financials TO query_api;
ALTER POLICY granted_financials ON gold.derived_financials TO query_api;
ALTER POLICY granted_financials ON gold.financials_known TO query_api;
ALTER POLICY granted_financials ON gold.screen_figures TO query_api;
ALTER POLICY granted_filings ON silver.filing_sections TO query_api;
ALTER POLICY own_rows ON authz.user_access TO query_api;
ALTER POLICY own_rows ON authz.user_tiers TO query_api;
ALTER POLICY signed_in ON authz.public_access TO query_api;
ALTER POLICY owner ON userdata.datasets TO query_api;
ALTER POLICY owner ON userdata.rows TO query_api;

REVOKE SELECT ON gold.quarterly_financials, gold.derived_financials, gold.financials_known,
    gold.screen_figures, gold.derived_metrics, gold.financials, gold.filing_sections,
    silver.metrics, silver.companies, silver.company_ciks, silver.company_sectors,
    silver.filings, silver.filing_sections,
    authz.user_access, authz.user_tiers, authz.public_access,
    userdata.datasets, userdata.rows, userdata.series
    FROM workspace_reader;
REVOKE SELECT (accession_no, cik, form, filed, period_of_report, document_name, url, fetched_at)
    ON bronze.filing_documents FROM workspace_reader;
REVOKE SELECT (accession_no, exhibit_type, description, document_name, url, fetched_at)
    ON bronze.filing_exhibits FROM workspace_reader;
REVOKE USAGE ON SCHEMA gold, silver, authz, userdata FROM workspace_reader;

COMMIT;
