-- Calendar-aligned quarters, trailing twelve months (TTM) and derived metrics over gold, and
-- data-quality checks after every gold refresh.
--
-- Calendar rule: a period belongs to the calendar quarter it ends in, unless it ends in the
-- first 35 days of one; then it belongs to the previous calendar quarter. Equivalently, the
-- calendar quarter containing period_end - 35 days. Walmart's and NVIDIA's quarters ending late
-- April land in Q1, Costco's 12-week quarter ending ~May 10 in Q2, its 16-week Q4 ending
-- ~Aug 31 in Q3, and 52/53-week years ending in the first days of January in Q4. Instants use
-- the same rule, so a quarter's balance sheet stays with its income statement. If two periods
-- of a metric land in the same calendar quarter, the one overlapping it most (an instant: the
-- latest) is the calendar row; ops.check_financials reports the collision.
--
-- Fiscal labels come from silver.companies.fiscal_year_end (MMDD, from the SEC submissions);
-- NULL when it's unknown. They follow the company's current fiscal year end, so years before a
-- change of fiscal year are mislabelled.
--
-- Quarterly rows stay in gold.quarterly_financials (the fiscal view). TTM sums and derived
-- metrics are materialized in gold.derived_financials, rebuilt per batch of companies with
-- the quarters (gold.refresh_financials): they need year-ago lookups and pivots across metrics
-- that a view would redo for every request. The labels and calendar dedupe are cheap per row
-- and stay in the view gold.financials, which reads both tables under their RLS policies.

-- First day of the calendar quarter a period ending on period_end belongs to.
CREATE FUNCTION gold.calendar_quarter(period_end date) RETURNS date
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT date_trunc('quarter', (period_end - 35)::timestamp)::date
$$;

-- MMDD in a given year (Feb 29 becomes Mar 1 in other years).
CREATE FUNCTION gold.nominal_date(year int, mmdd text) RETURNS date
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT make_date(year, left(mmdd, 2)::int, 1) + (right(mmdd, 2)::int - 1)
$$;

-- End of the fiscal year a period ending on period_end belongs to: the first nominal fiscal
-- year end on or after period_end - 10 days. The 10 days absorb 52/53-week years, which end
-- up to a week either side of the nominal date.
CREATE FUNCTION gold.fiscal_year_end(period_end date, mmdd text) RETURNS date
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN gold.nominal_date(extract(year FROM period_end - 10)::int, mmdd) >= period_end - 10
        THEN gold.nominal_date(extract(year FROM period_end - 10)::int, mmdd)
        ELSE gold.nominal_date(extract(year FROM period_end - 10)::int + 1, mmdd)
    END
$$;

-- Derived metrics: what gold.refresh_derived_financials computes. Ratios are fractions.
CREATE TABLE gold.derived_metrics (
    metric     text   PRIMARY KEY,
    unit       text   NOT NULL,
    spans      text[] NOT NULL,  -- quarter, ttm
    definition text   NOT NULL
);

INSERT INTO gold.derived_metrics VALUES
    ('gross_margin',      'ratio', '{quarter,ttm}', 'gross_profit / revenue'),
    ('operating_margin',  'ratio', '{quarter,ttm}', 'operating_income / revenue'),
    ('net_margin',        'ratio', '{quarter,ttm}', 'net_income / revenue'),
    ('free_cash_flow',    'USD',   '{quarter,ttm}', 'operating_cash_flow - capex'),
    ('fcf_margin',        'ratio', '{quarter,ttm}', '(operating_cash_flow - capex) / revenue'),
    ('revenue_growth',    'ratio', '{quarter,ttm}',
     'revenue / revenue of the period ending 350-380 days earlier - 1; base > 0'),
    ('net_income_growth', 'ratio', '{quarter,ttm}',
     'net_income / net_income of the period ending 350-380 days earlier - 1; base > 0'),
    ('eps_growth',        'ratio', '{quarter}',
     'eps_diluted / eps_diluted of the quarter ending 350-380 days earlier - 1; base > 0. '
     'No TTM: fiscal Q4 EPS is never reported or derived'),
    ('payout_ratio',      'ratio', '{quarter,ttm}', 'dividends_paid / net_income; net_income > 0'),
    ('debt_to_equity',    'ratio', '{quarter,ttm}',
     '(long_term_debt + long_term_debt_current) / stockholders_equity; equity > 0'),
    ('net_debt',          'USD',   '{quarter,ttm}',
     'long_term_debt + long_term_debt_current - cash - short_term_investments'),
    ('current_ratio',     'ratio', '{quarter,ttm}', 'current_assets / current_liabilities'),
    ('roe',               'ratio', '{ttm}',
     'TTM net_income / average of stockholders_equity at the end and 350-380 days earlier');

