-- Data trust, part 1: nightly checks that find wrong numbers before people do, and checks that say
-- what they mean (db/DESIGN.md "072"). Measured on hermes 2026-11-16, read-only.
--
-- 1. tag_disagreement (ops.check_financials, warning): the filing gold reads a figure from tags the
--    same line another way, and the two can't both be right. For each fact gold.best_facts believes
--    (positive), the metric's other gold tags in the same filing, unit and period:
--      total_below_part  gold's tag is a total under a fifth of a part of it filed beside it
--                        (ops.tag_relations 'part', listed only where gold reads the total first:
--                        D&A under its depreciation, receivables under accounts receivable...)
--      same_line         two names of one line 5x apart (EPS basic and basic-and-diluted, contract
--                        revenue with and without assessed tax, a us-gaap tag beside its IFRS twin)
--      scale             any two tags of the metric 1,000 or 1,000,000 times apart (within 0.5%):
--                        one figure in two scales; a revenue pair is 064's, resolved in best_facts,
--                        so no finding (Rectitude, Tigo and Plexus are none now)
--    One finding per company, metric and period end (shortest period, then widest gap); detail: both
--    tags, factor (the other tag's figure over gold's), the filing, never the values (014's rule).
--    A bare "two tags 5x apart" finds 84,000 pairs at 2,900 companies, almost all normal sub-totals
--    (Depreciation beside D&A 15,000, contract revenue beside Revenues 7,600, cash and due from banks
--    5,500): those relations aren't listed. Per filing, over all of silver, the rules find 3,780
--    company-metric-period findings at 876 companies (scale 946, same_line 476, total_below_part
--    2,362); as the check runs (best_facts' facts), 41 at 14 of 100 random companies, so about 2,900
--    at 1,000. Sample: Zura Bio's cash and restricted cash read 205,134 beside cash of 205,134,000;
--    First Northern Community Bancorp's 2024 D&A $1.057B beside $1.057M; Sempra's receivables $141M
--    under its accounts receivable $891M; Virco's cost of revenue $480K under cost of goods and
--    services $34.9M; Ultrapar's trade and other receivables 12-170x under its trade receivables
--    (2016-2024); Freshpet's DD&A 1,007x under its D&A; Kamada's current liabilities 222,000 beside
--    24,967,000; Amplify Energy's weighted shares 22,837,000 beside 22,837 (gold right). Dropped after
--    the sample: cash and due from banks as a part of cash (a bank's line can exceed its cash flow
--    statement's cash: Shinhan), and D&A "and accretion, net" as a total (accretion nets it down).
--
-- 2. Tiers that say what they mean (ops.quality_tier, the status page's groups), never hiding a
--    finding: error (errors readers could see), warning (to double-check), kept_out, coverage, info.
--      mistagged_fact            error -> info, tier kept_out: gold already leaves the figure out
--                                (634 at 262 companies)
--      series_gap, net_debt_gap  warning -> info, tier coverage: a figure missing, not a wrong one
--                                (19,534 at 3,865; 134)
--      outlier                   info (rule 'lumpy') on lines that swing by nature: financing and
--                                investing cash flow, change in cash, buybacks, dividends, income tax
--                                (5,855 of 13,244: 2,217 / 1,897 / 580 / 337 / 242 / 582). No stronger
--                                bar separates them: 465 financing spikes are 100x or more, 195 300x
--                                or more, and only 11 sit near 1,000x. Warnings still: share counts
--                                (weighted shares 230 of 328 at 100x+, mostly scale slips), earnings,
--                                cash, current debt.
--      annual_sum                info with detail restated 'year' (the year's own figure changed by a
--                                later filing) or 'quarter' (one of its quarters restated); else a
--                                warning. Of 100 random companies' 320: 144 year, 104 quarter, 72 stay.
--    ops.quality_companies gains `figures` (the company's gold quarters, TTM and computed rows at its
--    check: 13.79M in all) for findings per 1,000 figures. ops.check_financials_version() 57 -> 72:
--    the hourly catch-up re-checks every company (071), nothing by hand. The new body checks 100
--    random companies in 61.7 s against best_facts' own 59-66 s, finding exactly the stored findings
--    of every unchanged rule. Until a company is re-checked its findings keep their old severity
--    (their tier follows the check's name for kept_out and coverage).
--
-- 3. ops.quality_tiers (new view): one row per check and severity (a check can now have two), with
--    its tier, figures and per_1000. ops.quality_summary keeps its six columns (the ops exporter's;
--    the 038-041 backout replaces it) and reads the new view. ops.public_quality returns the new
--    columns (DROP and CREATE, 028's grants; an older Query API pod's shape copies named fields).
--
-- 4. Parser-output checks, per document (ops.check_sections; findings in ops.section_findings, keys
--    and counts only; stamps in ops.section_checks): invalid_key (error: a key the form doesn't have;
--    8-K items 1.01-9.01, 10-K items with 4A, 10-Q items of their Part, exhibit keys),
--    missing_core_item (warning: an original 10-K without 1, 1A, 7 or 8 unless it has a
--    cross-reference index; an original 10-Q without Part I items 1 and 2), duplicate_section
--    (warning: two keys, the same text), empty_section (info: nothing past the heading; a 10-K's
--    reserved Item 6 isn't one), no_sections (error: a stored document a day old with none). Twins of
--    services/parser filing_parser/invariants.py. section-index runs it for the filings it indexes; the
--    catch-up (kind check_sections, ops.check_sections_version() 72) every stored document once, oldest
--    first, 2,000 a batch (0.8 s; the whole corpus 24 s as one query). First counts, of 634,653 stored
--    documents: invalid_key 6,973 (10-Q 4,749: Part II items read as Part I, 8-K items quoted in Item
--    5; 8-K 1,084: "Item 7 - Regulation FD", "Item 7.0", exhibit index lines read as "Item 99"; 10-Q/A
--    644; 10-K 455; 10-K/A 41), missing_core_item 17,591 (10-Q 15,645, mostly Part I Item 1 whose
--    heading runs into "PART I" or is only a title; 10-K 1,946), empty_section 1,659 (8-K 1,639:
--    "Item 2.02." heading another item's text), no_sections 761 (8-K 468, 10-K/A 172, 10-Q/A 99),
--    duplicate_section 3.
--
-- 5. Extremes (ops.refresh_extremes, ops.extremes, ops.extremes_report for ops_reader): for each
--    screener value column (ops.extreme_columns: silver.metrics and gold.derived_metrics, the formula
--    CATALOG's), the top and bottom 20 companies as the screener sorts them (gold.latest_reading,
--    readings applied), each with what explains it: the checks with a finding on the metric or its
--    inputs in the year to its period, a quarter derived from year-to-date figures, or the company's
--    own figure a period earlier (a year for TTM) of the same sign within 3x (in_line). `review` when
--    nothing does. On hermes: 118 columns, 4,720 rows in 38 s; with checks alone 3,942 rows flagged
--    (Amazon's revenue, JPMorgan's assets), with in_line 1,036 at 633 companies: Evernorth's quarterly
--    EPS $155M and Virtuix's $32.8M (after -$0.09), VIP Play's gross margin -562% (after +21%),
--    GIVBUX's net margin +692% (after -1,205%), Amazon's TTM free cash flow -$46.9B (after +$10.6B,
--    real). A figure wrong the same way every period reads in_line (Boost Run's EPS -$51,000): the
--    report finds jumps, the checks persistent slips. Refreshed after the nightly financials-bulk run;
--    never public (it names companies).
--
-- 6. ops.check_segments (070's body, one edit): members that overlap beside a zero or a negative
--    member. NVIDIA's 19 segment findings: 6 are its two cuts (GPU and Tegra beside Compute and
--    Networking and Graphics, Jul 2019-Jan 2020) beside a $0 corporate revenue member or -$300M of
--    corporate operating costs, which made 070's search give up (037's rule: 100% and 152% more).
--    Zeros now drop out and the negative members count once in every set (the search runs over the
--    positive members for the figure less the negative ones). The other 13 are real: derived Q4
--    members of two bases (the FY2026 10-K's re-cut comparatives against earlier 10-Qs), geography
--    members missing in a year's Q4, and segment operating income without the unallocated "All
--    Other" costs. Over all 27,170 stored segment findings (the old rule reproduces every one): 3,125
--    at 611 companies go (a zero member 1,334, a negative one 1,791), 24,045 stay, none is added; 5 s
--    for every company with a finding. ops.check_segments_version() 70 -> 72: the catch-up re-checks
--    every company.
--
-- Locks on hermes (lock_timeout 3 s; re-run the hook if it times out): ALTER TABLE ADD COLUMN on
-- ops.quality_companies (no default: catalog only, ACCESS EXCLUSIVE for an instant; the Query API's
-- data notes read it), a CHECK replaced on ops.catchup (6 rows, read by the hourly jobs), CREATE OR
-- REPLACE VIEW ops.quality_summary (the ops exporter, every 5 minutes), DROP and CREATE of
-- ops.public_quality (the status route, cached a minute), CREATE OR REPLACE of ops.check_financials,
-- ops.check_segments and their versions (catalog). New tables, functions and views otherwise.
-- Nothing is rebuilt: findings change as the catch-up re-checks each company and document. Undo:
-- 057's ops.check_financials and 070's ops.check_segments with versions 57 and 70 (the backouts'
-- guards pin 072's bodies); drop what this file creates.

SET LOCAL lock_timeout = '3s';

-- 1. Tag relations, for tag_disagreement ---------------------------------------------------------

-- Two gold tags of one metric that can't both be right when far apart. 'part': `other` is a line
-- within `tag` (us-gaap's definitions), listed only where gold reads `tag` first (a better priority
-- in silver.metric_tags), so a total under a fifth of its own part is gold's figure. 'same': two
-- names of one line (either may be gold's). us-gaap's Revenues over its contract revenue tags isn't
-- here: 039 already reads Revenues under 20% of contract revenue as a part.
CREATE TABLE ops.tag_relations (
    metric   text NOT NULL,  -- no FK: backouts delete metrics (049's), and a stale row matches nothing
    tag      text NOT NULL,
    other    text NOT NULL,
    relation text NOT NULL CHECK (relation IN ('part', 'same')),
    PRIMARY KEY (metric, tag, other)
);

INSERT INTO ops.tag_relations (metric, tag, other, relation) VALUES
    ('cash', 'CashAndCashEquivalentsAtCarryingValue', 'Cash', 'part'),
    ('cash', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents', 'Cash', 'part'),
    ('cash_and_restricted_cash',
     'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations',
     'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents', 'part'),
    ('cash_and_restricted_cash',
     'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations',
     'CashAndCashEquivalentsAtCarryingValue', 'part'),
    ('cash_and_restricted_cash', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents',
     'CashAndCashEquivalentsAtCarryingValue', 'part'),
    ('long_term_debt', 'LongTermDebt', 'LongTermNotesPayable', 'part'),
    ('long_term_debt', 'LongTermDebt', 'LongTermNotesAndLoans', 'part'),
    ('long_term_debt_current', 'DebtCurrent', 'NotesPayableCurrent', 'part'),
    ('long_term_debt_current', 'CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings',
     'CurrentPortionOfLongtermBorrowings', 'part'),
    ('long_term_debt_current', 'CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings',
     'ShorttermBorrowings', 'part'),
    ('depreciation_amortization', 'DepreciationDepletionAndAmortization', 'DepreciationAndAmortization', 'part'),
    ('depreciation_amortization', 'DepreciationDepletionAndAmortization', 'Depreciation', 'part'),
    ('depreciation_amortization', 'DepreciationAndAmortization', 'Depreciation', 'part'),
    ('revenue', 'Revenue', 'RevenueFromContractsWithCustomers', 'part'),
    ('cost_of_revenue', 'CostOfRevenue', 'CostOfGoodsAndServicesSold', 'part'),
    ('cost_of_revenue', 'CostOfRevenue', 'CostOfGoodsSold', 'part'),
    ('cost_of_revenue', 'CostOfGoodsAndServicesSold', 'CostOfGoodsSold', 'part'),
    ('capex', 'PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireOtherPropertyPlantAndEquipment', 'part'),
    ('receivables', 'ReceivablesNetCurrent', 'AccountsNotesAndLoansReceivableNetCurrent', 'part'),
    ('receivables', 'ReceivablesNetCurrent', 'AccountsReceivableNetCurrent', 'part'),
    ('receivables', 'AccountsNotesAndLoansReceivableNetCurrent', 'AccountsReceivableNetCurrent', 'part'),
    ('receivables', 'TradeAndOtherCurrentReceivables', 'CurrentTradeReceivables', 'part'),
    ('accounts_payable', 'AccountsPayableCurrent', 'AccountsPayableTradeCurrent', 'part'),
    ('interest_expense', 'InterestExpense', 'InterestExpenseDebt', 'part'),
    ('interest_expense', 'InterestExpense', 'InterestExpenseOperating', 'part'),
    ('research_and_development', 'ResearchAndDevelopmentExpense',
     'ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost', 'part'),
    ('eps_basic', 'EarningsPerShareBasic', 'EarningsPerShareBasicAndDiluted', 'same'),
    ('eps_basic', 'EarningsPerShareBasic', 'BasicEarningsLossPerShare', 'same'),
    ('eps_diluted', 'EarningsPerShareDiluted', 'EarningsPerShareBasicAndDiluted', 'same'),
    ('eps_diluted', 'EarningsPerShareDiluted', 'DilutedEarningsLossPerShare', 'same'),
    ('weighted_shares_basic', 'WeightedAverageNumberOfSharesOutstandingBasic',
     'WeightedAverageNumberOfShareOutstandingBasicAndDiluted', 'same'),
    ('weighted_shares_basic', 'WeightedAverageNumberOfSharesOutstandingBasic', 'WeightedAverageShares', 'same'),
    ('weighted_shares_diluted', 'WeightedAverageNumberOfDilutedSharesOutstanding',
     'WeightedAverageNumberOfShareOutstandingBasicAndDiluted', 'same'),
    ('weighted_shares_diluted', 'WeightedAverageNumberOfDilutedSharesOutstanding',
     'AdjustedWeightedAverageShares', 'same'),
    ('revenue', 'RevenueFromContractWithCustomerExcludingAssessedTax',
     'RevenueFromContractWithCustomerIncludingAssessedTax', 'same'),
    ('current_assets', 'AssetsCurrent', 'CurrentAssets', 'same'),
    ('current_liabilities', 'LiabilitiesCurrent', 'CurrentLiabilities', 'same'),
    ('stockholders_equity', 'StockholdersEquity', 'EquityAttributableToOwnersOfParent', 'same'),
    ('net_income', 'NetIncomeLoss', 'ProfitLossAttributableToOwnersOfParent', 'same'),
    ('operating_cash_flow', 'NetCashProvidedByUsedInOperatingActivities',
     'CashFlowsFromUsedInOperatingActivities', 'same'),
    ('investing_cash_flow', 'NetCashProvidedByUsedInInvestingActivities',
     'CashFlowsFromUsedInInvestingActivities', 'same'),
    ('financing_cash_flow', 'NetCashProvidedByUsedInFinancingActivities',
     'CashFlowsFromUsedInFinancingActivities', 'same'),
    ('inventory', 'InventoryNet', 'Inventories', 'same');

-- 2. Tiers, figures, the check -------------------------------------------------------------------

-- The status page's groups: what a finding means for a reader. kept_out and coverage go by the
-- check whatever its stored severity (a company the catch-up hasn't re-checked yet).
CREATE FUNCTION ops.quality_tier(p_check text, p_severity text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN p_check = 'mistagged_fact' THEN 'kept_out'
                WHEN p_check IN ('series_gap', 'net_debt_gap') THEN 'coverage'
                WHEN p_severity = 'error' THEN 'error'
                WHEN p_severity = 'warning' THEN 'warning'
                ELSE 'info' END
$$;

ALTER TABLE ops.quality_companies ADD COLUMN figures integer;  -- 072: gold rows at the check

-- 057's body with the edits marked "072" (header, 1 and 2).
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
    -- 072: what gold believes, once: the annual comparison and the tag check read it
    best AS MATERIALIZED (
        SELECT * FROM gold.best_facts(p_company_ids)
    ),
    annual AS (  -- each reported fiscal year, picked as gold picks its facts
        SELECT b.company_id, b.metric, b.period_start, b.period_end, b.value, b.accession_no,
               b.tag, b.unit, b.filed
        FROM best b
        WHERE b.period_type = 'annual' AND b.additive
    ),
    -- 072: the metric's other gold tags in the believed fact's own filing and period, positive
    -- (interest expense and debt without their sign, as gold reads them)
    tag_facts AS MATERIALIZED (
        SELECT k.company_id, t.metric, f.tag, f.unit, f.period_start, f.period_end,
               f.accession_no,
               CASE WHEN t.metric IN ('interest_expense', 'long_term_debt',
                                      'long_term_debt_current')
                    THEN abs(f.value) ELSE f.value END AS value
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.taxonomy = f.taxonomy AND t.tag = f.tag AND t.gold_line
                                 AND (t.company_id IS NULL OR t.company_id = k.company_id)
        WHERE k.company_id = ANY(p_company_ids) AND f.value <> 0
    ),
    -- 072: two tags of one line in one filing and period that can't both be right: the figure gold
    -- reads under a fifth of a part of it (ops.tag_relations 'part'), two names of one line 5x
    -- apart ('same'), or any two 1,000 or 1,000,000 times apart (one figure in two scales; revenue's
    -- 064 resolves in best_facts, so a revenue pair that far apart is no finding at all). One per
    -- company, metric and period end: the shortest period, then the largest gap.
    disagree AS (
        SELECT DISTINCT ON (b.company_id, b.metric, b.period_end)
               b.company_id, b.metric, b.period_start, b.period_end, b.tag, b.accession_no,
               b.form, b.filed, o.tag AS other_tag, o.value / b.value AS factor,
               CASE WHEN x.scale THEN 'scale'
                    WHEN r.relation = 'same' THEN 'same_line'
                    ELSE 'total_below_part' END AS rule
        FROM best b
        JOIN tag_facts o ON o.company_id = b.company_id AND o.metric = b.metric
                        AND o.accession_no = b.accession_no AND o.unit = b.unit
                        AND o.period_start IS NOT DISTINCT FROM b.period_start
                        AND o.period_end = b.period_end AND o.tag <> b.tag
        LEFT JOIN ops.tag_relations r
          ON r.metric = b.metric
         AND ((r.tag = b.tag AND r.other = o.tag)
              OR (r.relation = 'same' AND r.tag = o.tag AND r.other = b.tag))
        CROSS JOIN LATERAL (
            SELECT greatest(o.value, b.value) / least(o.value, b.value) AS gap
        ) g
        CROSS JOIN LATERAL (
            SELECT abs(g.gap - 1000) <= 5 OR abs(g.gap - 1000000) <= 5000 AS scale
        ) x
        WHERE b.value > 0 AND o.value > 0
          AND CASE WHEN x.scale THEN b.metric <> 'revenue'
                   ELSE (r.relation = 'same' AND g.gap > 5)
                        OR (r.relation = 'part' AND r.tag = b.tag AND b.value * 5 < o.value) END
        ORDER BY b.company_id, b.metric, b.period_end,
                 b.period_end - coalesce(b.period_start, b.period_end), g.gap DESC, o.tag
    ),
    -- 072: quarters whose first filing (same tag, unit, dates) said something else; the
    -- restatement finding, and what explains a year whose quarters don't add up
    restated AS MATERIALIZED (
        SELECT g.company_id, g.metric, g.period_start, g.period_end, g.accession_no, g.filed,
               o.accession_no AS original_accession, o.filed AS original_filed,
               round((g.value - o.value) / abs(o.value), 4) AS change
        FROM gold.quarterly_financials g
        CROSS JOIN LATERAL (
            -- 050: the metrics gold reads without their sign are compared without it
            SELECT f.accession_no, f.filed,
                   CASE WHEN g.metric IN ('interest_expense', 'long_term_debt',
                                          'long_term_debt_current')
                        THEN abs(f.value) ELSE f.value END AS value
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
    debt_scale AS (  -- a debt fact over five times the same date's total liabilities and total assets
        SELECT DISTINCT ON (k.company_id, t.metric, f.period_end)
               k.company_id, t.metric, f.period_end, f.tag, f.accession_no, f.form, f.filed
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.tag = f.tag AND t.metric IN ('long_term_debt', 'long_term_debt_current')
        JOIN LATERAL (
            SELECT x.value FROM silver.financial_facts x
            WHERE x.cik = f.cik AND x.metric = 'total_liabilities' AND x.tag = 'Liabilities'
              AND x.period_end = f.period_end AND x.period_start IS NULL
              AND x.unit = f.unit  -- 057: in the debt's own currency (header, 4)
            ORDER BY x.filed DESC, x.accession_no DESC LIMIT 1
        ) l ON f.value > l.value * 5
        JOIN LATERAL (
            SELECT x.value FROM silver.financial_facts x
            WHERE x.cik = f.cik AND x.metric = 'total_assets' AND x.tag = 'Assets'
              AND x.period_end = f.period_end AND x.period_start IS NULL
              AND x.unit = f.unit  -- 057
            ORDER BY x.filed DESC, x.accession_no DESC LIMIT 1
        ) a ON f.value > a.value * 5
        WHERE k.company_id = ANY(p_company_ids) AND f.period_start IS NULL
        ORDER BY k.company_id, t.metric, f.period_end, f.filed DESC, f.accession_no DESC
    ),
    -- 050: debt parts adding up to 1,000 or 1,000,000 times the same filing's total for the date
    -- (gold.usable_facts leaves them out); one per company and date, its latest filing
    parts_scale AS (
        SELECT DISTINCT ON (k.company_id, p.period_end)
               k.company_id, p.period_end, p.accession_no, p.form, p.filed,
               round(p.parts / p.total) AS factor
        FROM silver.company_ciks k
        CROSS JOIN LATERAL (
            SELECT f.period_end, f.accession_no, max(f.form) AS form, max(f.filed) AS filed,
                   sum(abs(f.value)) FILTER (WHERE f.tag IN ('LongTermDebtCurrent',
                                                             'LongTermDebtNoncurrent')) AS parts,
                   max(abs(f.value)) FILTER (WHERE f.tag = 'LongTermDebt') AS total
            FROM silver.financial_facts f
            WHERE f.cik = k.cik AND f.metric IN ('long_term_debt', 'long_term_debt_current')
              AND f.period_start IS NULL
              AND f.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent', 'LongTermDebt')
            GROUP BY f.period_end, f.accession_no, f.unit  -- 057: parts and total in one unit
        ) p
        WHERE k.company_id = ANY(p_company_ids) AND p.total > 0
          AND (abs(p.parts / p.total - 1000) <= 5 OR abs(p.parts / p.total - 1000000) <= 5000)
        ORDER BY k.company_id, p.period_end, p.filed DESC, p.accession_no DESC
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
            'weighted_shares_diluted', 'long_term_debt', 'long_term_debt_current')
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
        -- 072: a year whose own figure a later filing changed, or with a restated quarter, is
        -- info and says which (restated: year | quarter); otherwise a warning as before
        SELECT d.company_id, 'annual_sum', d.metric, d.period_end,
               CASE WHEN y.restated IS NOT NULL THEN 'info' ELSE 'warning' END,
               jsonb_build_object('deviation', round((d.value - a.value) / abs(a.value), 4),
                                  'annual_accession', a.accession_no,
                                  'quarter_accessions', d.source_accessions)
               || CASE WHEN y.restated IS NOT NULL
                       THEN jsonb_build_object('restated', y.restated) ELSE '{}'::jsonb END
        FROM gold.derived_financials d
        JOIN annual a ON a.company_id = d.company_id AND a.metric = d.metric
                     AND a.period_end = d.period_end
                     AND abs(a.period_start - d.period_start) <= 7
        CROSS JOIN LATERAL (
            SELECT CASE
                WHEN EXISTS (
                    SELECT 1 FROM (
                        SELECT f.filed,
                               CASE WHEN a.metric IN ('interest_expense', 'long_term_debt',
                                                      'long_term_debt_current')
                                    THEN abs(f.value) ELSE f.value END AS value
                        FROM silver.company_ciks k
                        JOIN silver.financial_facts f ON f.cik = k.cik
                        WHERE k.company_id = a.company_id AND f.tag = a.tag
                          AND f.unit = a.unit AND f.period_start = a.period_start
                          AND f.period_end = a.period_end
                        ORDER BY f.filed, f.accession_no
                        LIMIT 1) o
                    WHERE o.filed < a.filed AND o.value <> 0
                      AND abs(a.value - o.value) > 0.005 * abs(o.value)) THEN 'year'
                WHEN EXISTS (SELECT 1 FROM restated r
                             WHERE r.company_id = d.company_id AND r.metric = d.metric
                               AND r.period_end > d.period_start
                               AND r.period_end <= d.period_end) THEN 'quarter'
                END AS restated
        ) y
        WHERE d.company_id = ANY(p_company_ids) AND d.span = 'ttm' AND d.value_source = 'ttm'
          AND a.value <> 0
          AND abs(d.value - a.value) > 0.01 * abs(a.value)
        UNION ALL
        SELECT company_id, 'series_gap', metric, period_end, 'info',  -- 072: coverage
               jsonb_build_object('missing_from', period_start - gap + 1,
                                  'missing_to', period_start - 1)
        FROM runs
        WHERE additive AND gap > 7 AND contiguous_before = 3
        UNION ALL
        -- 050: a negative stock-based compensation quarter is a benefit as filed (forfeitures
        -- reversing earlier expense, liability awards remeasured), not a slip, unless another filing
        -- reports the same quarter positive: info, rule 'benefit'
        SELECT company_id, 'sign_flip', metric, period_end, s.severity,
               jsonb_build_object('accession_no', accession_no)
               || CASE WHEN s.severity = 'info' THEN '{"rule": "benefit"}'::jsonb
                       ELSE '{}'::jsonb END
        FROM q
        CROSS JOIN LATERAL (
            SELECT CASE WHEN q.metric = 'stock_based_compensation' AND q.value < 0
                             AND NOT EXISTS (
                                 SELECT 1 FROM silver.company_ciks k
                                 JOIN silver.financial_facts f ON f.cik = k.cik
                                 WHERE k.company_id = q.company_id
                                   AND f.metric = 'stock_based_compensation'
                                   AND f.period_end = q.period_end
                                   AND f.period_start = q.period_start AND f.value > 0)
                        THEN 'info' ELSE 'warning' END AS severity
        ) s
        WHERE metric IN ('cost_of_revenue', 'research_and_development', 'sga',
                         'interest_expense', 'capex', 'depreciation_amortization',
                         'stock_based_compensation', 'dividends_paid', 'share_buybacks')
          AND value <> 0 AND prev_value <> 0 AND next_value <> 0
          AND sign(value) <> sign(prev_value) AND sign(prev_value) = sign(next_value)
        UNION ALL
        -- 072: a spike on a line that swings by nature (cash flow subtotals, the change in cash,
        -- buybacks, dividends, income tax) is info, rule 'lumpy'; the test is 041's
        SELECT company_id, 'outlier', metric, period_end,
               CASE WHEN metric IN ('financing_cash_flow', 'investing_cash_flow', 'change_in_cash',
                                    'share_buybacks', 'dividends_paid', 'income_tax')
                    THEN 'info' ELSE 'warning' END,
               jsonb_build_object('vs_neighbours',
                                  round(value / greatest(abs(prev_value), abs(next_value)), 1),
                                  'accession_no', accession_no)
               || CASE WHEN metric IN ('financing_cash_flow', 'investing_cash_flow',
                                       'change_in_cash', 'share_buybacks', 'dividends_paid',
                                       'income_tax')
                       THEN '{"rule": "lumpy"}'::jsonb ELSE '{}'::jsonb END
        FROM q
        WHERE prev_value <> 0 AND next_value <> 0 AND sign(prev_value) = sign(next_value)
          AND greatest(abs(prev_value), abs(next_value))
              <= 2 * least(abs(prev_value), abs(next_value))
          AND abs(value) >= 10 * greatest(abs(prev_value), abs(next_value))
        UNION ALL
        -- restatement: the period's first filing (same tag, unit, dates) said something else
        SELECT company_id, 'restatement', metric, period_end, 'info',  -- 072: from `restated`
               jsonb_build_object('original_accession', original_accession,
                                  'original_filed', original_filed,
                                  'restated_accession', accession_no,
                                  'restated_filed', filed,
                                  'change', change)
        FROM restated
        UNION ALL
        SELECT company_id, 'mistagged_fact', metric, period_end, 'info',  -- 072: kept out
               jsonb_build_object('rule', 'period_after_filing', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'tag', tag, 'periods', periods)
        FROM mistagged
        UNION ALL
        SELECT company_id, 'mistagged_fact', metric, period_end, 'info',  -- 072: kept out
               jsonb_build_object('rule', 'debt_above_liabilities', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'tag', tag)
        FROM debt_scale
        UNION ALL
        SELECT company_id, 'mistagged_fact', 'long_term_debt_current', period_end, 'info',
               jsonb_build_object('rule', 'debt_parts_scale', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'factor', factor)
        FROM parts_scale
        UNION ALL
        SELECT company_id, 'series_gap', metric, period_end, 'info',  -- 072: coverage
               jsonb_build_object('missing_from', period_start - gap + 1,
                                  'missing_to', period_start - 1, 'repeated', years)
        FROM repeated_gap
        WHERE years >= 3
        UNION ALL
        SELECT company_id, 'net_debt_gap', 'net_debt', period_end, 'info',  -- 072: coverage
               jsonb_build_object('investments_end', investments_end)
        FROM net_debt_gap
        UNION ALL
        -- 072: the tags, the factor (the other tag's figure over gold's) and the filing; never the
        -- values (014's rule for detail)
        SELECT company_id, 'tag_disagreement', metric, period_end, 'warning',
               jsonb_build_object('rule', rule, 'tag', tag, 'other_tag', other_tag,
                                  'factor', round(factor, 6)::float8, 'accession_no', accession_no,
                                  'form', form, 'filed', filed, 'period_start', period_start)
        FROM disagree
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

    -- 072: and the gold figures checked (quarters, TTM and computed), for findings per 1,000
    INSERT INTO ops.quality_companies (company_id, run_id, checked_at, errors, warnings, figures)
    SELECT c.company_id, run, now(),
           count(f.*) FILTER (WHERE f.severity = 'error'),
           count(f.*) FILTER (WHERE f.severity = 'warning'),
           (SELECT count(*) FROM gold.quarterly_financials g WHERE g.company_id = c.company_id)
           + (SELECT count(*) FROM gold.derived_financials g WHERE g.company_id = c.company_id)
    FROM silver.companies c
    LEFT JOIN ops.quality_findings f ON f.company_id = c.company_id
    WHERE c.company_id = ANY(p_company_ids)
    GROUP BY c.company_id
    ON CONFLICT (company_id) DO UPDATE SET run_id = EXCLUDED.run_id,
        checked_at = EXCLUDED.checked_at, errors = EXCLUDED.errors,
        warnings = EXCLUDED.warnings, figures = EXCLUDED.figures;

    RETURN n;
END
$$;

-- 072: the hourly catch-up re-checks every company (071).
CREATE OR REPLACE FUNCTION ops.check_financials_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 72';

-- 3. The summary and the public page's read ------------------------------------------------------

-- One row per check and severity (a check can now have two; every expected pair listed even with
-- no finding), its tier, and findings per 1,000 gold figures of the companies checked with a count
-- (figures: 072's column, NULL until a company's first check at 72). A view of its own beside
-- ops.quality_summary, whose six columns stay as they are: the 038-041 backout replaces that view.
CREATE VIEW ops.quality_tiers AS
WITH found AS (
    SELECT f.check_name, f.severity, count(*) AS findings,
           count(DISTINCT f.company_id) AS companies,
           count(*) FILTER (WHERE q.figures IS NOT NULL) AS counted
    FROM ops.quality_findings f
    LEFT JOIN ops.quality_companies q ON q.company_id = f.company_id
    GROUP BY f.check_name, f.severity
),
checks AS (
    SELECT check_name, severity
    FROM (VALUES ('impossible_value', 'error'), ('overlapping_quarters', 'error'),
                 ('tag_disagreement', 'warning'), ('outlier', 'warning'), ('sign_flip', 'warning'),
                 ('annual_sum', 'warning'), ('calendar_collision', 'warning'),
                 ('segment_sum_business_segment', 'warning'), ('segment_sum_geography', 'warning'),
                 ('segment_sum_product_service', 'warning'),
                 ('mistagged_fact', 'info'), ('series_gap', 'info'), ('net_debt_gap', 'info'),
                 ('outlier', 'info'), ('sign_flip', 'info'), ('annual_sum', 'info'),
                 ('restatement', 'info')) v(check_name, severity)
    UNION
    SELECT check_name, severity FROM found
),
figures AS (
    SELECT sum(figures) AS figures FROM ops.quality_companies
)
SELECT c.check_name, c.severity,
       coalesce(f.findings, 0) AS findings,
       coalesce(f.companies, 0) AS companies,
       (SELECT count(*) FROM ops.quality_companies) AS companies_checked,
       (SELECT max(checked_at) FROM ops.quality_runs) AS last_checked_at,
       ops.quality_tier(c.check_name, c.severity) AS tier,
       g.figures,
       round(1000.0 * coalesce(f.counted, 0) / nullif(g.figures, 0), 3) AS per_1000
FROM checks c
LEFT JOIN found f ON f.check_name = c.check_name AND f.severity = c.severity
CROSS JOIN figures g;

GRANT SELECT ON ops.quality_tiers TO ops_reader;

-- 041's summary, its columns unchanged (the ops exporter's), one row per check and severity: a
-- check's findings of two severities are labelled each with its own.
CREATE OR REPLACE VIEW ops.quality_summary AS
SELECT check_name, severity, findings, companies, companies_checked, last_checked_at
FROM ops.quality_tiers;

-- 028's function with the new columns, in the page's order (tier, then check).
DROP FUNCTION ops.public_quality();
CREATE FUNCTION ops.public_quality()
RETURNS TABLE (check_name text, severity text, findings bigint, companies bigint,
               companies_checked bigint, last_checked_at timestamptz, tier text, figures bigint,
               per_1000 numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
    SELECT s.check_name, s.severity, s.findings, s.companies, s.companies_checked,
           s.last_checked_at, s.tier, s.figures, s.per_1000
    FROM ops.quality_tiers s
    ORDER BY array_position(ARRAY['error', 'warning', 'kept_out', 'coverage', 'info'], s.tier),
             s.check_name, s.severity
$$;
REVOKE ALL ON FUNCTION ops.public_quality() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.public_quality() TO query_api;

-- 4. Parser-output checks ------------------------------------------------------------------------

-- The checks' version: bump it with a replace of ops.check_sections (or a rule below), and the
-- catch-up re-checks every stored document.
CREATE FUNCTION ops.check_sections_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 72';

-- Whether a section key is one the form has. Its twin is services/parser filing_parser/invariants.py
-- (KEY_PATTERNS), the parser tests' invariant: test_invariants.py checks the patterns are equal.
CREATE FUNCTION ops.section_key_valid(p_form text, p_key text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN p_key ~ '^EX-' THEN p_key ~ '^EX-[0-9]{1,3}(\.[0-9]{1,3})?[A-Z]?$'
        WHEN p_form ~ '^8-K' THEN p_key ~ '^(1\.0[1-5]|2\.0[1-6]|3\.0[1-3]|4\.0[12]|5\.0[1-8]|6\.(0[1-9]|10)|7\.01|8\.01|9\.01)$'
        WHEN p_form ~ '^10-K' THEN p_key ~ '^(1|1A|1B|1C|2|3|4|4A|5|6|7|7A|8|9|9A|9B|9C|10|11|12|13|14|15|16)$'
        WHEN p_form ~ '^10-Q' THEN p_key ~ '^(I-[1-4]|II-(1|1A|2|3|4|5|6))$'
        ELSE true END
$$;

-- The items an original 10-K or 10-Q always has (an amendment can carry only what it amends).
CREATE FUNCTION ops.core_section_keys(p_form text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE p_form WHEN '10-K' THEN ARRAY['1', '1A', '7', '8']
                       WHEN '10-Q' THEN ARRAY['I-1', 'I-2'] END
$$;

-- Each document's latest check: the version, when, its sections and findings. No FK to bronze (a
-- table with one joins pg-backup's SLIM_EXCLUDE_DATA).
CREATE TABLE ops.section_checks (
    accession_no text        PRIMARY KEY,
    form         text        NOT NULL,
    version      integer     NOT NULL,
    checked_at   timestamptz NOT NULL,
    sections     integer     NOT NULL,
    findings     integer     NOT NULL
);

-- Findings of each document's latest check. detail holds section keys and counts, never text.
CREATE TABLE ops.section_findings (
    accession_no text  NOT NULL,
    check_name   text  NOT NULL,
    severity     text  NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    detail       jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (accession_no, check_name)
);

-- Check these documents' sections (the rows gold serves: one parser's per key, rust's first) and
-- replace their findings and stamps (header, 4). Returns the number of findings.
CREATE FUNCTION ops.check_sections(p_accessions text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
    v integer := ops.check_sections_version();
BEGIN
    DELETE FROM ops.section_findings WHERE accession_no = ANY(p_accessions);

    WITH docs AS MATERIALIZED (
        SELECT d.accession_no, d.form
        FROM bronze.filing_documents d
        WHERE d.accession_no = ANY(p_accessions)
    ),
    s AS MATERIALIZED (
        SELECT DISTINCT ON (x.accession_no, x.section_key)
               x.accession_no, d.form, x.section_key, x.parser, octet_length(x.text) AS bytes,
               CASE WHEN octet_length(x.text) <= 80 THEN x.text END AS short_text
        FROM silver.filing_sections x
        JOIN docs d ON d.accession_no = x.accession_no
        ORDER BY x.accession_no, x.section_key, x.parser = 'rust' DESC, x.parsed_at DESC
    ),
    keys AS MATERIALIZED (  -- set-based per document: no lookup into s per document and key
        SELECT accession_no, array_agg(section_key) AS keys FROM s GROUP BY accession_no
    ),
    findings AS (
        SELECT accession_no, 'invalid_key' AS check_name, 'error' AS severity,
               jsonb_build_object('keys', (array_agg(section_key ORDER BY section_key))[1:20],
                                  'count', count(*)) AS detail
        FROM s
        WHERE NOT ops.section_key_valid(form, section_key)
        GROUP BY accession_no
        UNION ALL
        SELECT d.accession_no, 'missing_core_item', 'warning',
               jsonb_build_object('missing', m.missing)
        FROM docs d
        JOIN keys k ON k.accession_no = d.accession_no  -- none at all: no_sections says it
        CROSS JOIN LATERAL (
            SELECT array_agg(c.k ORDER BY c.o) AS missing
            FROM unnest(ops.core_section_keys(d.form)) WITH ORDINALITY c(k, o)
            WHERE c.k <> ALL(k.keys)
        ) m
        WHERE m.missing IS NOT NULL
          -- a 10-K laid out by a cross-reference index (GE, Intel) numbers no items
          AND NOT (d.form = '10-K' AND EXISTS (
                       SELECT 1 FROM silver.filing_sections x
                       WHERE x.accession_no = d.accession_no
                         AND x.text ~* 'cross[- ]?reference (index|table)'))
        UNION ALL
        SELECT accession_no, 'empty_section', 'info',
               jsonb_build_object('keys', (array_agg(section_key ORDER BY section_key))[1:20],
                                  'count', count(*))
        FROM s
        WHERE (bytes = 0
               OR short_text ~* '^(item|part)\s+[0-9ivx]{1,4}[a-z]?(\.\s?[0-9]{1,2})?[[:space:].:\-–—]*$')
          AND NOT (form ~ '^10-K' AND section_key = '6')  -- removed in 2021: "Item 6. [Reserved]"
        GROUP BY accession_no
        UNION ALL
        SELECT accession_no, 'duplicate_section', 'warning',
               jsonb_build_object('keys', (array_agg(k ORDER BY k))[1:20])
        FROM (  -- the same text under two keys: hashed only where two sections' sizes are equal
            SELECT DISTINCT x.accession_no, unnest(array_agg(x.section_key)) AS k
            FROM (SELECT accession_no, bytes FROM s WHERE bytes > 200
                  GROUP BY accession_no, bytes HAVING count(*) > 1) g
            JOIN s a ON a.accession_no = g.accession_no AND a.bytes = g.bytes
            JOIN silver.filing_sections x ON x.accession_no = a.accession_no
                                         AND x.section_key = a.section_key AND x.parser = a.parser
            GROUP BY x.accession_no, md5(x.text)
            HAVING count(*) > 1
        ) d
        GROUP BY accession_no
        UNION ALL
        SELECT d.accession_no, 'no_sections', 'error', '{}'::jsonb
        FROM docs d
        LEFT JOIN keys k ON k.accession_no = d.accession_no
        WHERE k.accession_no IS NULL
    )
    INSERT INTO ops.section_findings (accession_no, check_name, severity, detail)
    SELECT accession_no, check_name, severity, detail FROM findings
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;

    INSERT INTO ops.section_checks AS c (accession_no, form, version, checked_at, sections,
                                         findings)
    SELECT d.accession_no, d.form, v, now(),
           (SELECT count(DISTINCT x.section_key) FROM silver.filing_sections x
            WHERE x.accession_no = d.accession_no),
           (SELECT count(*) FROM ops.section_findings f WHERE f.accession_no = d.accession_no)
    FROM bronze.filing_documents d
    WHERE d.accession_no = ANY(p_accessions)
    ON CONFLICT (accession_no) DO UPDATE
    SET form = EXCLUDED.form, version = EXCLUDED.version, checked_at = EXCLUDED.checked_at,
        sections = EXCLUDED.sections, findings = EXCLUDED.findings;

    RETURN n;
END
$$;

-- Stored documents of mapped companies (gold serves no other) that no check of this version has
-- seen, oldest filing first. A document under a day old waits for its parse: section-index checks
-- what it indexes.
CREATE FUNCTION ops.section_check_backlog(p_version integer)
RETURNS TABLE (accession_no text, filed date)
LANGUAGE sql STABLE AS $$
    SELECT d.accession_no, d.filed
    FROM bronze.filing_documents d
    LEFT JOIN ops.section_checks c ON c.accession_no = d.accession_no
    WHERE (c.accession_no IS NULL OR c.version < p_version)
      AND d.fetched_at < now() - interval '1 day'
      AND EXISTS (SELECT 1 FROM silver.company_ciks k WHERE k.cik = d.cik)
$$;

-- Per check: documents with a finding, documents checked, the latest check. Counts only.
CREATE VIEW ops.parser_quality_summary AS
SELECT ch.check_name, ch.severity, count(f.accession_no) AS documents,
       (SELECT count(*) FROM ops.section_checks) AS documents_checked,
       (SELECT max(checked_at) FROM ops.section_checks) AS last_checked_at
FROM (VALUES ('invalid_key', 'error'), ('no_sections', 'error'),
             ('missing_core_item', 'warning'), ('duplicate_section', 'warning'),
             ('empty_section', 'info')) ch(check_name, severity)
LEFT JOIN ops.section_findings f ON f.check_name = ch.check_name
GROUP BY ch.check_name, ch.severity;

GRANT SELECT ON ops.parser_quality_summary TO ops_reader;

-- For the public status page (028's rule: aggregates only).
CREATE FUNCTION ops.public_parser_quality()
RETURNS TABLE (check_name text, severity text, documents bigint, documents_checked bigint,
               last_checked_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
    SELECT s.check_name, s.severity, s.documents, s.documents_checked, s.last_checked_at
    FROM ops.parser_quality_summary s
    ORDER BY array_position(ARRAY['error', 'warning', 'info'], s.severity), s.check_name
$$;
REVOKE ALL ON FUNCTION ops.public_parser_quality() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.public_parser_quality() TO query_api;

-- The catch-up's kind (071): every stored document checked once per version.
ALTER TABLE ops.catchup DROP CONSTRAINT catchup_kind_check;
ALTER TABLE ops.catchup ADD CONSTRAINT catchup_kind_check
    CHECK (kind IN ('parser', 'passages', 'diffs', 'gold', 'check_financials', 'check_segments',
                    'check_sections'));
INSERT INTO ops.catchup (kind, paused, baseline) VALUES ('check_sections', false, NULL);

-- 5. Extremes ------------------------------------------------------------------------------------

-- The screener's value columns: query_api's formula CATALOG, which mirrors silver.metrics (quarter;
-- TTM for additive durations and balances) and gold.derived_metrics' spans. test_extremes_db.py
-- checks the two lists are equal.
CREATE FUNCTION ops.extreme_columns()
RETURNS TABLE (column_key text, metric text, span text)
LANGUAGE sql STABLE AS $$
    SELECT m.metric || CASE s.span WHEN 'quarter' THEN '_q' ELSE '_ttm' END, m.metric, s.span
    FROM silver.metrics m
    CROSS JOIN LATERAL unnest(CASE WHEN m.kind = 'instant' OR m.additive
                                   THEN ARRAY['quarter', 'ttm'] ELSE ARRAY['quarter'] END) s(span)
    UNION ALL
    SELECT d.metric || CASE s.span WHEN 'quarter' THEN '_q' ELSE '_ttm' END, d.metric, s.span
    FROM gold.derived_metrics d
    CROSS JOIN LATERAL unnest(d.spans) s(span)
$$;

-- The latest refresh: per column, side and rank, the company and its figure as the screener reads it,
-- what explains it (the checks with a finding on the metric or its inputs in the year to the figure's
-- period; derived_quarter for a quarter derived from year-to-date figures; in_line when the company's
-- figure a period earlier, a year for TTM, has its sign and is within 3x: Amazon's revenue is large,
-- not wrong), and review when nothing does. A figure wrong the same way every period reads in_line:
-- the report finds jumps, the checks persistent slips.
CREATE TABLE ops.extremes (
    column_key   text        NOT NULL,
    metric       text        NOT NULL,
    span         text        NOT NULL,
    side         text        NOT NULL CHECK (side IN ('top', 'bottom')),
    rank         integer     NOT NULL,
    company_id   text        NOT NULL,
    value        numeric     NOT NULL,
    unit         text,
    period_end   date,
    accession_no text,
    status       text,                   -- the screener's reading: NULL or 'behind'
    prev_value   numeric,                -- the column's figure a period earlier (TTM: a year)
    findings     text[]      NOT NULL DEFAULT '{}',
    review       boolean     NOT NULL,
    refreshed_at timestamptz NOT NULL,
    PRIMARY KEY (column_key, side, rank)
);

-- Rebuild ops.extremes: the top and bottom p_n companies of every column, as the screener sorts
-- them (gold.latest_reading over the companies with gold financials; a value with a reading other
-- than 'behind' doesn't sort). Returns the rows flagged for review.
CREATE FUNCTION ops.refresh_extremes(p_n integer DEFAULT 20) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    DELETE FROM ops.extremes;
    WITH cols AS MATERIALIZED (
        SELECT * FROM ops.extreme_columns()
    ),
    cos AS MATERIALIZED (
        SELECT c.company_id, c.sic, p.latest
        FROM silver.companies c
        LEFT JOIN LATERAL gold.latest_period(c.company_id) p ON true
        WHERE EXISTS (SELECT 1 FROM gold.financials_coverage v WHERE v.company_id = c.company_id)
    ),
    vals AS MATERIALIZED (
        SELECT l.column_key, l.metric, l.span, c.company_id, x.value, x.unit, x.period_end,
               x.accession_no, x.status
        FROM cols l
        CROSS JOIN cos c
        CROSS JOIN LATERAL gold.latest_reading(c.company_id, l.span, l.metric, c.sic, c.latest) x
        WHERE x.value IS NOT NULL AND (x.status IS NULL OR x.status = 'behind')
    ),
    ranked AS (
        SELECT v.*,
               row_number() OVER (PARTITION BY v.column_key ORDER BY v.value DESC, v.company_id)
                   AS top_rank,
               row_number() OVER (PARTITION BY v.column_key ORDER BY v.value, v.company_id)
                   AS bottom_rank
        FROM vals v
    ),
    picked AS (
        SELECT r.*, s.side, s.rank
        FROM ranked r
        CROSS JOIN LATERAL (VALUES ('top', r.top_rank), ('bottom', r.bottom_rank)) s(side, rank)
        WHERE s.rank <= p_n
    ),
    prev AS (  -- 072: the same column's figure a period earlier (a year for TTM), as gold holds it
        SELECT p.*, pv.value AS prev_value
        FROM picked p
        LEFT JOIN LATERAL (
            SELECT y.value FROM (
                (SELECT q.period_end, q.value FROM gold.quarterly_financials q
                 WHERE q.company_id = p.company_id AND q.metric = p.metric
                   AND q.period_end < p.period_end
                   AND (p.span = 'quarter' OR EXISTS (SELECT 1 FROM silver.metrics m
                                                      WHERE m.metric = p.metric
                                                        AND m.kind = 'instant'))
                 ORDER BY q.period_end DESC LIMIT 1)
                UNION ALL
                (SELECT d.period_end, d.value FROM gold.derived_financials d
                 WHERE d.company_id = p.company_id AND d.span = p.span AND d.metric = p.metric
                   AND d.period_end <= p.period_end - CASE p.span WHEN 'ttm' THEN 350 ELSE 1 END
                 ORDER BY d.period_end DESC LIMIT 1)
            ) y
            ORDER BY y.period_end DESC LIMIT 1
        ) pv ON true
    ),
    explained AS (
        SELECT p.*,
               ARRAY(
                   SELECT DISTINCT f.check_name FROM ops.quality_findings f
                   WHERE f.company_id = p.company_id
                     AND f.period_end > p.period_end - 400 AND f.period_end <= p.period_end
                     AND (f.metric = p.metric OR f.metric IN (
                             SELECT jsonb_object_keys(d.inputs) FROM gold.derived_financials d
                             WHERE d.company_id = p.company_id AND d.span = p.span
                               AND d.metric = p.metric AND d.period_end = p.period_end
                               AND jsonb_typeof(d.inputs) = 'object'))
                   ORDER BY 1)
               || CASE WHEN p.span = 'quarter' AND EXISTS (
                           SELECT 1 FROM gold.quarterly_financials q
                           WHERE q.company_id = p.company_id AND q.metric = p.metric
                             AND q.period_end = p.period_end AND q.value_source = 'derived')
                       THEN ARRAY['derived_quarter'] ELSE '{}'::text[] END
           || CASE WHEN p.prev_value IS NOT NULL AND sign(p.prev_value) = sign(p.value)
                        AND (p.value = 0 OR greatest(abs(p.value), abs(p.prev_value))
                                            <= 3 * least(abs(p.value), abs(p.prev_value)))
                   THEN ARRAY['in_line'] ELSE '{}'::text[] END AS findings
        FROM prev p
    )
    INSERT INTO ops.extremes (column_key, metric, span, side, rank, company_id, value, unit,
                              period_end, accession_no, status, prev_value, findings, review,
                              refreshed_at)
    SELECT column_key, metric, span, side, rank, company_id, value, unit, period_end,
           accession_no, status, prev_value, findings, cardinality(findings) = 0, now()
    FROM explained;
    SELECT count(*) INTO n FROM ops.extremes WHERE review;
    RETURN n;
END
$$;

-- With the company's name, for the ops CLI (facts-fetcher-extremes) and Grafana; it names
-- companies, so ops_reader only, never the Query API.
CREATE VIEW ops.extremes_report AS
SELECT e.column_key, e.side, e.rank, e.company_id, c.name AS company, e.value, e.unit,
       e.period_end, e.accession_no, e.status, e.prev_value, e.findings, e.review, e.refreshed_at
FROM ops.extremes e
JOIN silver.companies c ON c.company_id = e.company_id;

GRANT SELECT ON ops.extremes_report TO ops_reader;

-- 6. ops.check_segments: overlapping members beside a zero or a negative one ---------------------

-- 070's body with the edit marked "072" (header, 6).
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
          -- 072: a zero member adds nothing to any set, and the members below zero (corporate
          -- costs, eliminations) count once in every set: the search is over the positive members
          -- for the figure less the negative ones (NVIDIA's two cuts beside its corporate costs)
          AND NOT coalesce(ops.segment_cover(
                  ARRAY(SELECT p FROM unnest(s.parts) p WHERE p > 0),
                  g.value - (SELECT coalesce(sum(p), 0) FROM unnest(s.parts) p WHERE p < 0),
                  0.005 * abs(g.value)), false)
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

-- 072: the hourly catch-up re-checks every company's segments (071).
CREATE OR REPLACE FUNCTION ops.check_segments_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 72';
