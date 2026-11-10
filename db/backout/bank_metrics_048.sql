-- Undo migration 048 (a bank's own metrics): gold.refresh_derived_financials goes back to 040's
-- body, copied unchanged, and 048's metrics, tags and rows go. Not a migration
-- (db/backout/README.md). Run by hand on hermes only if the bank figures after the rebuild are wrong,
-- then run the rebuild job again (it re-stores, under revenue, the facts of the two tags 048 shares
-- with 038's bank revenue sum, InterestIncomeExpenseNet and NoninterestIncome, if silver had kept
-- them under the new metrics):
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f bank_metrics_048.sql
--   kubectl -n tributary create job --from=cronjob/financials-rebuild financials-rebuild-2
--
-- Independent of the other files here (048 touches no function they restore). Generated from 040 by a
-- script (the function's text exactly).
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as postgres,
-- a function it drops and re-creates would belong to postgres, and the non-superuser migrate login
-- could never replace it again. The guard below refuses any login that doesn't own the functions,
-- and any database where a newer backout hasn't run yet (README.md, Order).

SET LOCAL lock_timeout = '30s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: rebuild_speed_063.sql,
-- currency_speed_061.sql, annual_currency_060.sql, banks_058.sql, currency_readings_057.sql,
-- ifrs_currencies_056.sql, filing_xbrl_facts_055.sql, filers_debt_050.sql, kpis_049.sql) and this
-- one hasn't, and unless the login owns the functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('gold.amount_currency(text)', NULL),
        ('gold.amount_currency_of(text)', NULL),
        ('gold.annual_per_share(text,integer)', '7c5af423f8d31e10da7db51ee87018b3'),
        ('gold.companies_for_ciks(text[])', '71eb935f94130a3bfba07359561a5c8f'),
        ('gold.figure_facts(text,text,date)', '44aa5118a18f693837d60aaa72b243d4'),
        ('gold.filing_figures(text,text)', '29ad18c630f986e62b947fa423f2566a'),
        ('gold.latest_reading(text,text,text,text,date)', '2b1ce5dae3700a2524e6eed6466f2903'),
        ('gold.not_applicable_metrics(text,text)', '643329a0af9f702ea97b413431b4aa8d'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', 'f314a4ec94a8dcb54f6644bc0933a6e4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', NULL),
        ('gold.refresh_derived_financials(text[])', '18d51d59ce10407632f72445c5509d7b'),
        ('gold.refresh_quarterly_financials(text[])', '84ce53d8fc3987c84012b4360b69fed4'),
        ('gold.usable_facts(text[])', 'c3fec536a845fce4b98f1239b85760c7'),
        ('ops.check_financials(text[])', '40644268e3fce350dde68de157bacd60'),
        ('silver.refresh_filing_facts(text[])', '2f5e5eeefb1edf917bfb879e7071190d'),
        ('silver.refresh_financial_facts(text[])', 'b407aac7dbda2938ba7ebed789e3f2e3')
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
                'changed.', w.fn, 'rebuild_speed_063.sql, currency_speed_061.sql, annual_currency_060.sql, banks_058.sql, currency_readings_057.sql, ifrs_currencies_056.sql, filing_xbrl_facts_055.sql, filers_debt_050.sql, kpis_049.sql');
        END IF;
    END LOOP;
END
$guard$;
-- end guard

DELETE FROM gold.derived_financials
WHERE metric IN ('efficiency_ratio', 'net_interest_income', 'noninterest_income', 'noninterest_expense',
                 'provision_for_credit_losses', 'loans', 'deposits', 'cet1_ratio');
DELETE FROM gold.quarterly_financials
WHERE metric IN ('net_interest_income', 'noninterest_income', 'noninterest_expense',
                 'provision_for_credit_losses', 'loans', 'deposits', 'cet1_ratio');
DELETE FROM silver.financial_facts
WHERE metric IN ('net_interest_income', 'noninterest_income', 'noninterest_expense',
                 'provision_for_credit_losses', 'loans', 'deposits', 'cet1_ratio');
DELETE FROM silver.metric_tags
WHERE metric IN ('net_interest_income', 'noninterest_income', 'noninterest_expense',
                 'provision_for_credit_losses', 'loans', 'deposits', 'cet1_ratio');
DELETE FROM silver.metrics
WHERE metric IN ('net_interest_income', 'noninterest_income', 'noninterest_expense',
                 'provision_for_credit_losses', 'loans', 'deposits', 'cet1_ratio');
DELETE FROM gold.derived_metrics WHERE metric = 'efficiency_ratio';

CREATE OR REPLACE FUNCTION gold.refresh_derived_financials(p_company_ids text[]) RETURNS integer
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

    -- a year the 10-K states, where four quarters don't exist to sum (header). Not the quarters'
    -- gross_profit: a filing that printed one has it here, and revenue less cost is a quarter's rule.
    -- Ends within 7 days are one period (031's rule): ATI's FY2021 is tagged ending 2021-12-31 in one
    -- 10-K and 2022-01-02 in a later one, and was in gold twice.
    WITH years AS (
        SELECT y.*,
               EXISTS (SELECT 1 FROM gold.quarterly_financials q
                       WHERE q.company_id = y.company_id AND q.metric = y.metric
                         AND q.period_end = y.period_end) AS in_series
        FROM (
            -- one per period end: two annual facts can end on one day (a re-labelled start)
            SELECT DISTINCT ON (f.company_id, f.metric, f.period_end) f.*
            FROM gold.best_facts(p_company_ids) f
            JOIN silver.metrics m ON m.metric = f.metric AND m.kind = 'duration'
            WHERE f.period_type = 'annual' AND f.additive
            ORDER BY f.company_id, f.metric, f.period_end, f.priority, f.filed DESC,
                     f.accession_no DESC, f.period_start DESC
        ) y
        -- a year that can't hold the quarters gold has for it is another tag's part, not the year
        -- (Clover Health's 2022 revenue at 1,097 beside quarters of about 800 each; Peoples
        -- Bancorp's 2018 at -127): only for the metrics that can't be negative (029's list)
        WHERE NOT (y.metric IN ('revenue', 'cost_of_revenue', 'research_and_development', 'sga',
                                'capex', 'depreciation_amortization', 'stock_based_compensation',
                                'share_buybacks', 'dividends_paid')
                   AND (y.value < 0 OR y.value < 0.99 * (
                       SELECT coalesce(sum(q.value), 0) FROM gold.quarterly_financials q
                       WHERE q.company_id = y.company_id AND q.metric = y.metric
                         AND q.period_start >= y.period_start - 7
                         AND q.period_end <= y.period_end + 7)))
    )
    INSERT INTO gold.derived_financials
        (company_id, span, metric, period_start, period_end, value, unit, value_source,
         source_tag, inputs, accession_no, form, filed, source_accessions, ingested_at)
    SELECT b.company_id, 'ttm', b.metric, b.period_start, b.period_end, b.value, b.unit, 'ttm',
           b.taxonomy || ':' || b.tag,
           jsonb_build_object('quarters', jsonb_build_array(jsonb_build_object(
               'period_start', b.period_start, 'period_end', b.period_end, 'value', b.value,
               'accession_no', b.accession_no))),
           b.accession_no, b.form, b.filed, ARRAY[b.accession_no], b.ingested_at
    FROM years b
    WHERE NOT EXISTS (
        SELECT 1 FROM gold.derived_financials d
        WHERE d.company_id = b.company_id AND d.span = 'ttm' AND d.metric = b.metric
          AND d.period_end BETWEEN b.period_end - 7 AND b.period_end + 7)
      -- of two stated years ending within 7 days, one: the end gold's quarters use, then the
      -- later filing, then the later end
      AND NOT EXISTS (
        SELECT 1 FROM years o
        WHERE o.company_id = b.company_id AND o.metric = b.metric
          AND o.period_end <> b.period_end
          AND o.period_end BETWEEN b.period_end - 7 AND b.period_end + 7
          AND (o.in_series, o.filed, o.period_end) > (b.in_series, b.filed, b.period_end));

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
                         'stockholders_equity', 'total_assets', 'receivables', 'inventory')
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
               max(value) FILTER (WHERE metric = 'total_assets') AS total_assets,
               max(value) FILTER (WHERE metric = 'receivables') AS receivables,
               max(value) FILTER (WHERE metric = 'inventory') AS inventory,
               max(ya_value) FILTER (WHERE metric = 'revenue') AS revenue_year_ago,
               max(ya_value) FILTER (WHERE metric = 'net_income') AS net_income_year_ago,
               max(ya_value) FILTER (WHERE metric = 'eps_diluted') AS eps_diluted_year_ago,
               max(ya_value) FILTER (WHERE metric = 'stockholders_equity')
                   AS stockholders_equity_year_ago,
               max(ya_value) FILTER (WHERE metric = 'long_term_debt') AS long_term_debt_year_ago,
               jsonb_object_agg(metric, jsonb_build_object(
                   'period_start', period_start, 'accession_no', accession_no, 'form', form,
                   'filed', filed, 'accessions', source_accessions,
                   'ingested_at', ingested_at, 'year_ago_accessions', ya_accessions)) AS prov
        FROM year_ago
        GROUP BY span, company_id, period_end
    ),
    debt AS (  -- the net debt rule's reading of each balance sheet (header)
        SELECT p.*,
               coalesce(p.short_term_investments, 0) AS investments_now,
               p.current_assets - p.cash - coalesce(p.receivables, 0) - coalesce(p.inventory, 0)
                   AS room,
               x.value AS last_investments,
               x.period_end AS last_investments_end,
               t.value AS interest
        FROM periods p
        LEFT JOIN LATERAL (
            SELECT q.value, q.period_end
            FROM gold.quarterly_financials q
            WHERE p.short_term_investments IS NULL AND p.long_term_debt IS NOT NULL
              AND q.company_id = p.company_id AND q.metric = 'short_term_investments'
              AND q.period_end < p.period_end AND q.period_end >= p.period_end - 730 AND q.value > 0
            ORDER BY q.period_end DESC LIMIT 1
        ) x ON true
        LEFT JOIN LATERAL (
            SELECT d.value
            FROM gold.derived_financials d
            WHERE p.long_term_debt IS NOT NULL
              AND d.company_id = p.company_id AND d.span = 'ttm' AND d.metric = 'interest_expense'
              AND d.period_end = p.period_end
        ) t ON true
    ),
    derived AS (
        SELECT p.span, p.company_id, p.period_end, p.prov, d.metric,
               CASE WHEN dm.unit = 'ratio' THEN round(d.value, 6) ELSE d.value END AS value,
               dm.unit, jsonb_strip_nulls(d.inputs) AS inputs
        FROM debt p
        CROSS JOIN LATERAL (
            SELECT p.long_term_debt + coalesce(p.long_term_debt_current, 0) AS total,
                   p.long_term_debt + coalesce(p.long_term_debt_current, 0) > 0
                   AND p.interest > 2 * (p.long_term_debt + coalesce(p.long_term_debt_current, 0))
                   AND coalesce(p.long_term_debt_year_ago <= 2 * (p.long_term_debt
                                + coalesce(p.long_term_debt_current, 0)), true) AS sliver,
                   p.short_term_investments > p.room * 1.01 AS in_cash,
                   p.last_investments >= 0.01 * p.total_assets
                   AND p.last_investments >= 0.10 * abs(p.long_term_debt
                                                        + coalesce(p.long_term_debt_current, 0) - p.cash)
                   AND (p.room IS NULL OR p.last_investments <= p.room) AS unknown
        ) r
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
             CASE WHEN p.stockholders_equity > 0 AND r.sliver IS NOT TRUE
                  THEN r.total / p.stockholders_equity END,
             jsonb_build_object('long_term_debt', p.long_term_debt,
                                'long_term_debt_current', p.long_term_debt_current,
                                'stockholders_equity', p.stockholders_equity)),
            ('net_debt',
             CASE WHEN r.sliver IS NOT TRUE AND r.unknown IS NOT TRUE
                  THEN r.total - p.cash
                       - CASE WHEN r.in_cash THEN 0 ELSE p.investments_now END END,
             jsonb_build_object('long_term_debt', p.long_term_debt,
                                'long_term_debt_current', p.long_term_debt_current,
                                'cash', p.cash,
                                'short_term_investments',
                                CASE WHEN r.in_cash IS NOT TRUE THEN p.short_term_investments END)),
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
