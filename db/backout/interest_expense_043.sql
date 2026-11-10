-- Undo migration 043 (interest expense's sign): gold.usable_facts and gold.refresh_quarterly_financials go
-- back to 039's bodies, copied unchanged. Not a migration (db/backout/README.md). Run by hand on hermes only if
-- the interest expense figures after the rebuild are wrong, then run the rebuild job again:
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f interest_expense_043.sql
--   kubectl -n tributary create job --from=cronjob/financials-rebuild financials-rebuild-2
--
-- Generated from 039 with 043 (scratch script, the same copy without 043's two edits). 039's
-- fallback-debt total rule was then fixed in 039, 043 and here alike (before either was deployed): a
-- fallback tag below the current portion is the noncurrent line, never a total.
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as postgres,
-- a function it drops and re-creates would belong to postgres, and the non-superuser migrate login
-- could never replace it again. The guard below refuses any login that doesn't own the functions,
-- and any database where a newer backout hasn't run yet (README.md, Order).

SET LOCAL lock_timeout = '30s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: revenue_part_064.sql,
-- rebuild_speed_063.sql, currency_speed_061.sql, annual_currency_060.sql, banks_058.sql,
-- currency_readings_057.sql, ifrs_currencies_056.sql, filing_xbrl_facts_055.sql,
-- filers_debt_050.sql, kpis_049.sql, bank_metrics_048.sql) and this one hasn't, and unless the
-- login owns the functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('gold.amount_currency(text)', NULL),
        ('gold.amount_currency_of(text)', NULL),
        ('gold.annual_per_share(text,integer)', '7c5af423f8d31e10da7db51ee87018b3'),
        ('gold.best_facts(text[])', '62873ad55ef4c2f947442d18f9604f27'),
        ('gold.companies_for_ciks(text[])', '71eb935f94130a3bfba07359561a5c8f'),
        ('gold.figure_facts(text,text,date)', '44aa5118a18f693837d60aaa72b243d4'),
        ('gold.filing_figures(text,text)', '29ad18c630f986e62b947fa423f2566a'),
        ('gold.latest_reading(text,text,text,text,date)', '2b1ce5dae3700a2524e6eed6466f2903'),
        ('gold.not_applicable_metrics(text,text)', '643329a0af9f702ea97b413431b4aa8d'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', 'f314a4ec94a8dcb54f6644bc0933a6e4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', NULL),
        ('gold.refresh_derived_financials(text[])', '67d01d71c8798a7bcffb691d9f26bf67'),
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
                'changed.', w.fn, 'revenue_part_064.sql, rebuild_speed_063.sql, currency_speed_061.sql, annual_currency_060.sql, banks_058.sql, currency_readings_057.sql, ifrs_currencies_056.sql, filing_xbrl_facts_055.sql, filers_debt_050.sql, kpis_049.sql, bank_metrics_048.sql');
        END IF;
    END LOOP;
END
$guard$;
-- end guard

-- Order (README): this file, then gold_rules_038_041.sql if that is wanted too; never this one after
-- it. gold_rules_038_041.sql empties silver.metric_sums and removes 038's tags, and these bodies are
-- 039's fact selection, which reads them: run after it, they'd bring 039 back with its sums gone.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM silver.metric_sums) THEN
        RAISE EXCEPTION USING MESSAGE = 'gold_rules_038_041.sql has already run (silver.metric_sums '
            || 'is empty): interest_expense_043.sql goes before it, never after. Nothing was changed.';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION gold.usable_facts(p_company_ids text[])
RETURNS TABLE (company_id text, metric text, tag text, taxonomy text, unit text, period_start date,
               period_end date, period_type text, value numeric, accession_no text, form text,
               filed date, ingested_at timestamptz, cik text, priority numeric, additive boolean)
