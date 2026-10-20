-- Data for the research workspace UI (ROADMAP Phase 6): screener, watchlist, expected filing
-- dates, filing recaps and peer suggestions. Read by the Query API as query_api.
--
-- Public vs permissioned (services/query-api/README.md, "Public metadata"):
--   * company identity and metadata (silver.companies) and the SEC filing index
--     (silver.filings: accession, form, dates, period) are public SEC metadata;
--   * financial values (gold.*financials) and section text (gold.filing_sections) stay behind
--     the RLS policies of 007/014: everything below that reads them is SECURITY INVOKER or
--     security_invoker, so the caller's policies apply.

-- Company lookups by company_id (silver.filings, peers, watchlists).
CREATE INDEX company_ciks_company ON silver.company_ciks (company_id);

-- Which companies have any gold financials: membership only, no values. A plain view runs with
-- its owner's privileges, so it lists every covered company regardless of the caller's grants
-- (having XBRL financials at the SEC is public); the values stay behind RLS.
CREATE VIEW gold.financials_coverage AS
SELECT c.company_id
FROM silver.companies c
WHERE EXISTS (SELECT 1 FROM gold.quarterly_financials q WHERE q.company_id = c.company_id);

-- The latest value of every metric and span per company (span ttm includes balance-sheet
-- instants, as in gold.financials). Each value keeps its own period: a metric a company stopped
-- reporting keeps an old period_end, which the API returns with the value.
CREATE VIEW gold.latest_financials WITH (security_invoker = true) AS
SELECT DISTINCT ON (company_id, span, metric)
       company_id, span, metric, period_start, period_end, value, unit, value_source,
       accession_no, form, filed, ingested_at
FROM (
    SELECT company_id, 'quarter' AS span, metric, period_start, period_end, value, unit,
           value_source, accession_no, form, filed, ingested_at
    FROM gold.quarterly_financials
    UNION ALL
    SELECT g.company_id, 'ttm', g.metric, g.period_start, g.period_end, g.value, g.unit,
           g.value_source, g.accession_no, g.form, g.filed, g.ingested_at
    FROM gold.quarterly_financials g
    JOIN silver.metrics m ON m.metric = g.metric
    WHERE m.kind = 'instant'
    UNION ALL
    SELECT company_id, span, metric, period_start, period_end, value, unit, value_source,
           accession_no, form, filed, ingested_at
    FROM gold.derived_financials
) x
ORDER BY company_id, span, metric, period_end DESC;

-- SEC filer category (submissions "category", e.g. {Non-accelerated filer, Smaller reporting
-- company}) as large_accelerated | accelerated | non_accelerated; NULL when unknown. A smaller
-- reporting company with no accelerated status listed is non-accelerated.
CREATE FUNCTION gold.filer_category(categories text[]) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN EXISTS (SELECT 1 FROM unnest(categories) c WHERE c ILIKE 'large accelerated%')
            THEN 'large_accelerated'
        WHEN EXISTS (SELECT 1 FROM unnest(categories) c WHERE c ILIKE 'accelerated%')
            THEN 'accelerated'
        WHEN EXISTS (SELECT 1 FROM unnest(categories) c
                     WHERE c ILIKE 'non-accelerated%' OR c ILIKE 'smaller reporting%')
            THEN 'non_accelerated'
    END
$$;

-- Revenue size band: 0 under $100M, 1 $100M-$1B, 2 $1B-$10B, 3 $10B-$100B, 4 $100B and up;
-- NULL for no or non-positive revenue.
CREATE FUNCTION gold.revenue_band(revenue numeric) RETURNS int
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN revenue IS NULL OR revenue <= 0 THEN NULL
        WHEN revenue < 1e8 THEN 0
        WHEN revenue < 1e9 THEN 1
        WHEN revenue < 1e10 THEN 2
        WHEN revenue < 1e11 THEN 3
        ELSE 4
    END
$$;

GRANT SELECT ON gold.financials_coverage, gold.latest_financials TO query_api;
