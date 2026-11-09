-- Backout of 053_segment_same_basis.sql: gold.refresh_segment_financials as 018 defined it (its
-- body copied unchanged by a script). Run as the migrate login, then the segments refresh again
-- (db/README.md "053"). Not a migration: never add it to db/migrations.
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as postgres,
-- a function it drops and re-creates would belong to postgres, and the non-superuser migrate login
-- could never replace it again. The guard below refuses any login that doesn't own the functions,
-- and any database where a newer backout hasn't run yet (README.md, Order).
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f segments_same_basis_053.sql

BEGIN;
SET LOCAL lock_timeout = '30s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: none, this is the newest) and this one
-- hasn't, and unless the login owns the functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('gold.refresh_segment_financials(text[])', 'c7073ac071aa24b884dfab187e889472')
    ) v(fn, want) LOOP
        SELECT pg_get_userbyid(p.proowner) INTO fn_owner FROM pg_proc p WHERE p.oid = w.id;
        IF fn_owner IS NOT NULL AND fn_owner <> current_user THEN
            RAISE EXCEPTION USING MESSAGE = format('run this as %s, the owner of %s (the '
                'migrate login), not as %s: see the header. Nothing was changed.', fn_owner, w.fn,
                current_user);
        END IF;
        IF w.want IS DISTINCT FROM (SELECT md5(p.prosrc) FROM pg_proc p WHERE p.oid = w.id) THEN
            RAISE EXCEPTION USING MESSAGE = format('%s is not where this backout starts: run '
                'the newer backouts first, newest first (%s), and this one only once. Nothing was '
                'changed.', w.fn, 'none, this is the newest');
        END IF;
    END LOOP;
END
$guard$;
-- end guard


