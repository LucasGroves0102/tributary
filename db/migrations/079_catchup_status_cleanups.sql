-- Leftovers (Riley test 6 follow-ups and the 2026-11-16 docs pass; db/DESIGN.md "079").
--
-- 1. Catch-up status says why a run did nothing. When a hand-run job holds parser-index's lock (the
--    section-passages Job, 2026-11-16), the hourly run is locked out and its catch-up never ran, so
--    ops.catchup_status kept an old row or, with none, said "current, 0 behind" while 409,157
--    filings' passages were behind. ops.catchup_runs gains `note`; ops.catchup_skipped(kind, version,
--    note) records a run that did nothing, keeping the last count when that count is of the same
--    version (else the caller measures: catchup.py). The view gains `note` (the latest run's) as its
--    last column, and a skipped run counts as progress: another job holds the work, so the
--    CatchupStalled alert stays quiet while it runs. ops.catchup_record gains a ninth argument, the
--    note (the eight-argument form stays for a job image from before this migration).
--    The other half is in Python: a new version's backlog is recorded at the start of its first run
--    (both catch-ups), so the status never reads "current, 0 behind" while that run works.
-- 2. The parser-output checks' backlog in 0.2 s instead of 149 s. ops.section_check_backlog's LEFT
--    JOIN ... WHERE (no check OR an older one) is estimated at one row from the checks table's
--    statistics (8,161 rows when last analysed), so the planner joins every document to a seq scan of
--    silver.company_ciks: 149 s on hermes (2026-11-17, EXPLAIN ANALYZE, 12.2M buffers) per batch of
--    2,000, twice per run (the batch and the count), hence 4,000 documents a run (6.5 days for
--    630,653). As NOT EXISTS (an anti-join the planner estimates from the documents) the same rows
--    take 0.17 s (EXPLAIN ANALYZE, LIMIT 10,000). ops.statement_backlog has the same shape (1.2 s
--    today, the same misestimate): rewritten alike. Same rows: section_checks and statement_reads
--    have one row per accession (primary keys) and NOT NULL version/reader.
-- 3. gold.search_passages (032) and the nine-argument gold.search_page (052) dropped: kept since
--    052/065 for an API pod from before them; every pod since 065 calls the ten-argument form
--    (store.py), and nothing else calls them (the passages catch-up writes through
--    gold.refresh_section_search, 068).
--
-- Locks on hermes: ALTER TABLE ops.catchup_runs ADD COLUMN (nullable, no default: catalog only, a
-- moment's ACCESS EXCLUSIVE on a table both hourly catch-ups write; lock_timeout 3 s: re-run the hook
-- if a catch-up held it), CREATE OR REPLACE of the view and functions (catalog only), DROP FUNCTION
-- (catalog only). No backout touches these functions.

SET LOCAL lock_timeout = '3s';

-- 1. Why a run did nothing ------------------------------------------------------------------------

ALTER TABLE ops.catchup_runs ADD COLUMN note text;  -- e.g. 'skipped: lock held'

CREATE FUNCTION ops.catchup_record(p_kind text, p_version text, p_behind bigint,
                                   p_in_flight bigint, p_failed bigint, p_oldest date,
                                   p_done bigint, p_seconds real, p_note text)
RETURNS void
LANGUAGE sql AS $$
    INSERT INTO ops.catchup_runs (kind, run_at, version, behind, in_flight, failed, oldest, done,
                                  seconds, note)
    VALUES (p_kind, clock_timestamp(), p_version, p_behind, p_in_flight, p_failed, p_oldest, p_done,
            p_seconds, p_note);
    DELETE FROM ops.catchup_runs WHERE kind = p_kind AND run_at < now() - interval '30 days';
$$;

-- A run that did nothing (p_note says why): the kind's last counts again when they are of p_version.
-- True when recorded; NULL when there is no such row (the caller measures and records instead).
CREATE FUNCTION ops.catchup_skipped(p_kind text, p_version text, p_note text) RETURNS boolean
LANGUAGE sql AS $$
    INSERT INTO ops.catchup_runs (kind, run_at, version, behind, in_flight, failed, oldest, done,
                                  seconds, note)
    SELECT l.kind, clock_timestamp(), l.version, l.behind, l.in_flight, l.failed, l.oldest, 0, 0,
           p_note
    FROM (SELECT * FROM ops.catchup_runs x WHERE x.kind = p_kind ORDER BY x.run_at DESC LIMIT 1) l
    WHERE l.version = p_version
    RETURNING true
$$;

-- 071's view; a skipped run counts as progress (header, 1); `note` last.
CREATE OR REPLACE VIEW ops.catchup_status AS
SELECT c.kind,
       CASE WHEN c.paused THEN 'paused'
            WHEN coalesce(r.behind, 0) + coalesce(r.in_flight, 0) = 0 THEN 'current'
            WHEN greatest(c.started_at, d.last_done_at) < now() - interval '1 day' THEN 'stalled'
            ELSE 'catching_up' END AS state,
       c.paused,
       c.version,
       c.started_at,
       r.run_at AS measured_at,
       coalesce(r.behind, 0) AS behind,
       coalesce(r.in_flight, 0) AS in_flight,
       coalesce(r.failed, 0) AS failed,
       r.oldest,
       coalesce(d.done_last_day, 0) AS done_last_day,
       greatest(c.started_at, d.last_done_at) AS progress_at,
       CASE WHEN coalesce(r.behind, 0) + coalesce(r.in_flight, 0) = 0 THEN interval '0'
            WHEN NOT c.paused AND d.done_last_day > 0
            THEN (r.behind + r.in_flight)::float8 / d.done_last_day * interval '1 day' END AS eta,
       r.note
FROM ops.catchup c
LEFT JOIN LATERAL (
    SELECT x.* FROM ops.catchup_runs x WHERE x.kind = c.kind ORDER BY x.run_at DESC LIMIT 1
) r ON true
LEFT JOIN LATERAL (
    SELECT sum(x.done) FILTER (WHERE x.run_at > now() - interval '1 day') AS done_last_day,
           max(x.run_at) FILTER (WHERE x.done > 0 OR x.note LIKE 'skipped:%') AS last_done_at
    FROM ops.catchup_runs x WHERE x.kind = c.kind
) d ON true;

-- 2. Backlogs as anti-joins -----------------------------------------------------------------------

-- 072's rows (header, 2).
CREATE OR REPLACE FUNCTION ops.section_check_backlog(p_version integer)
RETURNS TABLE (accession_no text, filed date)
LANGUAGE sql STABLE AS $$
    SELECT d.accession_no, d.filed
    FROM bronze.filing_documents d
    WHERE d.fetched_at < now() - interval '1 day'
      AND NOT EXISTS (SELECT 1 FROM ops.section_checks c
                      WHERE c.accession_no = d.accession_no AND c.version >= p_version)
      AND EXISTS (SELECT 1 FROM silver.company_ciks k WHERE k.cik = d.cik)
$$;

-- 074's rows (header, 2).
CREATE OR REPLACE FUNCTION ops.statement_backlog(p_reader text)
RETURNS TABLE (accession_no text, filed date)
LANGUAGE sql STABLE AS $$
    SELECT d.accession_no, d.filed
    FROM bronze.filing_documents d
    WHERE d.form IN ('10-K', '10-Q') AND d.period_of_report IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM silver.statement_reads r
                      WHERE r.accession_no = d.accession_no AND r.reader = p_reader)
      AND EXISTS (SELECT 1 FROM silver.filing_tables t WHERE t.accession_no = d.accession_no)
      AND EXISTS (SELECT 1 FROM silver.company_ciks k WHERE k.cik = d.cik)
$$;

-- 3. Search's old forms (header, 3) ---------------------------------------------------------------

DROP FUNCTION gold.search_passages(tsquery, text[], text, text, date, date, int, int);
DROP FUNCTION gold.search_page(tsquery, text[], text, text, date, date, text, int, int);
