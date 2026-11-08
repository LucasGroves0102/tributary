-- Findings, so what gold now leaves out stays visible, and the annual comparison reads the facts gold
-- reads. ops.check_financials is 023's body otherwise: it keeps 018's segment findings and the restatement
-- check.
--   mistagged_fact (error; never the value):
--     rule period_after_filing, one per company and metric: a period ending over 7 days after its filing
--       was filed. Legacy Housing's income tax at 2033-03-31 and Quest Patent's equity at 2029-06-30 now read
--       as what they are, not as a gap "from Oct 1, 2026 to Dec 31, 2032" (M10), and gold.usable_facts (039)
--       keeps them out. periods: how many (PennyMac tags a maturity schedule as 40 future balance sheets).
--     rule debt_above_liabilities: a debt fact over five times total liabilities at that date, a scale
--       slip (Bank First's 2019 10-K, 38.4 billion for 38.4 million).
--   net_debt_gap (warning, at most one per company): the latest balance sheet with debt and cash has no net
--     debt (040): the investments series ended recently and there's room for it, or debt is only a part.
--     investments_end says when the series stopped, if it did. This is #33's silent gap.
-- The annual comparison picks through gold.best_facts, so a masked zero or a part isn't compared with a
-- TTM that doesn't use it (Verra Mobility would warn every year).
--
--   series_gap gains the hole every fourth quarter (NVIDIA's capex, FY2017-21): one finding, the latest,
--     with `repeated` years. The check needed four contiguous quarters first, which that never has.
--
-- ops.quality_summary is 023's view plus the two checks.
--
-- Locks on hermes: CREATE OR REPLACE of the function and of a view with no dependants, brief ACCESS
-- EXCLUSIVE on the view. New findings on `facts-fetcher --rebuild`.

SET LOCAL lock_timeout = '30s';

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
    annual AS (  -- each reported fiscal year, picked as gold picks its facts
        SELECT b.company_id, b.metric, b.period_start, b.period_end, b.value, b.accession_no
        FROM gold.best_facts(p_company_ids) b
        WHERE b.period_type = 'annual' AND b.additive
    ),
    -- what gold.usable_facts drops, for the reader: facts of mapped tags that can't be a figure of
    -- the period. Never the value (014's rule for detail).
    mistagged AS (  -- one per company and metric, its latest, with how many periods
        SELECT DISTINCT ON (x.company_id, x.metric) x.*, count(*) OVER (PARTITION BY x.company_id, x.metric) AS periods
        FROM (
            SELECT DISTINCT ON (k.company_id, t.metric, f.period_end)
                   k.company_id, t.metric, f.period_end, f.tag, f.accession_no, f.form, f.filed
            FROM silver.company_ciks k
            JOIN silver.financial_facts f ON f.cik = k.cik
            JOIN silver.metric_tags t ON t.tag = f.tag AND t.gold_line
            WHERE k.company_id = ANY(p_company_ids)
              AND f.period_end > f.filed + 7
              AND coalesce(f.form, '') !~ '^(DEF|PRE)'
            ORDER BY k.company_id, t.metric, f.period_end, f.filed DESC, f.accession_no DESC
        ) x
        ORDER BY x.company_id, x.metric, x.period_end DESC
    ),
    debt_scale AS (  -- a debt fact over five times the same date's total liabilities
        SELECT DISTINCT ON (k.company_id, t.metric, f.period_end)
               k.company_id, t.metric, f.period_end, f.tag, f.accession_no, f.form, f.filed
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.tag = f.tag AND t.metric IN ('long_term_debt', 'long_term_debt_current')
        JOIN LATERAL (
            SELECT x.value FROM silver.financial_facts x
            WHERE x.cik = f.cik AND x.metric = 'total_liabilities' AND x.tag = 'Liabilities'
              AND x.period_end = f.period_end AND x.period_start IS NULL
            ORDER BY x.filed DESC, x.accession_no DESC LIMIT 1
        ) l ON f.value > l.value * 5
        WHERE k.company_id = ANY(p_company_ids) AND f.period_start IS NULL
        ORDER BY k.company_id, t.metric, f.period_end, f.filed DESC, f.accession_no DESC
    ),
    -- net debt left out: the latest balance sheet with debt and cash has no figure (040's rule)
    net_debt_gap AS (
        SELECT b.company_id, b.period_end,
               (SELECT max(s.period_end) FROM q s
                WHERE s.company_id = b.company_id AND s.metric = 'short_term_investments'
                  AND s.period_end < b.period_end AND s.value > 0
                  AND s.period_end >= b.period_end - 730
                  AND NOT EXISTS (SELECT 1 FROM q x
                                  WHERE x.company_id = b.company_id AND x.period_end = b.period_end
                                    AND x.metric = 'short_term_investments')) AS investments_end
        FROM (
            SELECT DISTINCT ON (d.company_id) d.company_id, d.period_end
            FROM q d
            JOIN q c ON c.company_id = d.company_id AND c.metric = 'cash' AND c.period_end = d.period_end
            WHERE d.metric = 'long_term_debt'
            ORDER BY d.company_id, d.period_end DESC
        ) b
        WHERE NOT EXISTS (SELECT 1 FROM gold.derived_financials n
                          WHERE n.company_id = b.company_id AND n.span = 'quarter'
                            AND n.metric = 'net_debt' AND n.period_end = b.period_end)
    ),
    -- the same quarter missing year after year: the 10-K tags the year in a way the 10-Qs don't, so
    -- no fourth quarter and no annual total (NVIDIA's capex, FY2017-21). series_gap needs four contiguous
    -- quarters first, which a hole every fourth quarter never has. One finding, the latest.
    repeated_gap AS (
        SELECT DISTINCT ON (company_id, metric) company_id, metric, period_end, period_start, gap,
               count(*) OVER (PARTITION BY company_id, metric) AS years
        FROM runs
        WHERE additive AND gap > 7 AND contiguous_before = 2
        ORDER BY company_id, metric, period_end DESC
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
        UNION ALL
        SELECT company_id, 'mistagged_fact', metric, period_end, 'error',
               jsonb_build_object('rule', 'period_after_filing', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'tag', tag, 'periods', periods)
        FROM mistagged
        UNION ALL
        SELECT company_id, 'mistagged_fact', metric, period_end, 'error',
               jsonb_build_object('rule', 'debt_above_liabilities', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'tag', tag)
        FROM debt_scale
        UNION ALL
        SELECT company_id, 'series_gap', metric, period_end, 'warning',
               jsonb_build_object('missing_from', period_start - gap + 1,
                                  'missing_to', period_start - 1, 'repeated', years)
        FROM repeated_gap
        WHERE years >= 3
        UNION ALL
        SELECT company_id, 'net_debt_gap', 'net_debt', period_end, 'warning',
               jsonb_build_object('investments_end', investments_end)
        FROM net_debt_gap
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

-- 023's summary, plus the two checks above.
CREATE OR REPLACE VIEW ops.quality_summary AS
SELECT ch.check_name, ch.severity,
       count(f.company_id) AS findings,
       count(DISTINCT f.company_id) AS companies,
       (SELECT count(*) FROM ops.quality_companies) AS companies_checked,
       (SELECT max(checked_at) FROM ops.quality_runs) AS last_checked_at
FROM (VALUES ('impossible_value', 'error'), ('overlapping_quarters', 'error'),
             ('mistagged_fact', 'error'),
             ('calendar_collision', 'warning'), ('annual_sum', 'warning'),
             ('series_gap', 'warning'), ('sign_flip', 'warning'), ('outlier', 'warning'),
             ('net_debt_gap', 'warning'),
             ('segment_sum_business_segment', 'warning'), ('segment_sum_geography', 'warning'),
             ('segment_sum_product_service', 'warning'), ('restatement', 'info'))
     ch(check_name, severity)
LEFT JOIN ops.quality_findings f ON f.check_name = ch.check_name
GROUP BY ch.check_name, ch.severity;
