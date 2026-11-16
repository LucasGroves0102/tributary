-- ops.check_segments stops calling overlap a deviation (Riley, test 6, finding 8: Microsoft's data note
-- "10 products sum to 100.0% more than consolidated, members overlap or one is missing"; data trust:
-- checks that say what they mean).
--
-- 018/037 sum every member of an axis and flag the quarter when the sum is more than 1% off the
-- consolidated figure, unless leaving out ONE member (a subtotal) closes the gap. Filers report more
-- than one cut on an axis: Microsoft's Product + Service and Other beside its eight product lines, each
-- exactly revenue (100.0% more); NVIDIA's Data Center beside Hyperscale and AI Clouds, which make it;
-- Costco's Product beside two of its categories (46.6% more). None of that is a missing member or a
-- different basis, and the web app already separates it (services/web chart/segments.ts
-- `separateMembers` / `memberTree`: the members that make revenue, the rest children or another cut).
--
-- 1. ops.segment_cover(parts, target, tolerance): whether some of these members add up to the target
--    within the tolerance: a depth-first search over the parts, largest first, pruned by what is left
--    and by overshooting, with a budget of 200,000 steps. Positive parts only: with a negative member
--    (an operating loss, eliminations) the search can't prune, and the answer is NULL (unknown).
-- 2. ops.check_segments: 037's body with one edit marked "070": a quarter whose members don't add up
--    is flagged only when no set of them makes the consolidated figure within 0.5% (the web's widest
--    partition tier); unknown (NULL) keeps 037's rule.
--
-- Measured on hermes (2026-11-15) over the 38,461 stored segment_sum findings at their quarters'
-- members (037's members and kept rows, the check's own consolidated figure), with this search in
-- Python: 11,430 have a set of members within 0.5% of the figure and go (business segments 2,059 at
-- 417 companies, geography 1,825 at 240, products and services 7,546 at 741, Microsoft's, NVIDIA's and
-- Costco's among them); 15,764 have none and stay (a missing member or another basis); 11,267 have a
-- negative member and keep 037's rule. At 0.1% the count gone is 10,972. Stored findings change only
-- when the check runs again: on hermes, without re-deriving anything (as 037's header):
--   SELECT ops.check_segments(array_agg(DISTINCT company_id)) FROM gold.segment_financials;
--
-- Locks on hermes: CREATE FUNCTION / CREATE OR REPLACE of two functions, none on tables. Running the
-- check rewrites ops.quality_findings' segment_* rows (row locks). No backout file: putting back
-- 037's ops.check_segments (and dropping ops.segment_cover) undoes it; no other function reads them.

SET LOCAL lock_timeout = '30s';

-- 1.
CREATE FUNCTION ops.segment_cover(p_parts numeric[], p_target numeric, p_tolerance numeric)
RETURNS boolean
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE
    parts numeric[];
    suffix numeric[];
    n integer;
    lo numeric := p_target - p_tolerance;
    hi numeric := p_target + p_tolerance;
    stack_i integer[] := ARRAY[1];
    stack_s numeric[] := ARRAY[0];
    i integer;
    s numeric;
    steps integer := 0;
BEGIN
    IF p_target IS NULL OR p_target <= 0 OR p_parts IS NULL OR cardinality(p_parts) = 0 THEN
        RETURN NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM unnest(p_parts) p WHERE p IS NULL OR p <= 0) THEN
        RETURN NULL;
    END IF;
    SELECT array_agg(p ORDER BY p DESC) INTO parts FROM unnest(p_parts) p;
    n := cardinality(parts);
    suffix := array_fill(0::numeric, ARRAY[n + 1]);
    FOR k IN REVERSE n..1 LOOP
        suffix[k] := suffix[k + 1] + parts[k];
    END LOOP;
    WHILE cardinality(stack_i) > 0 LOOP
        i := stack_i[cardinality(stack_i)];
        s := stack_s[cardinality(stack_s)];
        stack_i := stack_i[1:cardinality(stack_i) - 1];
        stack_s := stack_s[1:cardinality(stack_s) - 1];
        steps := steps + 1;
        IF steps > 200000 THEN
            RETURN NULL;
        END IF;
        IF s > 0 AND s >= lo AND s <= hi THEN
            RETURN true;
        END IF;
        CONTINUE WHEN i > n OR s + suffix[i] < lo;
        stack_i := stack_i || (i + 1);
        stack_s := stack_s || s;
        IF s + parts[i] <= hi THEN
            stack_i := stack_i || (i + 1);
            stack_s := stack_s || (s + parts[i]);
        END IF;
    END LOOP;
    RETURN false;
END
$$;

-- 2. 037's body + the edit marked "070"
CREATE OR REPLACE FUNCTION ops.check_segments(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    run bigint;
    n integer;
BEGIN
    INSERT INTO ops.quality_runs (companies)
    SELECT count(DISTINCT id) FROM unnest(p_company_ids) id
    RETURNING run_id INTO run;

    DELETE FROM ops.quality_findings
    WHERE company_id = ANY(p_company_ids) AND check_name LIKE 'segment\_%';

    WITH members AS (
        SELECT company_id,
               CASE WHEN axis = 'reconciliation' THEN 'business_segment' ELSE axis END
                   AS checked_axis,
               axis, metric, period_end, value, accession_no
        FROM gold.segment_financials
        WHERE company_id = ANY(p_company_ids) AND span = 'quarter'
          AND (metric = 'revenue'
               OR (metric = 'operating_income' AND axis IN ('business_segment',
                                                            'reconciliation')))
    ),
    segment_sums AS (
        SELECT company_id, checked_axis, metric, period_end,
               sum(value) FILTER (WHERE axis <> 'reconciliation') AS segments
        FROM members
        GROUP BY company_id, checked_axis, metric, period_end
    ),
    kept AS (  -- a reconciliation member that just restates the segments' sum isn't an addend
        SELECT m.*
        FROM members m
        JOIN segment_sums s USING (company_id, checked_axis, metric, period_end)
        WHERE NOT (m.axis = 'reconciliation' AND s.segments <> 0
                   AND abs(m.value - s.segments) <= 0.005 * abs(s.segments))
    ),
    sums AS (
        SELECT company_id, checked_axis, metric, period_end,
               sum(value) AS total, array_agg(value) AS parts,
               count(*) FILTER (WHERE axis <> 'reconciliation') AS n_members,
               max(accession_no) AS accession_no
        FROM kept
        GROUP BY company_id, checked_axis, metric, period_end
    ),
    findings AS (
        SELECT s.company_id, 'segment_sum_' || s.checked_axis AS check_name, s.metric,
               s.period_end, 'warning' AS severity,
               jsonb_build_object('axis', s.checked_axis, 'members', s.n_members,
                                  'deviation', round((s.total - g.value) / abs(g.value), 4),
                                  'accession_no', s.accession_no,
                                  'total_accession', g.accession_no) AS detail
        FROM sums s
        JOIN gold.quarterly_financials g
          ON g.company_id = s.company_id AND g.metric = s.metric
         AND g.period_end = s.period_end
        WHERE s.n_members >= 2 AND g.value <> 0
          AND abs(s.total - g.value) > 0.01 * abs(g.value)
          AND NOT EXISTS (SELECT 1 FROM unnest(s.parts) p
                          WHERE abs(s.total - p - g.value) <= 0.01 * abs(g.value))
          -- 070: members that overlap (a parent beside its children, two cuts of the same
          -- revenue) aren't a deviation: some set of them makes the figure (header)
          AND NOT coalesce(ops.segment_cover(s.parts, g.value, 0.005 * abs(g.value)), false)
    )
    INSERT INTO ops.quality_findings
        (company_id, check_name, metric, period_end, severity, detail, run_id)
    SELECT company_id, check_name, metric, period_end, severity, detail, run
    FROM findings
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;

    UPDATE ops.quality_runs SET findings = n, by_check = coalesce((
        SELECT jsonb_object_agg(check_name, c)
        FROM (SELECT check_name, count(*) AS c FROM ops.quality_findings
              WHERE run_id = run GROUP BY check_name) x), '{}')
    WHERE run_id = run;

    INSERT INTO ops.quality_companies (company_id, run_id, checked_at, errors, warnings)
    SELECT c.company_id, run, now(),
           count(f.*) FILTER (WHERE f.severity = 'error'),
           count(f.*) FILTER (WHERE f.severity = 'warning')
    FROM silver.companies c
    LEFT JOIN ops.quality_findings f ON f.company_id = c.company_id
    WHERE c.company_id = ANY(p_company_ids)
    GROUP BY c.company_id
    ON CONFLICT (company_id) DO UPDATE SET run_id = EXCLUDED.run_id,
        checked_at = EXCLUDED.checked_at, errors = EXCLUDED.errors,
        warnings = EXCLUDED.warnings;

    RETURN n;
END
$$;
