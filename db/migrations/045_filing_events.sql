-- 8-K items for the Query API's earnings releases and events (query_api/events.py: the web app's
-- Earnings view and "Since the last report", the agent's get_earnings_release and
-- get_events_since_report).
--
-- Every 8-K's item numbers ("2.02,9.01") are in the SEC's submissions history, which
-- company-loader-submissions loads nightly into bronze.submission_filings.items (013). On hermes on
-- 2026-11-08 all 140,884 8-Ks filed since 2024-10-01 had them. They are filing-index metadata,
-- public like silver.filings (017), so the Query API may read those three columns; the documents'
-- text stays behind RLS (silver.filing_sections). A filing the nightly load doesn't have yet (the
-- live feed runs up to a day ahead of it) comes back with items NULL: the API then reads the item
-- headings the parser found in the 8-K (its sections keyed "2.02"), under RLS.
--
-- silver.company_8ks(company, from): the company's 8-K and 8-K/A filings filed on or after `from`,
-- one row per accession (a filing with several filers once, its submissions row preferred), items
-- as an array. Plain SQL, SECURITY INVOKER: silver.filings runs as its owner (017) and the items
-- come through the column grant below. One company is a few index probes (NVIDIA, two years of
-- 8-Ks: 1.4 ms on hermes); there is no catalog-wide form of it on purpose.
--
-- Locks: GRANT and CREATE FUNCTION update the catalog only. Neither blocks reads of
-- bronze.submission_filings or the nightly load's writes (checked on a throwaway database: the
-- GRANT holds no lock on the table beyond the transaction's own catalog rows).

GRANT SELECT (accession_no, cik, items) ON bronze.submission_filings TO query_api;

CREATE FUNCTION silver.company_8ks(p_company_id text, p_from date)
RETURNS TABLE (
    accession_no     text,
    cik              text,
    form             text,
    filing_date      date,
    accepted_at      timestamptz,
    report_date      date,         -- the 8-K's date of report: the earliest event it reports
    primary_document text,
    items            text[]        -- {2.02,9.01}; NULL until the nightly submissions load has it
)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (f.accession_no)
           f.accession_no, f.cik, f.form, f.filing_date, f.accepted_at, f.report_date,
           f.primary_document,
           CASE WHEN btrim(coalesce(h.items, '')) <> ''
                THEN regexp_split_to_array(btrim(h.items), '\s*,\s*') END
    FROM silver.filings f
    LEFT JOIN bronze.submission_filings h ON h.accession_no = f.accession_no AND h.cik = f.cik
    WHERE f.company_id = p_company_id
      AND f.form IN ('8-K', '8-K/A')
      AND f.filing_date >= p_from
    ORDER BY f.accession_no, h.items IS NULL, f.cik
$$;

GRANT EXECUTE ON FUNCTION silver.company_8ks(text, date) TO query_api;
