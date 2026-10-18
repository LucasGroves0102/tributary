-- 8-K exhibits (EX-99.x: earnings press releases, CFO commentary), one row per exhibit file.
-- The parser stores each as a section keyed by its type ("EX-99.1").

CREATE TABLE bronze.filing_exhibits (
    accession_no   text        NOT NULL REFERENCES bronze.filing_documents,
    exhibit_type   text        NOT NULL,  -- "EX-99.1"
    description    text,                  -- from the filing index, e.g. "PRESS RELEASE"
    document_name  text        NOT NULL,
    url            text        NOT NULL,
    content_gzip   bytea       NOT NULL,
    content_sha256 text        NOT NULL,
    size_bytes     int         NOT NULL,
    fetched_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (accession_no, exhibit_type)
);

GRANT SELECT (accession_no, exhibit_type, description, document_name, url, fetched_at)
    ON bronze.filing_exhibits TO query_api;

-- Exhibit sections cite the exhibit's own file.
CREATE OR REPLACE VIEW gold.filing_sections AS
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
       coalesce(e.url, d.url) AS document_url,
       s.char_start,
       s.char_end,
       s.parser,
       s.parser_version,
       d.fetched_at AS ingested_at,
       s.parsed_at
FROM silver.filing_sections s
JOIN bronze.filing_documents d USING (accession_no)
JOIN silver.company_ciks k ON k.cik = d.cik
LEFT JOIN bronze.filing_exhibits e
       ON e.accession_no = s.accession_no AND e.exhibit_type = s.section_key
ORDER BY s.accession_no, s.section_key, s.parser = 'rust' DESC, s.parsed_at DESC;

ALTER VIEW gold.filing_sections SET (security_invoker = true);
