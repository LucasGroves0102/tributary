-- Insider filings (Riley #117: Forms 3, 4 and 5 showed no owner, trade, share count or value).
-- A Form 3/4/5 is structured XML (the SEC's ownershipDocument), so it is read, not guessed:
--   bronze.ownership_documents   the <ownershipDocument> element of each filing, as filed (doc-fetcher
--                                stores it: new filings from the live feed, a bounded backfill job)
--   silver.insider_filings       one row per filing: issuer company, form, period, the reporting
--                                owners with their relationship to the issuer (jsonb array)
--   silver.insider_transactions  one row per line of the non-derivative and derivative tables, in
--                                document order: transactions and holdings, code, shares, price,
--                                acquired/disposed, shares owned after, direct/indirect
-- silver.refresh_insider_filings(accessions) re-derives silver from bronze (delete, then insert):
-- idempotent on the accession, so a filing stored twice or re-parsed reads the same. XMLTABLE reads
-- the XML in Postgres (medallion transforms live in SQL); a document that isn't well formed XML is
-- left out of silver, not an error. Value (shares x price) is the Query API's, never stored.
--
-- The issuer is the XML's issuerCik through silver.company_ciks: a filing whose issuer isn't a
-- company here has no silver rows. Access: filing content, so the filings tier, RLS granted_filings
-- on company_id (030's form). query_api reads silver only (bronze holds no company_id).
--
-- Locks on hermes: new tables, indexes and functions only; nothing existing is locked or rewritten.

SET LOCAL lock_timeout = '30s';

CREATE TABLE bronze.ownership_documents (
    accession_no    text        PRIMARY KEY,
    cik             text        NOT NULL,  -- the CIK the filing was fetched under (an issuer's)
    form            text        NOT NULL,  -- as filed: 3, 4, 5, 3/A, 4/A, 5/A
    filed           date        NOT NULL,
    url             text        NOT NULL,
    xml             text        NOT NULL,  -- the <ownershipDocument> element as filed
    content_sha256  text        NOT NULL,
    fetched_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ownership_documents_cik ON bronze.ownership_documents (cik, filed DESC);

CREATE TABLE silver.insider_filings (
    accession_no      text        PRIMARY KEY
                                  REFERENCES bronze.ownership_documents ON DELETE CASCADE,
    company_id        text        NOT NULL,
    issuer_cik        text        NOT NULL,
    form              text        NOT NULL,
    filed             date        NOT NULL,
    period_of_report  date,
    owners            jsonb       NOT NULL,  -- [{cik, name, director, officer, title, ten_percent, other, other_text}]
    aff10b5_one       boolean,              -- a Rule 10b5-1 plan, when the filing says
    refreshed_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX insider_filings_company ON silver.insider_filings (company_id, filed DESC, accession_no);

CREATE TABLE silver.insider_transactions (
    accession_no       text    NOT NULL REFERENCES silver.insider_filings ON DELETE CASCADE,
    line               integer NOT NULL,  -- document order: non-derivative table, then derivative
    company_id         text    NOT NULL,
    kind               text    NOT NULL CHECK (kind IN ('transaction', 'holding')),
    derivative         boolean NOT NULL,
    security_title     text,
    transaction_date   date,
    code               text,              -- P purchase, S sale, A award, M exercise, F tax, G gift, ...
    equity_swap        boolean,
    shares             numeric,
    price              numeric,
    acquired_disposed  text CHECK (acquired_disposed IN ('A', 'D')),
    shares_after       numeric,
    direct_indirect    text,
    nature             text,              -- of indirect ownership ("By trust")
    exercise_price     numeric,
    underlying_title   text,
    underlying_shares  numeric,
    PRIMARY KEY (accession_no, line)
);
CREATE INDEX insider_transactions_company ON silver.insider_transactions (company_id, transaction_date DESC);

-- '0'/'1'/'true'/'false' as the schema allows; anything else is unknown
CREATE FUNCTION silver.xml_flag(v text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE lower(btrim(v)) WHEN '1' THEN true WHEN 'true' THEN true
                                WHEN '0' THEN false WHEN 'false' THEN false END
$$;

CREATE FUNCTION silver.xml_number(v text) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN btrim(v) ~ '^-?[0-9]+(\.[0-9]+)?$' THEN btrim(v)::numeric END
$$;

-- a date, also with the zone the schema allows after it ("2026-09-16-05:00")
CREATE FUNCTION silver.xml_date(v text) RETURNS date
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN btrim(v) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN left(btrim(v), 10)::date END
$$;

CREATE FUNCTION silver.refresh_insider_filings(p_accessions text[]) RETURNS integer
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

GRANT SELECT ON silver.insider_filings, silver.insider_transactions TO query_api;

ALTER TABLE silver.insider_filings ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON silver.insider_filings FOR SELECT TO query_api
    USING (company_id IN (SELECT r FROM authz.readable_companies('filings') r));

ALTER TABLE silver.insider_transactions ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON silver.insider_transactions FOR SELECT TO query_api
    USING (company_id IN (SELECT r FROM authz.readable_companies('filings') r));
