-- Document backfill for every operating company (doc-fetcher's `doc-crawler`): 10-K, 10-Q and
-- 8-K primary documents (+ 8-K EX-99.x exhibits) of every company with gold financials, for the
-- last 5 years, fetched as SEC caller class `bulk` and stored in the same bronze tables as the
-- live doc-fetcher (gzip, as before).
--
-- ops.doc_crawl_targets is the work queue and the durable checkpoint: one row per filing, planned
-- from silver.filings by ops.doc_crawl_plan, claimed in batches, and marked done in the same
-- transaction that stores the documents. A crash leaves claimed rows that a later claim takes
-- back after the lease expires; done rows whose documents.backfill event was not confirmed
-- (published_at NULL) are published again (outbox), so the parser never misses a document.

-- Bodies are gzip already: skip TOAST's pglz attempt on them (applies to new rows only).
ALTER TABLE bronze.filing_documents ALTER COLUMN content_gzip SET STORAGE EXTERNAL;
ALTER TABLE bronze.filing_exhibits ALTER COLUMN content_gzip SET STORAGE EXTERNAL;

CREATE TABLE ops.doc_crawl_targets (
    accession_no     text        PRIMARY KEY,
    cik              text        NOT NULL,  -- the operating company's filer CIK
    company_id       text        NOT NULL,
    form             text        NOT NULL,
    filing_date      date        NOT NULL,
    report_date      date,
    primary_document text,                  -- from the submissions history; NULL: read the index
    items            text,                  -- 8-K items, e.g. "2.02,9.01"
    priority         smallint    NOT NULL,  -- 0 tracked companies, 1 the rest; lower goes first
    status           text        NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'claimed', 'done', 'failed', 'skipped')),
    attempts         smallint    NOT NULL DEFAULT 0,  -- failed fetches so far
    not_before       timestamptz,           -- retry backoff
    claimed_at       timestamptz,
    finished_at      timestamptz,
    published_at     timestamptz,           -- documents.backfill event confirmed (done rows)
    last_error       text,
    files            int,                   -- primary document + exhibits stored
    raw_bytes        bigint,                -- uncompressed
    stored_bytes     bigint,                -- gzip, as stored
    planned_at       timestamptz NOT NULL DEFAULT now()
);

-- Claim order: tracked companies first, then most recent first.
CREATE INDEX doc_crawl_targets_queue ON ops.doc_crawl_targets (priority, filing_date DESC, accession_no)
    WHERE status IN ('pending', 'claimed');
CREATE INDEX doc_crawl_targets_unpublished ON ops.doc_crawl_targets (finished_at)
    WHERE status = 'done' AND published_at IS NULL;

-- One row: the operator's pause switch and the crawler's last reported state.
CREATE TABLE ops.doc_crawl_control (
    id              boolean     PRIMARY KEY DEFAULT true CHECK (id),
    paused          boolean     NOT NULL DEFAULT false,  -- UPDATE to pause/resume without a deploy
    state           text,       -- running | idle | paused | disk_low | stopped
    disk_free_bytes bigint,
    min_free_bytes  bigint,
    heartbeat_at    timestamptz,
    planned_at      timestamptz
);
INSERT INTO ops.doc_crawl_control DEFAULT VALUES;

-- Add the filings to crawl that aren't queued or stored yet: 10-K, 10-K/A, 10-Q, 10-Q/A and 8-K
-- filed on or after p_since by companies with gold financials (operating companies). 8-Ks only
-- when one of their items is in p_8k_items (NULL: every 8-K; unknown items: kept). A filing with
-- several filers is queued once, under a tracked company's CIK when there is one. Returns the
-- number of filings added.
CREATE FUNCTION ops.doc_crawl_plan(p_since date, p_8k_items text[] DEFAULT NULL)
RETURNS int
LANGUAGE plpgsql AS $$
DECLARE
    added int;
BEGIN
    INSERT INTO ops.doc_crawl_targets
        (accession_no, cik, company_id, form, filing_date, report_date, primary_document, items,
         priority)
    SELECT DISTINCT ON (f.accession_no)
           f.accession_no, f.cik, f.company_id, f.form, f.filing_date, f.report_date,
           f.primary_document, h.items, CASE WHEN c.tracked THEN 0 ELSE 1 END
    FROM silver.filings f
    JOIN silver.companies c ON c.company_id = f.company_id
    LEFT JOIN bronze.submission_filings h
           ON h.accession_no = f.accession_no AND h.cik = f.cik
    WHERE f.form IN ('10-K', '10-K/A', '10-Q', '10-Q/A', '8-K')
      AND f.filing_date >= p_since
      AND EXISTS (SELECT 1 FROM gold.quarterly_financials q WHERE q.company_id = f.company_id)
      AND (f.form <> '8-K' OR p_8k_items IS NULL OR h.items IS NULL
           OR regexp_split_to_array(btrim(h.items), '\s*,\s*') && p_8k_items)
      AND NOT EXISTS (SELECT 1 FROM bronze.filing_documents d
                      WHERE d.accession_no = f.accession_no)
    ORDER BY f.accession_no, c.tracked DESC, f.primary_document IS NULL, f.cik
    ON CONFLICT (accession_no) DO NOTHING;
    GET DIAGNOSTICS added = ROW_COUNT;
    UPDATE ops.doc_crawl_control SET planned_at = now();
    RETURN added;