-- TTM sums of additive metrics and derived metrics, with provenance (FR6).
--   span quarter: per fiscal quarter; span ttm: per four contiguous quarters ending there
--   value_source ttm: sum of four quarters (inputs lists them); computed: a derived metric
--   (inputs holds the values it used, keyed by metric; <metric>_year_ago for the earlier one)
CREATE TABLE gold.derived_financials (
    company_id        text        NOT NULL REFERENCES silver.companies ON DELETE CASCADE,
    span              text        NOT NULL CHECK (span IN ('quarter', 'ttm')),
    metric            text        NOT NULL,
    period_start      date,       -- NULL for balance-sheet ratios
    period_end        date        NOT NULL,
    value             numeric     NOT NULL,
    unit              text        NOT NULL,
    value_source      text        NOT NULL CHECK (value_source IN ('ttm', 'computed')),
    source_tag        text,       -- TTM sums: the latest quarter's XBRL tag
    inputs            jsonb       NOT NULL,
    accession_no      text        NOT NULL,  -- filing to cite: the latest-filed input
    form              text,
    filed             date        NOT NULL,
    source_accessions text[]      NOT NULL,  -- every filing the value depends on
    ingested_at       timestamptz NOT NULL,  -- latest of the inputs'
    refreshed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, span, metric, period_end)
);

