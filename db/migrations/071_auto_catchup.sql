-- Automatic catch-up: stored data built with an older rule upgrades itself in the background, so a
-- deploy that changes the parser, gold's rules, the search passages or a quality check needs no
-- reparse, rebuild, passages or check Job by hand (db/DESIGN.md "071"; parser and facts-fetcher
-- READMEs "Catch-up").
--
-- Each kind of derived data has a current version that lives with its rule and a stamp of the version
-- that built each item:
--   parser       sections.py PARSER_VERSION ("python-7" / "rust-7": the number), per document in
--                silver.filing_sections.parser_version
--   passages     index.py SEARCH_RULE, per filing in ops.section_index.passages_rule (068)
--   diffs        diff.py ALGORITHM, per filing in ops.section_index.algorithm (021; already automatic)
--   gold         gold.rules_version() (here: 64, the last gold rule change), per company in
--                ops.company_builds, kind 'gold'
--   checks       ops.check_financials_version() (57) and ops.check_segments_version() (70), per company
--                in ops.company_builds, kinds 'check_financials' and 'check_segments'
-- A migration that changes gold's rules or metric tags (anything that needed financials-rebuild)
-- replaces gold.rules_version() with its own number; one that replaces a check bumps that check's
-- version the same way (db/CLAUDE.md).
--
-- 1. The versions, and ops.parser_version_number(text).
-- 2. ops.company_builds: (kind, company, version, built_at), plus the version a rebuild last failed at.
--    No row = built with ops.catchup.baseline, the version current at this migration: on the first
--    deploy every company counts as current (the last full rebuild ran 2026-11-09 and 064's canary
--    covered its companies; 069/070 changed readings and the segment check, whose rerun is done:
--    27,168 segment_sum findings on 2026-11-16, 070's expected ~27,000). No FK to silver.companies:
--    fixtures TRUNCATE it without CASCADE.
-- 3. gold.lock_companies: gold writers of the same company take turns (a transaction advisory lock per
--    company, sorted; over 1,000 companies one exclusive lock instead, which everyone else takes
--    shared). The catch-up rebuilds companies while the nightly jobs refresh theirs: without it two
--    refreshes of one company could interleave their DELETE and INSERT (a unique violation, or stale
--    findings).
-- 4. gold.refresh_financials (014's body) takes the locks and stamps 'gold' and 'check_financials';
--    gold.refresh_segments (018's body) stamps 'check_segments'; ops.recheck runs one check and stamps
--    it. Every refresh path calls these, so a hand-run rebuild stamps too.
-- 5. ops.catchup (one row per kind: a pause switch, the version being caught up to and when the
--    catch-up first saw it), ops.catchup_runs (each run's measurement, 30 days), ops.catchup_status
--    (ops_reader: counts, dates and rates only). The parser kind ships paused (the owner unpauses
--    it); the others run from the first deploy.
-- 6. ops.parser_catchup: re-queues documents with an older parser version, oldest filing first,
--    through the crawl's outbox (ops.doc_crawl_targets.published_at -> documents.backfill ->
--    parser-backfill), never documents.fetched; a budget per call, only while the crawl's own queue,
--    the outbox and the requests not parsed yet are small. A document the live doc-fetcher stored has
--    no crawl target (020's plan skips stored filings): it gets the row the crawl writes for a filing
--    it finds already stored ('done', files 0, 'already stored').
-- 7. ops.section_index_pending (021's body): filings never indexed or never diffed go first, then
--    the rest oldest first as before, so re-indexing re-parsed history or recomputing diffs after an
--    ALGORITHM bump never delays a new filing's passages and diffs.
--
-- Measured on hermes (2026-11-16, read-only): 595,775 documents parsed before rust-7 (rust-3 231,424,
-- rust-4 87,106, rust-5 58,018, rust-6 219,227), 2,715 of them stored by the live doc-fetcher with no
-- crawl target; the selection behind ops.parser_catchup (every document's version, grouped) takes 5.4 s.
-- 433,157 filings' passages predate furniture-1; 0 diffs predate diff-1; 8,102 companies.
--
-- Locks on hermes: new tables, functions and a view; CREATE OR REPLACE of gold.refresh_financials,
-- gold.refresh_segments and ops.section_index_pending (catalog only; a running caller keeps the body
-- it started with). No table is rewritten or scanned. No backout file touches these functions; undo:
-- 014's refresh_financials, 018's refresh_segments and 021's section_index_pending bodies, then drop
-- what this file creates.

SET LOCAL lock_timeout = '3s';

-- 1. Versions -------------------------------------------------------------------------------------

-- The gold rules (silver.metric_tags, gold.usable_facts, best_facts, refresh_quarterly_financials,
-- refresh_derived_financials) as of migration 064. Replace with the number of the migration that
-- changes them: the hourly catch-up then rebuilds every company, oldest build first.
CREATE FUNCTION gold.rules_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 64';

-- ops.check_financials as of 057 and ops.check_segments as of 070 (db/transforms/README.md).
CREATE FUNCTION ops.check_financials_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 57';
CREATE FUNCTION ops.check_segments_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 70';

-- 'rust-7' -> 7 (0 when there is no number).
CREATE FUNCTION ops.parser_version_number(p_version text) RETURNS integer
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT coalesce(substring(p_version FROM '(\d+)$')::integer, 0)
$$;

-- 2. Per-company stamps ---------------------------------------------------------------------------

CREATE TABLE ops.company_builds (
    kind           text        NOT NULL CHECK (kind IN ('gold', 'check_financials', 'check_segments')),
    company_id     text        NOT NULL,
    version        integer     NOT NULL,  -- the rule version that built (or checked) it last
    built_at       timestamptz NOT NULL,
    failed_version integer,               -- the catch-up's rebuild raised at this version: not retried
    failed_at      timestamptz,
    error          text,
    PRIMARY KEY (kind, company_id)
);

-- The kinds and their state. baseline: the version a company without an ops.company_builds row was
-- built with (this migration's); NULL for the per-filing kinds.
CREATE TABLE ops.catchup (
    kind       text        PRIMARY KEY
        CHECK (kind IN ('parser', 'passages', 'diffs', 'gold', 'check_financials', 'check_segments')),
    paused     boolean     NOT NULL DEFAULT false,  -- UPDATE to pause or resume one kind, no deploy
    version    text,                   -- the version being caught up to
    started_at timestamptz,            -- when the catch-up first saw it
    baseline   integer
);
-- The parser kind ships paused: re-parsing every document before rust-7 (595,775, about 8 days at
-- 3,000 an hour) is the owner's call. Paused, it is measured hourly and does nothing; unpause with
--   UPDATE ops.catchup SET paused = false WHERE kind = 'parser';
INSERT INTO ops.catchup (kind, paused, baseline) VALUES
    ('parser', true, NULL), ('passages', false, NULL), ('diffs', false, NULL),
    ('gold', false, 64), ('check_financials', false, 57), ('check_segments', false, 70);

CREATE FUNCTION ops.company_rule_version(p_kind text) RETURNS integer
LANGUAGE sql STABLE AS $$
    SELECT CASE p_kind WHEN 'gold' THEN gold.rules_version()
                       WHEN 'check_financials' THEN ops.check_financials_version()
                       WHEN 'check_segments' THEN ops.check_segments_version() END
$$;

CREATE FUNCTION ops.stamp_builds(p_kind text, p_company_ids text[], p_version integer)
RETURNS void
LANGUAGE sql AS $$
    INSERT INTO ops.company_builds AS b (kind, company_id, version, built_at)
    SELECT DISTINCT p_kind, id, p_version, now() FROM unnest(p_company_ids) id WHERE id IS NOT NULL
    ON CONFLICT (kind, company_id) DO UPDATE
    SET version = EXCLUDED.version, built_at = EXCLUDED.built_at,
        failed_version = NULL, failed_at = NULL, error = NULL
$$;

-- The catch-up gave up on this company at this version (its rebuild or check raised); a new version
-- tries it again.
CREATE FUNCTION ops.build_failed(p_kind text, p_company_id text, p_version integer, p_error text)
RETURNS void
LANGUAGE sql AS $$
    INSERT INTO ops.company_builds AS b (kind, company_id, version, built_at, failed_version,
                                         failed_at, error)
    SELECT p_kind, p_company_id, c.baseline, now(), p_version, now(), left(p_error, 500)
    FROM ops.catchup c WHERE c.kind = p_kind
    ON CONFLICT (kind, company_id) DO UPDATE
    SET failed_version = EXCLUDED.failed_version, failed_at = EXCLUDED.failed_at,
        error = EXCLUDED.error
$$;

-- Every company an older version of this kind's rule built (or checked), with when, and whether the
-- catch-up gave up on it at the current version. A check_financials rerun waits while the company's
-- gold rebuild is due: the rebuild runs the check.
CREATE FUNCTION ops.company_backlog(p_kind text)
RETURNS TABLE (company_id text, built_at timestamptz, failed boolean)
LANGUAGE sql STABLE AS $$
    SELECT c.company_id, b.built_at,
           b.failed_version IS NOT DISTINCT FROM ops.company_rule_version(p_kind)
    FROM silver.companies c
    JOIN ops.catchup k ON k.kind = p_kind
    LEFT JOIN ops.company_builds b ON b.kind = p_kind AND b.company_id = c.company_id
    WHERE coalesce(b.version, k.baseline) < ops.company_rule_version(p_kind)
      AND (p_kind <> 'check_financials' OR c.company_id NOT IN (
              SELECT g.company_id FROM ops.company_backlog('gold') g WHERE NOT g.failed))
$$;

-- 3. Gold writers take turns per company ----------------------------------------------------------

CREATE FUNCTION gold.lock_companies(p_company_ids text[]) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    k integer;
BEGIN
    IF cardinality(p_company_ids) > 1000 THEN  -- the lock table's size: one lock for everything
        PERFORM pg_advisory_xact_lock(71, 0);
        RETURN;
    END IF;
    PERFORM pg_advisory_xact_lock_shared(71, 0);
    FOR k IN SELECT DISTINCT hashtext(id) FROM unnest(p_company_ids) id ORDER BY 1 LOOP
        PERFORM pg_advisory_xact_lock(72, k);
    END LOOP;
END
$$;

-- 4. The refreshes stamp what they built ----------------------------------------------------------

-- 014's body, with the locks and the stamps (071). Versions are read first: a rule replaced while
-- this runs leaves the older stamp, so the catch-up rebuilds the company again.
CREATE OR REPLACE FUNCTION gold.refresh_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
    rules integer := gold.rules_version();
    checks integer := ops.check_financials_version();
BEGIN
    PERFORM gold.lock_companies(p_company_ids);
    n := gold.refresh_quarterly_financials(p_company_ids);
    PERFORM gold.refresh_derived_financials(p_company_ids);
    PERFORM ops.check_financials(p_company_ids);
    PERFORM ops.stamp_builds('gold', p_company_ids, rules);
    PERFORM ops.stamp_builds('check_financials', p_company_ids, checks);
    RETURN n;
END
$$;

-- 018's body, with the stamp (071).
CREATE OR REPLACE FUNCTION gold.refresh_segments(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
    checks integer := ops.check_segments_version();
BEGIN
    n := gold.refresh_segment_financials(p_company_ids);
    PERFORM ops.check_segments(p_company_ids);
    PERFORM ops.stamp_builds('check_segments', p_company_ids, checks);
    RETURN n;
END
$$;

-- Run one check again on these companies (nothing re-derived) and stamp it. check_financials reads
-- gold, so it takes gold's company locks.
CREATE FUNCTION ops.recheck(p_kind text, p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    v integer;
    n integer;
BEGIN
    IF p_kind = 'check_financials' THEN
        v := ops.check_financials_version();
        PERFORM gold.lock_companies(p_company_ids);
        n := ops.check_financials(p_company_ids);
    ELSIF p_kind = 'check_segments' THEN
        v := ops.check_segments_version();
        n := ops.check_segments(p_company_ids);
    ELSE
        RAISE EXCEPTION 'ops.recheck: no check %', p_kind;
    END IF;
    PERFORM ops.stamp_builds(p_kind, p_company_ids, v);
    RETURN n;
END
$$;

-- 5. State, history, status -----------------------------------------------------------------------

CREATE TABLE ops.catchup_runs (
    kind      text        NOT NULL REFERENCES ops.catchup,
    run_at    timestamptz NOT NULL,
    version   text        NOT NULL,
    behind    bigint      NOT NULL,           -- items not yet tried with `version`
    in_flight bigint      NOT NULL DEFAULT 0, -- parser: re-queued, not parsed yet (within a day)
    failed    bigint      NOT NULL DEFAULT 0, -- tried with `version` and still behind
    oldest    date,                           -- the oldest such filing's date (companies: build)
    done      bigint      NOT NULL DEFAULT 0, -- items this run rebuilt (parser: re-queued)
    seconds   real,
    PRIMARY KEY (kind, run_at)
);

-- A kind's run begins: the version it catches up to now and since when (a new version restarts the
-- clock), and whether it is paused.
CREATE FUNCTION ops.catchup_begin(p_kind text, p_version text)
RETURNS TABLE (paused boolean, started_at timestamptz)
LANGUAGE sql AS $$
    UPDATE ops.catchup c
    SET version = p_version,
        started_at = CASE WHEN c.version IS DISTINCT FROM p_version OR c.started_at IS NULL
                          THEN clock_timestamp() ELSE c.started_at END
    WHERE c.kind = p_kind
    RETURNING c.paused, c.started_at
$$;

CREATE FUNCTION ops.catchup_record(p_kind text, p_version text, p_behind bigint,
                                   p_in_flight bigint, p_failed bigint, p_oldest date,
                                   p_done bigint, p_seconds real)
RETURNS void
LANGUAGE sql AS $$
    INSERT INTO ops.catchup_runs (kind, run_at, version, behind, in_flight, failed, oldest, done,
                                  seconds)
    VALUES (p_kind, clock_timestamp(), p_version, p_behind, p_in_flight, p_failed, p_oldest, p_done,
            p_seconds);
    DELETE FROM ops.catchup_runs WHERE kind = p_kind AND run_at < now() - interval '30 days';
$$;

-- One row per kind: the latest measurement, the last day's pace and the ETA at it, when it last
-- moved (progress_at: the last run that did something, else when this version's catch-up began), and
-- its state: paused (set by hand; measured, never stalled), current (nothing left), stalled (work
-- left and no progress for a day: the CatchupStalled alert's rule) or catching_up. Counts, dates and
-- labels only.
CREATE VIEW ops.catchup_status AS
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
            THEN (r.behind + r.in_flight)::float8 / d.done_last_day * interval '1 day' END AS eta
FROM ops.catchup c
LEFT JOIN LATERAL (
    SELECT x.* FROM ops.catchup_runs x WHERE x.kind = c.kind ORDER BY x.run_at DESC LIMIT 1
) r ON true
LEFT JOIN LATERAL (
    SELECT sum(x.done) FILTER (WHERE x.run_at > now() - interval '1 day') AS done_last_day,
           max(x.run_at) FILTER (WHERE x.done > 0) AS last_done_at
    FROM ops.catchup_runs x WHERE x.kind = c.kind
) d ON true;

GRANT SELECT ON ops.catchup_status TO ops_reader;

-- 6. Parser: re-queue through the crawl's outbox ----------------------------------------------------

-- Documents whose sections a parser older than p_version wrote (mapped companies only: gold serves no
-- other), and what the catch-up did with them since this version began (p_since): re-queued (crawl
-- target done, published_at NULL or since p_since) or due. Queues up to p_limit due documents, oldest
-- filing first, unless the crawl has p_crawl_busy targets of its own pending, the outbox holds
-- p_outbox events, or p_in_flight of the last day's requests are not parsed yet. A document the crawl
-- never planned (the live doc-fetcher stored it) gets the row the crawl writes for a filing it finds
-- already stored. Returns: due after this call; re-queued, not parsed yet (within a day); re-queued
-- over a day ago and still old (the parser skipped it); the oldest due filing; queued by this call.
CREATE FUNCTION ops.parser_catchup(p_version integer, p_since timestamptz, p_limit integer,
                                   p_outbox integer, p_in_flight integer, p_crawl_busy integer)
RETURNS TABLE (behind bigint, in_flight bigint, failed bigint, oldest date, queued bigint)
LANGUAGE sql AS $$
    WITH old AS MATERIALIZED (
        SELECT s.accession_no
        FROM silver.filing_sections s
        GROUP BY s.accession_no
        HAVING min(ops.parser_version_number(s.parser_version)) < p_version
    ), docs AS MATERIALIZED (
        SELECT d.accession_no, d.filed, t.status, t.published_at,
               t.accession_no IS NOT NULL AS targeted,
               coalesce(t.status = 'done' AND (t.published_at IS NULL
                                               OR t.published_at >= p_since), false) AS requested
        FROM old o
        JOIN bronze.filing_documents d ON d.accession_no = o.accession_no
        LEFT JOIN ops.doc_crawl_targets t ON t.accession_no = o.accession_no
        WHERE EXISTS (SELECT 1 FROM silver.company_ciks k WHERE k.cik = d.cik)
    ), room AS (
        SELECT CASE
            WHEN (SELECT count(*) FROM ops.doc_crawl_targets
                  WHERE status IN ('pending', 'claimed')) >= p_crawl_busy THEN 0
            ELSE greatest(0, least(
                p_limit,
                p_outbox - (SELECT count(*) FROM ops.doc_crawl_targets
                            WHERE status = 'done' AND published_at IS NULL),
                p_in_flight - (SELECT count(*) FROM docs WHERE requested
                               AND published_at > now() - interval '1 day')))
            END AS n
    ), pick AS MATERIALIZED (
        SELECT accession_no, targeted FROM docs
        WHERE NOT requested AND coalesce(status, 'done') NOT IN ('pending', 'claimed')
        ORDER BY filed, accession_no
        LIMIT (SELECT n FROM room)
    ), requeued AS (
        UPDATE ops.doc_crawl_targets t SET status = 'done', published_at = NULL
        FROM pick p WHERE p.targeted AND t.accession_no = p.accession_no
        RETURNING t.accession_no
    ), added AS (
        INSERT INTO ops.doc_crawl_targets AS t
            (accession_no, cik, company_id, form, filing_date, report_date, primary_document, items,
             priority, status, finished_at, published_at, last_error, files)
        SELECT d.accession_no, d.cik, k.company_id, d.form, d.filed, d.period_of_report,
               d.document_name, h.items, CASE WHEN c.tracked THEN 0 ELSE 1 END, 'done',
               d.fetched_at, NULL, 'already stored', 0
        FROM pick p
        JOIN bronze.filing_documents d ON d.accession_no = p.accession_no
        JOIN silver.company_ciks k ON k.cik = d.cik
        JOIN silver.companies c ON c.company_id = k.company_id
        LEFT JOIN bronze.submission_filings h ON h.accession_no = d.accession_no AND h.cik = d.cik
        WHERE NOT p.targeted
        ON CONFLICT (accession_no) DO NOTHING
        RETURNING t.accession_no
    ), queued AS (
        SELECT (SELECT count(*) FROM requeued) + (SELECT count(*) FROM added) AS n
    )
    SELECT count(*) FILTER (WHERE NOT d.requested AND p.accession_no IS NULL),
           count(*) FILTER (WHERE d.requested AND (d.published_at IS NULL
                                                   OR d.published_at > now() - interval '1 day'))
               + (SELECT n FROM queued),
           count(*) FILTER (WHERE d.requested AND d.published_at <= now() - interval '1 day'),
           min(d.filed) FILTER (WHERE NOT d.requested AND p.accession_no IS NULL),
           (SELECT n FROM queued)
    FROM docs d
    LEFT JOIN pick p ON p.accession_no = d.accession_no
$$;

-- 7. New filings first in section-index's work --------------------------------------------------

-- 021's body; each half's ORDER BY puts filings never indexed (never diffed) first (071).
CREATE OR REPLACE FUNCTION ops.section_index_pending(p_algorithm text, p_limit int)
RETURNS TABLE (accession_no text, reason text)
LANGUAGE sql STABLE AS $$
    (SELECT s.accession_no, 'sections'
     FROM (SELECT accession_no, max(parsed_at) AS parsed_at
           FROM silver.filing_sections GROUP BY accession_no) s
     JOIN bronze.filing_documents d ON d.accession_no = s.accession_no
     JOIN silver.company_ciks k ON k.cik = d.cik  -- gold serves only filings of a company
     LEFT JOIN ops.section_index i ON i.accession_no = s.accession_no
     WHERE i.accession_no IS NULL OR i.sections_parsed_at < s.parsed_at
     ORDER BY i.accession_no IS NOT NULL, d.filed, s.accession_no
     LIMIT p_limit)
    UNION ALL
    (SELECT i.accession_no, 'diffs'
     FROM ops.section_index i
     JOIN ops.section_index_priors(ARRAY(
         SELECT accession_no FROM ops.section_index WHERE form IN ('10-K', '10-Q'))) e
       ON e.accession_no = i.accession_no
     LEFT JOIN ops.section_index p ON p.accession_no = i.prior_accession_no
     LEFT JOIN ops.section_index y ON y.accession_no = i.year_ago_accession_no
     WHERE i.diffed_at IS NULL
        OR i.algorithm IS DISTINCT FROM p_algorithm
        OR i.prior_accession_no IS DISTINCT FROM e.prior_accession_no
        OR i.year_ago_accession_no IS DISTINCT FROM e.year_ago_accession_no
        OR p.indexed_at > i.diffed_at
        OR y.indexed_at > i.diffed_at
     ORDER BY i.diffed_at IS NOT NULL, i.filed, i.accession_no
     LIMIT p_limit)
$$;

