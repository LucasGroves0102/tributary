-- Point-in-time speed: gold.financial_rows (086) lets gold.derived_rows read the quarters as of the
-- day itself instead of handing it the quarters it just computed. Speed only: the same rows, so no
-- rules version moves and nothing is rebuilt (db/CLAUDE.md, "a speed-only replace (063)").
--
-- Why: given p_quarters, derived_rows reads them through unnest() of an array built at run time, which
-- the planner estimates at 10 rows; a company has hundreds to thousands (Apple 1,736 as of
-- 2020-06-30), and the plan chosen for 10 took 14.2 s for Apple's view of one day against 1.05 s when
-- derived_rows computes the quarters (gold.quarterly_rows, 0.17 s, deterministic: the same rows).
-- Every replay of a company's history (gold.replay_known: one view per filing date) and every as-of
-- read of a company without its history built (gold.company_financials_at) goes through here: on
-- hermes the 'known' catch-up ran at about 690-835 s a company (ops.catchup_runs, 2026-11-17 to
-- 2026-11-23), 335 of 6,662 companies built, 8,019 behind, about 50 a day.
--
-- Measured on hermes before shipping (2026-11-23, as postgres, read only): the old and the new form
-- over seven views (Apple 2020-06-30 and 2026-11-01, JPMorgan 2023-03-31, TSMC 2025-06-30, NuScale
-- 2026-11-20, AES 2019-12-31, Cameco 2026-06-30) gave the same 17,792 rows, EXCEPT ALL empty both
-- ways (every column, inputs and source_accessions included); 104.7 s against 23.0 s.
--
-- p_as_of NULL (no caller passes it: replay_known and company_financials_at always give a day) keeps
-- handing the quarters over: derived_rows without them and without a day reads gold's stored
-- quarters, which the view being built may not equal.
--
-- Locks on hermes: CREATE OR REPLACE of one function, no table lock; a replay running at the deploy
-- finishes on the old body.

SET LOCAL lock_timeout = '3s';

CREATE OR REPLACE FUNCTION gold.financial_rows(p_company_ids text[], p_as_of date DEFAULT NULL)
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
    FROM gold.derived_rows(p_company_ids, p_as_of)
         d
    WHERE p_as_of IS NOT NULL  -- 161: derived_rows reads the quarters as of the day itself
    UNION ALL
    SELECT d.*
    FROM gold.derived_rows(p_company_ids, NULL,
                           ARRAY(SELECT x::gold.quarter_row FROM q x)) d
    WHERE p_as_of IS NULL
$$;