LANGUAGE sql STABLE AS $$
    WITH tagged AS (
        -- by tag, not by silver's metric column: its identity index has no metric, so a tag two metrics
        -- map (EarningsPerShareBasicAndDiluted) is stored once, under one of them
        SELECT k.company_id, t.metric, f.tag, t.taxonomy, f.unit, f.period_start, f.period_end,
               f.period_type, f.value, f.accession_no, f.form, f.filed, f.ingested_at, f.cik,
               t.priority::numeric AS priority, m.additive, t.gold_line
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.tag = f.tag
        JOIN silver.metrics m ON m.metric = t.metric AND m.unit = f.unit
        WHERE k.company_id = ANY(p_company_ids)
          -- proxy and information statements (DEF 14A, DEFA14A, DEFR14A, DEFM14A,
          -- DEF 14C, PRE 14A, ...): pay-versus-performance net income, rounded
          AND coalesce(f.form, '') !~ '^(DEF|PRE)'
          AND f.period_end <= f.filed + 7
    ),
    summed AS (
        SELECT x.company_id, s.metric, array_to_string(s.tags, '+') AS tag, x.taxonomy, x.unit,
               x.period_start, x.period_end, x.period_type, sum(x.value) AS value, x.accession_no,
               x.form, x.filed, max(x.ingested_at) AS ingested_at, min(x.cik) AS cik, s.priority,
               x.additive, true AS gold_line
        FROM silver.metric_sums s
        JOIN tagged x ON x.metric = s.metric AND x.tag = ANY(s.tags)
        GROUP BY x.company_id, s.metric, s.tags, x.taxonomy, x.unit, x.period_start, x.period_end,
                 x.period_type, x.accession_no, x.form, x.filed, s.priority, x.additive
        -- every part present, and none a rounding of the other: State Street's 2019 10-Qs tag
        -- NoninterestIncome as -1 and 0 (it's a securities gain), beside 613 of interest income
        HAVING count(DISTINCT x.tag) = cardinality(s.tags)
           AND min(abs(x.value)) >= 0.01 * max(abs(x.value))
    ),
    loss_eps AS (  -- a loss quarter's diluted EPS is its basic EPS; ranks after the two diluted tags
        SELECT x.company_id, 'eps_diluted' AS metric, x.tag, x.taxonomy, x.unit, x.period_start,
               x.period_end, x.period_type, x.value, x.accession_no, x.form, x.filed,
               x.ingested_at, x.cik, 3::numeric AS priority, x.additive, true AS gold_line
        FROM tagged x
        WHERE x.metric = 'eps_basic' AND x.tag = 'EarningsPerShareBasic' AND x.value < 0
    ),
    lines AS (
        SELECT * FROM tagged WHERE gold_line
        UNION ALL SELECT * FROM summed
        UNION ALL SELECT * FROM loss_eps
    ),
    balance AS (  -- what the debt tests read at each date: each tag's latest filing
        SELECT y.company_id, y.period_end,
               max(y.value) FILTER (WHERE y.tag = 'Liabilities') AS liabilities,
               max(y.value) FILTER (WHERE y.tag = 'LiabilitiesCurrent') AS liabilities_current,
               max(y.value) FILTER (WHERE y.tag = 'Assets') AS assets,
               max(y.value) FILTER (WHERE y.metric = 'long_term_debt_current'
                                      AND y.rnk = 1) AS current_debt
        FROM (
            SELECT DISTINCT ON (x.company_id, x.tag, x.period_end) x.*,
                   dense_rank() OVER (PARTITION BY x.company_id, x.metric, x.period_end
                                      ORDER BY x.priority) AS rnk
            FROM tagged x
            WHERE x.period_start IS NULL
              AND (x.tag IN ('Liabilities', 'LiabilitiesCurrent', 'Assets')
                   OR x.metric = 'long_term_debt_current')
            ORDER BY x.company_id, x.tag, x.period_end, x.filed DESC, x.accession_no DESC
        ) y
        GROUP BY y.company_id, y.period_end
    )
    SELECT l.company_id, l.metric,
           CASE WHEN c.total THEN l.tag || '-' || 'CurrentPortion' ELSE l.tag END, l.taxonomy, l.unit,
           l.period_start, l.period_end, l.period_type,
           CASE WHEN c.total THEN l.value - b.current_debt ELSE l.value END,
           l.accession_no, l.form, l.filed, l.ingested_at, l.cik, l.priority, l.additive
    FROM lines l
    LEFT JOIN balance b ON (l.metric IN ('long_term_debt', 'long_term_debt_current') OR l.tag = 'Cash')
                       AND b.company_id = l.company_id AND b.period_end = l.period_end
    LEFT JOIN silver.companies co ON l.tag IN ('LongTermDebt', 'LongTermNotesPayable',
                                               'LongTermNotesAndLoans')
                                 AND co.company_id = l.company_id
    CROSS JOIN LATERAL (
        SELECT l.tag IN ('LongTermDebt', 'LongTermNotesPayable', 'LongTermNotesAndLoans')
               AND b.current_debt > 0
               -- a total holds its current portion: equal to it (all of the debt is current), or
               -- above it and above all noncurrent liabilities; below it, it's the noncurrent line
               AND (l.value = b.current_debt
                    OR (l.value > b.current_debt
                        AND l.value > (b.liabilities - b.liabilities_current) * 1.01)) AS total
    ) c
    WHERE NOT (l.metric IN ('long_term_debt', 'long_term_debt_current')
               AND l.value > coalesce(b.liabilities * 5, 'Infinity')
               AND l.value > coalesce(b.assets * 5, 'Infinity'))
      -- Cash only on a balance sheet's date: elsewhere it's a note (cash paid in a deal, Boston
      -- Scientific on 2023-02-20)
      AND NOT (l.tag = 'Cash' AND b.assets IS NULL)
      -- a negative "investment" under the tags 038 added is a mis-signed note (HCA's 2012 10-Q)
      AND NOT (l.metric = 'short_term_investments' AND l.priority > 4 AND l.value < 0)
      AND NOT (l.tag IN ('LongTermDebt', 'LongTermNotesPayable', 'LongTermNotesAndLoans')
               AND (co.sic IS NULL OR 'net_debt' = ANY(gold.not_applicable_metrics(l.company_id, co.sic))))
$$;

CREATE OR REPLACE FUNCTION gold.refresh_quarterly_financials(p_company_ids text[])
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('gold.quarterly_financials'), hashtext(id))
    FROM (SELECT DISTINCT id FROM unnest(p_company_ids) id ORDER BY id) ids;

    DELETE FROM gold.quarterly_financials WHERE company_id = ANY(p_company_ids);

    WITH RECURSIVE best AS MATERIALIZED (
        SELECT * FROM gold.best_facts(p_company_ids)
    ),
    year_to_date AS (
        SELECT b.*,
               lag(value)        OVER w AS prev_value,
               lag(period_end)   OVER w AS prev_end,
               lag(accession_no) OVER w AS prev_accession,
               lag(ingested_at)  OVER w AS prev_ingested_at,
               lag(priority)     OVER w AS prev_priority
        FROM best b
        WHERE period_type <> 'instant'
        WINDOW w AS (PARTITION BY company_id, metric, period_start ORDER BY period_end)
    ),
    reported AS (
        SELECT company_id, metric, period_start, period_end, value, unit,
               'reported' AS value_source, taxonomy || ':' || tag AS source_tag, accession_no,
               form, filed, ARRAY[accession_no] AS source_accessions, ingested_at, priority
        FROM best
        -- instants are balance-sheet values; a flow metric tagged at an instant (UnitedHealth
        -- tags each dividend payment on its payment date) is not a quarter
        WHERE period_type = 'quarter' OR (period_type = 'instant' AND NOT additive)
    ),
    derived AS (
        SELECT company_id, metric, prev_end + 1 AS period_start, period_end, value - prev_value AS value,
               unit, 'derived' AS value_source, taxonomy || ':' || tag AS source_tag, accession_no,
               form, filed, ARRAY[prev_accession, accession_no] AS source_accessions,
               greatest(ingested_at, prev_ingested_at) AS ingested_at,
               greatest(priority, prev_priority) AS priority
        FROM year_to_date
        WHERE additive AND period_end - prev_end BETWEEN 80 AND 120
          -- a derived quarter of a metric that can't be negative came out negative: one of its
          -- two inputs is wrong (Cytek's 2024 10-K re-tagged 2022 Revenues as 2,963,000 against
          -- 164,036,000 everywhere else). Leave the quarter missing rather than publish it.
          AND NOT (value - prev_value < 0 AND metric IN (
              'revenue', 'cost_of_revenue', 'research_and_development', 'sga', 'capex',
              'depreciation_amortization', 'stock_based_compensation', 'share_buybacks',
              'dividends_paid'))
    ),
    both_kinds AS (
        SELECT * FROM reported UNION ALL SELECT * FROM derived
    ),
    compared AS (
        SELECT b.*, max(b.value) FILTER (WHERE b.value_source = 'derived') OVER (
                   PARTITION BY b.company_id, b.metric, b.period_end, b.accession_no, b.source_tag
               ) AS difference
        FROM both_kinds b
    ),
    candidates AS (
        SELECT company_id, metric, period_start, period_end, value, unit, value_source, source_tag,
               accession_no, form, filed, source_accessions, ingested_at, priority
        FROM compared c
        -- a figure rounded for a sentence ("we paid $6.0 billion"), tagged in the same filing as an
        -- exact year to date: the difference is the exact quarter (NVIDIA's Q2 FY2027 dividends,
        -- 6,290 - 243 = 6,047, not 6,000; Cisco's "$1.5 billion" against 1,486; American Eagle's
        -- "$9.3 million" of stock compensation against 9.257). The reported figure has at most two
        -- significant digits, the difference at least three that aren't zero, and they're 0.2-2%
        -- apart. Tried on the sample and narrowed: three digits also took IBM's 5,260 of SG&A, which a
        -- filer in millions can state exactly while an earlier quarter was restated; and a difference
        -- like Lowe's 449 against 450 is that filer's own rounding of each year-to-date figure.
        WHERE NOT (c.value_source = 'reported' AND c.difference IS NOT NULL AND c.value <> 0
                   AND abs(c.value - c.difference) BETWEEN 0.002 * abs(c.difference)
                                                       AND 0.02 * abs(c.difference)
                   AND mod(c.value, power(10::numeric, floor(log(nullif(abs(c.value), 0))) - 1)) = 0
                   AND mod(c.difference, power(10::numeric, floor(log(nullif(abs(c.value), 0))) - 2)) <> 0)
    ),
    -- calendar votes per company and date: metrics with a balance-sheet value on it, and
    -- metrics with any fact ending on it
    votes AS (
        SELECT company_id, period_end AS day,
               count(DISTINCT metric) FILTER (WHERE period_type = 'instant') AS instants,
               count(DISTINCT metric) AS metrics
        FROM best
        GROUP BY company_id, period_end
    ),
    scored AS (  -- a quarter's votes: its last day's plus the day before its first day's
        SELECT c.*,
               coalesce(e.instants, 0) + coalesce(s.instants, 0) AS calendar_instants,
               coalesce(e.metrics, 0) + coalesce(s.metrics, 0) AS calendar_metrics
        FROM candidates c
        LEFT JOIN votes e ON e.company_id = c.company_id AND e.day = c.period_end
        LEFT JOIN votes s ON s.company_id = c.company_id AND s.day = c.period_start - 1
    ),
    per_end AS (  -- one row per period end
        SELECT DISTINCT ON (company_id, metric, period_end) *
        FROM scored
        ORDER BY company_id, metric, period_end, calendar_instants DESC, calendar_metrics DESC,
                 priority, value_source = 'reported' DESC, filed DESC, accession_no DESC,
                 source_accessions DESC, period_start DESC, source_tag, value,
                 ingested_at DESC
    ),
    ranked AS MATERIALIZED (  -- the same order across period ends: rnk 1 is the best row
        SELECT p.*, daterange(period_start, period_end, '[]') AS span,
               row_number() OVER (
                   PARTITION BY company_id, metric
                   ORDER BY calendar_instants DESC, calendar_metrics DESC, priority,
                            value_source = 'reported' DESC, filed DESC, accession_no DESC,
                            source_accessions DESC, period_start DESC, source_tag, value,
                            ingested_at DESC, period_end DESC) AS rnk
        FROM per_end p
    ),
    clashing AS (  -- duration rows overlapping another of their metric by more than 7 days
        SELECT DISTINCT a.company_id, a.metric, a.rnk, a.span
        FROM ranked a
        JOIN ranked b ON b.company_id = a.company_id AND b.metric = a.metric AND b.rnk <> a.rnk
        WHERE a.period_start IS NOT NULL AND b.period_start IS NOT NULL
          AND upper(a.span * b.span) - lower(a.span * b.span) > 7
    ),
    queue AS MATERIALIZED (
        SELECT c.*, row_number() OVER (PARTITION BY company_id, metric ORDER BY rnk) AS i
        FROM clashing c
    ),
    greedy AS (  -- best first: keep a row unless it overlaps one already kept by > 7 days
        SELECT company_id, metric, i, ARRAY[span] AS kept, ARRAY[rnk] AS kept_ranks
        FROM queue
        WHERE i = 1
        UNION ALL
        SELECT q.company_id, q.metric, q.i,
               CASE WHEN x.clash THEN g.kept ELSE g.kept || q.span END,
               CASE WHEN x.clash THEN g.kept_ranks ELSE g.kept_ranks || q.rnk END
        FROM greedy g
        JOIN queue q ON q.company_id = g.company_id AND q.metric = g.metric AND q.i = g.i + 1
        CROSS JOIN LATERAL (
            SELECT EXISTS (SELECT 1 FROM unnest(g.kept) k
                           WHERE upper(k * q.span) - lower(k * q.span) > 7) AS clash
        ) x
    ),
    dropped AS (
        SELECT q.company_id, q.metric, q.rnk
        FROM queue q
        JOIN (SELECT DISTINCT ON (company_id, metric) company_id, metric, kept_ranks
              FROM greedy
              ORDER BY company_id, metric, i DESC) g
          ON g.company_id = q.company_id AND g.metric = q.metric
        WHERE q.rnk <> ALL (g.kept_ranks)
    )
    INSERT INTO gold.quarterly_financials
        (company_id, metric, period_start, period_end, value, unit, value_source, source_tag,
         accession_no, form, filed, source_accessions, ingested_at)
    SELECT r.company_id, r.metric, r.period_start, r.period_end, r.value, r.unit,
           r.value_source, r.source_tag, r.accession_no, r.form, r.filed, r.source_accessions,
           r.ingested_at
    FROM ranked r
    LEFT JOIN dropped d
      ON d.company_id = r.company_id AND d.metric = r.metric AND d.rnk = r.rnk
    WHERE d.rnk IS NULL
    ORDER BY r.company_id, r.metric, r.period_end;

    GET DIAGNOSTICS n = ROW_COUNT;

    -- revenue less cost, where the filing prints no gross profit (header)
    WITH pairs AS (
        SELECT r.company_id, r.period_start, r.period_end, r.value AS revenue, c.value AS cost,
               o.value AS operating_income, s.value AS sga, d.value AS rnd, r.unit,
               (r.value - c.value) / r.value AS margin,
               CASE WHEN c.filed > r.filed THEN c.accession_no ELSE r.accession_no END AS accession_no,
               CASE WHEN c.filed > r.filed THEN c.form ELSE r.form END AS form,
               greatest(c.filed, r.filed) AS filed,
               ARRAY(SELECT DISTINCT a FROM unnest(r.source_accessions || c.source_accessions) a
                     ORDER BY 1) AS source_accessions,
               greatest(c.ingested_at, r.ingested_at) AS ingested_at
        FROM gold.quarterly_financials r
        JOIN silver.companies co ON co.company_id = r.company_id
        JOIN gold.quarterly_financials c
          ON c.company_id = r.company_id AND c.metric = 'cost_of_revenue'
         AND c.period_end = r.period_end AND abs(c.period_start - r.period_start) <= 7
        LEFT JOIN gold.quarterly_financials o
          ON o.company_id = r.company_id AND o.metric = 'operating_income' AND o.period_end = r.period_end
        LEFT JOIN gold.quarterly_financials s
          ON s.company_id = r.company_id AND s.metric = 'sga' AND s.period_end = r.period_end
        LEFT JOIN gold.quarterly_financials d
          ON d.company_id = r.company_id AND d.metric = 'research_and_development'
         AND d.period_end = r.period_end
        WHERE r.company_id = ANY(p_company_ids) AND r.metric = 'revenue' AND r.value > 0
          AND r.period_end >= DATE '2019-01-01'
          AND co.sic IS NOT NULL
          AND co.sic !~ '^(6[0-7]|13|48|58)' AND co.sic !~ '^(4911|493.|2911|5331|5912)$'
          AND c.value >= 0.15 * r.value
          AND (o.value IS NULL OR r.value - c.value >= o.value)
          AND NOT (o.value IS NOT NULL AND s.value IS NOT NULL
                   AND r.value - c.value - o.value - s.value - coalesce(d.value, 0) > 0.25 * r.value)
          AND NOT EXISTS (
              SELECT 1 FROM gold.quarterly_financials g
              WHERE g.company_id = r.company_id AND g.metric = 'gross_profit'
                AND g.period_start <= r.period_end - 7 AND g.period_end >= r.period_start + 7)
    ),
    usual AS (
        SELECT company_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY margin)::numeric AS margin
        FROM pairs GROUP BY company_id
    )
    INSERT INTO gold.quarterly_financials
        (company_id, metric, period_start, period_end, value, unit, value_source, source_tag,
         accession_no, form, filed, source_accessions, ingested_at)
    SELECT p.company_id, 'gross_profit', p.period_start, p.period_end, p.revenue - p.cost, p.unit,
           'derived', 'derived:revenue-cost_of_revenue', p.accession_no, p.form, p.filed,
           p.source_accessions, p.ingested_at
    FROM pairs p
    JOIN usual u USING (company_id)
    WHERE abs(p.margin - u.margin) <= 0.08
    ORDER BY p.company_id, p.period_end;

    RETURN n;
END
$$;
