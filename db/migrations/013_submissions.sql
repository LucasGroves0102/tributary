-- Filing history and company metadata for every SEC filer, from the nightly bulk
-- submissions.zip (one JSON per CIK, plus CIK..-submissions-NNN.json pages for long histories).
--
-- History lands in its own bronze table, not bronze.filings: bronze.filings is the filings.raw
-- sink, and publishing years of history there would make doc-fetcher fetch documents for it and
-- skew the ops freshness views. silver.filings unions both sources.

-- One row per CIK: the latest submissions JSON without its filing arrays.
CREATE TABLE bronze.submissions (
    cik            text        PRIMARY KEY,
    content_sha256 text        NOT NULL,  -- over the CIK's main JSON and its pages, as zipped
    payload        jsonb       NOT NULL,
    filings        int         NOT NULL,  -- filings listed (pages can repeat one)
    snapshot       timestamptz NOT NULL,  -- Last-Modified of the zip it came from
    loaded_at      timestamptz NOT NULL DEFAULT now()
);

-- One row per (filing, filer), like bronze.filings. accepted_at is NULL when the SEC gives none.
CREATE TABLE bronze.submission_filings (
    accession_no            text    NOT NULL,
    cik                     text    NOT NULL,
    form                    text    NOT NULL,
    filing_date             date    NOT NULL,  -- EDGAR date (US Eastern)
    accepted_at             timestamptz,
    report_date             date,
    primary_document        text,
    primary_doc_description text,
    items                   text,              -- 8-K items, e.g. "2.02,9.01"
    file_number             text,
    size_bytes              bigint,
    is_xbrl                 boolean,
    is_inline_xbrl          boolean,
    PRIMARY KEY (accession_no, cik)
);

CREATE INDEX submission_filings_cik_date ON bronze.submission_filings (cik, filing_date DESC);
CREATE INDEX submission_filings_form_date ON bronze.submission_filings (form, filing_date DESC);

-- Progress of each zip snapshot. A load can span many runs: each run loads a bounded number of
-- changed CIKs and commits as it goes; finished_at is set by the run that finds none left.
CREATE TABLE bronze.submissions_loads (
    snapshot     timestamptz PRIMARY KEY,  -- Last-Modified of the zip
    started_at   timestamptz NOT NULL DEFAULT now(),
    runs         int         NOT NULL DEFAULT 0,
    ciks_loaded  int         NOT NULL DEFAULT 0,  -- changed CIKs written, across runs
    finished_at  timestamptz
);

ALTER TABLE silver.companies
    ADD COLUMN tickers                text[],
    ADD COLUMN exchanges              text[],
    ADD COLUMN fiscal_year_end        text CHECK (fiscal_year_end ~ '^\d{4}$'),  -- MMDD
    ADD COLUMN state_of_incorporation text,
    ADD COLUMN filer_categories       text[];  -- e.g. {Large accelerated filer}

CREATE INDEX companies_sic ON silver.companies (sic);

-- Metadata and name aliases of companies whose current or former CIKs are in ciks (all when
-- NULL), from bronze.submissions. Companies without a submissions row keep what they have.
-- Returns the number of companies changed.
CREATE FUNCTION silver.refresh_company_metadata(ciks text[] DEFAULT NULL)
RETURNS int
LANGUAGE plpgsql AS $$
DECLARE
    changed int;
