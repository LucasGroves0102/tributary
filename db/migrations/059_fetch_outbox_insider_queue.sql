-- doc-fetcher pre-ship fixes (review 2026-11-09, B2 and B3, and the insider catch-up):
--
-- 1. ops.document_outbox: a live document's event is owed. The live doc-fetcher stores a filing's
--    document, its 8-K exhibits and this row in ONE transaction (after every file was fetched),
--    publishes documents.fetched, and deletes the row only when Kafka confirms delivery. A failed
--    delivery or a crash leaves the row, and the consumer republishes it (startup, then every
--    minute). The document crawl reads it too: a filing it finds already stored is marked
--    published only when no live event is owed; otherwise it publishes documents.backfill itself.
--    Before this, an 8-K whose exhibit fetch failed was committed without exhibits and without an
--    event, and the crawl then marked it published: never parsed.
-- 2. ops.insider_queue: Forms 3/4/5 from the live feed wait here and are fetched when the consumer
--    is idle (or after a few minutes of continuous traffic, with a short time budget), so a 10-K,
--    10-Q or 8-K never queues behind thousands of Form 4s (3,268 on 2026-03-03). Retries back off;
--    a filing that can't be stored is kept as `failed` with its error, and every path (live, the
--    daily catch-up, the backfill) moves on to the next one.
-- 3. ops.insider_findings: what parsing an insider filing noticed (a bad date read as NULL, several
--    values where one was expected, XML that isn't well formed, a parse that failed and left the
--    filing in bronze only). Counts and short details only, never the XML.
-- 4. silver.xml_date: a date that doesn't exist ("2026-02-30") is NULL, not an error that aborted
--    the whole filing's transaction (and, before, crash-looped the live consumer).
-- 5. silver.refresh_insider_filings (054's body, edits marked "059"): every column path takes the
--    first node, `(path)[1]`, so a repeated element no longer raises "more than one value returned
--    by column XPath expression"; repeats and bad dates become findings.
--
-- No FK to bronze.filing_documents on purpose (deploy/host/pg-backup.sh's slim dump excludes that
-- subtree's data; the outbox is not part of it).
--
-- Locks on hermes: three new tables (nothing existing is locked); CREATE OR REPLACE of two functions
-- (054's, called only by doc-fetcher, which isn't running 054 on hermes yet). No rewrite, no scan.

SET LOCAL lock_timeout = '5s';

CREATE TABLE ops.document_outbox (
    accession_no  text        PRIMARY KEY,  -- bronze.filing_documents' key (no FK, see above)
    queued_at     timestamptz NOT NULL DEFAULT now(),
    attempts      int         NOT NULL DEFAULT 0,  -- republish attempts after the first
    last_attempt  timestamptz
);

CREATE TABLE ops.insider_queue (
    accession_no  text        PRIMARY KEY,
    cik           text        NOT NULL,  -- the issuer's CIK it is fetched under
    form          text        NOT NULL,
    filed         date        NOT NULL,
    source        text        NOT NULL CHECK (source IN ('feed', 'catch-up')),
    status        text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'failed')),
    attempts      smallint    NOT NULL DEFAULT 0,
    not_before    timestamptz,             -- backoff after a failed attempt
    last_error    text,
    queued_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX insider_queue_pending ON ops.insider_queue (queued_at) WHERE status = 'pending';

CREATE TABLE ops.insider_findings (
    accession_no  text        NOT NULL,
    kind          text        NOT NULL CHECK (kind IN ('bad_date', 'several_values',
                                                       'not_well_formed', 'parse_failed')),
    detail        text        CHECK (length(detail) <= 500),
    found_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (accession_no, kind)
);

-- 059: a date that doesn't exist is NULL. plpgsql so it's never inlined: a constant argument can't
-- reach the cast at plan time. pg_input_is_valid (Postgres 16) tests without raising.
CREATE OR REPLACE FUNCTION silver.xml_date(v text) RETURNS date
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    d text := left(btrim(v), 10);
BEGIN
    IF btrim(v) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' AND pg_input_is_valid(d, 'date') THEN
        RETURN d::date;
    END IF;
    RETURN NULL;
END
$$;

CREATE OR REPLACE FUNCTION silver.refresh_insider_filings(p_accessions text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    DELETE FROM silver.insider_filings WHERE accession_no = ANY(p_accessions);
    -- 059: a refresh re-derives the findings too
    DELETE FROM ops.insider_findings WHERE accession_no = ANY(p_accessions);

    INSERT INTO ops.insider_findings (accession_no, kind)
    SELECT d.accession_no, 'not_well_formed'
    FROM bronze.ownership_documents d
    WHERE d.accession_no = ANY(p_accessions) AND NOT xml_is_well_formed_document(d.xml);

    WITH docs AS (
        SELECT d.accession_no, d.form, d.filed, d.xml::xml AS doc
        FROM bronze.ownership_documents d
        WHERE d.accession_no = ANY(p_accessions) AND xml_is_well_formed_document(d.xml)
    ),
    heads AS (
        SELECT d.accession_no, d.form, d.filed, d.doc, h.*
        FROM docs d,
             XMLTABLE('/ownershipDocument' PASSING d.doc COLUMNS
                 issuer_cik   text PATH '(issuer/issuerCik)[1]',
                 period       text PATH '(periodOfReport)[1]',
                 aff10b5_one  text PATH '(aff10b5One)[1]',
                 -- 059: repeats of what is read once (the first is taken)
                 several      text PATH 'count(issuer/issuerCik) > 1 or count(periodOfReport) > 1
                                         or count(aff10b5One) > 1
                                         or count(reportingOwner[count(reportingOwnerId/rptOwnerCik) > 1
                                                  or count(reportingOwnerId/rptOwnerName) > 1
                                                  or count(reportingOwnerRelationship/isDirector) > 1
                                                  or count(reportingOwnerRelationship/isOfficer) > 1
                                                  or count(reportingOwnerRelationship/officerTitle) > 1
                                                  or count(reportingOwnerRelationship/isTenPercentOwner) > 1
                                                  or count(reportingOwnerRelationship/isOther) > 1
                                                  or count(reportingOwnerRelationship/otherText) > 1]) > 0') h
    ),
    filings AS (
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
                                  cik          text PATH '(reportingOwnerId/rptOwnerCik)[1]',
                                  name         text PATH '(reportingOwnerId/rptOwnerName)[1]',
                                  director     text PATH '(reportingOwnerRelationship/isDirector)[1]',
                                  officer      text PATH '(reportingOwnerRelationship/isOfficer)[1]',
                                  title        text PATH '(reportingOwnerRelationship/officerTitle)[1]',
                                  ten_percent  text PATH '(reportingOwnerRelationship/isTenPercentOwner)[1]',
                                  other        text PATH '(reportingOwnerRelationship/isOther)[1]',
                                  other_text   text PATH '(reportingOwnerRelationship/otherText)[1]') o),
                        '[]'::jsonb),
               silver.xml_flag(h.aff10b5_one)
        FROM heads h
        JOIN silver.company_ciks k ON k.cik = lpad(btrim(h.issuer_cik), 10, '0')
        RETURNING 1
    )
    -- 059: findings of the header (a data-modifying CTE above runs whether or not it is read)
    INSERT INTO ops.insider_findings (accession_no, kind, detail)
    SELECT h.accession_no, 'bad_date', left('periodOfReport ' || btrim(h.period), 500)
    FROM heads h
    WHERE btrim(h.period) <> '' AND silver.xml_date(h.period) IS NULL
    UNION ALL
    SELECT h.accession_no, 'several_values', 'issuer, period or a reporting owner'
    FROM heads h
    WHERE h.several = 'true'
    ON CONFLICT (accession_no, kind) DO NOTHING;

    WITH lines AS (
        SELECT f.accession_no, f.company_id, t.*
        FROM silver.insider_filings f  -- only well formed documents reached it
        JOIN bronze.ownership_documents d ON d.accession_no = f.accession_no,
             XMLTABLE('/ownershipDocument/nonDerivativeTable/* | /ownershipDocument/derivativeTable/*'
                      PASSING (d.xml::xml) COLUMNS
                 line               FOR ORDINALITY,
                 element            text PATH 'local-name()',
                 security_title     text PATH '(securityTitle/value)[1]',
                 transaction_date   text PATH '(transactionDate/value)[1]',
                 code               text PATH '(transactionCoding/transactionCode)[1]',
                 equity_swap        text PATH '(transactionCoding/equitySwapInvolved)[1]',
                 shares             text PATH '(transactionAmounts/transactionShares/value)[1]',
                 price              text PATH '(transactionAmounts/transactionPricePerShare/value)[1]',
                 acquired_disposed  text PATH '(transactionAmounts/transactionAcquiredDisposedCode/value)[1]',
                 shares_after       text PATH '(postTransactionAmounts/sharesOwnedFollowingTransaction/value)[1]',
                 direct_indirect    text PATH '(ownershipNature/directOrIndirectOwnership/value)[1]',
                 nature             text PATH '(ownershipNature/natureOfOwnership/value)[1]',
                 exercise_price     text PATH '(conversionOrExercisePrice/value)[1]',
                 underlying_title   text PATH '(underlyingSecurity/underlyingSecurityTitle/value)[1]',
                 underlying_shares  text PATH '(underlyingSecurity/underlyingSecurityShares/value)[1]',
                 -- 059: repeats of what is read once (the first is taken)
                 several            text PATH 'count(securityTitle/value) > 1
                                               or count(transactionDate/value) > 1
                                               or count(transactionCoding/transactionCode) > 1
                                               or count(transactionCoding/equitySwapInvolved) > 1
                                               or count(transactionAmounts/transactionShares/value) > 1
                                               or count(transactionAmounts/transactionPricePerShare/value) > 1
                                               or count(transactionAmounts/transactionAcquiredDisposedCode/value) > 1
                                               or count(postTransactionAmounts/sharesOwnedFollowingTransaction/value) > 1
                                               or count(ownershipNature/directOrIndirectOwnership/value) > 1
                                               or count(ownershipNature/natureOfOwnership/value) > 1
                                               or count(conversionOrExercisePrice/value) > 1
                                               or count(underlyingSecurity/underlyingSecurityTitle/value) > 1
                                               or count(underlyingSecurity/underlyingSecurityShares/value) > 1') t
        WHERE f.accession_no = ANY(p_accessions)
          AND t.element IN ('nonDerivativeTransaction', 'nonDerivativeHolding',
                            'derivativeTransaction', 'derivativeHolding')
    ),
    inserted AS (
        INSERT INTO silver.insider_transactions
            (accession_no, line, company_id, kind, derivative, security_title, transaction_date, code,
             equity_swap, shares, price, acquired_disposed, shares_after, direct_indirect, nature,
             exercise_price, underlying_title, underlying_shares)
        SELECT l.accession_no, l.line::int, l.company_id,
               CASE WHEN l.element LIKE '%Holding' THEN 'holding' ELSE 'transaction' END,
               l.element LIKE 'derivative%', nullif(btrim(l.security_title), ''),
               silver.xml_date(l.transaction_date), nullif(btrim(l.code), ''),
               silver.xml_flag(l.equity_swap), silver.xml_number(l.shares),
               silver.xml_number(l.price),
               CASE WHEN btrim(l.acquired_disposed) IN ('A', 'D') THEN btrim(l.acquired_disposed) END,
               silver.xml_number(l.shares_after), nullif(btrim(l.direct_indirect), ''),
               nullif(btrim(l.nature), ''), silver.xml_number(l.exercise_price),
               nullif(btrim(l.underlying_title), ''), silver.xml_number(l.underlying_shares)
        FROM lines l
        RETURNING 1
    )
    -- 059: findings of the lines, one row per filing and kind
    INSERT INTO ops.insider_findings (accession_no, kind, detail)
    SELECT l.accession_no, 'bad_date',
           left(string_agg('line ' || l.line || ' transactionDate ' || btrim(l.transaction_date),
                           '; ' ORDER BY l.line), 500)
    FROM lines l
    WHERE btrim(l.transaction_date) <> '' AND silver.xml_date(l.transaction_date) IS NULL
    GROUP BY l.accession_no
    UNION ALL
    SELECT l.accession_no, 'several_values',
           left('lines ' || string_agg(l.line::text, ', ' ORDER BY l.line), 500)
    FROM lines l
    WHERE l.several = 'true'
    GROUP BY l.accession_no
    ON CONFLICT (accession_no, kind) DO UPDATE
        SET detail = left(ops.insider_findings.detail || '; ' || EXCLUDED.detail, 500);

    SELECT count(*) INTO n FROM silver.insider_filings WHERE accession_no = ANY(p_accessions);
    RETURN n;
END
$$;
