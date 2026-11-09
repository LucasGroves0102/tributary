-- Foreign filers (F8a, H12): IFRS tags, the company's own currency, and what's annual.
--
-- Measured on hermes 2026-11-09 (read only, one connection): of 6,066 companies listed on NYSE, Nasdaq,
-- Cboe or TXSE, 984 have no gold financials. 525 of them file 20-F or 40-F (2024-26); 517 have a
-- companyfacts payload, and 486 of those carry ifrs-full facts filed since 2024 (483 have no fact of a
-- mapped tag at all: silver read us-gaap only, in USD only). Their latest 20-F/40-F (485 companies)
-- tags: Assets, Equity and ProfitLoss 478, CashAndCashEquivalents 476, CashFlowsFromUsedInOperating-
-- Activities 467, EPS 424-435, CurrentAssets/CurrentLiabilities 423-424, Liabilities 422,
-- PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities 380, ProfitLossFromOperating-
-- Activities 344, Revenue 335 and RevenueFromContractsWithCustomers 187 (Birkenstock has only the
-- latter), CostOfSales 283, Borrowings 265, LongtermBorrowings 254, GrossProfit 249. Currencies: USD
-- at 367 (many IFRS filers report in dollars), CAD 102, EUR 88, GBP 29, AUD and BRL 24, then 40 more.
-- 6-K reports carry ifrs-full facts at 198 companies: half years at 195, quarters at 154.
--
-- 1. IFRS tags (silver.metric_tags with taxonomy 'ifrs-full', after every us-gaap tag of the metric,
--    so a company that moved from US GAAP keeps its us-gaap rows where both exist). Borrowings: in
--    the latest 20-F/40-F of the 265 companies tagging Borrowings, the total equals LongtermBorrowings
--    plus CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings (37 + 11 + 10 + ...), plus
--    ShorttermBorrowings (26 + 10 ...) or plus CurrentPortionOfLongtermBorrowings (26 + 11 ...), and
--    Philips', Eni's and Honda's equal LongtermBorrowings + Shortterm + CurrentPortion: so
--    LongtermBorrowings is the noncurrent line (long_term_debt) and the current line is the first of
--    CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings, Shortterm + CurrentPortion (a 038 sum),
--    CurrentPortion, Shortterm. About 60 of the 265 tag them some other way (Novo Nordisk's Borrowings
--    is a tenth of its LongtermBorrowings): their debt reads as tagged, like any filer's. Not mapped:
--    banks' IFRS revenue (no standard net revenue line), FinanceCosts (wider than interest on debt).
--
-- 2. Currencies. silver keeps an amount in any ISO currency (silver.unit_fits: a metric in 'USD' is an
--    amount of money, in 'USD/shares' an amount per share), so a euro filer's facts reach silver at
--    all. gold.usable_facts reads each company in one currency, its reporting currency: the one most
--    of the monetary facts of its latest 10-K/10-Q/20-F/40-F/6-K are in. A 20-F's convenience
--    translation of the latest year into dollars is the minority and drops out; a company never has
--    two currencies in one series. Every US filer's latest report is in USD, so their gold is as
--    before. Computed amounts (free cash flow, net debt, cash and investments) take the company's
--    currency (gold.refresh_derived_financials), not the definition's 'USD'.
--
-- 3. Never mixed with dollars. gold.latest_reading, the one place a figure's reading is decided (035),
--    gives an amount in another currency the status 'other_currency' (reason: "reported in EUR..."),
--    and returns the unit. The screener's sort and filter value (k<i>), its peers' size bands and the
--    web's medians take only figures with no status or 'behind', so a euro figure is shown, labelled,
--    and never sorted, filtered, banded or averaged with dollars. Ratios (margins, growth, returns)
--    have no currency and compare as before. gold.figure_facts and gold.annual_per_share read the
--    company's currency too.
--
-- 4. What's annual. Gold derives a quarter only from two year-to-date figures 80-120 days apart, so an
--    annual or half-year report never makes a quarter: a 20-F filer's gold is its balance sheets at
--    each year end and, through 040's stated-year TTM, its fiscal years; a half year (170-190 days)
--    is in silver but nowhere in gold. Where a 6-K tags quarters, they are real quarters. The Query
--    API says which (`cadence`, query_api README).
--
-- Measured after it, on local copies built from hermes' rows: the 517 listed 20-F/40-F filers with a
-- payload get 335,244 silver facts and gold at 490 companies (revenue 426, net income 487, total
-- assets 484, operating cash flow 477, capex 440, net debt 292), 113 of them with real quarters since
-- 2024 (6-K XBRL), none with two currencies, no quarter over 120 days; Birkenstock's FY2025 revenue
-- EUR 2,097,429,000, its December 2025 quarter's EUR 401,901,000. Of the companies that already have
-- gold, about 200 file 20-F/40-F in another currency (CNY 120, HKD 21, CAD 19, SGD 18, JPY 11...: their
-- gold today is scraps of convenience-translated dollars) and move to their own currency.
--
-- Golden figures don't move: the five golden companies file 10-Ks in USD, and no us-gaap tag's
-- priority changes. On the 46-company replica of F8a (Citigroup, Clorox, NVIDIA, Ford, GM and the
-- companies with companyfacts gaps), every existing gold row was identical before and after this
-- migration; JBS N.V. (an IFRS filer of 10-Qs, in dollars) gained 105 rows.
--
-- Bodies: silver.refresh_financial_facts (011's, plus the taxonomy column and unit_fits);
-- silver.refresh_filing_facts and gold.usable_facts (055's, edits marked "056"); gold.refresh_derived_
-- financials (049's, edits marked "056"); gold.latest_reading (035's, plus the unit and the status;
-- DROP and CREATE: its result gains a column); gold.figure_facts (049's) and gold.annual_per_share
-- (036's), each with the taxonomy join and the currency rule. Generated by a script asserting each
-- anchor once. A later rewrite keeps all of it.
--
-- Locks on hermes: INSERTs into silver.metric_tags and silver.metric_sums (small tables, row locks);
-- DROP/CREATE of gold.latest_reading and CREATE OR REPLACE of the others (catalog only; a screener
-- query planned before the commit finishes with the old body). Gold changes on the financials-rebuild
-- job: it re-projects every stored companyfacts payload with the IFRS tags (no SEC requests).

SET LOCAL lock_timeout = '10s';

-- An amount of money in a currency (a metric whose unit is 'USD'), or an amount per share in one
-- ('USD/shares'), or exactly the metric's unit (shares, pure). Inlinable.
CREATE FUNCTION silver.unit_fits(p_metric_unit text, p_fact_unit text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT p_fact_unit = p_metric_unit
        OR (p_metric_unit = 'USD' AND p_fact_unit ~ '^[A-Z]{3}$')
        OR (p_metric_unit = 'USD/shares' AND p_fact_unit ~ '^[A-Z]{3}/shares$')
$$;

-- IFRS tags (header, 1): after each metric's us-gaap tags
INSERT INTO silver.metric_tags (metric, taxonomy, tag, priority) VALUES
    ('revenue', 'ifrs-full', 'Revenue', 9),
    ('revenue', 'ifrs-full', 'RevenueFromContractsWithCustomers', 10),
    ('cost_of_revenue', 'ifrs-full', 'CostOfSales', 4),
    ('gross_profit', 'ifrs-full', 'GrossProfit', 2),
    ('operating_income', 'ifrs-full', 'ProfitLossFromOperatingActivities', 2),
    ('pretax_income', 'ifrs-full', 'ProfitLossBeforeTax', 4),
    ('income_tax', 'ifrs-full', 'IncomeTaxExpenseContinuingOperations', 3),
    ('net_income', 'ifrs-full', 'ProfitLossAttributableToOwnersOfParent', 3),
    ('net_income', 'ifrs-full', 'ProfitLoss', 4),
    ('eps_basic', 'ifrs-full', 'BasicEarningsLossPerShare', 3),
    ('eps_diluted', 'ifrs-full', 'DilutedEarningsLossPerShare', 4),
    ('weighted_shares_basic', 'ifrs-full', 'WeightedAverageShares', 3),
    ('weighted_shares_diluted', 'ifrs-full', 'AdjustedWeightedAverageShares', 3),
    ('research_and_development', 'ifrs-full', 'ResearchAndDevelopmentExpense', 3),
    ('sga', 'ifrs-full', 'SellingGeneralAndAdministrativeExpense', 2),
    ('interest_expense', 'ifrs-full', 'InterestExpense', 5),
    ('depreciation_amortization', 'ifrs-full', 'DepreciationAndAmortisationExpense', 5),
    ('depreciation_amortization', 'ifrs-full', 'AdjustmentsForDepreciationAndAmortisationExpense', 6),
    ('stock_based_compensation', 'ifrs-full', 'AdjustmentsForSharebasedPayments', 3),
    ('stock_based_compensation', 'ifrs-full', 'ExpenseFromSharebasedPaymentTransactionsWithEmployees', 4),
    ('operating_cash_flow', 'ifrs-full', 'CashFlowsFromUsedInOperatingActivities', 3),
    ('investing_cash_flow', 'ifrs-full', 'CashFlowsFromUsedInInvestingActivities', 3),
    ('financing_cash_flow', 'ifrs-full', 'CashFlowsFromUsedInFinancingActivities', 3),
    ('change_in_cash', 'ifrs-full', 'IncreaseDecreaseInCashAndCashEquivalents', 6),
    ('capex', 'ifrs-full', 'PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities', 6),
    ('capex', 'ifrs-full',
     'PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets', 7),
    ('dividends_paid', 'ifrs-full', 'DividendsPaidToEquityHoldersOfParentClassifiedAsFinancingActivities', 4),
    ('dividends_paid', 'ifrs-full', 'DividendsPaidClassifiedAsFinancingActivities', 5),
    ('dividends_paid', 'ifrs-full', 'DividendsPaid', 6),
    ('share_buybacks', 'ifrs-full', 'PaymentsToAcquireOrRedeemEntitysShares', 3),
    ('total_assets', 'ifrs-full', 'Assets', 2),
    ('current_assets', 'ifrs-full', 'CurrentAssets', 2),
    ('total_liabilities', 'ifrs-full', 'Liabilities', 2),
    ('current_liabilities', 'ifrs-full', 'CurrentLiabilities', 2),
    ('stockholders_equity', 'ifrs-full', 'EquityAttributableToOwnersOfParent', 3),
    ('stockholders_equity', 'ifrs-full', 'Equity', 4),
    ('cash', 'ifrs-full', 'CashAndCashEquivalents', 5),
    ('cash_and_restricted_cash', 'ifrs-full', 'CashAndCashEquivalents', 4),
    ('short_term_investments', 'ifrs-full', 'CurrentInvestments', 12),
    ('receivables', 'ifrs-full', 'TradeAndOtherCurrentReceivables', 4),
    ('receivables', 'ifrs-full', 'CurrentTradeReceivables', 5),
    ('inventory', 'ifrs-full', 'Inventories', 2),
    ('accounts_payable', 'ifrs-full', 'TradeAndOtherCurrentPayablesToTradeSuppliers', 3),
    ('contract_liabilities_current', 'ifrs-full', 'CurrentContractLiabilities', 4),
    ('contract_liabilities', 'ifrs-full', 'ContractLiabilities', 7),
    ('long_term_debt', 'ifrs-full', 'LongtermBorrowings', 9),
    ('long_term_debt_current', 'ifrs-full', 'CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings', 7),
    ('long_term_debt_current', 'ifrs-full', 'CurrentPortionOfLongtermBorrowings', 8),
    ('long_term_debt_current', 'ifrs-full', 'ShorttermBorrowings', 9),
    ('deposits', 'ifrs-full', 'DepositsFromCustomers', 2),
    ('loans', 'ifrs-full', 'LoansAndAdvancesToCustomers', 3),
    ('shares_outstanding', 'ifrs-full', 'NumberOfSharesOutstanding', 2);

-- the current borrowings of a filer that tags short-term borrowings and the current portion apart
INSERT INTO silver.metric_sums (metric, priority, tags) VALUES
    ('long_term_debt_current', 7.5, '{ShorttermBorrowings,CurrentPortionOfLongtermBorrowings}');

-- 011's body: the taxonomy recorded, an amount in any currency (header, 2), never a company's own tag
CREATE OR REPLACE FUNCTION silver.refresh_financial_facts(p_ciks text[]) RETURNS integer
LANGUAGE sql AS $$
  WITH latest AS (
    SELECT DISTINCT ON (cik) cik, payload FROM bronze.company_facts
    WHERE cik = ANY(p_ciks)
    ORDER BY cik, last_checked_at DESC, fetched_at DESC
  ),
  inserted AS (
    INSERT INTO silver.financial_facts
        (cik, metric, tag, unit, period_start, period_end, value, accession_no, form, filed, frame,
         taxonomy)
    SELECT l.cik, t.metric, t.tag, u.key,
           (r->>'start')::date, (r->>'end')::date, (r->>'val')::numeric,
           r->>'accn', r->>'form', (r->>'filed')::date, r->>'frame', t.taxonomy
    FROM latest l
    CROSS JOIN silver.metric_tags t
    JOIN silver.metrics m ON m.metric = t.metric
    CROSS JOIN LATERAL jsonb_each(l.payload->'facts'->t.taxonomy->t.tag->'units') u
    CROSS JOIN LATERAL jsonb_array_elements(u.value) r
    WHERE silver.unit_fits(m.unit, u.key) AND t.company_id IS NULL
    ON CONFLICT DO NOTHING
    RETURNING 1
  )
  SELECT count(*)::int FROM inserted
$$;

-- 055's silver.refresh_filing_facts: an amount in any currency (edits marked "056")
CREATE OR REPLACE FUNCTION silver.refresh_filing_facts(p_accessions text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
    total integer := 0;
BEGIN
    DELETE FROM silver.financial_facts
    WHERE accession_no = ANY(p_accessions) AND source IN ('filing', 'filing_extra');

    WITH b AS (
        SELECT b.*, k.company_id
        FROM bronze.filing_facts b
        LEFT JOIN silver.company_ciks k ON k.cik = b.cik
        WHERE b.accession_no = ANY(p_accessions)
    ),
    x AS (
        SELECT b.accession_no, b.cik, b.company_id, b.form, b.filed, b.gap, b.superseded_at,
               split_part(f->>'c', ':', 1) AS taxonomy, split_part(f->>'c', ':', 2) AS tag,
               f->>'u' AS unit, (f->>'s')::date AS period_start,
               coalesce(f->>'e', f->>'i')::date AS period_end, (f->>'v')::numeric AS value
        FROM b CROSS JOIN LATERAL jsonb_array_elements(b.facts) f
        WHERE NOT f ? 'm'
    )
    INSERT INTO silver.financial_facts
        (cik, metric, tag, unit, period_start, period_end, value, accession_no, form, filed,
         taxonomy, source)
    SELECT x.cik, t.metric, x.tag, x.unit, x.period_start, x.period_end, x.value, x.accession_no,
           x.form, x.filed, x.taxonomy,
           CASE WHEN t.company_id IS NULL THEN 'filing' ELSE 'filing_extra' END
    FROM x
    JOIN silver.metric_tags t ON t.taxonomy = x.taxonomy AND t.tag = x.tag
    JOIN silver.metrics m ON m.metric = t.metric AND silver.unit_fits(m.unit, x.unit)  -- 056
    WHERE (t.company_id IS NULL AND x.gap AND x.superseded_at IS NULL
           AND NOT EXISTS (SELECT 1 FROM silver.financial_facts c
                           WHERE c.accession_no = x.accession_no AND c.source = 'companyfacts'))
       OR t.company_id = x.company_id
    ORDER BY x.accession_no, x.tag, t.priority
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;
    total := total + n;

    -- member sums (header): every listed member, no other, and no consolidated fact of the concept
    WITH b AS (
        SELECT b.*, k.company_id
        FROM bronze.filing_facts b
        JOIN silver.company_ciks k ON k.cik = b.cik
        WHERE b.accession_no = ANY(p_accessions)
    ),
    x AS (
        SELECT b.accession_no, b.cik, b.company_id, b.form, b.filed, f->>'c' AS concept,
               f->>'u' AS unit, (f->>'s')::date AS period_start,
               coalesce(f->>'e', f->>'i')::date AS period_end, (f->>'v')::numeric AS value,
               (f->'m') - 'srt:ConsolidationItemsAxis'::text AS m,
               f->'m'->>'srt:ConsolidationItemsAxis' AS consolidation
        FROM b CROSS JOIN LATERAL jsonb_array_elements(b.facts) f
        WHERE b.company_id IN (SELECT s.company_id FROM silver.member_sums s)
    ),
    sums AS (
        SELECT x.accession_no, x.cik, x.form, x.filed, s.tag, s.concept, x.unit, x.period_start,
               x.period_end, sum(x.value) AS value
        FROM silver.member_sums s
        JOIN x ON x.company_id = s.company_id AND x.concept = s.concept
              AND x.m ? s.axis AND (SELECT count(*) FROM jsonb_object_keys(x.m)) = 1
              -- a second dimension that says only "operating segments" (018's rule)
              AND coalesce(x.consolidation, 'us-gaap:OperatingSegmentsMember')
                  = 'us-gaap:OperatingSegmentsMember'
        GROUP BY x.accession_no, x.cik, x.form, x.filed, s.tag, s.concept, s.members, x.unit,
                 x.period_start, x.period_end
        HAVING count(DISTINCT x.m->>s.axis) = cardinality(s.members)
           AND bool_and(x.m->>s.axis = ANY(s.members))
    )
    INSERT INTO silver.financial_facts
        (cik, metric, tag, unit, period_start, period_end, value, accession_no, form, filed,
         taxonomy, source)
    SELECT s.cik, t.metric, s.tag, s.unit, s.period_start, s.period_end, s.value, s.accession_no,
           s.form, s.filed, split_part(s.concept, ':', 1), 'filing_extra'
    FROM sums s
    JOIN silver.company_ciks k ON k.cik = s.cik
    JOIN silver.metric_tags t ON t.company_id = k.company_id AND t.tag = s.tag
    JOIN silver.metrics m ON m.metric = t.metric AND silver.unit_fits(m.unit, s.unit)  -- 056
    WHERE NOT EXISTS (
        SELECT 1 FROM bronze.filing_facts b CROSS JOIN LATERAL jsonb_array_elements(b.facts) f
        WHERE b.accession_no = s.accession_no AND f->>'c' = s.concept AND NOT f ? 'm'
          AND coalesce(f->>'e', f->>'i')::date = s.period_end
          AND (f->>'s')::date IS NOT DISTINCT FROM s.period_start)
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN total + n;
END
$$;

-- 055's gold.usable_facts: each company in its reporting currency (header, 2; edits marked "056")
CREATE OR REPLACE FUNCTION gold.usable_facts(p_company_ids text[])
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
        UNION ALL
        SELECT b.company_id, f.*
        FROM borrowed b
        JOIN silver.financial_facts f ON f.accession_no = b.accession_no AND f.cik = b.cik
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

-- 049's gold.refresh_derived_financials: computed amounts in the company's currency (edits
-- marked "056")
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

    -- 056: a computed amount (free cash flow, net debt, cash and investments) is in the currency of
    -- its inputs: the company's, which every monetary row of its gold shares (gold.usable_facts)
    WITH currency AS (
        SELECT DISTINCT ON (q.company_id) q.company_id, q.unit
        FROM gold.quarterly_financials q
        WHERE q.company_id = ANY(p_company_ids) AND q.unit ~ '^[A-Z]{3}$'
        ORDER BY q.company_id, q.period_end DESC
    ),
    facts AS (
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
                         'stockholders_equity', 'total_assets', 'receivables', 'inventory',
                         -- 048: the efficiency ratio's inputs
                         'net_interest_income', 'noninterest_income', 'noninterest_expense')
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
               -- 048
               max(value) FILTER (WHERE metric = 'net_interest_income') AS net_interest_income,
               max(value) FILTER (WHERE metric = 'noninterest_income') AS noninterest_income,
               max(value) FILTER (WHERE metric = 'noninterest_expense') AS noninterest_expense,
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
            -- 049: also where there is cash and no debt, for cash_and_investments (header);
            -- net_debt reads it only with debt, so it doesn't change
            WHERE p.short_term_investments IS NULL
              AND (p.long_term_debt IS NOT NULL OR p.cash IS NOT NULL)
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
                   AND (p.room IS NULL OR p.last_investments <= p.room) AS unknown,
                   -- 049: investments missing here but reported lately: unknown, not zero
                   -- (unknown's test without the part about net debt's size)
                   p.last_investments >= 0.01 * p.total_assets
                   AND (p.room IS NULL OR p.last_investments <= p.room) AS investments_unknown
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
                                p.stockholders_equity_year_ago)),
            -- 048: a bank's cost per dollar of revenue (header)
            ('efficiency_ratio',
             CASE WHEN p.net_interest_income + p.noninterest_income > 0
                  THEN p.noninterest_expense / (p.net_interest_income + p.noninterest_income) END,
             jsonb_build_object('noninterest_expense', p.noninterest_expense,
                                'net_interest_income', p.net_interest_income,
                                'noninterest_income', p.noninterest_income)),
            -- 049: cash and short-term investments, by net debt's reading of them (header)
            ('cash_and_investments',
             CASE WHEN r.investments_unknown IS NOT TRUE
                  THEN p.cash + CASE WHEN r.in_cash THEN 0 ELSE p.investments_now END END,
             jsonb_build_object('cash', p.cash,
                                'short_term_investments',
                                CASE WHEN r.in_cash IS NOT TRUE THEN p.short_term_investments END))
        ) d(metric, value, inputs)
        JOIN gold.derived_metrics dm ON dm.metric = d.metric
        WHERE d.value IS NOT NULL
    )
    INSERT INTO gold.derived_financials
        (company_id, span, metric, period_start, period_end, value, unit, value_source,
         inputs, accession_no, form, filed, source_accessions, ingested_at)
    SELECT d.company_id, d.span, d.metric, c.period_start, d.period_end, d.value,
           CASE WHEN d.unit = 'USD' THEN coalesce(cu.unit, 'USD') ELSE d.unit END,  -- 056
           'computed', d.inputs, c.accession_no, c.form, c.filed, s.accessions, c.ingested_at
    FROM derived d
    LEFT JOIN currency cu ON cu.company_id = d.company_id  -- 056
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

-- 035's gold.latest_reading plus the unit and 'other_currency' (header, 3). Its result gains a column,
-- so DROP and CREATE; still one plain SQL SELECT, SECURITY INVOKER, inlinable (test_readings_db).
DROP FUNCTION gold.latest_reading(text, text, text, text, date);
CREATE FUNCTION gold.latest_reading(p_company_id text, p_span text, p_metric text, p_sic text,
                                    p_latest date)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date,
               status text, reason text, unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT x.period_end, x.value, x.accession_no, x.form, x.filed,
           CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'not_applicable'
                -- 056: an amount in another currency is never compared with dollars (header, 3)
                WHEN x.unit ~ '^[A-Z]{3}(/shares)?$' AND x.unit !~ '^USD'
                    THEN 'other_currency'
                WHEN gold.not_meaningful(p_metric, x.value, x.inputs, e.value) IS NOT NULL
                    THEN 'not_meaningful'
                ELSE gold.period_status(x.period_end, p_latest) END,
           CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'this is a bank: deposits are its debt and lending its operations, so '
                         'the figure has no comparable meaning'
                WHEN x.unit ~ '^[A-Z]{3}(/shares)?$' AND x.unit !~ '^USD'
                    THEN 'reported in ' || split_part(x.unit, '/', 1)
                         || ', so it isn''t sorted, filtered or averaged with US-dollar figures'
                ELSE gold.not_meaningful(p_metric, x.value, x.inputs, e.value) END,
           x.unit
    FROM (SELECT 1) one
    LEFT JOIN LATERAL (
        SELECT y.period_end, y.value, y.accession_no, y.form, y.filed, y.inputs, y.unit
        FROM (
            (SELECT q.period_end, q.value, q.accession_no, q.form, q.filed, NULL::jsonb AS inputs,
                    q.unit
             FROM gold.quarterly_financials q
             WHERE q.company_id = p_company_id AND q.metric = p_metric
               AND (p_span = 'quarter'
                    OR (p_span = 'ttm' AND EXISTS (SELECT 1 FROM silver.metrics m
                                                   WHERE m.metric = p_metric AND m.kind = 'instant')))
             ORDER BY q.period_end DESC LIMIT 1)
            UNION ALL
            (SELECT d.period_end, d.value, d.accession_no, d.form, d.filed, d.inputs, d.unit
             FROM gold.derived_financials d
             WHERE d.company_id = p_company_id AND d.span = p_span AND d.metric = p_metric
             ORDER BY d.period_end DESC LIMIT 1)
        ) y
        ORDER BY y.period_end DESC LIMIT 1
    ) x ON true
    LEFT JOIN LATERAL (
        SELECT q.value FROM gold.quarterly_financials q
        WHERE p_metric IN ('debt_to_equity', 'roe')
          AND q.company_id = p_company_id AND q.metric = 'stockholders_equity'
          AND q.period_end <= current_date
        ORDER BY q.period_end DESC LIMIT 1
    ) e ON true
$$;
GRANT EXECUTE ON FUNCTION gold.latest_reading(text, text, text, text, date) TO query_api;

-- 049's gold.figure_facts: tags joined by taxonomy and company (055), and an amount only in the
-- company's currency (header, 2): the currency of its latest monetary gold row
CREATE OR REPLACE FUNCTION gold.figure_facts(p_company_id text, p_metric text, p_period_end date)
RETURNS TABLE (tag text, taxonomy text, priority int, gold_line boolean, period_start date,
               period_end date, value numeric, unit text, accession_no text, filer_cik text,
               form text, filed date, filings int)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    SELECT DISTINCT ON (t.tag, f.period_start)
           t.tag, t.taxonomy, t.priority, t.gold_line, f.period_start, f.period_end, f.value, f.unit,
           f.accession_no, f.cik, f.form, f.filed,
           (count(*) OVER (PARTITION BY t.tag, f.period_start))::int
    FROM silver.company_ciks k
    JOIN silver.metric_tags t ON t.metric = p_metric
                             AND (t.company_id IS NULL OR t.company_id = k.company_id)
    JOIN silver.metrics m ON m.metric = t.metric
    CROSS JOIN LATERAL (
        SELECT coalesce((SELECT q.unit FROM gold.quarterly_financials q
                         WHERE q.company_id = p_company_id AND q.unit ~ '^[A-Z]{3}$'
                         ORDER BY q.period_end DESC LIMIT 1), 'USD') AS currency
    ) cu
    JOIN silver.financial_facts f ON f.cik = k.cik AND f.tag = t.tag AND f.taxonomy = t.taxonomy
                                 AND f.unit = CASE m.unit WHEN 'USD' THEN cu.currency
                                                          WHEN 'USD/shares' THEN cu.currency || '/shares'
                                                          ELSE m.unit END
                                 AND f.period_end = p_period_end
    WHERE k.company_id = p_company_id
      AND k.company_id IN (SELECT r FROM authz.readable_companies('financials') r)
      -- what gold reads (039's usable_facts): no proxy statement, no period after its filing
      AND coalesce(f.form, '') !~ '^(DEF|PRE)'
      AND f.period_end <= f.filed + 7
    ORDER BY t.tag, f.period_start, f.filed DESC, f.accession_no DESC
$fn$;
REVOKE EXECUTE ON FUNCTION gold.figure_facts(text, text, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.figure_facts(text, text, date) TO query_api;

-- 036's gold.annual_per_share: tags joined by taxonomy, per-share amounts in the company's currency
CREATE OR REPLACE FUNCTION gold.annual_per_share(p_company_id text, p_years int)
RETURNS TABLE (metric text, period_start date, period_end date, value numeric, unit text,
               source_tag text, accession_no text, filer_cik text, form text, filed date,
               ingested_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    SELECT y.metric, y.period_start, y.period_end, y.value, y.unit, y.source_tag, y.accession_no,
           y.cik, y.form, y.filed, y.ingested_at
    FROM (
        SELECT DISTINCT ON (f.metric, f.period_end)
               f.metric, f.period_start, f.period_end, f.value, f.unit, f.tag AS source_tag,
               f.accession_no, f.cik, f.form, f.filed, f.ingested_at,
               dense_rank() OVER (PARTITION BY f.metric ORDER BY f.period_end DESC) AS recency
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.metric = f.metric AND t.tag = f.tag
                                 AND t.taxonomy = f.taxonomy AND t.company_id IS NULL
        JOIN silver.metrics m ON m.metric = f.metric
        CROSS JOIN LATERAL (
            SELECT coalesce((SELECT q.unit FROM gold.quarterly_financials q
                             WHERE q.company_id = p_company_id AND q.unit ~ '^[A-Z]{3}$'
                             ORDER BY q.period_end DESC LIMIT 1), 'USD') AS currency
        ) cu
        WHERE k.company_id = p_company_id
          AND k.company_id IN (SELECT r FROM authz.readable_companies('financials') r)
          AND f.metric IN ('eps_diluted', 'eps_basic', 'weighted_shares_diluted',
                           'weighted_shares_basic')
          AND f.unit = CASE m.unit WHEN 'USD/shares' THEN cu.currency || '/shares' ELSE m.unit END
          AND f.period_type = 'annual'
          AND coalesce(f.form, '') !~ '^(DEF|PRE)'
        ORDER BY f.metric, f.period_end, t.priority, f.filed DESC, f.accession_no DESC
    ) y
    WHERE y.recency <= p_years
$fn$;
REVOKE EXECUTE ON FUNCTION gold.annual_per_share(text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gold.annual_per_share(text, int) TO query_api;