BEGIN
    UPDATE silver.companies c SET
        sic = nullif(s.payload->>'sic', ''),
        sic_description = nullif(s.payload->>'sicDescription', ''),
        entity_type = nullif(s.payload->>'entityType', ''),
        tickers = m.tickers,
        exchanges = m.exchanges,
        fiscal_year_end = m.fiscal_year_end,
        state_of_incorporation = nullif(s.payload->>'stateOfIncorporation', ''),
        filer_categories = m.filer_categories,
        updated_at = now()
    FROM bronze.submissions s
    CROSS JOIN LATERAL (
        SELECT ARRAY(SELECT t FROM jsonb_array_elements_text(s.payload->'tickers')
                         WITH ORDINALITY AS x(t, i)
                     WHERE t <> '' ORDER BY i) AS tickers,
               ARRAY(SELECT e FROM jsonb_array_elements_text(s.payload->'exchanges')
                         WITH ORDINALITY AS x(e, i)
                     WHERE e <> '' GROUP BY e ORDER BY min(i)) AS exchanges,  -- SEC order
               CASE WHEN s.payload->>'fiscalYearEnd' ~ '^\d{4}$'
                    THEN s.payload->>'fiscalYearEnd' END AS fiscal_year_end,
               ARRAY(SELECT btrim(x) FROM regexp_split_to_table(
                         coalesce(s.payload->>'category', ''), '<br>') x
                     WHERE btrim(x) <> '') AS filer_categories
    ) m
    WHERE s.cik = c.current_cik
      AND (ciks IS NULL OR EXISTS (
          SELECT 1 FROM silver.company_ciks k
          WHERE k.company_id = c.company_id AND k.cik = ANY(ciks)))
      AND (c.sic, c.sic_description, c.entity_type, c.tickers, c.exchanges, c.fiscal_year_end,
           c.state_of_incorporation, c.filer_categories)
          IS DISTINCT FROM
          (nullif(s.payload->>'sic', ''), nullif(s.payload->>'sicDescription', ''),
           nullif(s.payload->>'entityType', ''), m.tickers, m.exchanges, m.fiscal_year_end,
           nullif(s.payload->>'stateOfIncorporation', ''), m.filer_categories);
    GET DIAGNOSTICS changed = ROW_COUNT;

    -- names and former names of every known CIK: same rows company-loader derives per CIK
    DELETE FROM silver.company_aliases a
    USING bronze.submissions s
    WHERE a.source = 'sec_submissions' AND a.cik = s.cik
      AND (ciks IS NULL OR s.cik = ANY(ciks));

    INSERT INTO silver.company_aliases
        (company_id, cik, alias_type, alias, valid_from, valid_to, source)
    SELECT k.company_id, s.cik, 'name', n.name, n.valid_from, n.valid_to, 'sec_submissions'
    FROM bronze.submissions s
    JOIN silver.company_ciks k ON k.cik = s.cik
    CROSS JOIN LATERAL (
        SELECT s.payload->>'name' AS name, k.valid_from, k.valid_to
        UNION ALL
        SELECT f->>'name', left(f->>'from', 10)::date, left(f->>'to', 10)::date
        FROM jsonb_array_elements(s.payload->'formerNames') f
    ) n
    WHERE (ciks IS NULL OR s.cik = ANY(ciks)) AND coalesce(n.name, '') <> '';

    RETURN changed;
END
$$;

-- Every known filing, one row per (filing, filer): the live filings.raw sink plus the bulk
-- history. source is feed, daily-index or submissions. accepted_at is exact for feed rows and
-- from the bulk history otherwise (NULL when neither has it).
CREATE VIEW silver.filings AS
SELECT f.accession_no,
       f.cik,
       k.company_id,
       f.form_type AS form,
       coalesce(h.filing_date, (f.filed_at AT TIME ZONE 'America/New_York')::date)
           AS filing_date,
       CASE WHEN coalesce(f.payload->>'source', 'feed') = 'feed' THEN f.filed_at
            ELSE h.accepted_at END AS accepted_at,
       h.report_date,
       h.primary_document,
       coalesce(f.payload->>'source', 'feed') AS source
FROM bronze.filings f
LEFT JOIN bronze.submission_filings h USING (accession_no, cik)
LEFT JOIN silver.company_ciks k ON k.cik = f.cik
UNION ALL
SELECT h.accession_no,
       h.cik,
       k.company_id,
       h.form,
       h.filing_date,
       h.accepted_at,
       h.report_date,
       h.primary_document,
       'submissions'
FROM bronze.submission_filings h
LEFT JOIN silver.company_ciks k ON k.cik = h.cik
WHERE NOT EXISTS (
    SELECT 1 FROM bronze.filings f WHERE f.accession_no = h.accession_no AND f.cik = h.cik
);