-- Rebuild many companies' TTM sums and derived metrics from gold.quarterly_financials.
--   TTM: an additive duration metric summed over four contiguous quarters (each starting 0-7
--   days after the previous one ends; some filers start a quarter on the previous one's end
--   date), spanning 350-380 days (52/53-week years, 12/16-week quarters).
--   Instants (balance sheet) are not summed: the TTM view uses their value at the period end.
--   Non-additive durations (EPS, weighted shares) have no TTM.
--   Year-ago values: the same metric and span ending 350-380 days earlier.
CREATE FUNCTION gold.refresh_derived_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('gold.quarterly_financials'), hashtext(id))
    FROM (SELECT DISTINCT id FROM unnest(p_company_ids) id ORDER BY id) ids;

    DELETE FROM gold.derived_financials WHERE company_id = ANY(p_company_ids);

    WITH quarters AS (
        SELECT g.*,
               coalesce(g.period_start - lag(g.period_end) OVER w BETWEEN 0 AND 7, false)
                   AS follows
        FROM gold.quarterly_financials g
        JOIN silver.metrics m ON m.metric = g.metric
        WHERE g.company_id = ANY(p_company_ids) AND m.kind = 'duration' AND m.additive
        WINDOW w AS (PARTITION BY g.company_id, g.metric ORDER BY g.period_end)
    ),
    windows AS (
        SELECT q.*,
               count(*) OVER w4 AS n_quarters,
               bool_and(follows) OVER w3 AS contiguous,
               first_value(period_start) OVER w4 AS ttm_start,
               sum(value) OVER w4 AS ttm_value,
               max(ingested_at) OVER w4 AS ttm_ingested_at,
               jsonb_agg(jsonb_build_object('period_start', period_start,
                                            'period_end', period_end, 'value', value,
                                            'accession_no', accession_no)) OVER w4 AS parts,
               source_accessions || lag(source_accessions, 1) OVER w
                   || lag(source_accessions, 2) OVER w || lag(source_accessions, 3) OVER w
                   AS accessions
        FROM quarters q
        WINDOW w AS (PARTITION BY company_id, metric ORDER BY period_end),
               w4 AS (w ROWS 3 PRECEDING),
               w3 AS (w ROWS 2 PRECEDING)
    )
    INSERT INTO gold.derived_financials
        (company_id, span, metric, period_start, period_end, value, unit, value_source,
         source_tag, inputs, accession_no, form, filed, source_accessions, ingested_at)
    SELECT company_id, 'ttm', metric, ttm_start, period_end, ttm_value, unit, 'ttm',
           source_tag, jsonb_build_object('quarters', parts), accession_no, form, filed,
           ARRAY(SELECT DISTINCT a FROM unnest(accessions) a ORDER BY 1), ttm_ingested_at
    FROM windows
    WHERE n_quarters = 4 AND contiguous AND period_end - ttm_start + 1 BETWEEN 350 AND 380;

    WITH facts AS (
        SELECT 'quarter' AS span, company_id, metric, period_start, period_end, value,
               accession_no, form, filed, source_accessions, ingested_at
        FROM gold.quarterly_financials
        WHERE company_id = ANY(p_company_ids)
        UNION ALL
        SELECT 'ttm', company_id, metric, period_start, period_end, value,
               accession_no, form, filed, source_accessions, ingested_at
        FROM gold.derived_financials
        WHERE company_id = ANY(p_company_ids)  -- the TTM sums just inserted
        UNION ALL
        SELECT 'ttm', g.company_id, g.metric, g.period_start, g.period_end, g.value,
               g.accession_no, g.form, g.filed, g.source_accessions, g.ingested_at
        FROM gold.quarterly_financials g
        JOIN silver.metrics m ON m.metric = g.metric
        WHERE g.company_id = ANY(p_company_ids) AND m.kind = 'instant'
    ),
    base AS (  -- the inputs of derived metrics
        SELECT * FROM facts
        WHERE metric IN ('revenue', 'gross_profit', 'operating_income', 'net_income',
                         'eps_diluted', 'operating_cash_flow', 'capex', 'dividends_paid',
                         'long_term_debt', 'long_term_debt_current', 'cash',
                         'short_term_investments', 'current_assets', 'current_liabilities',
                         'stockholders_equity')
    ),
    year_ago AS (
        SELECT b.*,
               last_value(value) OVER ya AS ya_value,
               last_value(source_accessions) OVER ya AS ya_accessions
        FROM base b
        WINDOW ya AS (PARTITION BY span, company_id, metric ORDER BY period_end
                      RANGE BETWEEN '380 days' PRECEDING AND '350 days' PRECEDING)
    ),
    periods AS (  -- one row per (span, company, period end), with each input metric's value
        SELECT span, company_id, period_end,
               max(value) FILTER (WHERE metric = 'revenue') AS revenue,
               max(value) FILTER (WHERE metric = 'gross_profit') AS gross_profit,
               max(value) FILTER (WHERE metric = 'operating_income') AS operating_income,
               max(value) FILTER (WHERE metric = 'net_income') AS net_income,
               max(value) FILTER (WHERE metric = 'eps_diluted') AS eps_diluted,
               max(value) FILTER (WHERE metric = 'operating_cash_flow') AS operating_cash_flow,
               max(value) FILTER (WHERE metric = 'capex') AS capex,
               max(value) FILTER (WHERE metric = 'dividends_paid') AS dividends_paid,
               max(value) FILTER (WHERE metric = 'long_term_debt') AS long_term_debt,
               max(value) FILTER (WHERE metric = 'long_term_debt_current')
                   AS long_term_debt_current,
               max(value) FILTER (WHERE metric = 'cash') AS cash,
               max(value) FILTER (WHERE metric = 'short_term_investments')
                   AS short_term_investments,
               max(value) FILTER (WHERE metric = 'current_assets') AS current_assets,
               max(value) FILTER (WHERE metric = 'current_liabilities') AS current_liabilities,
               max(value) FILTER (WHERE metric = 'stockholders_equity') AS stockholders_equity,
               max(ya_value) FILTER (WHERE metric = 'revenue') AS revenue_year_ago,
               max(ya_value) FILTER (WHERE metric = 'net_income') AS net_income_year_ago,
               max(ya_value) FILTER (WHERE metric = 'eps_diluted') AS eps_diluted_year_ago,
               max(ya_value) FILTER (WHERE metric = 'stockholders_equity')
                   AS stockholders_equity_year_ago,
               jsonb_object_agg(metric, jsonb_build_object(
                   'period_start', period_start, 'accession_no', accession_no, 'form', form,
                   'filed', filed, 'accessions', source_accessions,
                   'ingested_at', ingested_at, 'year_ago_accessions', ya_accessions)) AS prov
        FROM year_ago
        GROUP BY span, company_id, period_end
    ),
    derived AS (
        SELECT p.span, p.company_id, p.period_end, p.prov, d.metric,
               CASE WHEN dm.unit = 'ratio' THEN round(d.value, 6) ELSE d.value END AS value,
               dm.unit, jsonb_strip_nulls(d.inputs) AS inputs
        FROM periods p
        CROSS JOIN LATERAL (VALUES
            ('gross_margin',
             CASE WHEN p.revenue > 0 THEN p.gross_profit / p.revenue END,
             jsonb_build_object('gross_profit', p.gross_profit, 'revenue', p.revenue)),
            ('operating_margin',
             CASE WHEN p.revenue > 0 THEN p.operating_income / p.revenue END,
             jsonb_build_object('operating_income', p.operating_income, 'revenue', p.revenue)),
            ('net_margin',
             CASE WHEN p.revenue > 0 THEN p.net_income / p.revenue END,
             jsonb_build_object('net_income', p.net_income, 'revenue', p.revenue)),
            ('free_cash_flow',
             p.operating_cash_flow - p.capex,
             jsonb_build_object('operating_cash_flow', p.operating_cash_flow,
                                'capex', p.capex)),
            ('fcf_margin',
             CASE WHEN p.revenue > 0 THEN (p.operating_cash_flow - p.capex) / p.revenue END,
             jsonb_build_object('operating_cash_flow', p.operating_cash_flow,
                                'capex', p.capex, 'revenue', p.revenue)),
            ('revenue_growth',
             CASE WHEN p.revenue_year_ago > 0 THEN p.revenue / p.revenue_year_ago - 1 END,
             jsonb_build_object('revenue', p.revenue,
                                'revenue_year_ago', p.revenue_year_ago)),
            ('net_income_growth',
             CASE WHEN p.net_income_year_ago > 0
                  THEN p.net_income / p.net_income_year_ago - 1 END,
             jsonb_build_object('net_income', p.net_income,
                                'net_income_year_ago', p.net_income_year_ago)),
            ('eps_growth',
             CASE WHEN p.eps_diluted_year_ago > 0
                  THEN p.eps_diluted / p.eps_diluted_year_ago - 1 END,
             jsonb_build_object('eps_diluted', p.eps_diluted,
                                'eps_diluted_year_ago', p.eps_diluted_year_ago)),
            ('payout_ratio',
             CASE WHEN p.net_income > 0 THEN p.dividends_paid / p.net_income END,
             jsonb_build_object('dividends_paid', p.dividends_paid,
                                'net_income', p.net_income)),
            ('debt_to_equity',
             CASE WHEN p.stockholders_equity > 0
                  THEN (p.long_term_debt + coalesce(p.long_term_debt_current, 0))
                       / p.stockholders_equity END,
             jsonb_build_object('long_term_debt', p.long_term_debt,
                                'long_term_debt_current', p.long_term_debt_current,
                                'stockholders_equity', p.stockholders_equity)),
            ('net_debt',
             p.long_term_debt + coalesce(p.long_term_debt_current, 0) - p.cash
                 - coalesce(p.short_term_investments, 0),
             jsonb_build_object('long_term_debt', p.long_term_debt,
                                'long_term_debt_current', p.long_term_debt_current,
                                'cash', p.cash,
                                'short_term_investments', p.short_term_investments)),
            ('current_ratio',
             CASE WHEN p.current_liabilities > 0
                  THEN p.current_assets / p.current_liabilities END,
             jsonb_build_object('current_assets', p.current_assets,
                                'current_liabilities', p.current_liabilities)),
            ('roe',
             CASE WHEN p.span = 'ttm'
                       AND p.stockholders_equity + p.stockholders_equity_year_ago > 0
                  THEN p.net_income
                       / ((p.stockholders_equity + p.stockholders_equity_year_ago) / 2) END,
             jsonb_build_object('net_income', p.net_income,
                                'stockholders_equity', p.stockholders_equity,
                                'stockholders_equity_year_ago',
                                p.stockholders_equity_year_ago))
        ) d(metric, value, inputs)
        JOIN gold.derived_metrics dm ON dm.metric = d.metric
        WHERE d.value IS NOT NULL
    )
    INSERT INTO gold.derived_financials
        (company_id, span, metric, period_start, period_end, value, unit, value_source,
         inputs, accession_no, form, filed, source_accessions, ingested_at)
    SELECT d.company_id, d.span, d.metric, c.period_start, d.period_end, d.value, d.unit,
           'computed', d.inputs, c.accession_no, c.form, c.filed, s.accessions, c.ingested_at
    FROM derived d
    -- provenance of the inputs at this period end: latest filing, and the earliest start
    -- (NULL when all are instants)
    CROSS JOIN LATERAL (
        SELECT (array_agg(j->>'accession_no' ORDER BY j->>'filed' DESC,
                          j->>'accession_no' DESC))[1] AS accession_no,
               (array_agg(j->>'form' ORDER BY j->>'filed' DESC,
                          j->>'accession_no' DESC))[1] AS form,
               max((j->>'filed')::date) AS filed,
               min((j->>'period_start')::date) AS period_start,
               max((j->>'ingested_at')::timestamptz) AS ingested_at
        FROM jsonb_object_keys(d.inputs) k
        CROSS JOIN LATERAL (SELECT d.prov->k) x(j)
        WHERE k NOT LIKE '%\_year\_ago'
    ) c
    -- every filing behind the inputs, year-ago ones included
    CROSS JOIN LATERAL (
        SELECT array_agg(DISTINCT a ORDER BY a) AS accessions
        FROM jsonb_object_keys(d.inputs) k
        CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE WHEN k LIKE '%\_year\_ago' THEN d.prov->left(k, -9)->'year_ago_accessions'
                 ELSE d.prov->k->'accessions' END) a
    ) s;

    SELECT count(*) INTO n FROM gold.derived_financials WHERE company_id = ANY(p_company_ids);
    RETURN n;