CREATE OR REPLACE FUNCTION gold.refresh_segment_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('gold.segment_financials'), hashtext(id))
    FROM (SELECT DISTINCT id FROM unnest(p_company_ids) id ORDER BY id) ids;

    DELETE FROM gold.segment_financials WHERE company_id = ANY(p_company_ids);

    WITH RECURSIVE facts AS (
        SELECT k.company_id, f.*, t.priority, m.additive,
               regexp_replace(lower(replace(f.member_label, '&', ' and ')), '[^a-z0-9]+', '',
                              'g') AS label_key
        FROM silver.company_ciks k
        JOIN silver.segment_facts f ON f.cik = k.cik
        JOIN silver.segment_metric_tags t ON t.metric = f.metric AND t.tag = f.concept
        JOIN silver.segment_metrics m ON m.metric = f.metric AND m.unit = f.unit
        WHERE k.company_id = ANY(p_company_ids) AND f.axis <> 'total'
    ),
    by_key AS (  -- 1a: one QName per label key, the one filed most recently
        SELECT DISTINCT ON (company_id, axis, label_key)
               company_id, axis, label_key, member AS canon
        FROM facts
        ORDER BY company_id, axis, label_key, filed DESC, accession_no DESC, member
    ),
    keyed AS (
        SELECT f.*, b.canon
        FROM facts f
        JOIN by_key b USING (company_id, axis, label_key)
    ),
    spans AS (
        SELECT company_id, axis, canon, min(filed) AS first_filed, max(filed) AS last_filed
        FROM keyed
        GROUP BY company_id, axis, canon
    ),
    matches AS (  -- 1b: equal values for the same period, the old member gone before the new
        SELECT a.company_id, a.axis, a.canon AS old, b.canon AS new, count(*) AS matched,
               max(sb.last_filed) AS new_last_filed
        FROM keyed a
        JOIN keyed b ON b.company_id = a.company_id AND b.axis = a.axis
                    AND b.metric = a.metric AND b.period_start = a.period_start
                    AND b.period_end = a.period_end AND b.value = a.value
                    AND b.canon <> a.canon
        JOIN spans sa ON sa.company_id = a.company_id AND sa.axis = a.axis
                     AND sa.canon = a.canon
        JOIN spans sb ON sb.company_id = b.company_id AND sb.axis = b.axis
                     AND sb.canon = b.canon
        WHERE a.value <> 0 AND sa.last_filed < sb.first_filed
        GROUP BY a.company_id, a.axis, a.canon, b.canon
    ),
    renames AS (
        SELECT DISTINCT ON (company_id, axis, old) company_id, axis, old, new
        FROM matches
        ORDER BY company_id, axis, old, matched DESC, new_last_filed DESC, new
    ),
    chain (company_id, axis, old, new, depth) AS (
        SELECT company_id, axis, old, new, 1 FROM renames
        UNION ALL
        SELECT c.company_id, c.axis, c.old, r.new, c.depth + 1
        FROM chain c
        JOIN renames r ON r.company_id = c.company_id AND r.axis = c.axis AND r.old = c.new
        WHERE c.depth < 20
    ),
    final AS (
        SELECT DISTINCT ON (company_id, axis, old) company_id, axis, old, new
        FROM chain
        ORDER BY company_id, axis, old, depth DESC
    ),
    resolved AS (
        SELECT k.*, coalesce(fn.new, k.canon) AS final_member
        FROM keyed k
        LEFT JOIN final fn ON fn.company_id = k.company_id AND fn.axis = k.axis
                          AND fn.old = k.canon
    ),
    members AS (  -- label of the latest filing, and every QName merged in
        SELECT DISTINCT ON (company_id, axis, final_member)
               company_id, axis, final_member, member_label,
               ARRAY(SELECT DISTINCT r2.member FROM resolved r2
                     WHERE r2.company_id = r.company_id AND r2.axis = r.axis
                       AND r2.final_member = r.final_member
                       AND r2.member <> r.final_member ORDER BY 1) AS aliases
        FROM resolved r
        ORDER BY company_id, axis, final_member, (member = final_member) DESC, filed DESC,
                 accession_no DESC
    ),
    best AS (
        SELECT DISTINCT ON (company_id, axis, final_member, metric, period_start, period_end) *
        FROM resolved
        ORDER BY company_id, axis, final_member, metric, period_start, period_end, priority,
                 filed DESC, accession_no DESC, cik, member
    ),
    year_to_date AS (
        SELECT b.*,
               lag(value)        OVER w AS prev_value,
               lag(period_end)   OVER w AS prev_end,
               lag(accession_no) OVER w AS prev_accession,
               lag(ingested_at)  OVER w AS prev_ingested_at,
               lag(priority)     OVER w AS prev_priority
        FROM best b
        WINDOW w AS (PARTITION BY company_id, axis, final_member, metric, period_start
                     ORDER BY period_end)
    ),
    candidates AS (
        SELECT company_id, axis, final_member, metric, period_start, period_end, value, unit,
               'reported' AS value_source, concept, axis_qname, accession_no, cik, form, filed,
               ARRAY[accession_no] AS source_accessions, ingested_at, priority
        FROM best
        WHERE period_type = 'quarter'
        UNION ALL
        SELECT company_id, axis, final_member, metric, prev_end + 1, period_end,
               value - prev_value, unit, 'derived', concept, axis_qname, accession_no, cik,
               form, filed, ARRAY[prev_accession, accession_no],
               greatest(ingested_at, prev_ingested_at), greatest(priority, prev_priority)
        FROM year_to_date
        WHERE additive AND period_end - prev_end BETWEEN 80 AND 120
    ),
    quarters AS (
        SELECT DISTINCT ON (company_id, axis, final_member, metric, period_end) *
        FROM candidates
        ORDER BY company_id, axis, final_member, metric, period_end, priority,
                 value_source = 'reported' DESC, filed DESC, accession_no DESC,
                 source_accessions DESC, period_start DESC, concept, value, ingested_at DESC,
                 cik
    )
    INSERT INTO gold.segment_financials
        (company_id, span, axis, member, member_label, member_aliases, metric, period_start,
         period_end, value, unit, value_source, source_tag, axis_qname, accession_no,
         filer_cik, form, filed, source_accessions, ingested_at)
    SELECT q.company_id, 'quarter', q.axis, q.final_member, m.member_label, m.aliases, q.metric,
           q.period_start, q.period_end, q.value, q.unit, q.value_source, q.concept,
           q.axis_qname, q.accession_no, q.cik, q.form, q.filed, q.source_accessions,
           q.ingested_at
    FROM quarters q
    JOIN members m ON m.company_id = q.company_id AND m.axis = q.axis
                  AND m.final_member = q.final_member;

    WITH quarters AS (
        SELECT g.*,
               coalesce(g.period_start - lag(g.period_end) OVER w BETWEEN 0 AND 7, false)
                   AS follows
        FROM gold.segment_financials g
        JOIN silver.segment_metrics m ON m.metric = g.metric
        WHERE g.company_id = ANY(p_company_ids) AND g.span = 'quarter' AND m.additive
        WINDOW w AS (PARTITION BY g.company_id, g.axis, g.member, g.metric ORDER BY g.period_end)
    ),
    windows AS (
        SELECT q.*,
               count(*) OVER w4 AS n_quarters,
               bool_and(follows) OVER w3 AS contiguous,
               first_value(period_start) OVER w4 AS ttm_start,
               sum(value) OVER w4 AS ttm_value,
               max(ingested_at) OVER w4 AS ttm_ingested_at,
               source_accessions || lag(source_accessions, 1) OVER w
                   || lag(source_accessions, 2) OVER w || lag(source_accessions, 3) OVER w
                   AS accessions
        FROM quarters q
        WINDOW w AS (PARTITION BY company_id, axis, member, metric ORDER BY period_end),
               w4 AS (w ROWS 3 PRECEDING),
               w3 AS (w ROWS 2 PRECEDING)
    )
    INSERT INTO gold.segment_financials
        (company_id, span, axis, member, member_label, member_aliases, metric, period_start,
         period_end, value, unit, value_source, source_tag, axis_qname, accession_no,
         filer_cik, form, filed, source_accessions, ingested_at)
    SELECT company_id, 'ttm', axis, member, member_label, member_aliases, metric, ttm_start,
           period_end, ttm_value, unit, 'ttm', source_tag, axis_qname, accession_no, filer_cik,
           form, filed, ARRAY(SELECT DISTINCT a FROM unnest(accessions) a ORDER BY 1),
           ttm_ingested_at
    FROM windows
    WHERE n_quarters = 4 AND contiguous AND period_end - ttm_start + 1 BETWEEN 350 AND 380;

    SELECT count(*) INTO n FROM gold.segment_financials
    WHERE company_id = ANY(p_company_ids) AND span = 'quarter';
    RETURN n;
END
$$;

COMMIT;
