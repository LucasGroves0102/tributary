-- Backout of 059_fetch_outbox_insider_queue.sql (never a migration: README.md). Puts back 054's
-- silver.xml_date (a date that doesn't exist raises again) and silver.refresh_insider_filings (no
-- findings, no first-node paths), copied unchanged by a script, and drops ops.document_outbox,
-- ops.insider_queue and ops.insider_findings. Independent of the gold chain: it touches no function
-- another backout touches, so it can run in any order with them.
--
-- First deploy the previous doc-fetcher image (pre-059: it fetches insider filings inline and has no
-- outbox); the current one needs these tables. The guard refuses while ops.document_outbox still holds
-- an owed documents.fetched event (dropping it would lose the event): wait for the new image's sweep,
-- or republish them, before switching images.
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as postgres,
-- a function it re-creates would belong to postgres, and the non-superuser migrate login could never
-- replace it again. The guard below refuses any login that doesn't own the functions.
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f insider_queue_059.sql

BEGIN;
SET LOCAL lock_timeout = '10s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: none, this is the newest) and this one
-- hasn't, and unless the login owns the functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('silver.refresh_insider_filings(text[])', 'cdb39280a93c62adb80ff457ddb8808c'),
        ('silver.xml_date(text)', '7766844ec2c1ed3f90e1892aa312530a')
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

-- 059's own check (the generated guard above covers order, a second run and the login): dropping
-- ops.document_outbox while it holds an owed documents.fetched event would lose the event.
DO $outbox$
BEGIN
    IF EXISTS (SELECT 1 FROM ops.document_outbox) THEN
        RAISE EXCEPTION USING MESSAGE = 'ops.document_outbox still holds owed documents.fetched '
            'events: let the current doc-fetcher send them first (header). Nothing was changed.';
    END IF;
END
$outbox$;

CREATE OR REPLACE FUNCTION silver.xml_date(v text) RETURNS date
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN btrim(v) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN left(btrim(v), 10)::date END
$$;

CREATE OR REPLACE FUNCTION silver.refresh_insider_filings(p_accessions text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    DELETE FROM silver.insider_filings WHERE accession_no = ANY(p_accessions);

    WITH docs AS (
        SELECT d.accession_no, d.form, d.filed, d.xml::xml AS doc
        FROM bronze.ownership_documents d
        WHERE d.accession_no = ANY(p_accessions) AND xml_is_well_formed_document(d.xml)
    ),
    heads AS (
        SELECT d.accession_no, d.form, d.filed, d.doc, h.*
        FROM docs d,
             XMLTABLE('/ownershipDocument' PASSING d.doc COLUMNS
                 issuer_cik   text PATH 'issuer/issuerCik',
                 period       text PATH 'periodOfReport',
                 aff10b5_one  text PATH 'aff10b5One') h
    )
    INSERT INTO silver.insider_filings
        (accession_no, company_id, issuer_cik, form, filed, period_of_report, owners, aff10b5_one)
    SELECT h.accession_no, k.company_id, lpad(btrim(h.issuer_cik), 10, '0'), h.form, h.filed,
           silver.xml_date(h.period),
           coalesce((SELECT jsonb_agg(jsonb_build_object(
                         'cik', lpad(btrim(o.cik), 10, '0'), 'name', btrim(o.name),
                         'director', silver.xml_flag(o.director),
                         'officer', silver.xml_flag(o.officer),
                         'title', nullif(btrim(o.title), ''),
                         'ten_percent', silver.xml_flag(o.ten_percent),
                         'other', silver.xml_flag(o.other),
                         'other_text', nullif(btrim(o.other_text), '')) ORDER BY o.ord)
                     FROM XMLTABLE('/ownershipDocument/reportingOwner' PASSING h.doc COLUMNS
                              ord          FOR ORDINALITY,
                              cik          text PATH 'reportingOwnerId/rptOwnerCik',
                              name         text PATH 'reportingOwnerId/rptOwnerName',
                              director     text PATH 'reportingOwnerRelationship/isDirector',
                              officer      text PATH 'reportingOwnerRelationship/isOfficer',
                              title        text PATH 'reportingOwnerRelationship/officerTitle',
                              ten_percent  text PATH 'reportingOwnerRelationship/isTenPercentOwner',
                              other        text PATH 'reportingOwnerRelationship/isOther',
                              other_text   text PATH 'reportingOwnerRelationship/otherText') o),
                    '[]'::jsonb),
           silver.xml_flag(h.aff10b5_one)
    FROM heads h
    JOIN silver.company_ciks k ON k.cik = lpad(btrim(h.issuer_cik), 10, '0');

    INSERT INTO silver.insider_transactions
        (accession_no, line, company_id, kind, derivative, security_title, transaction_date, code,
         equity_swap, shares, price, acquired_disposed, shares_after, direct_indirect, nature,
         exercise_price, underlying_title, underlying_shares)
    SELECT f.accession_no, t.line::int, f.company_id,
           CASE WHEN t.element LIKE '%Holding' THEN 'holding' ELSE 'transaction' END,
           t.element LIKE 'derivative%', nullif(btrim(t.security_title), ''),
           silver.xml_date(t.transaction_date), nullif(btrim(t.code), ''),
           silver.xml_flag(t.equity_swap), silver.xml_number(t.shares),
           silver.xml_number(t.price),
           CASE WHEN btrim(t.acquired_disposed) IN ('A', 'D') THEN btrim(t.acquired_disposed) END,
           silver.xml_number(t.shares_after), nullif(btrim(t.direct_indirect), ''),
           nullif(btrim(t.nature), ''), silver.xml_number(t.exercise_price),
           nullif(btrim(t.underlying_title), ''), silver.xml_number(t.underlying_shares)
    FROM silver.insider_filings f  -- only well formed documents reached it
    JOIN bronze.ownership_documents d ON d.accession_no = f.accession_no,
         XMLTABLE('/ownershipDocument/nonDerivativeTable/* | /ownershipDocument/derivativeTable/*'
                  PASSING (d.xml::xml) COLUMNS
             line               FOR ORDINALITY,
             element            text PATH 'local-name()',
             security_title     text PATH 'securityTitle/value',
             transaction_date   text PATH 'transactionDate/value',
             code               text PATH 'transactionCoding/transactionCode',
             equity_swap        text PATH 'transactionCoding/equitySwapInvolved',
             shares             text PATH 'transactionAmounts/transactionShares/value',
             price              text PATH 'transactionAmounts/transactionPricePerShare/value',
             acquired_disposed  text PATH 'transactionAmounts/transactionAcquiredDisposedCode/value',
             shares_after       text PATH 'postTransactionAmounts/sharesOwnedFollowingTransaction/value',
             direct_indirect    text PATH 'ownershipNature/directOrIndirectOwnership/value',
             nature             text PATH 'ownershipNature/natureOfOwnership/value',
             exercise_price     text PATH 'conversionOrExercisePrice/value',
             underlying_title   text PATH 'underlyingSecurity/underlyingSecurityTitle/value',
             underlying_shares  text PATH 'underlyingSecurity/underlyingSecurityShares/value') t
    WHERE f.accession_no = ANY(p_accessions)
      AND t.element IN ('nonDerivativeTransaction', 'nonDerivativeHolding',
                        'derivativeTransaction', 'derivativeHolding');

    SELECT count(*) INTO n FROM silver.insider_filings WHERE accession_no = ANY(p_accessions);
    RETURN n;
END
$$;

DROP TABLE ops.document_outbox;
DROP TABLE ops.insider_queue;
DROP TABLE ops.insider_findings;

COMMIT;