END
$$;

-- Progress per form and filing year. Counts and sizes only, never document text.
CREATE VIEW ops.doc_crawl_progress AS
SELECT form,
       extract(year FROM filing_date)::int AS year,
       count(*) AS targets,
       count(*) FILTER (WHERE status = 'done') AS done,
       count(*) FILTER (WHERE status IN ('pending', 'claimed')) AS remaining,
       count(*) FILTER (WHERE status = 'failed') AS failed,
       count(*) FILTER (WHERE status = 'skipped') AS skipped,
       coalesce(sum(files), 0) AS files,
       coalesce(sum(raw_bytes), 0) AS raw_bytes,
       coalesce(sum(stored_bytes), 0) AS stored_bytes
FROM ops.doc_crawl_targets
GROUP BY 1, 2;

-- One row: crawler state, totals, the last hour's rate, an ETA at that rate, and the final
-- stored size projected from the average stored size per done filing of each form.
CREATE VIEW ops.doc_crawl_status AS
WITH by_form AS (
    SELECT form,
           count(*) AS targets,
           count(*) FILTER (WHERE status = 'done') AS done,
           count(*) FILTER (WHERE status IN ('pending', 'claimed')) AS remaining,
           count(*) FILTER (WHERE status = 'failed') AS failed,
           count(*) FILTER (WHERE status = 'skipped') AS skipped,
           count(*) FILTER (WHERE status = 'done' AND published_at IS NULL) AS unpublished,
           count(*) FILTER (WHERE status = 'done' AND stored_bytes > 0) AS sampled,
           coalesce(sum(files), 0) AS files,
           coalesce(sum(raw_bytes), 0) AS raw_bytes,
           coalesce(sum(stored_bytes), 0) AS stored_bytes,
           count(*) FILTER (WHERE status IN ('done', 'failed', 'skipped')
                              AND finished_at > now() - interval '1 hour') AS finished_last_hour,
           coalesce(sum(stored_bytes) FILTER (WHERE finished_at > now() - interval '1 hour'), 0)
               AS stored_bytes_last_hour
    FROM ops.doc_crawl_targets
    GROUP BY form
),
totals AS (
    SELECT coalesce(sum(targets), 0)::bigint AS targets,
           coalesce(sum(done), 0)::bigint AS done,
           coalesce(sum(remaining), 0)::bigint AS remaining,
           coalesce(sum(failed), 0)::bigint AS failed,
           coalesce(sum(skipped), 0)::bigint AS skipped,
           coalesce(sum(unpublished), 0)::bigint AS unpublished,
           coalesce(sum(files), 0)::bigint AS files,
           coalesce(sum(raw_bytes), 0)::bigint AS raw_bytes,
           coalesce(sum(stored_bytes), 0)::bigint AS stored_bytes,
           coalesce(sum(finished_last_hour), 0)::bigint AS finished_last_hour,
           coalesce(sum(stored_bytes_last_hour), 0)::bigint AS stored_bytes_last_hour,
           -- forms with nothing stored yet add nothing to the projection
           sum(stored_bytes + CASE WHEN sampled > 0
                                   THEN stored_bytes::numeric / sampled * remaining
                                   ELSE 0 END)::bigint AS projected_stored_bytes
    FROM by_form
)
SELECT c.state,
       c.paused,
       c.heartbeat_at,
       c.planned_at,
       c.disk_free_bytes,
       c.min_free_bytes,
       t.targets,
       t.done,
       t.remaining,
       t.failed,
       t.skipped,
       t.unpublished,
       t.files,
       t.raw_bytes,
       t.stored_bytes,
       round(t.raw_bytes::numeric / nullif(t.stored_bytes, 0), 1) AS compression_ratio,
       t.projected_stored_bytes,
       t.finished_last_hour,
       t.stored_bytes_last_hour,
       CASE WHEN t.finished_last_hour > 0
            THEN t.remaining::float8 / t.finished_last_hour * interval '1 hour' END AS eta
FROM ops.doc_crawl_control c
CROSS JOIN totals t;

GRANT SELECT ON ops.doc_crawl_progress, ops.doc_crawl_status TO ops_reader;
