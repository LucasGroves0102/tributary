-- Backout of 165_release_figures.sql (never a migration: README.md): gold.usable_facts back to
-- 155's body (no release figures read), gold.knowledge_facts and ops.company_backlog to 086's, and
-- the release figures (silver.release_reads, silver.release_figures, ops.release_backlog, the
-- 'releases' catch-up kind) dropped. Release-only rows then leave gold at each company's next
-- rebuild: list the companies first, then run this, then rebuild them (facts-fetcher --rebuild
-- --ciks, their CIKs; or the financials-rebuild job); the rules version stays 165 (db/CLAUDE.md: a
-- backout leaves the versions):
--
--   SELECT DISTINCT company_id FROM gold.quarterly_financials WHERE source_tag LIKE 'release:%';
--
-- Deploy the previous parser image first (parser-index writes silver.release_figures). Run it as
-- the migration owner, the migrate login `tributary` (SET ROLE as below): run as postgres, a
-- function it re-creates would belong to postgres, and the non-superuser migrate login could never
-- replace it again. The guard below refuses any login that doesn't own the functions, and any
-- database where a newer backout hasn't run yet (README.md, Order).
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f release_figures_165.sql

BEGIN;
SET LOCAL lock_timeout = '10s';

-- guard (scripts/backout_guards.py writes it; db/backout/README.md, Order). It refuses, changing
-- nothing, unless every newer backout has run (newest first: none, this is the newest) and this one
-- hasn't, and unless the login owns the functions (the migrate login; header).
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
        ('gold.amount_currency(text)', 'd59a928b3b91fb9a6da98fc15e72a536'),
        ('gold.amount_currency_of(text)', NULL),
        ('gold.amount_currency_of(text,date)', '7ef505eacda72c0de066a26d5f3303ed'),
        ('gold.annual_per_share(text,integer)', NULL),
        ('gold.annual_per_share(text,integer,date)', 'c27e50f38738222c370bb9b834620cab'),
        ('gold.best_facts(text[])', NULL),
        ('gold.best_facts(text[],date)', '214d30d74690d0aee61de7cdd130e44a'),
        ('gold.companies_for_ciks(text[])', '5f02b9f1e395d7f743635d0a399f8f6c'),
        ('gold.company_confidence(text,text,date)', '41c7e0fdee366021092db7988acda051'),
        ('gold.company_financials_at(text,date,text,text[])', '63de4900f9b4285f61d220d0141ceda7'),
        ('gold.derived_rows(text[],date,gold.quarter_row[])', 'd22751c4c57103d6fda7665e6477a179'),
        ('gold.figure_facts(text,text,date)', '6789892e97611dd6c0260a25f695d7fc'),
        ('gold.filing_figures(text,text)', '51376508ac8a1a66d7e42cc92852346f'),
        ('gold.financial_rows(text[],date)', '341a1dec1ff52a3b51a05312c7e0de16'),
        ('gold.financials_at(date)', '9bdd938e555828de0b4eed294e0dad5d'),
        ('gold.input_values(jsonb)', '06de5fab08b2d846d578a006a9efc859'),
        ('gold.knowledge_facts(text)', '77bde9b91cee42391a816961876d855c'),
        ('gold.known_changed_from(text)', '61c37386d5747694eadbb421824e579f'),
        ('gold.latest_equity(text)', NULL),
        ('gold.latest_equity(text,date)', '1890ba1959496c3ea2c74996620e23c1'),
        ('gold.latest_period(text)', NULL),
        ('gold.latest_period(text,date)', 'a666140990b5da7e3a53846f08a39bf6'),
        ('gold.latest_reading(text,text,text,text,date)', NULL),
        ('gold.latest_reading(text,text,text,text,date,date)', '57d405f2fb1fd44c33a77abaa962cd08'),
        ('gold.latest_row(text,text,text)', NULL),
        ('gold.latest_row(text,text,text,date)', '78b74abf479901f860df8e016ef725fb'),
        ('gold.not_applicable_metrics(text,text)', '31aa1cba6092282684aaf6b699539359'),
        ('gold.not_computed(text,text)', 'a1d6b45f36a10d029335de6f30893f96'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric)', '472cb5abb2f6cb3ad0cbf64734468ee4'),
        ('gold.not_meaningful(text,numeric,jsonb,numeric,text)', '5dcc5951d31edcf599c0487ea824ae07'),
        ('gold.not_meaningful_for(text,text)', '11783b17a8285b739857e203ff81704e'),
        ('gold.quarterly_recent(text,date)', NULL),
        ('gold.quarterly_recent(text,date,date)', 'b7e20593a6dc7b936c9a138a15fb0951'),
        ('gold.quarterly_rows(text[],date)', '04aed32736aa812ba018df601360fd27'),
        ('gold.reading(text,text,text,date,date,numeric,jsonb,text,text,numeric,text,boolean)', '851d2fceb05393856db24cbda0691ac3'),
        ('gold.rebuild_known(text[])', '7c0e1472047486fb812194cfe5b5919b'),
        ('gold.refresh_derived_financials(text[])', '6e9debd1c4fec0936d3498b7a9e4c147'),
        ('gold.refresh_financials(text[])', 'b3f53c9f634d1d1383ec72d363982a61'),
        ('gold.refresh_known(text[])', '4e601c1d2eab3b6f283829ea2c4774fc'),
        ('gold.refresh_quarterly_financials(text[])', '4cf929c3db5e1b7830220b9b3574bfe2'),
        ('gold.replay_companies(text[],boolean)', 'a88eba6b35d5504daf597ad643fe35a0'),
        ('gold.replay_known(text,date)', '39c0339c527f6718a084b2bc4c656f29'),
        ('gold.restatements(text,date)', '7097e219996f52c772e678e98917ef29'),
        ('gold.screen_backlog()', '972441f449f03e3d8fa36a9748610e85'),
        ('gold.screen_company(text)', 'd0ab770fb304683145115aeb8dcee30d'),
        ('gold.screen_reading(text,text,text,text,date,boolean,numeric,text,boolean)', 'f110e0894f0dff22b6fb8fe0423313fe'),
        ('gold.usable_facts(text[])', NULL),
        ('gold.usable_facts(text[],date)', 'a67de25130f7e5c4f1bd3b42b7d9a348'),
        ('ops.check_financials(text[])', 'a8a94bf23f986a93f3f61c8fc5f35461'),
        ('ops.company_backlog(text)', '84355c31e9c36245000351005a0674ce'),
        ('ops.company_rule_version(text)', 'c42c4ab6b3199df8659901cfcff470c4'),
        ('ops.rebuild_soon(text[])', '9426fba21e20547353faa6ed8bc0824e'),
        ('ops.release_backlog(text)', '1c46b025634f8b394bb74d648620f22b'),
        ('silver.refresh_filing_facts(text[])', '3b400f0ebef7ea4ae47aeac60b50cca6'),
        ('silver.refresh_financial_facts(text[])', '813785ee2a47de9ea6e3a86f6176fef2')
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

