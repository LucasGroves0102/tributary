-- Data notes in the product (Phase 6): the Query API shows each company's data-quality
-- findings to users who can read its financials. Findings are about values (where a series
-- has a gap, which quarters overlap, which figures were restated), so they are NOT public
-- metadata: OpenFGA `financials` first, then the RLS policies below (same shape as 007).
--
--   1. A new check, restatement (severity info): a reported quarter or balance whose value
--      differs by more than 0.5% from what the first filing for that period (same tag, unit
--      and dates) said. detail: both filings and dates, and the relative change; never the
--      values (014's rule for detail). quality_companies keeps counting errors and warnings
--      only.
--   2. ops.check_financials: 018's body (it keeps the segment_* findings, which belong to
--      ops.check_segments), plus that check.
--   3. ops.quality_summary: 018's view, plus that check.
--   4. query_api: USAGE on schema ops (its functions are invoker-rights and the role has no
--      write privileges; sec_take_token / sec_report_throttle aren't executable by PUBLIC,
--      022) and SELECT on ops.quality_findings / ops.quality_companies under RLS keyed on
--      authz.user_access tier financials. ops_reader still reads views only.
--
-- Derived quarters and "companyfacts behind EDGAR" need no new grants: the API reads them
-- from gold.quarterly_financials (RLS) and silver.filings (public SEC index) plus the
-- accession column of silver.financial_facts it already has (008).
--
-- Existing companies get restatement findings on their next gold refresh
-- (`make financials-rebuild`, or the facts-fetcher's next run for each company).

ALTER TABLE ops.quality_findings DROP CONSTRAINT quality_findings_severity_check;
ALTER TABLE ops.quality_findings ADD CONSTRAINT quality_findings_severity_check
    CHECK (severity IN ('error', 'warning', 'info'));

CREATE OR REPLACE FUNCTION ops.check_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    run bigint;
    n integer;
BEGIN
    INSERT INTO ops.quality_runs (companies)
    SELECT count(DISTINCT id) FROM unnest(p_company_ids) id
    RETURNING run_id INTO run;

    DELETE FROM ops.quality_findings
    WHERE company_id = ANY(p_company_ids) AND check_name NOT LIKE 'segment\_%';

    WITH q AS (
        SELECT g.company_id, g.metric, g.period_start, g.period_end, g.value, m.kind,
               m.additive, g.accession_no,
               g.period_start - lag(g.period_end) OVER w AS gap,
               lag(g.value) OVER w AS prev_value,
               lead(g.value) OVER w AS next_value
        FROM gold.quarterly_financials g
        JOIN silver.metrics m ON m.metric = g.metric
        WHERE g.company_id = ANY(p_company_ids)
        WINDOW w AS (PARTITION BY g.company_id, g.metric ORDER BY g.period_end)
    ),
    runs AS (  -- durations: are the three quarters before this one contiguous?
        SELECT q.*,
               count(*) FILTER (WHERE gap BETWEEN 0 AND 7) OVER (
                   PARTITION BY company_id, metric ORDER BY period_end
                   ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS contiguous_before
        FROM q
        WHERE kind = 'duration'
    ),
    pairs AS (
        SELECT company_id, period_end,
               max(value) FILTER (WHERE metric = 'revenue') AS revenue,
               max(value) FILTER (WHERE metric = 'gross_profit') AS gross_profit,
               max(value) FILTER (WHERE metric = 'current_assets') AS current_assets,
               max(value) FILTER (WHERE metric = 'total_assets') AS total_assets,
               max(value) FILTER (WHERE metric = 'current_liabilities') AS current_liabilities,
               max(value) FILTER (WHERE metric = 'total_liabilities') AS total_liabilities
        FROM q
        GROUP BY company_id, period_end
    ),
    annual AS (  -- each reported fiscal year, picked like gold picks quarters
        SELECT DISTINCT ON (k.company_id, f.metric, f.period_start, f.period_end)
               k.company_id, f.metric, f.period_start, f.period_end, f.value, f.accession_no
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.metric = f.metric AND t.tag = f.tag
        JOIN silver.metrics m ON m.metric = f.metric AND m.unit = f.unit
        WHERE k.company_id = ANY(p_company_ids) AND f.period_type = 'annual' AND m.additive
        ORDER BY k.company_id, f.metric, f.period_start, f.period_end, t.priority,
                 f.filed DESC, f.accession_no DESC
    ),
    findings AS (
        SELECT company_id, 'impossible_value' AS check_name, metric, period_end,
               'error' AS severity, jsonb_build_object('rule', 'negative',
                                                       'accession_no', accession_no) AS detail
        FROM q
        WHERE value < 0 AND metric IN (
            'revenue', 'total_assets', 'current_assets', 'total_liabilities',
            'current_liabilities', 'cash', 'short_term_investments', 'inventory',
            'receivables', 'shares_outstanding', 'weighted_shares_basic',
            'weighted_shares_diluted')
        UNION ALL
        SELECT company_id, 'impossible_value', r.metric, period_end, 'error',
               jsonb_build_object('rule', r.rule)
        FROM pairs
        CROSS JOIN LATERAL (VALUES
            ('current_assets', 'current_assets > total_assets', current_assets > total_assets),
            ('current_liabilities', 'current_liabilities > total_liabilities',
             current_liabilities > total_liabilities),
            ('gross_profit', 'gross_profit > revenue', revenue > 0 AND gross_profit > revenue)
        ) r(metric, rule, failed)
        WHERE r.failed
        UNION ALL
        SELECT company_id, 'overlapping_quarters', metric, period_end, 'error',
               jsonb_build_object('overlap_days', -gap, 'accession_no', accession_no)
        FROM runs
        WHERE gap < 0
        UNION ALL
        SELECT company_id, 'calendar_collision', metric, max(period_end), 'warning',
               jsonb_build_object('calendar_quarter', to_char(cq, 'YYYY"Q"Q'),
                                  'period_ends', array_agg(period_end ORDER BY period_end))
        FROM (SELECT r.*, gold.calendar_quarter(period_end) AS cq FROM runs r) c
        GROUP BY company_id, metric, cq
        HAVING count(*) > 1
        UNION ALL
        SELECT d.company_id, 'annual_sum', d.metric, d.period_end, 'warning',
               jsonb_build_object('deviation', round((d.value - a.value) / abs(a.value), 4),
                                  'annual_accession', a.accession_no,
                                  'quarter_accessions', d.source_accessions)
        FROM gold.derived_financials d
        JOIN annual a ON a.company_id = d.company_id AND a.metric = d.metric
                     AND a.period_end = d.period_end
                     AND abs(a.period_start - d.period_start) <= 7
        WHERE d.company_id = ANY(p_company_ids) AND d.span = 'ttm' AND d.value_source = 'ttm'
          AND a.value <> 0
          AND abs(d.value - a.value) > 0.01 * abs(a.value)
        UNION ALL
        SELECT company_id, 'series_gap', metric, period_end, 'warning',
               jsonb_build_object('missing_from', period_start - gap + 1,
                                  'missing_to', period_start - 1)
        FROM runs
        WHERE additive AND gap > 7 AND contiguous_before = 3
        UNION ALL
        SELECT company_id, 'sign_flip', metric, period_end, 'warning',
               jsonb_build_object('accession_no', accession_no)
        FROM q
        WHERE metric IN ('cost_of_revenue', 'research_and_development', 'sga',
                         'interest_expense', 'capex', 'depreciation_amortization',
                         'stock_based_compensation', 'dividends_paid', 'share_buybacks')
          AND value <> 0 AND prev_value <> 0 AND next_value <> 0
          AND sign(value) <> sign(prev_value) AND sign(prev_value) = sign(next_value)
        UNION ALL
        SELECT company_id, 'outlier', metric, period_end, 'warning',
               jsonb_build_object('vs_neighbours',
                                  round(value / greatest(abs(prev_value), abs(next_value)), 1),
                                  'accession_no', accession_no)
        FROM q
        WHERE prev_value <> 0 AND next_value <> 0 AND sign(prev_value) = sign(next_value)
          AND greatest(abs(prev_value), abs(next_value))
              <= 2 * least(abs(prev_value), abs(next_value))
          AND abs(value) >= 10 * greatest(abs(prev_value), abs(next_value))
        UNION ALL
        -- restatement: the period's first filing (same tag, unit, dates) said something else
        SELECT g.company_id, 'restatement', g.metric, g.period_end, 'info',
               jsonb_build_object('original_accession', o.accession_no,
                                  'original_filed', o.filed,
                                  'restated_accession', g.accession_no,
                                  'restated_filed', g.filed,
                                  'change', round((g.value - o.value) / abs(o.value), 4))
        FROM gold.quarterly_financials g
        CROSS JOIN LATERAL (
            SELECT f.accession_no, f.filed, f.value
            FROM silver.company_ciks k
            JOIN silver.financial_facts f ON f.cik = k.cik
            WHERE k.company_id = g.company_id AND f.metric = g.metric
              AND f.period_end = g.period_end
              AND f.period_start IS NOT DISTINCT FROM g.period_start
              AND f.tag = split_part(g.source_tag, ':', 2) AND f.unit = g.unit
            ORDER BY f.filed, f.accession_no
            LIMIT 1
        ) o
        WHERE g.company_id = ANY(p_company_ids) AND g.value_source = 'reported'
          AND o.filed < g.filed AND o.value <> 0
          AND abs(g.value - o.value) > 0.005 * abs(o.value)
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

-- 018's summary, plus restatement.
CREATE OR REPLACE VIEW ops.quality_summary AS
SELECT ch.check_name, ch.severity,
       count(f.company_id) AS findings,
       count(DISTINCT f.company_id) AS companies,
       (SELECT count(*) FROM ops.quality_companies) AS companies_checked,
       (SELECT max(checked_at) FROM ops.quality_runs) AS last_checked_at
FROM (VALUES ('impossible_value', 'error'), ('overlapping_quarters', 'error'),
             ('calendar_collision', 'warning'), ('annual_sum', 'warning'),
             ('series_gap', 'warning'), ('sign_flip', 'warning'), ('outlier', 'warning'),
             ('segment_sum_business_segment', 'warning'), ('segment_sum_geography', 'warning'),
             ('segment_sum_product_service', 'warning'), ('restatement', 'info'))
     ch(check_name, severity)
LEFT JOIN ops.quality_findings f ON f.check_name = ch.check_name
GROUP BY ch.check_name, ch.severity;

GRANT USAGE ON SCHEMA ops TO query_api;
GRANT SELECT ON ops.quality_findings, ops.quality_companies TO query_api;

ALTER TABLE ops.quality_findings ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_financials ON ops.quality_findings FOR SELECT TO query_api
    USING (EXISTS (
        SELECT 1 FROM authz.user_access a
        WHERE a.user_id = current_setting('app.user_id', true)
          AND a.company_id = quality_findings.company_id
          AND a.tier = 'financials'
    ));

ALTER TABLE ops.quality_companies ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_financials ON ops.quality_companies FOR SELECT TO query_api
    USING (EXISTS (
        SELECT 1 FROM authz.user_access a
        WHERE a.user_id = current_setting('app.user_id', true)
          AND a.company_id = quality_companies.company_id
          AND a.tier = 'financials'
    ));