END
$$;

-- Fiscal quarters, TTM and derived metrics with fiscal and calendar labels: what the Query
-- API reads. span: quarter | ttm. calendar_primary marks the row to show per calendar quarter.
-- TTM instants are the quarter's balance-sheet values.
CREATE VIEW gold.financials WITH (security_invoker = true) AS
WITH facts AS (
    SELECT company_id, 'quarter' AS span, metric, period_start, period_end, value, unit,
           value_source, source_tag, NULL::jsonb AS inputs, accession_no, form, filed,
           source_accessions, ingested_at
    FROM gold.quarterly_financials
    UNION ALL
    SELECT g.company_id, 'ttm', g.metric, g.period_start, g.period_end, g.value, g.unit,
           g.value_source, g.source_tag, NULL, g.accession_no, g.form, g.filed,
           g.source_accessions, g.ingested_at
    FROM gold.quarterly_financials g
    JOIN silver.metrics m ON m.metric = g.metric
    WHERE m.kind = 'instant'
    UNION ALL
    SELECT company_id, span, metric, period_start, period_end, value, unit, value_source,
           source_tag, inputs, accession_no, form, filed, source_accessions, ingested_at
    FROM gold.derived_financials
),
labeled AS (
    SELECT f.*,
           gold.fiscal_year_end(f.period_end, c.fiscal_year_end) AS fiscal_year_end,
           gold.calendar_quarter(f.period_end) AS calendar_start
    FROM facts f
    JOIN silver.companies c ON c.company_id = f.company_id
)
SELECT company_id, span, metric, period_start, period_end,
       -- a fiscal year ending in the first week of January is named for the year before
       extract(year FROM fiscal_year_end - 7)::int AS fiscal_year,
       CASE WHEN fiscal_year_end IS NOT NULL  -- greatest/least ignore NULLs
            THEN greatest(1, least(4, 4 - round((fiscal_year_end - period_end) / 91.31)::int))
       END AS fiscal_quarter,
       extract(year FROM calendar_start)::int AS calendar_year,
       extract(quarter FROM calendar_start)::int AS calendar_quarter,
       row_number() OVER (
           PARTITION BY company_id, span, metric, calendar_start
           ORDER BY CASE WHEN period_start IS NOT NULL
                         THEN least(period_end, (calendar_start + interval '3 months')::date - 1)
                              - greatest(period_start, calendar_start) END DESC NULLS LAST,
                    period_end DESC
       ) = 1 AS calendar_primary,
       value, unit, value_source, source_tag, inputs, accession_no, form, filed,
       source_accessions, ingested_at
