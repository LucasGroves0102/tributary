-- gold.filing_sections: let a filter on accession_no reach the base tables.
--
-- 025 put company_id in the DISTINCT ON key so company filters push down. A filter on accession_no
-- still couldn't: the key says s.accession_no but the view outputs d.accession_no, and Postgres
-- pushes a WHERE condition into a DISTINCT ON subquery only when it is on the same output
-- expression as a key column. So every "this filing's sections" query de-duplicated all 1.8M
-- sections first (gold.filing_flags: ~8 s, 387 MB of temp files on hermes, over the Query API's
-- 5 s limit, so every /v1/filings/{acc}/recap answered 503). gold.refresh_section_search filters
-- the same way and sorted them all once per batch.
--
-- Outputting s.accession_no gives the same rows (JOIN ... USING (accession_no) makes them equal).
-- Measured as query_api with RLS and the catalog open (6,039 companies, 1.6M short sections,
-- local): gold.filing_flags 5.1-5.8 s and a 673 MB on-disk sort → 3 ms, same rows; a session
-- without a user still reads nothing. Hermes, the same query without RLS: 2 ms. company_id stays
-- first in the key (025) and security_invoker is re-issued so RLS keeps applying.
--
-- Locks: the view only, ACCESS EXCLUSIVE for an instant; no table rewrite, no index. The view is read
-- on every filing page and by the hourly section-index job, whose long transactions hold it: while
-- the migration waits for them, every reader queues behind it. So a 3 s lock_timeout: a busy moment
-- fails the deploy hook fast (re-run the release, or pause section-index first: the B6 runbook)
-- instead of stalling the reader for up to 30 s.

SET LOCAL lock_timeout = '3s';

CREATE OR REPLACE VIEW gold.filing_sections AS
SELECT DISTINCT ON (k.company_id, s.accession_no, s.section_key)
       k.company_id,
       d.cik,
       s.accession_no,
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
