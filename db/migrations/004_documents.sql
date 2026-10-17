-- Filing documents and sections (FR2b, FR3, FR6).

-- Bronze: the primary document of each tracked 10-K / 10-Q / 8-K, gzip-compressed as fetched.
CREATE TABLE bronze.filing_documents (
    accession_no     text        PRIMARY KEY,
    cik              text        NOT NULL,  -- the filer whose event triggered the fetch
    form             text        NOT NULL,
    filed            date        NOT NULL,
    period_of_report date,
    document_name    text        NOT NULL,
    url              text        NOT NULL,
    content_gzip     bytea       NOT NULL,
    content_sha256   text        NOT NULL,  -- of the uncompressed bytes
    size_bytes       int         NOT NULL,  -- uncompressed
    fetched_at       timestamptz NOT NULL DEFAULT now()
);

-- Silver: sections per document per parser. Keyed by parser so the Python and Rust parsers
-- can run side by side and be compared (week 4).
CREATE TABLE silver.filing_sections (
    accession_no   text        NOT NULL REFERENCES bronze.filing_documents,
    section_key    text        NOT NULL,  -- 10-K "1A", 10-Q "II-1A", 8-K "2.02"
    parser         text        NOT NULL,  -- python | rust
    parser_version text        NOT NULL,
    part           text,
    item           text        NOT NULL,
    title          text        NOT NULL,
    text           text        NOT NULL,
    char_start     int         NOT NULL,  -- offsets into the parser's extracted document text
    char_end       int         NOT NULL,
    parsed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (accession_no, section_key, parser)
);

-- Gold: one row per section, from the preferred parser, with provenance to cite.
CREATE VIEW gold.filing_sections AS
SELECT DISTINCT ON (s.accession_no, s.section_key)
       k.company_id,
       d.cik,
       d.accession_no,
       d.form,
       d.filed,
       d.period_of_report,
       s.section_key,
       s.part,
       s.item,
       s.title,
       s.text,
       d.url AS document_url,
       s.char_start,
       s.char_end,
       s.parser,
       s.parser_version,
       d.fetched_at AS ingested_at,
       s.parsed_at
FROM silver.filing_sections s
JOIN bronze.filing_documents d USING (accession_no)
JOIN silver.company_ciks k ON k.cik = d.cik
-- prefer the Rust parser once it exists, then the most recent parse
ORDER BY s.accession_no, s.section_key, s.parser = 'rust' DESC, s.parsed_at DESC;
