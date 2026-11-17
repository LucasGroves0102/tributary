-- Point-in-time data, part 2: what gold believed on any past day (db/DESIGN.md "086"), written by
-- gold's own pipeline (085's pure functions) as of each day a company's facts were filed.
--
-- 1. gold.financials_known: gold's rows (a quarterly row as span 'quarter', a derived row with its
--    span) in versions. A version is a run of the same row (value, unit, period start, value source,
--    source tag, inputs' values: gold.input_values) across the days the company's filings were
--    filed; it holds the citation of the first day it was believed (the first filing to state it, as
--    the pipeline cited it that day), known_from that day (filed by its end) to known_to (the day a
--    filing changed it or took it away; exclusive; NULL = still believed). As of a day D gold's rows
--    are the versions with known_from <= D < known_to. TTM rows of balance-sheet metrics aren't
--    stored (their quarter rows are, as in gold.financials). RLS: 030's financials policy. Derived
--    data: pg-backup leaves its rows out (rebuilt by the catch-up below).
-- 2. gold.financial_rows(ids, p_as_of): the rows of one view (gold.quarterly_rows, then
--    gold.derived_rows over those quarters). gold.knowledge_facts(company): the company's facts per
--    filing date (count and sum of fact ids: what a view as of that date can read; a superset of
--    gold.usable_facts' facts, with filings whose XBRL names the company), kept per company in
--    gold.known_companies to find what changed since the last replay.
-- 3. gold.replay_known(company, from): the history from that date on rebuilt, one view per filing
--    date (versions starting on or after it deleted, those open on it reopened, then each date's view
--    diffed against the open versions: changed or gone ones closed, new ones written).
--    gold.rebuild_known(ids): full replays under the companies' gold locks, stamped in
--    ops.company_builds kind 'known' (version gold.rules_version()). gold.refresh_known(ids), from
--    gold.refresh_financials (same locks, same transaction): for a company whose history this rules
--    version built, a replay from the first filing date whose facts changed since (a new filing: its
--    own date; facts that arrived late for an older filing: from that one); nothing otherwise. A
--    company built by an older version, or with no gold.known_companies row (a database restored
--    from a backup, which leaves both history tables' rows out), is the catch-up's.
-- 4. A catch-up kind 'known' (ops.catchup, baseline 0: nothing is built at the deploy, so every
--    company is behind; not paused, the owner's call of 2026-11-16): facts_fetcher/catchup.py
--    rebuilds companies oldest build first after the gold kind, each only once its gold is current
--    (ops.company_backlog, like check_financials); ops.rebuild_soon marks 'known' as well as 'gold'.
--    A rules_version bump replays every company again.
--
-- Measured (DESIGN.md "086"): replay cost per company and filing date, the history's rows and size
-- on hermes, the catch-up's duration.
--
-- Locks on hermes (lock_timeout 3 s): new tables and functions; ALTER of the CHECK constraints of
-- ops.company_builds and ops.catchup (ACCESS EXCLUSIVE for the instant of the swap: the hourly
-- catch-up and the nightly gold builds write them; the constraint check scans their few thousand
-- rows); CREATE OR REPLACE of gold.refresh_financials, ops.company_rule_version,
-- ops.company_backlog and ops.rebuild_soon (catalog only: a running build keeps the body it started
-- with). Backout db/backout/point_in_time_085.sql.

SET LOCAL lock_timeout = '3s';

-- 1. The history ----------------------------------------------------------------------------------

CREATE TABLE gold.financials_known (
    company_id        text        NOT NULL,
    span              text        NOT NULL CHECK (span IN ('quarter', 'ttm')),
    metric            text        NOT NULL,
    period_end        date        NOT NULL,
    known_from        date        NOT NULL,  -- the filing date it was first believed (end of day)
    known_to          date,                  -- the filing date it stopped being (exclusive); NULL: now
    period_start      date,
    value             numeric     NOT NULL,
    unit              text        NOT NULL,
    value_source      text        NOT NULL,
    source_tag        text,
    inputs            jsonb,
    accession_no      text        NOT NULL,  -- the citation on known_from
    form              text,
    filed             date        NOT NULL,
    source_accessions text[]      NOT NULL,
    ingested_at       timestamptz NOT NULL,
    CHECK (known_to IS NULL OR known_to > known_from),
    PRIMARY KEY (company_id, span, metric, period_end, known_from)
        INCLUDE (known_to, value, unit, accession_no, form, filed)
) WITH (autovacuum_vacuum_scale_factor = 0.02, autovacuum_vacuum_insert_scale_factor = 0.02);

GRANT SELECT ON gold.financials_known TO query_api;
ALTER TABLE gold.financials_known ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_financials ON gold.financials_known FOR SELECT TO query_api
    USING (company_id IN (SELECT r FROM authz.readable_companies('financials') r));

-- Per company: its facts per filing date when its history was last replayed (what changed since).
CREATE TABLE gold.known_companies (
    company_id  text        PRIMARY KEY,
    dates       date[]      NOT NULL,
    facts       bigint[]    NOT NULL,
    ids         numeric[]   NOT NULL,
    replayed_at timestamptz NOT NULL
);

-- A row's inputs as values only: a TTM sum's quarters without their accession numbers (a later
-- filing restating the same quarter at the same value is the same version).
CREATE FUNCTION gold.input_values(p_inputs jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN p_inputs ? 'quarters'
                THEN jsonb_build_object('quarters', (
                         SELECT jsonb_agg(q.e - 'accession_no' ORDER BY q.i)
                         FROM jsonb_array_elements(p_inputs -> 'quarters') WITH ORDINALITY q(e, i)))
                ELSE p_inputs END
$$;

-- 2. One view, and what a company's views read -----------------------------------------------------

-- gold's rows as of p_as_of (NULL: today's): the quarters (span 'quarter', no inputs) and the derived
-- rows computed from those very quarters.
CREATE FUNCTION gold.financial_rows(p_company_ids text[], p_as_of date DEFAULT NULL)
RETURNS SETOF gold.derived_row
LANGUAGE sql STABLE AS $$
    WITH q AS MATERIALIZED (
        SELECT * FROM gold.quarterly_rows(p_company_ids, p_as_of)
    )
    SELECT q.company_id, 'quarter', q.metric, q.period_start, q.period_end, q.value, q.unit,
           q.value_source, q.source_tag, NULL::jsonb, q.accession_no, q.form, q.filed,
           q.source_accessions, q.ingested_at
    FROM q
    UNION ALL
    SELECT d.*
    FROM gold.derived_rows(p_company_ids, p_as_of,
                           ARRAY(SELECT x::gold.quarter_row FROM q x)) d
$$;

-- The company's facts per filing date: its own CIKs' and those of filings whose XBRL names one of
-- them (what gold.usable_facts may read, borrowed facts included). Count and sum of fact ids: a fact
-- added or replaced (supersede, a re-projection) changes its date's pair.
CREATE FUNCTION gold.knowledge_facts(p_company_id text)
RETURNS TABLE (filed date, facts bigint, ids numeric)
LANGUAGE sql STABLE AS $$
    SELECT x.filed, count(*), sum(x.fact_id)
    FROM (
        SELECT f.filed, f.fact_id
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        WHERE k.company_id = p_company_id
        UNION
        SELECT f.filed, f.fact_id
        FROM silver.company_ciks k
        JOIN bronze.filing_xbrl x ON x.entity_cik = k.cik
        JOIN silver.financial_facts f ON f.accession_no = x.accession_no AND f.cik <> x.entity_cik
        WHERE k.company_id = p_company_id
    ) x
    GROUP BY x.filed
$$;

-- 3. Replays --------------------------------------------------------------------------------------

-- The company's history from p_from on, rebuilt (header, 3). The caller holds the company's gold
-- lock. Returns the versions written.
CREATE FUNCTION gold.replay_known(p_company_id text, p_from date) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    d date;
    n integer := 0;
    k integer;
BEGIN
    DELETE FROM gold.financials_known
    WHERE company_id = p_company_id AND known_from >= p_from;
    UPDATE gold.financials_known SET known_to = NULL
    WHERE company_id = p_company_id AND known_to >= p_from;

    FOR d IN SELECT x.filed FROM gold.knowledge_facts(p_company_id) x
             WHERE x.filed >= p_from ORDER BY 1 LOOP
        WITH v AS MATERIALIZED (  -- the view as of d
            SELECT r.*, gold.input_values(r.inputs) AS input_values
            FROM gold.financial_rows(ARRAY[p_company_id], d) r
        ),
        open AS MATERIALIZED (  -- the versions believed until d
            SELECT o.span, o.metric, o.period_end, o.known_from, o.period_start, o.value, o.unit,
                   o.value_source, o.source_tag, gold.input_values(o.inputs) AS input_values
            FROM gold.financials_known o
            WHERE o.company_id = p_company_id AND o.known_to IS NULL
        ),
        diff AS MATERIALIZED (
            SELECT o.span AS o_span, o.metric AS o_metric, o.period_end AS o_end, o.known_from,
                   v.span AS v_span, v.metric AS v_metric, v.period_end AS v_end
            FROM open o
            FULL JOIN v ON v.span = o.span AND v.metric = o.metric AND v.period_end = o.period_end
            WHERE o.span IS NULL OR v.span IS NULL
               OR (o.value, o.unit, o.period_start, o.value_source, o.source_tag, o.input_values)
                  IS DISTINCT FROM (v.value, v.unit, v.period_start, v.value_source,
                                    v.source_tag, v.input_values)
        ),
        closed AS (
            UPDATE gold.financials_known c SET known_to = d
            FROM diff x
            WHERE x.o_span IS NOT NULL AND c.company_id = p_company_id AND c.span = x.o_span
              AND c.metric = x.o_metric AND c.period_end = x.o_end AND c.known_from = x.known_from
            RETURNING 1
        ),
        written AS (
            INSERT INTO gold.financials_known
                (company_id, span, metric, period_end, known_from, known_to, period_start, value,
                 unit, value_source, source_tag, inputs, accession_no, form, filed,
                 source_accessions, ingested_at)
            SELECT v.company_id, v.span, v.metric, v.period_end, d, NULL, v.period_start, v.value,
                   v.unit, v.value_source, v.source_tag, v.inputs, v.accession_no, v.form, v.filed,
                   v.source_accessions, v.ingested_at
            FROM diff x
            JOIN v ON v.span = x.v_span AND v.metric = x.v_metric AND v.period_end = x.v_end
            RETURNING 1
        )
        -- (closed runs whether read or not: a data-modifying CTE always runs to completion)
        SELECT count(*) INTO k FROM written;
        n := n + k;
    END LOOP;
    RETURN n;
END
$$;

-- The first filing date whose facts changed since the company's last replay (NULL: none; every
-- date when it was never replayed).
CREATE FUNCTION gold.known_changed_from(p_company_id text) RETURNS date
LANGUAGE sql STABLE AS $$
    WITH now AS (SELECT * FROM gold.knowledge_facts(p_company_id)),
    was AS (
        SELECT x.filed, x.facts, x.ids
        FROM gold.known_companies c
        CROSS JOIN LATERAL unnest(c.dates, c.facts, c.ids) x(filed, facts, ids)
        WHERE c.company_id = p_company_id
    )
    SELECT min(coalesce(a.filed, b.filed))
    FROM now a
    FULL JOIN was b ON b.filed = a.filed
    WHERE (a.facts, a.ids) IS DISTINCT FROM (b.facts, b.ids)
$$;

-- Replay these companies' history from p_from (NULL: from the first change since the last replay,
-- skipping those with none) and remember what their facts were. Read the facts before the views
-- (a fact committed meanwhile is found again next time). The caller holds the gold locks.
CREATE FUNCTION gold.replay_companies(p_company_ids text[], p_full boolean) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    c text;
    f date;
    n integer := 0;
    seen date[];
    counts bigint[];
    sums numeric[];
BEGIN
    FOR c IN SELECT DISTINCT id FROM unnest(p_company_ids) id WHERE id IS NOT NULL ORDER BY 1 LOOP
        SELECT coalesce(array_agg(x.filed ORDER BY x.filed), '{}'),
               coalesce(array_agg(x.facts ORDER BY x.filed), '{}'),
               coalesce(array_agg(x.ids ORDER BY x.filed), '{}')
        INTO seen, counts, sums
        FROM gold.knowledge_facts(c) x;
        f := CASE WHEN p_full THEN '-infinity'::date ELSE gold.known_changed_from(c) END;
        CONTINUE WHEN f IS NULL;
        n := n + gold.replay_known(c, f);
        INSERT INTO gold.known_companies AS k (company_id, dates, facts, ids, replayed_at)
        VALUES (c, seen, counts, sums, now())
        ON CONFLICT (company_id) DO UPDATE
        SET dates = EXCLUDED.dates, facts = EXCLUDED.facts, ids = EXCLUDED.ids,
            replayed_at = EXCLUDED.replayed_at;
    END LOOP;
    RETURN n;
END
$$;

-- The catch-up's work: these companies' whole history at the current rules, under their gold
-- locks, stamped 'known'. Returns the versions written.
CREATE FUNCTION gold.rebuild_known(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
    rules integer := gold.rules_version();
BEGIN
    PERFORM gold.lock_companies(p_company_ids);
    n := gold.replay_companies(p_company_ids, true);
    PERFORM ops.stamp_builds('known', p_company_ids, rules);
    RETURN n;
END
$$;

-- After a gold build (the caller holds the locks): the companies whose history this rules version
-- built, from the first filing date whose facts changed. A company never replayed, or replayed by
-- an older version, is left to the catch-up.
CREATE FUNCTION gold.refresh_known(p_company_ids text[]) RETURNS integer
LANGUAGE sql AS $$
    SELECT gold.replay_companies(ARRAY(
        SELECT b.company_id FROM ops.company_builds b
        WHERE b.kind = 'known' AND b.company_id = ANY(p_company_ids)
          AND b.version = gold.rules_version()
          AND EXISTS (SELECT 1 FROM gold.known_companies k WHERE k.company_id = b.company_id)),
        false)
$$;

-- 078's body + the history (086), after the stamps' versions are read, under the same locks.
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
    PERFORM gold.refresh_screen(p_company_ids);  -- 078
    PERFORM ops.check_financials(p_company_ids);
    PERFORM gold.refresh_known(p_company_ids);  -- 086
    PERFORM ops.stamp_builds('gold', p_company_ids, rules);
    PERFORM ops.stamp_builds('check_financials', p_company_ids, checks);
    RETURN n;
END
$$;

-- 4. The catch-up kind ----------------------------------------------------------------------------

ALTER TABLE ops.company_builds DROP CONSTRAINT company_builds_kind_check;
ALTER TABLE ops.company_builds ADD CONSTRAINT company_builds_kind_check
    CHECK (kind IN ('gold', 'check_financials', 'check_segments', 'known'));
ALTER TABLE ops.catchup DROP CONSTRAINT catchup_kind_check;
ALTER TABLE ops.catchup ADD CONSTRAINT catchup_kind_check
    CHECK (kind IN ('parser', 'passages', 'diffs', 'gold', 'check_financials', 'check_segments',
                    'check_sections', 'statements', 'known'));
-- baseline 0: no company has a history yet
INSERT INTO ops.catchup (kind, paused, baseline) VALUES ('known', false, 0);

CREATE OR REPLACE FUNCTION ops.company_rule_version(p_kind text) RETURNS integer
LANGUAGE sql STABLE AS $$
    SELECT CASE p_kind WHEN 'gold' THEN gold.rules_version()
                       WHEN 'check_financials' THEN ops.check_financials_version()
                       WHEN 'check_segments' THEN ops.check_segments_version()
                       WHEN 'known' THEN gold.rules_version() END  -- 086
$$;

-- 074's body; 'known' waits like check_financials while the company's gold is due, and is due
-- without a gold.known_companies row (a restored database: the dumps leave the history out) (086).
CREATE OR REPLACE FUNCTION ops.company_backlog(p_kind text)
RETURNS TABLE (company_id text, built_at timestamptz, failed boolean)
LANGUAGE sql STABLE AS $$
    SELECT c.company_id, b.built_at,
           b.failed_version IS NOT DISTINCT FROM ops.company_rule_version(p_kind)
    FROM silver.companies c
    JOIN ops.catchup k ON k.kind = p_kind
    LEFT JOIN ops.company_builds b ON b.kind = p_kind AND b.company_id = c.company_id
    WHERE (coalesce(b.version, k.baseline) < ops.company_rule_version(p_kind)
           -- 074
           OR (p_kind = 'check_financials' AND EXISTS (
                  SELECT 1 FROM silver.company_ciks x
                  JOIN silver.statement_reads r ON r.cik = x.cik
                  WHERE x.company_id = c.company_id
                    AND r.read_at > coalesce(b.built_at, '-infinity'::timestamptz)))
           -- 086
           OR (p_kind = 'known' AND NOT EXISTS (
                  SELECT 1 FROM gold.known_companies kc WHERE kc.company_id = c.company_id)))
      AND (p_kind NOT IN ('check_financials', 'known') OR c.company_id NOT IN (  -- 086
              SELECT g.company_id FROM ops.company_backlog('gold') g WHERE NOT g.failed))
$$;

-- 076's body; the history is replayed too (086), after the gold rebuild.
CREATE OR REPLACE FUNCTION ops.rebuild_soon(p_company_ids text[]) RETURNS integer
LANGUAGE sql AS $$
    WITH due AS (
        INSERT INTO ops.company_builds AS b (kind, company_id, version, built_at)
        SELECT DISTINCT k.kind, id, 0, 'epoch'::timestamptz
        FROM unnest(p_company_ids) id
        CROSS JOIN (VALUES ('gold'), ('known')) k(kind)  -- 086
        WHERE id IS NOT NULL
        ON CONFLICT (kind, company_id) DO UPDATE
        SET version = 0, built_at = 'epoch', failed_version = NULL, failed_at = NULL,
            error = NULL
        RETURNING b.kind
    )
    SELECT count(*) FILTER (WHERE kind = 'gold')::integer FROM due
$$;