-- 1. gold.usable_facts: 155's body
CREATE OR REPLACE FUNCTION gold.usable_facts(p_company_ids text[], p_as_of date DEFAULT NULL)
RETURNS TABLE (company_id text, metric text, tag text, taxonomy text, unit text, period_start date,
               period_end date, period_type text, value numeric, accession_no text, form text,
               filed date, ingested_at timestamptz, cik text, priority numeric, additive boolean)
LANGUAGE sql STABLE AS $$
    WITH own AS (
        SELECT k.company_id, k.cik FROM silver.company_ciks k WHERE k.company_id = ANY(p_company_ids)
    ),
    -- 050: a filing's facts belong to the filer its XBRL names (header). lent: (filing, CIK) of
    -- these companies' facts that are another company's filing; borrowed: facts under another
    -- filer's CIK of a filing whose XBRL names one of these companies' CIKs, when none of the
    -- company's own CIKs has the filing's facts.
    lent AS MATERIALIZED (
        SELECT DISTINCT a.accession_no, a.cik
        FROM (SELECT DISTINCT f.accession_no, f.cik
              FROM own o JOIN silver.financial_facts f ON f.cik = o.cik) a
        JOIN bronze.filing_xbrl x ON x.accession_no = a.accession_no AND x.entity_cik <> a.cik
        JOIN silver.company_ciks e ON e.cik = x.entity_cik
        JOIN own o ON o.cik = a.cik
        WHERE e.company_id <> o.company_id
          AND gold.filed_by(a.accession_no, x.entity_cik)
    ),
    borrowed AS MATERIALIZED (
        SELECT DISTINCT o.company_id, f.accession_no, f.cik
        FROM own o
        JOIN bronze.filing_xbrl x ON x.entity_cik = o.cik
        JOIN silver.financial_facts f ON f.accession_no = x.accession_no AND f.cik <> x.entity_cik
        WHERE NOT EXISTS (SELECT 1 FROM silver.company_ciks k
                          WHERE k.cik = f.cik AND k.company_id = o.company_id)
          AND NOT EXISTS (SELECT 1 FROM own m
                          JOIN silver.financial_facts g
                            ON g.cik = m.cik AND g.accession_no = x.accession_no
                          WHERE m.company_id = o.company_id)
          AND gold.filed_by(x.accession_no, x.entity_cik)
    ),
    facts AS (
        SELECT o.company_id, f.*
        FROM own o
        JOIN silver.financial_facts f ON f.cik = o.cik
        WHERE NOT EXISTS (SELECT 1 FROM lent l
                          WHERE l.accession_no = f.accession_no AND l.cik = f.cik)
          AND (p_as_of IS NULL OR f.filed <= p_as_of)  -- 085: what was filed by the end of p_as_of
        UNION ALL
        SELECT b.company_id, f.*
        FROM borrowed b
        JOIN silver.financial_facts f ON f.accession_no = b.accession_no AND f.cik = b.cik
        WHERE p_as_of IS NULL OR f.filed <= p_as_of  -- 085
    ),
    -- 056: each company's reporting currency: the currency most of the monetary facts of its latest
    -- annual or quarterly report are in (a 20-F's convenience translation of one year into USD is
    -- the minority). One series never mixes currencies.
    report AS (
        SELECT DISTINCT ON (f.company_id) f.company_id, f.accession_no
        FROM facts f
        WHERE f.unit ~ '^[A-Z]{3}$' AND coalesce(f.form, '') ~ '^(10-K|10-Q|20-F|40-F|6-K)'
        ORDER BY f.company_id, f.filed DESC, f.accession_no DESC
    ),
    currency AS (
        SELECT DISTINCT ON (c.company_id) c.company_id, c.unit
        FROM (
            SELECT f.company_id, f.unit, count(*) AS n
            FROM facts f
            JOIN report r ON r.company_id = f.company_id AND r.accession_no = f.accession_no
            WHERE f.unit ~ '^[A-Z]{3}$'
            GROUP BY f.company_id, f.unit
        ) c
        ORDER BY c.company_id, c.n DESC, c.unit = 'USD' DESC, c.unit
    ),
    tagged AS (
        -- by tag, not by silver's metric column: its identity index has no metric, so a tag two metrics
        -- map (EarningsPerShareBasicAndDiluted) is stored once, under one of them
        SELECT f.company_id, t.metric, f.tag, t.taxonomy, f.unit, f.period_start, f.period_end,
               f.period_type,
               -- 043: interest expense is a cost, its sign in a filing is presentation (header);
               -- 050: so is a debt balance's, which can't be below zero
               CASE WHEN t.metric IN ('interest_expense', 'long_term_debt', 'long_term_debt_current')
                    THEN abs(f.value) ELSE f.value END AS value,
               f.accession_no, f.form, f.filed, f.ingested_at, f.cik,
               t.priority::numeric AS priority, m.additive, t.gold_line
        FROM facts f
        -- 055: and by taxonomy (an ifrs-full tag can share a us-gaap tag's name), and a company's own
        -- tags (an extension, a sum of members) only for that company
        JOIN silver.metric_tags t ON t.tag = f.tag AND t.taxonomy = f.taxonomy
                                 AND (t.company_id IS NULL OR t.company_id = f.company_id)
        -- 056: a currency for a monetary metric (silver.unit_fits), the company's own (header)
        JOIN silver.metrics m ON m.metric = t.metric AND silver.unit_fits(m.unit, f.unit)
        LEFT JOIN currency cu ON cu.company_id = f.company_id
        WHERE (m.unit NOT IN ('USD', 'USD/shares')
               OR f.unit = coalesce(cu.unit, 'USD')
                           || CASE WHEN m.unit = 'USD/shares' THEN '/shares' ELSE '' END)
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
    -- 155: what a filing says is due within a year at a date, side by side: the current portion of
    -- long-term debt (LongTermDebtCurrent, else its tag with leases), the filer's own DebtCurrent,
    -- short-term borrowings and commercial paper (header, 1)
    current_debt AS (
        SELECT x.*,
               max(x.value) FILTER (WHERE x.tag = 'LongTermDebtCurrent') OVER w AS ltdc,
               max(x.value) FILTER (WHERE x.tag = 'LongTermDebtAndCapitalLeaseObligationsCurrent')
                   OVER w AS ltdc_leases,
               max(x.value) FILTER (WHERE x.tag = 'DebtCurrent') OVER w AS debt_current,
               max(x.value) FILTER (WHERE x.tag = 'ShortTermBorrowings') OVER w AS borrowings,
               bool_or(x.gold_line AND x.tag NOT IN ('LongTermDebtCurrent', 'DebtCurrent',
                       'LongTermDebtAndCapitalLeaseObligationsCurrent')) OVER w AS other_line
        FROM tagged x
        WHERE x.metric = 'long_term_debt_current' AND x.taxonomy = 'us-gaap'
          AND x.period_start IS NULL
        WINDOW w AS (PARTITION BY x.company_id, x.accession_no, x.period_end, x.unit)
    ),
    current_lines AS (
        SELECT d.company_id, d.metric,
               CASE WHEN k.joins THEN k.portion_tag || '+' || d.tag ELSE d.tag END AS tag,
               d.taxonomy, d.unit, d.period_start, d.period_end, d.period_type,
               CASE WHEN k.joins THEN k.portion + d.value ELSE d.value END AS value,
               d.accession_no, d.form, d.filed, d.ingested_at, d.cik,
               CASE WHEN d.tag = 'DebtCurrent' AND d.debt_current > k.portion THEN 0.5
                    WHEN k.joins AND d.tag = 'ShortTermBorrowings' THEN 0.6
                    WHEN k.joins THEN 0.7
                    WHEN k.alone AND d.tag = 'ShortTermBorrowings' THEN 14
                    WHEN k.alone THEN 15
                    ELSE d.priority END AS priority,
               d.additive, true AS gold_line
        FROM current_debt d
        CROSS JOIN LATERAL (
            SELECT coalesce(d.ltdc, d.ltdc_leases) AS portion,
                   CASE WHEN d.ltdc IS NOT NULL THEN 'LongTermDebtCurrent'
                        ELSE 'LongTermDebtAndCapitalLeaseObligationsCurrent' END AS portion_tag,
                   -- short-term borrowings, else commercial paper: the one part read
                   d.tag = 'ShortTermBorrowings'
                       OR (d.tag = 'CommercialPaper' AND d.borrowings IS NULL) AS part
        ) p
        CROSS JOIN LATERAL (
            -- a part added to the current portion where the filer gives no DebtCurrent and the part
            -- isn't the portion itself (equal, or up to 10% more: a total holding it)
            SELECT p.portion, p.portion_tag,
                   p.part AND p.portion IS NOT NULL AND d.debt_current IS NULL AND d.value > 0
                   AND NOT (d.value BETWEEN 0.99 * p.portion AND 1.10 * p.portion) AS joins,
                   -- a part that is all the filing gives
                   p.part AND p.portion IS NULL AND d.debt_current IS NULL
                   AND NOT coalesce(d.other_line, false) AS alone
        ) k
        WHERE d.gold_line OR k.joins OR k.alone
    ),
    -- 155: a long-term debt of zero where a filing's balance sheet says there is none (header, 2):
    -- each periodic filing's assets at a date, whether it gives any long-term debt fact there (a
    -- line or an evidence tag) or a debt cash flow ending there, and its interest expense or paid,
    -- a year's worth
    debt_marks AS (
        SELECT x.company_id, x.accession_no, x.period_end,
               max(x.value) FILTER (WHERE x.tag = 'Assets' AND x.period_start IS NULL) AS assets,
               max(x.unit) FILTER (WHERE x.tag = 'Assets' AND x.period_start IS NULL) AS unit,
               max(x.form) AS form, max(x.filed) AS filed, max(x.ingested_at) AS ingested_at,
               min(x.cik) AS cik,
               coalesce(bool_or(x.value <> 0 AND (
                   (x.metric = 'long_term_debt' AND x.period_start IS NULL)
                   OR (x.metric = 'financing_cash_flow' AND NOT x.gold_line))), false) AS debt,
               max(x.value * 365 / (x.period_end - x.period_start + 1)) FILTER (
                   WHERE x.metric = 'interest_expense' AND x.period_end - x.period_start >= 80)
                   AS interest
        FROM tagged x
        WHERE (x.tag = 'Assets' OR x.metric IN ('long_term_debt', 'interest_expense')
               OR (x.metric = 'financing_cash_flow' AND NOT x.gold_line))
          AND coalesce(x.form, '') ~ '^(10-K|10-Q|20-F|40-F)'
        GROUP BY x.company_id, x.accession_no, x.period_end
    ),
    no_debt AS (  -- any debt fact within the year to the date, in any periodic filing
        SELECT m.*,
               bool_or(m.debt) OVER (PARTITION BY m.company_id ORDER BY m.period_end
                                     RANGE BETWEEN '365 days' PRECEDING AND CURRENT ROW)
                   AS debt_in_year
        FROM debt_marks m
    ),
    no_debt_lines AS (
        SELECT n.company_id, 'long_term_debt'::text AS metric, 'no-debt'::text AS tag,
               'derived'::text AS taxonomy, n.unit, NULL::date AS period_start, n.period_end,
               'instant'::text AS period_type, 0::numeric AS value, n.accession_no, n.form, n.filed,
               n.ingested_at, n.cik, 99::numeric AS priority, false AS additive, true AS gold_line
        FROM no_debt n
        JOIN silver.companies co ON co.company_id = n.company_id
        WHERE n.assets > 0 AND NOT n.debt_in_year
          AND coalesce(n.interest, 0) <= 0.0025 * n.assets
          AND co.sic IS NOT NULL AND co.sic !~ '^6[0-7]'
    ),
    lines AS (
        SELECT * FROM tagged
        WHERE gold_line
          -- 155: these come from current_lines
          AND NOT (metric = 'long_term_debt_current' AND taxonomy = 'us-gaap'
                   AND period_start IS NULL)
        UNION ALL SELECT * FROM current_lines
        UNION ALL SELECT * FROM no_debt_lines
        UNION ALL SELECT * FROM summed
        UNION ALL SELECT * FROM loss_eps
    ),
    -- 050: debt parts adding up to 1,000 or 1,000,000 times the same filing's total for the date
    -- are a scale slip in the parts (Bank First's 2019 10-K: 1,400,000,000 + 38,400,000,000 beside a
    -- LongTermDebt of 39,800,000)
    slipped AS (
        SELECT p.company_id, p.accession_no, p.period_end
        FROM (
            SELECT x.company_id, x.accession_no, x.period_end,
                   sum(x.value) FILTER (WHERE x.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent'))
                       AS parts,
                   max(x.value) FILTER (WHERE x.tag = 'LongTermDebt') AS total
            FROM tagged x
            WHERE x.period_start IS NULL
              AND x.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent', 'LongTermDebt')
            GROUP BY x.company_id, x.accession_no, x.period_end
        ) p
        WHERE p.total > 0
          AND (abs(p.parts / p.total - 1000) <= 5 OR abs(p.parts / p.total - 1000000) <= 5000)
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
                   -- 155: a short-term borrowing or commercial paper part isn't the current portion
                   OR (x.metric = 'long_term_debt_current' AND x.gold_line))
              AND NOT (x.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent')  -- 050
                       AND EXISTS (SELECT 1 FROM slipped s
                                   WHERE s.company_id = x.company_id
                                     AND s.accession_no = x.accession_no
                                     AND s.period_end = x.period_end))
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
      -- 050: debt parts that are a scale slip of the filing's own total
      AND NOT (l.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent') AND l.period_start IS NULL
               AND EXISTS (SELECT 1 FROM slipped s
                           WHERE s.company_id = l.company_id AND s.accession_no = l.accession_no
                             AND s.period_end = l.period_end))
$$;

-- 2. gold.knowledge_facts and ops.company_backlog: 086's bodies
CREATE OR REPLACE FUNCTION gold.knowledge_facts(p_company_id text)
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

-- 3. The release figures and their catch-up kind (the kind CHECK keeps 'releases': harmless)
DROP FUNCTION ops.release_backlog(text);
DROP TABLE silver.release_figures, silver.release_reads;
DELETE FROM ops.catchup_runs WHERE kind = 'releases';
DELETE FROM ops.catchup WHERE kind = 'releases';

COMMIT;