FROM labeled;

GRANT SELECT ON gold.derived_financials, gold.derived_metrics, gold.financials TO query_api;

ALTER TABLE gold.derived_financials ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_financials ON gold.derived_financials FOR SELECT TO query_api
    USING (EXISTS (
        SELECT 1 FROM authz.user_access a
        WHERE a.user_id = current_setting('app.user_id', true)
          AND a.company_id = derived_financials.company_id
          AND a.tier = 'financials'
    ));

-- Data quality (FR11) -------------------------------------------------------------------------

-- One row per check run (a batch of companies after its gold refresh).
CREATE TABLE ops.quality_runs (
    run_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    checked_at timestamptz NOT NULL DEFAULT now(),
    companies  integer     NOT NULL,
    findings   integer     NOT NULL DEFAULT 0,
    by_check   jsonb       NOT NULL DEFAULT '{}'  -- check → findings
);

-- Each company's latest check.
CREATE TABLE ops.quality_companies (
    company_id text        PRIMARY KEY REFERENCES silver.companies ON DELETE CASCADE,
    run_id     bigint      NOT NULL REFERENCES ops.quality_runs,
    checked_at timestamptz NOT NULL,
    errors     integer     NOT NULL,
    warnings   integer     NOT NULL
);

-- Findings of each company's latest check. detail holds dates, filings and relative sizes,
-- never financial values.
CREATE TABLE ops.quality_findings (
    company_id text   NOT NULL REFERENCES silver.companies ON DELETE CASCADE,
    check_name text   NOT NULL,
    metric     text   NOT NULL,
    period_end date   NOT NULL,
    severity   text   NOT NULL CHECK (severity IN ('error', 'warning')),
    detail     jsonb  NOT NULL DEFAULT '{}',
    run_id     bigint NOT NULL REFERENCES ops.quality_runs,
    PRIMARY KEY (company_id, check_name, metric, period_end)
);

