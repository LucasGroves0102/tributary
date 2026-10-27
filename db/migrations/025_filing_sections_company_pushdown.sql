-- gold.filing_sections: let a filter on company_id reach the base tables.
--
-- Postgres pushes a WHERE condition into a DISTINCT ON subquery (a view) only when it is on a
-- DISTINCT ON column. 009's key, (accession_no, section_key), left company_id out, so every
-- "this company's sections" query de-duplicated all sections first: after the 5-year crawl
-- (1.78M sections) /v1/filings/latest for 20 companies took 4.1 s against the 5 s query limit.
-- company_id first in the key gives the same rows (a section's document has one CIK, a CIK one
-- company) and the filter now uses the indexes: 4.1 s → 0.03 s on hermes, identical results.
--
-- 009's body otherwise unchanged; security_invoker re-issued so RLS keeps applying.

CREATE OR REPLACE VIEW gold.filing_sections AS
SELECT DISTINCT ON (k.company_id, s.accession_no, s.section_key)
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
ORDER BY k.company_id, s.accession_no, s.section_key, s.parser = 'rust' DESC, s.parsed_at DESC;

ALTER VIEW gold.filing_sections SET (security_invoker = true);
