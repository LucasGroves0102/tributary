-- The public status page (Query API `/v1/status/data`, web `#/status`): how fresh, complete and
-- checked the data is, for anyone, without a token. query_api can't read bronze or ops tables, so,
-- as in 026, each figure comes from a SECURITY DEFINER function (search_path pinned, EXECUTE
-- revoked from PUBLIC, granted to query_api) that returns aggregate counts and timestamps only:
-- never a company, ticker, accession number, value or text. Never widen them to rows.
--
--   ops.public_ingest():       last 7 days of bronze.filings: filings, how many the live feed
--                              caught, and p50/p95 seconds from EDGAR acceptance to bronze
--                              (feed rows only, as ops.filing_latency)
--   ops.public_completeness(): the nightly completeness check (027) over the last 30 days
--   ops.public_coverage():     companies with gold financials, stored documents, search-indexed
--                              filings and their sections
--   ops.public_quality():      ops.quality_summary (finding and company counts per check)
--
-- Cost: the window queries read bronze.filings through a new index on filed_at (a few days of
-- rows instead of the table); companies with financials is a loose index scan over
-- gold.quarterly_financials' primary key (one probe per company, not every row); documents and
-- ops.section_index are counted whole (one row per filing, a few hundred thousand). The Query API
-- caches the result for 60 s. silver.filings (a UNION over ~26M rows) and the gold UNION views are
-- never read.

-- Range scans for "the last N days" of bronze.filings (also makes 026's max(filed_at) an index
-- probe). Built in the migration's transaction: writes to bronze.filings wait for it.
CREATE INDEX filings_filed_at ON bronze.filings (filed_at);

CREATE FUNCTION ops.public_ingest(
    OUT window_start timestamptz,
    OUT filings bigint,        -- distinct accessions accepted in the window, any source
    OUT via_feed bigint,       -- of those, caught by the live poller (source feed)
    OUT p50_seconds float8,    -- EDGAR acceptance → bronze, feed filings only
    OUT p95_seconds float8)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
    WITH a AS (
        SELECT f.accession_no,
               bool_or(f.payload->>'source' = 'feed') AS live,
               min(f.filed_at) FILTER (WHERE f.payload->>'source' = 'feed') AS accepted_at,
               min(f.ingested_at) FILTER (WHERE f.payload->>'source' = 'feed') AS bronze_at
        FROM bronze.filings f
        WHERE f.filed_at >= now() - interval '7 days'
        GROUP BY f.accession_no
    )
    SELECT now() - interval '7 days',
           count(*),
           count(*) FILTER (WHERE live),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM bronze_at - accepted_at)::float8)
               FILTER (WHERE live),
           percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM bronze_at - accepted_at)::float8)
               FILTER (WHERE live)
    FROM a
$$;

-- Days are EDGAR (US Eastern) dates; `missing` is what each day's latest check found before it
-- republished them (027), so it is not what is missing now.
CREATE FUNCTION ops.public_completeness(
    OUT window_start date,
    OUT days bigint,              -- days checked in the window (0 until the first nightly run)
    OUT indexed bigint,           -- accessions in those days' EDGAR master indexes
    OUT missing bigint,           -- accessions absent from bronze at each day's latest check
    OUT republished bigint,       -- (accession, filer) rows republished to filings.raw
    OUT days_with_missing bigint,
    OUT last_checked_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
    SELECT (now() AT TIME ZONE 'America/New_York')::date - 30,
           count(*), sum(indexed), sum(missing), sum(republished),
           count(*) FILTER (WHERE missing > 0), max(checked_at)
    FROM ops.filing_completeness
    WHERE day >= (now() AT TIME ZONE 'America/New_York')::date - 30
$$;

CREATE FUNCTION ops.public_coverage(
    OUT companies_with_financials bigint,  -- companies with any gold.quarterly_financials row
    OUT documents bigint,                  -- primary documents stored (bronze.filing_documents)
    OUT indexed_filings bigint,            -- filings whose parsed sections are search-indexed
    OUT sections bigint,                   -- sections gold serves for those filings
    OUT last_indexed_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
    WITH RECURSIVE companies(company_id) AS (
        (SELECT q.company_id FROM gold.quarterly_financials q ORDER BY q.company_id LIMIT 1)
        UNION ALL
        SELECT (SELECT q.company_id FROM gold.quarterly_financials q
                WHERE q.company_id > c.company_id ORDER BY q.company_id LIMIT 1)
        FROM companies c
        WHERE c.company_id IS NOT NULL
    )
    SELECT (SELECT count(company_id) FROM companies),
           (SELECT count(*) FROM bronze.filing_documents),
           i.filings, i.sections, i.last_indexed_at
    FROM (SELECT count(*) AS filings, coalesce(sum(sections), 0)::bigint AS sections,
                 max(indexed_at) AS last_indexed_at
          FROM ops.section_index) i
$$;

-- One row per check (014, 018, 023), with the totals repeated on each row as in the view.
CREATE FUNCTION ops.public_quality()
RETURNS TABLE (check_name text, severity text, findings bigint, companies bigint,
               companies_checked bigint, last_checked_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
    SELECT s.check_name, s.severity, s.findings, s.companies, s.companies_checked,
           s.last_checked_at
    FROM ops.quality_summary s
    ORDER BY CASE s.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, s.check_name
$$;

REVOKE ALL ON FUNCTION ops.public_ingest(), ops.public_completeness(), ops.public_coverage(),
    ops.public_quality() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.public_ingest(), ops.public_completeness(), ops.public_coverage(),
    ops.public_quality() TO query_api;  -- USAGE on ops: 023