-- Check these companies' gold rows; replaces their findings. Checks (severity):
--   impossible_value (error): negative revenue, assets, liabilities, cash, inventory,
--     receivables or share counts; current assets > total assets, current liabilities >
--     total liabilities or gross profit > revenue at the same period end
--   overlapping_quarters (error): a quarter starting before the previous one's last day
--   calendar_collision (warning): two quarters of a metric in one calendar quarter
--   annual_sum (warning): four quarters forming a fiscal year differ from the year's reported
--     figure by more than 1%
--   series_gap (warning): an additive metric's quarter missing after four contiguous ones
--     (non-additive ones never have a derived Q4)
--   sign_flip (warning): an expense or outflow whose sign differs from both neighbours'
--   outlier (warning): a one-period spike, typically a scale error: at least 10 times the
--     magnitude of both neighbouring values, which agree in sign and within a factor of 2
-- Returns the number of findings.
CREATE FUNCTION ops.check_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    run bigint;
    n integer;
BEGIN
    INSERT INTO ops.quality_runs (companies)
    SELECT count(DISTINCT id) FROM unnest(p_company_ids) id
    RETURNING run_id INTO run;

    DELETE FROM ops.quality_findings WHERE company_id = ANY(p_company_ids);

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

-- Rebuild these companies' gold quarters, TTM and derived metrics, then check them.
-- Returns the number of quarterly rows.
CREATE FUNCTION gold.refresh_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    n := gold.refresh_quarterly_financials(p_company_ids);
    PERFORM gold.refresh_derived_financials(p_company_ids);
    PERFORM ops.check_financials(p_company_ids);
    RETURN n;
END
$$;

-- Findings per check across all checked companies, for Grafana and the ops exporter.
CREATE VIEW ops.quality_summary AS
SELECT ch.check_name, ch.severity,
       count(f.company_id) AS findings,
       count(DISTINCT f.company_id) AS companies,
       (SELECT count(*) FROM ops.quality_companies) AS companies_checked,
       (SELECT max(checked_at) FROM ops.quality_runs) AS last_checked_at
FROM (VALUES ('impossible_value', 'error'), ('overlapping_quarters', 'error'),
             ('calendar_collision', 'warning'), ('annual_sum', 'warning'),
             ('series_gap', 'warning'), ('sign_flip', 'warning'), ('outlier', 'warning'))
     ch(check_name, severity)
LEFT JOIN ops.quality_findings f ON f.check_name = ch.check_name
GROUP BY ch.check_name, ch.severity;

-- Each finding with its company, for a Grafana table.
CREATE VIEW ops.quality_issues AS
SELECT f.company_id, c.name AS company, f.check_name, f.severity, f.metric, f.period_end,
       f.detail, q.checked_at
FROM ops.quality_findings f
JOIN silver.companies c ON c.company_id = f.company_id
JOIN ops.quality_companies q ON q.company_id = f.company_id;

GRANT SELECT ON ops.quality_summary, ops.quality_issues TO ops_reader;
