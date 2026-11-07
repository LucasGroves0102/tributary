-- ops.check_segments added a reconciliation member that is really the segments' subtotal. NVIDIA
-- tags $66,595M of operating income with us-gaap:CorporateNonSegmentMember, which is exactly Compute
-- and Networking ($62,696M) plus Graphics ($3,899M), so the check summed $133,190M against a
-- consolidated $63,734M and its data note said "109.0% more". The app's own figures say 4.5% more
-- (the costs no segment carries). A reconciliation member within 0.5% of the segments' sum is now
-- left out of the sum, as 018 already lets one subtotal among the members pass. Findings stay
-- findings: NVIDIA keeps its note, at +4.49%.
--
-- 018's body otherwise. Stored findings keep their old deviation until the check runs again: the next
-- gold refresh per company, `make segments-rebuild`, or on hermes, without re-deriving anything:
--   SELECT ops.check_segments(array_agg(DISTINCT company_id)) FROM gold.segment_financials;
--
-- Locks on hermes: replacing a function takes none on tables. Running the check rewrites
-- ops.quality_findings' segment_* rows (row locks, no table lock).

SET LOCAL lock_timeout = '30s';

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
