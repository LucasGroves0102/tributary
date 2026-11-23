-- Backout of 155_analyst_fixes_1.sql (never a migration: README.md): gold.usable_facts,
-- gold.best_facts and gold.quarterly_rows back to 085's bodies (debt due within a year is the current
-- portion alone again, no long-term debt of zero where a filing tags none, an 8-K cited over a 10-K
-- stating the same value, no gross profit on net sales at Walmart and Costco), gold.not_meaningful
-- to 083's, gold.reading to 078's and gold.company_confidence to 087's (a lender's gross margin and
-- growth over 400% read as figures again; "no report since" for a company whose later report the
-- figures lack), gold.not_meaningful_for dropped, and 155's tags removed with their silver facts.
-- Deploy the previous query-api image first (its statements call gold.not_meaningful_for). Then run
-- the financials-rebuild job, which puts the old rows back; the rules version stays 155 (db/CLAUDE.md:
-- a backout leaves the versions).
--
-- Run it as the migration owner, the migrate login `tributary` (SET ROLE as below): run as
-- postgres, a function it drops and re-creates would belong to postgres, and the non-superuser
-- migrate login could never replace it again. The guard below refuses any login that doesn't own
-- the functions, and any database where a newer backout hasn't run yet (README.md, Order).
--
--   sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f analyst_fixes_155.sql

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
        ('gold.knowledge_facts(text)', '18e26ac36e3c21f7ce905a870362750c'),
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
        ('gold.usable_facts(text[],date)', '77d9e7a0919694f250761f988026a377'),
        ('ops.check_financials(text[])', 'a8a94bf23f986a93f3f61c8fc5f35461'),
        ('ops.company_backlog(text)', 'c57e6d676904010c5295c661689911ca'),
        ('ops.company_rule_version(text)', 'c42c4ab6b3199df8659901cfcff470c4'),
        ('ops.rebuild_soon(text[])', '9426fba21e20547353faa6ed8bc0824e'),
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

-- 085's gold.usable_facts, copied unchanged
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
    lines AS (
        SELECT * FROM tagged WHERE gold_line
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
                   OR x.metric = 'long_term_debt_current')
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

-- 085's gold.best_facts, copied unchanged
CREATE OR REPLACE FUNCTION gold.best_facts(p_company_ids text[], p_as_of date DEFAULT NULL)
RETURNS TABLE (company_id text, metric text, tag text, taxonomy text, unit text, period_start date,
               period_end date, period_type text, value numeric, accession_no text, form text,
               filed date, ingested_at timestamptz, cik text, priority numeric, additive boolean)
LANGUAGE sql STABLE AS $$
    WITH tag_latest AS (
        SELECT DISTINCT ON (u.company_id, u.metric, u.tag, u.period_start, u.period_end) u.*,
               -- 077: when the tag first gave this period (a later filing's comparative or not)
               min(u.filed) OVER (PARTITION BY u.company_id, u.metric, u.tag, u.period_start,
                                               u.period_end) AS first_filed
        FROM gold.usable_facts(p_company_ids, p_as_of) u  -- 085
        ORDER BY u.company_id, u.metric, u.tag, u.period_start, u.period_end, u.filed DESC,
                 u.accession_no DESC, u.cik
    ),
    -- 077: a year whose net income the company's own filings of the year tag ProfitLoss only (the
    -- annual figure ProfitLoss, never NetIncomeLoss; a year: 350-380 days), NetIncomeLoss for its
    -- periods coming only from later filings' comparatives (first filed 100+ days after the year
    -- ended). Inside it ProfitLoss reads first, so the year's quarters, its year-to-date figures and
    -- the fiscal Q4 derived from them are one line. PNC's 2025: ProfitLoss in every filing of the
    -- year, NetIncomeLoss only in two 2026 10-Qs' comparatives (Q2, Q3, six and nine months): gold
    -- read Q1 ProfitLoss, Q2-Q3 NetIncomeLoss and a Q4 of the year's ProfitLoss less nine months of
    -- NetIncomeLoss.
    profit_loss_years AS MATERIALIZED (
        SELECT y.company_id, y.period_start, y.period_end
        FROM (SELECT t.company_id, t.period_start, t.period_end
              FROM tag_latest t
              WHERE t.metric = 'net_income' AND t.taxonomy = 'us-gaap'
                AND t.tag IN ('NetIncomeLoss', 'ProfitLoss')
                AND t.period_end - t.period_start BETWEEN 350 AND 380
              GROUP BY t.company_id, t.period_start, t.period_end
              HAVING NOT bool_or(t.tag = 'NetIncomeLoss')) y
        WHERE NOT EXISTS (SELECT 1 FROM tag_latest n
                          WHERE n.company_id = y.company_id AND n.metric = 'net_income'
                            AND n.taxonomy = 'us-gaap' AND n.tag = 'NetIncomeLoss'
                            AND n.period_start >= y.period_start
                            AND n.period_end <= y.period_end
                            AND n.first_filed <= y.period_end + 100)
    ),
    ranked AS (  -- 077: the order tags are believed in; priority, but for that rule
        SELECT t.*,
               CASE WHEN t.metric = 'net_income' AND t.taxonomy = 'us-gaap' AND t.tag = 'ProfitLoss'
                         AND EXISTS (SELECT 1 FROM profit_loss_years y
                                     WHERE y.company_id = t.company_id
                                       AND t.period_start >= y.period_start
                                       AND t.period_end <= y.period_end)
                    THEN 0 ELSE t.priority END AS rank,
               -- 080: an industry total, read only where no other revenue tag gives the period
               (t.metric = 'revenue' AND t.taxonomy = 'us-gaap'
                AND t.tag IN ('RealEstateRevenueNet', 'SalesRevenueGoodsNet',
                              'SalesRevenueServicesNet')) AS fallback
        FROM tag_latest t
    ),
    -- 064: the period's positive revenue values, and the largest of its other money figures (any
    -- other metric with the same start and end), for the scale test below
    scales AS (
        SELECT t.*,
               array_agg(t.value) FILTER (WHERE t.metric = 'revenue' AND t.value > 0) OVER wm
                   AS revenue_values,
               max(abs(t.value)) FILTER (WHERE t.metric <> 'revenue' AND t.unit ~ '^[A-Z]{3}$')
                   OVER wp AS other_max
        FROM ranked t  -- 077
        WINDOW wp AS (PARTITION BY t.company_id, t.period_start, t.period_end),
               wm AS (PARTITION BY t.company_id, t.period_start, t.period_end, t.metric)
    ),
    -- 064: two revenue tags of a period 1,000 or 1,000,000 times apart (within 0.5%) are one figure
    -- in two scales. The larger is the slip when it is over 100 times every other money figure of
    -- the period, else the smaller is (also when the period has no other figure). A slip is no
    -- evidence for revenue's rules below and sorts after every other tag.
    slips AS (
        SELECT s.*,
               CASE WHEN s.metric = 'revenue' AND s.value > 0 THEN EXISTS (
                   SELECT 1 FROM unnest(s.revenue_values) v
                   WHERE ((s.value BETWEEN v * 995 AND v * 1005
                           OR s.value BETWEEN v * 995000 AND v * 1005000)
                          AND s.value > 100 * s.other_max)
                      OR ((v BETWEEN s.value * 995 AND s.value * 1005
                           OR v BETWEEN s.value * 995000 AND s.value * 1005000)
                          AND (s.other_max IS NULL OR v <= 100 * s.other_max)))
               ELSE false END AS slipped
        FROM scales s
    ),
    weighed AS (  -- revenue's rules (039's and 064's headers) read the period's other tags
        SELECT t.*,
               -- 064: a slip counts for none of these
               max(t.filed) FILTER (WHERE t.value >= 1000000 AND NOT t.slipped) OVER w AS large_filed,
               max(t.filed) FILTER (WHERE t.value > 0 AND t.value < 1000000 AND NOT t.slipped) OVER w
                   AS small_filed,
               max(t.value) FILTER (WHERE NOT t.slipped AND t.tag IN (
                   'RevenueFromContractWithCustomerExcludingAssessedTax',
                   'RevenueFromContractWithCustomerIncludingAssessedTax')) OVER w AS contract_value,
               -- 064: the gross line a net revenue figure is compared with
               max(t.value) FILTER (WHERE NOT t.slipped AND t.tag IN ('Revenues',
                   'RevenueFromContractWithCustomerExcludingAssessedTax',
                   'RevenueFromContractWithCustomerIncludingAssessedTax')) OVER w AS gross_value,
               -- 080: the period's industry totals (the first and last of their tags) and the
               -- largest of its other revenue values; positive, neither a slip
               min(t.tag) FILTER (WHERE t.fallback AND t.value > 0) OVER w AS fallback_tag,
               max(t.tag) FILTER (WHERE t.fallback AND t.value > 0) OVER w AS fallback_tag_last,
               max(t.value) FILTER (WHERE t.fallback AND t.value > 0 AND NOT t.slipped) OVER w
                   AS fallback_value,
               max(t.value) FILTER (WHERE t.metric = 'revenue' AND NOT t.fallback AND t.value > 0
                                      AND NOT t.slipped) OVER w AS trusted_value
        FROM slips t  -- 064
        WINDOW w AS (PARTITION BY t.company_id, t.metric, t.period_start, t.period_end)
    ),
    -- 080: an industry total the company's own filings give as a part: under 80% of another
    -- revenue tag's value for the same period, in any filing (Ultragenyx's product sales beside
    -- its total, Tiptree's real estate line beside an insurer's revenues). Never read there; nor
    -- where silver.industry_total_exclusions says so (sales before excise taxes, a predecessor).
    fallback_parts AS MATERIALIZED (
        SELECT x.company_id, x.tag
        FROM weighed x
        WHERE x.fallback AND x.value < 0.8 * x.trusted_value
        UNION
        SELECT e.company_id, e.tag
        FROM silver.industry_total_exclusions e
        WHERE e.company_id = ANY(p_company_ids)
    ),
    judged AS (
        SELECT x.*,
               -- 080: the period's one industry total, at a company that never gives it as a part
               -- (two of them in one period are parts of a total no tag gives: neither is read)
               CASE WHEN x.fallback_tag IS NULL OR x.fallback_tag <> x.fallback_tag_last THEN false
                    ELSE NOT EXISTS (SELECT 1 FROM fallback_parts p
                                     WHERE p.company_id = x.company_id
                                       AND p.tag = x.fallback_tag) END AS fallback_readable
        FROM weighed x
    )
    SELECT DISTINCT ON (x.company_id, x.metric, x.period_start, x.period_end)
           x.company_id, x.metric, x.tag, x.taxonomy, x.unit, x.period_start, x.period_end,
           x.period_type, x.value, x.accession_no, x.form, x.filed, x.ingested_at, x.cik, x.priority,
           x.additive
    FROM judged x  -- 080
    WHERE NOT (x.fallback AND (x.value <= 0 OR NOT x.fallback_readable))  -- 080
    ORDER BY x.company_id, x.metric, x.period_start, x.period_end,
             x.slipped,  -- 064: a scale slip after every other tag
             (x.metric = 'revenue' AND x.period_start IS NOT NULL
              AND ((x.value = 0 AND (x.large_filed >= x.filed OR x.small_filed > x.filed))
                   OR (x.tag = 'Revenues' AND x.value > 0 AND x.value < 0.2 * x.contract_value)
                   -- 064: net revenue under 20% of the gross line is a part too (Rectitude 0.2%)
                   OR (x.tag = 'RevenuesNetOfInterestExpense' AND x.value > 0
                       AND x.value < 0.2 * x.gross_value)
                   -- 080: any other revenue tag under 20% of the period's industry total is a part
                   -- (Conagra's Revenues of $1.6-1.7B beside $13B of net sales, FY2012-2016)
                   OR (NOT x.fallback AND x.fallback_readable AND x.value > 0
                       AND x.value < 0.2 * x.fallback_value)))
                 IS TRUE,
             x.rank, x.filed DESC, x.accession_no DESC, x.cik  -- 077: rank, not priority
$$;

-- 085's gold.quarterly_rows, copied unchanged
CREATE OR REPLACE FUNCTION gold.quarterly_rows(p_company_ids text[], p_as_of date DEFAULT NULL)
RETURNS SETOF gold.quarter_row
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE best AS MATERIALIZED (
        SELECT * FROM gold.best_facts(p_company_ids, p_as_of)  -- 085
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
              'dividends_paid', 'interest_expense'))  -- interest_expense since 043
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
    ),
    -- 043's first INSERT: the series' rows
    series AS MATERIALIZED (
        SELECT r.company_id, r.metric, r.period_start, r.period_end, r.value, r.unit,
               r.value_source, r.source_tag, r.accession_no, r.form, r.filed, r.source_accessions,
               r.ingested_at
        FROM ranked r
        LEFT JOIN dropped d
          ON d.company_id = r.company_id AND d.metric = r.metric AND d.rnk = r.rnk
        WHERE d.rnk IS NULL
    ),
    -- revenue less cost, where the filing prints no gross profit (043's second INSERT, over the
    -- series' rows above instead of the table it read them back from). 085: its keyed reads of
    -- that table as one pass: each quarter's revenue, cost, operating income, SG&A and R&D side by
    -- side (one row per metric and end in the series)
    lines AS (
        SELECT s.company_id, s.period_end,
               max(s.value) FILTER (WHERE s.metric = 'revenue') AS revenue,
               max(s.period_start) FILTER (WHERE s.metric = 'revenue') AS revenue_start,
               max(s.unit) FILTER (WHERE s.metric = 'revenue') AS unit,
               max(s.accession_no) FILTER (WHERE s.metric = 'revenue') AS revenue_accession,
               max(s.form) FILTER (WHERE s.metric = 'revenue') AS revenue_form,
               max(s.filed) FILTER (WHERE s.metric = 'revenue') AS revenue_filed,
               max(s.source_accessions) FILTER (WHERE s.metric = 'revenue') AS revenue_accessions,
               max(s.ingested_at) FILTER (WHERE s.metric = 'revenue') AS revenue_ingested_at,
               max(s.value) FILTER (WHERE s.metric = 'cost_of_revenue') AS cost,
               max(s.period_start) FILTER (WHERE s.metric = 'cost_of_revenue') AS cost_start,
               max(s.accession_no) FILTER (WHERE s.metric = 'cost_of_revenue') AS cost_accession,
               max(s.form) FILTER (WHERE s.metric = 'cost_of_revenue') AS cost_form,
               max(s.filed) FILTER (WHERE s.metric = 'cost_of_revenue') AS cost_filed,
               max(s.source_accessions) FILTER (WHERE s.metric = 'cost_of_revenue')
                   AS cost_accessions,
               max(s.ingested_at) FILTER (WHERE s.metric = 'cost_of_revenue') AS cost_ingested_at,
               max(s.value) FILTER (WHERE s.metric = 'operating_income') AS operating_income,
               max(s.value) FILTER (WHERE s.metric = 'sga') AS sga,
               max(s.value) FILTER (WHERE s.metric = 'research_and_development') AS rnd
        FROM series s
        WHERE s.metric IN ('revenue', 'cost_of_revenue', 'operating_income', 'sga',
                           'research_and_development')
        GROUP BY s.company_id, s.period_end
    ),
    computable AS (  -- 043's tests of a quarter's revenue and cost
        SELECT l.*, (l.revenue - l.cost) / l.revenue AS margin
        FROM lines l
        JOIN silver.companies co ON co.company_id = l.company_id
        WHERE l.revenue > 0 AND abs(l.cost_start - l.revenue_start) <= 7
          AND l.period_end >= DATE '2019-01-01'
          AND co.sic IS NOT NULL
          AND co.sic !~ '^(6[0-7]|13|48|58)' AND co.sic !~ '^(4911|493.|2911|5331|5912)$'
          AND l.cost >= 0.15 * l.revenue
          AND (l.operating_income IS NULL OR l.revenue - l.cost >= l.operating_income)
          AND NOT (l.operating_income IS NOT NULL AND l.sga IS NOT NULL
                   AND l.revenue - l.cost - l.operating_income - l.sga - coalesce(l.rnd, 0)
                       > 0.25 * l.revenue)
    ),
    -- 085: no gross profit line of the series overlapping the quarter by more than 7 days (043's NOT
    -- EXISTS). A series' lines overlap each other by 7 days at most and last 79-120 days, so none
    -- lies inside another: the first one ending on or after the quarter's start + 7 is the only one
    -- to test (it overlaps when it starts by the quarter's end - 7). Found in one pass: the lines'
    -- ends and the quarters' starts + 7 in one order, the first line at or after each quarter.
    overlap AS (
        SELECT x.*,
               min(x.line COLLATE "C") OVER (PARTITION BY x.company_id ORDER BY x.k
                                             RANGE BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)
                   AS next_line
        FROM (
            SELECT c.company_id, c.revenue_start + 7 AS k, NULL::text AS line, c.period_end,
                   c.revenue, c.cost, c.unit, c.margin, c.revenue_accession, c.revenue_form,
                   c.revenue_filed, c.revenue_accessions, c.revenue_ingested_at, c.cost_accession,
                   c.cost_form, c.cost_filed, c.cost_accessions, c.cost_ingested_at,
                   c.revenue_start AS period_start
            FROM computable c
            UNION ALL
            SELECT s.company_id, s.period_end,
                   to_char(s.period_end, 'YYYYMMDD') || to_char(s.period_start, 'YYYYMMDD'), NULL,
                   NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                   NULL, NULL
            FROM series s
            WHERE s.metric = 'gross_profit'
        ) x
    ),
    pairs AS (
        SELECT o.company_id, o.period_start, o.period_end, o.revenue, o.cost, o.unit, o.margin,
               CASE WHEN o.cost_filed > o.revenue_filed THEN o.cost_accession
                    ELSE o.revenue_accession END AS accession_no,
               CASE WHEN o.cost_filed > o.revenue_filed THEN o.cost_form
                    ELSE o.revenue_form END AS form,
               greatest(o.cost_filed, o.revenue_filed) AS filed,
               ARRAY(SELECT DISTINCT a FROM unnest(o.revenue_accessions || o.cost_accessions) a
                     ORDER BY 1) AS source_accessions,
               greatest(o.cost_ingested_at, o.revenue_ingested_at) AS ingested_at
        FROM overlap o
        WHERE o.line IS NULL
          AND NOT (o.next_line IS NOT NULL
                   AND to_date(substr(o.next_line, 9, 8), 'YYYYMMDD') <= o.period_end - 7)
    ),
    usual AS (
        SELECT company_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY margin)::numeric AS margin
        FROM pairs GROUP BY company_id
    )
    SELECT s.company_id, s.metric, s.period_start, s.period_end, s.value, s.unit, s.value_source,
           s.source_tag, s.accession_no, s.form, s.filed, s.source_accessions, s.ingested_at
    FROM series s
    UNION ALL
    SELECT p.company_id, 'gross_profit', p.period_start, p.period_end, p.revenue - p.cost, p.unit,
           'derived', 'derived:revenue-cost_of_revenue', p.accession_no, p.form, p.filed,
           p.source_accessions, p.ingested_at
    FROM pairs p
    JOIN usual u USING (company_id)
    WHERE abs(p.margin - u.margin) <= 0.08
$$;

-- 083's gold.not_meaningful, copied unchanged
CREATE OR REPLACE FUNCTION gold.not_meaningful(p_metric text, p_value numeric, p_inputs jsonb,
                                               p_equity numeric, p_currency text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN p_metric IN ('debt_to_equity', 'roe') AND p_equity <= 0
            THEN 'stockholders'' equity is negative'
        WHEN p_value IS NULL THEN NULL
        -- 083: gross sales of traded assets; cost_of_revenue is in the inputs only where revenue
        -- can be that (gold.gross_sales_sic), from the readers (header)
        WHEN p_metric = 'revenue' THEN CASE
            WHEN p_value > 0 AND (p_inputs->>'cost_of_revenue')::numeric > 0.97 * p_value
                 AND (p_inputs->>'cost_of_revenue')::numeric <= p_value
                THEN 'cost of revenue is over 97% of revenue: a trading venue or dealer counts the '
                     'full value of the assets it sells as revenue, not what it earns on them, so '
                     'it isn''t compared with other companies'' revenue'
        END
        WHEN p_metric IN ('gross_margin', 'operating_margin', 'net_margin', 'fcf_margin') THEN CASE
            WHEN coalesce(p_currency, 'USD') = 'USD' AND (p_inputs->>'revenue')::numeric < 100000
                THEN 'revenue is under $100K, too small a base'
            WHEN p_metric = 'gross_margin' AND p_value > 1
                THEN 'gross profit is larger than revenue, so the two are on different bases'
            WHEN p_metric = 'operating_margin' AND p_value > 1
                THEN 'operating income is larger than revenue: a one-off gain, or revenue that '
                     'covers only part of the business'
            WHEN abs(p_value) > 10
                THEN 'the amount is more than ten times revenue'
        END
        WHEN p_metric = 'revenue_growth' THEN CASE
            WHEN coalesce(p_currency, 'USD') = 'USD'
                 AND (p_inputs->>'revenue_year_ago')::numeric < 100000
                THEN 'revenue a year earlier was under $100K, too small a base'
            WHEN p_value > 10 AND coalesce(p_currency, 'USD') = 'USD'
                 AND (p_inputs->>'revenue_year_ago')::numeric < 10000000
                THEN 'growth of over 1,000% from under $10M a year earlier'
            WHEN p_value < -1 THEN 'revenue is negative'
        END
        WHEN p_metric = 'net_income_growth' THEN CASE
            WHEN p_value < -1 THEN 'net income went from a profit to a loss'
            WHEN p_value > 10 AND coalesce(p_currency, 'USD') = 'USD'
                 AND (p_inputs->>'net_income_year_ago')::numeric < 10000000
                THEN 'growth of over 1,000% from under $10M a year earlier'
        END
        WHEN p_metric = 'eps_growth' THEN CASE
            WHEN p_value < -1 THEN 'earnings per share went from a profit to a loss'
            WHEN p_value > 10 THEN 'growth of over 1,000% from a very small base'
        END
        WHEN p_metric = 'debt_to_equity' THEN CASE
            WHEN p_value < 0 THEN 'debt is below zero, a sign error in the filing''s XBRL'
            WHEN p_value > 100 THEN 'debt is over 100 times equity, so equity is close to zero'
        END
        WHEN p_metric = 'roe' AND abs(p_value) > 10
            THEN 'net income is more than ten times equity, so equity is close to zero'
        WHEN p_metric = 'payout_ratio' AND p_value > 10
            THEN 'dividends are more than ten times net income, so net income is close to zero'
        WHEN p_metric = 'current_ratio' AND p_value > 100
            THEN 'current liabilities are close to zero'
    END
$$;

-- 078's gold.reading, copied unchanged
CREATE OR REPLACE FUNCTION gold.reading(p_company_id text, p_metric text, p_sic text, p_latest date,
                             p_period_end date, p_value numeric, p_inputs jsonb, p_unit text,
                             p_form text, p_equity numeric, p_currency text, p_recent boolean)
RETURNS TABLE (status text, reason text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'not_applicable'
                -- 056: an amount in another currency is never compared with dollars
                WHEN p_unit ~ '^[A-Z]{3}(/shares)?$' AND p_unit !~ '^USD'
                    THEN 'other_currency'
                -- 069: no figure (or only an old one) because Tributary computes none here
                WHEN (p_value IS NULL OR gold.period_status(p_period_end, p_latest) = 'old')
                     AND p_latest IS NOT NULL AND gold.not_computed(p_metric, p_sic) IS NOT NULL
                    THEN 'not_computed'
                WHEN gold.not_meaningful(p_metric, p_value, p_inputs, p_equity,
                                         coalesce(p_currency, 'USD')) IS NOT NULL
                    THEN 'not_meaningful'
                -- 069: the company's latest period is long past; an old value stays old
                WHEN gold.is_stale(p_latest, p_form, p_recent) AND p_value IS NOT NULL
                     AND gold.period_status(p_period_end, p_latest) IS DISTINCT FROM 'old'
                    THEN 'stale'
                ELSE gold.period_status(p_period_end, p_latest) END,
           CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'this is a bank: deposits are its debt and lending its operations, so '
                         'the figure has no comparable meaning'
                WHEN p_unit ~ '^[A-Z]{3}(/shares)?$' AND p_unit !~ '^USD'
                    THEN 'reported in ' || split_part(p_unit, '/', 1)
                         || ', so it isn''t sorted, filtered or averaged with US-dollar figures'
                WHEN (p_value IS NULL OR gold.period_status(p_period_end, p_latest) = 'old')
                     AND p_latest IS NOT NULL AND gold.not_computed(p_metric, p_sic) IS NOT NULL
                    THEN gold.not_computed(p_metric, p_sic)
                WHEN gold.not_meaningful(p_metric, p_value, p_inputs, p_equity,
                                         coalesce(p_currency, 'USD')) IS NOT NULL
                    THEN gold.not_meaningful(p_metric, p_value, p_inputs, p_equity,
                                             coalesce(p_currency, 'USD'))
                WHEN gold.is_stale(p_latest, p_form, p_recent) AND p_value IS NOT NULL
                     AND gold.period_status(p_period_end, p_latest) IS DISTINCT FROM 'old'
                    THEN 'this company''s latest figures here are for the period ended '
                         || to_char(p_latest, 'FMMon FMDD, YYYY') || ', and no report since has '
                         'reached Tributary, so they aren''t compared with current figures' END
$$;

-- 087's gold.company_confidence, copied unchanged
CREATE OR REPLACE FUNCTION gold.company_confidence(p_company_id text, p_sic text, p_latest date)
RETURNS TABLE (level text, reasons text[])
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT CASE WHEN q.company_id IS NULL AND p_latest IS NULL THEN NULL
                WHEN cardinality(x.low) > 0 THEN 'low'
                WHEN cardinality(x.medium) > 0 THEN 'medium'
                ELSE 'high' END,
           CASE WHEN q.company_id IS NULL AND p_latest IS NULL THEN NULL
                WHEN cardinality(x.low) > 0 THEN x.low
                WHEN cardinality(x.medium) > 0 THEN x.medium
                ELSE x.high END
    FROM (SELECT 1) one
    LEFT JOIN ops.quality_companies q ON q.company_id = p_company_id
    LEFT JOIN LATERAL gold.amount_currency_of(p_company_id) cu ON true
    CROSS JOIN LATERAL (
        -- 087: an as-of session sets tributary.today locally; on a pooled connection it reads ''
        -- afterwards, never NULL again (gold.is_stale's nullif)
        SELECT coalesce(nullif(current_setting('tributary.today', true), '')::date, current_date)
                   - p_latest
                   AS age,
               CASE WHEN q.figures > 0 THEN 1000.0 * q.errors / q.figures END AS errors_k,
               CASE WHEN q.figures > 0 THEN 1000.0 * q.warnings / q.figures END AS warnings_k
    ) t
    -- 069's annual filer (no quarterly revenue or net income in the 200 days before its latest
    -- period), probed only for a company past 270 days
    LEFT JOIN LATERAL (
        SELECT NOT EXISTS (
                   SELECT 1 FROM gold.quarterly_financials g
                   WHERE g.company_id = p_company_id AND g.metric IN ('revenue', 'net_income')
                     AND g.period_end BETWEEN p_latest - 200 AND p_latest
                     AND g.period_end - g.period_start BETWEEN 80 AND 120) AS annual
        WHERE t.age > 270
    ) a ON true
    CROSS JOIN LATERAL (
        SELECT array_remove(ARRAY[
                   CASE q.entity
                       WHEN 'shell' THEN 'a blank-check company with no business yet, so its '
                                         'figures say little about one'
                       WHEN 'commodity_pool' THEN 'a commodity pool or trust: its results are gains '
                                         'on what it holds, not sales and profits'
                       WHEN 'royalty_trust' THEN 'a royalty trust: it passes royalties through, so '
                                         'most operating figures don''t apply'
                       WHEN 'bdc' THEN 'a business development company: it reports investment '
                                         'income and net assets, which the standard lines read '
                                         'only in part' END,
                   CASE WHEN t.errors_k >= 2
                        THEN format('%s %s a reader could see in its %s figures', q.errors,
                                    CASE WHEN q.errors = 1 THEN 'error' ELSE 'errors' END,
                                    to_char(q.figures, 'FM999,999,999')) END,
                   CASE WHEN t.warnings_k >= 25
                        THEN format('%s %s to double-check in its %s', q.warnings,
                                    CASE WHEN q.warnings = 1 THEN 'figure' ELSE 'figures' END,
                                    to_char(q.figures, 'FM999,999,999')) END,
                   CASE WHEN t.age > 500
                        THEN 'no report since the period ended '
                             || to_char(p_latest, 'FMMon FMDD, YYYY') END,
                   CASE WHEN q.core_figures <= 2
                        THEN format('only %s of the six core figures for its latest period',
                                    q.core_figures) END
               ], NULL) AS low,
               array_remove(ARRAY[
                   CASE WHEN q.company_id IS NULL THEN 'not checked yet' END,
                   CASE WHEN q.errors > 0
                        THEN format('%s %s a reader could see', q.errors,
                                    CASE WHEN q.errors = 1 THEN 'error' ELSE 'errors' END) END,
                   CASE WHEN t.warnings_k >= 10
                        THEN format('%s %s to double-check in its %s', q.warnings,
                                    CASE WHEN q.warnings = 1 THEN 'figure' ELSE 'figures' END,
                                    to_char(q.figures, 'FM999,999,999')) END,
                   CASE WHEN q.statements_differ_at IS NOT NULL
                        THEN 'a filing''s own statement prints another headline figure than '
                             'the one here' END,
                   CASE WHEN cu.unit IS NOT NULL AND cu.unit <> 'USD'
                        THEN 'reported in ' || cu.unit || ': fewer of its lines map to the '
                             'standard ones, and nothing is converted' END,
                   CASE WHEN t.age > 270 AND t.age <= 500 AND NOT a.annual
                        THEN 'its latest figures are for the period ended '
                             || to_char(p_latest, 'FMMon FMDD, YYYY') END,
                   CASE WHEN q.core_figures IN (3, 4)
                        THEN format('%s of the six core figures for its latest period',
                                    q.core_figures) END
               ], NULL) AS medium,
               array_remove(ARRAY[
                   CASE WHEN q.figures > 0
                        THEN format('no error found in its %s figures',
                                    to_char(q.figures, 'FM999,999,999')) END,
                   CASE WHEN q.statements_agreed > 0
                        THEN format('%s %s from its filings'' own statements %s the figures here',
                                    q.statements_agreed,
                                    CASE WHEN q.statements_agreed = 1 THEN 'line' ELSE 'lines' END,
                                    CASE WHEN q.statements_agreed = 1 THEN 'agrees with'
                                         ELSE 'agree with' END) END
               ], NULL) AS high
    ) x
$$;

DROP FUNCTION gold.not_meaningful_for(text, text);

DELETE FROM silver.financial_facts
WHERE (metric, tag) IN (
    ('long_term_debt_current', 'ShortTermBorrowings'),
    ('long_term_debt_current', 'CommercialPaper'),
    ('long_term_debt_current', 'ConvertibleDebtCurrent'),
    ('long_term_debt_current', 'ConvertibleNotesPayableCurrent'),
    ('long_term_debt', 'ConvertibleDebtNoncurrent'),
    ('long_term_debt', 'ConvertibleLongTermNotesPayable'),
    ('long_term_debt', 'LongTermDebtFairValue'),
    ('long_term_debt', 'DebtInstrumentCarryingAmount'),
    ('long_term_debt', 'NotesPayable'),
    ('long_term_debt', 'SeniorNotes'),
    ('long_term_debt', 'LineOfCredit'),
    ('long_term_debt', 'ConvertibleNotesPayable'),
    ('long_term_debt', 'DebtLongtermAndShorttermCombinedAmount'),
    ('long_term_debt', 'LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths'),
    ('long_term_debt', 'OtherLongTermDebtNoncurrent'),
    ('long_term_debt', 'UnsecuredDebt'),
    ('long_term_debt', 'SecuredDebt'),
    ('long_term_debt', 'LoansPayable'),
    ('interest_expense', 'InterestPaidNet'),
    ('financing_cash_flow', 'RepaymentsOfLongTermDebt'),
    ('financing_cash_flow', 'ProceedsFromIssuanceOfLongTermDebt'),
    ('financing_cash_flow', 'RepaymentsOfDebt'),
    ('financing_cash_flow', 'ProceedsFromIssuanceOfDebt'));
DELETE FROM silver.metric_tags
WHERE taxonomy = 'us-gaap' AND company_id IS NULL AND (metric, tag) IN (
    ('long_term_debt_current', 'ShortTermBorrowings'),
    ('long_term_debt_current', 'CommercialPaper'),
    ('long_term_debt_current', 'ConvertibleDebtCurrent'),
    ('long_term_debt_current', 'ConvertibleNotesPayableCurrent'),
    ('long_term_debt', 'ConvertibleDebtNoncurrent'),
    ('long_term_debt', 'ConvertibleLongTermNotesPayable'),
    ('long_term_debt', 'LongTermDebtFairValue'),
    ('long_term_debt', 'DebtInstrumentCarryingAmount'),
    ('long_term_debt', 'NotesPayable'),
    ('long_term_debt', 'SeniorNotes'),
    ('long_term_debt', 'LineOfCredit'),
    ('long_term_debt', 'ConvertibleNotesPayable'),
    ('long_term_debt', 'DebtLongtermAndShorttermCombinedAmount'),
    ('long_term_debt', 'LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths'),
    ('long_term_debt', 'OtherLongTermDebtNoncurrent'),
    ('long_term_debt', 'UnsecuredDebt'),
    ('long_term_debt', 'SecuredDebt'),
    ('long_term_debt', 'LoansPayable'),
    ('interest_expense', 'InterestPaidNet'),
    ('financing_cash_flow', 'RepaymentsOfLongTermDebt'),
    ('financing_cash_flow', 'ProceedsFromIssuanceOfLongTermDebt'),
    ('financing_cash_flow', 'RepaymentsOfDebt'),
    ('financing_cash_flow', 'ProceedsFromIssuanceOfDebt'));

COMMIT;
