-- Tags that a line moved to, and lines a filing reports only as two tags.
--
-- Found by reading what the affected companies' raw companyfacts carry on the dates gold has nothing
-- (sample of 640 companies, every one the UI audit names; numbers are the sample's):
--   short_term_investments
--     DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent: where the AFS line went after the
--       2020-22 taxonomy change (Nike, 3M, IBM, Intuit, UPS, Pure Storage: 6 companies carry the old
--       series' value under this tag on a shared date and continue past its end).
--     AvailableForSaleSecuritiesCurrent: the same line before 2018 (59 companies, about 900 rows).
--     HeldToMaturitySecuritiesCurrent, its 2020 successor, TradingSecuritiesCurrent: the same balance
--       sheet line for securities classified another way.
--     DebtSecuritiesCurrent + EquitySecuritiesFvNi: NVIDIA's balance sheet line since January 2026 is its
--       own extension tag, which companyfacts doesn't carry, and its note gives the two parts in us-gaap:
--       39,065 + 12,886 = the 51,951 printed on the FY2026 balance sheet, 34,143 + 42,783 = 76,926 on
--       the July 2026 one. A sum is only taken when both parts are in one filing for one date.
--   revenue
--     RegulatedAndUnregulatedOperatingRevenue: DTE (stopped tagging Revenues in 2018) and Xcel (2019).
--     InterestIncomeExpenseNet + NoninterestIncome: a bank's net revenue, for the banks that tag no
--       RevenuesNetOfInterestExpense. Its Revenues and RevenueFromContract tags are parts: M&T 1.66B of
--       9.69B, Huntington 1.56B of 8.17B, Peoples 87M of 460M, which is what made net margin 176%. BNY's
--       sum is exactly its Revenues where both exist (FY2025: 15,136 + 4,944 = 20,080).
--   long_term_debt, long_term_debt_current
--     LongTermDebt, LongTermNotesPayable, LongTermNotesAndLoans; DebtCurrent, NotesPayableCurrent: Tesla,
--       Adobe, Lennar, Oracle, NVIDIA FY2017-19. LongTermDebt is the balance sheet's noncurrent line at
--       some filers and the note's total at others, so it's a fallback with a double-counting test
--       (039, gold.usable_facts). Beside LongTermDebtNoncurrent, 3,206 of 5,459 rows are totals: it never
--       outranks the noncurrent tag.
--   cash
--     Cash: small filers' balance sheet line (Guerrilla RF's cash stopped in 2021 under the other tags and
--       is 6.3M in 2026). Last, because at larger filers it's a note's part of cash and equivalents.
--   capex
--     PaymentsForCapitalImprovements: "Capital expenditures" on the cash flow statement of VF, Snap-on and
--       Western Midstream, which is why VF has no free cash flow. Last: at a REIT it's the improvements
--       line, beside development and acquisitions.
--
-- gold_line = false: kept in bronze and silver for a sum, never a line of its own. EquitySecuritiesFvNi
-- alone is often a long-term holding; NoninterestIncome alone is a part of revenue.
--
-- The `absent` golden row for NVIDIA's marketable securities is the gap this closes: the row's figure is
-- unchanged and hand-checked, its check is now `annual` (tests/golden/README.md).
--
-- Locks on hermes: ALTER TABLE takes ACCESS EXCLUSIVE on silver.metric_tags for an instant (a constant
-- default rewrites nothing); only the gold jobs and gold.annual_per_share read it. Nothing changes in
-- silver or gold until `facts-fetcher --rebuild`.

SET LOCAL lock_timeout = '30s';

ALTER TABLE silver.metric_tags ADD COLUMN gold_line boolean NOT NULL DEFAULT true;

-- A line a filing reports as parts. priority sits among the metric's tags (between 1 and 2 ranks after
-- the tag at 1 and before the tag at 2). All parts must be silver.metric_tags rows of the metric.
CREATE TABLE silver.metric_sums (
    metric   text    NOT NULL REFERENCES silver.metrics,
    priority numeric NOT NULL,
    tags     text[]  NOT NULL,
    PRIMARY KEY (metric, priority)
);

INSERT INTO silver.metric_tags (metric, tag, priority) VALUES
    ('short_term_investments', 'DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent', 5),
    ('short_term_investments', 'AvailableForSaleSecuritiesCurrent', 6),
    ('short_term_investments', 'HeldToMaturitySecuritiesCurrent', 7),
    ('short_term_investments', 'DebtSecuritiesHeldToMaturityAmortizedCostAfterAllowanceForCreditLossCurrent', 8),
    ('short_term_investments', 'TradingSecuritiesCurrent', 9),
    ('short_term_investments', 'DebtSecuritiesCurrent', 10),
    ('short_term_investments', 'EquitySecuritiesFvNi', 11),
    ('revenue', 'RegulatedAndUnregulatedOperatingRevenue', 6),
    ('revenue', 'InterestIncomeExpenseNet', 7),
    ('revenue', 'NoninterestIncome', 8),
    ('long_term_debt', 'LongTermDebt', 3),
    ('long_term_debt', 'LongTermNotesPayable', 4),
    ('long_term_debt', 'LongTermNotesAndLoans', 5),
    ('long_term_debt_current', 'DebtCurrent', 3),
    ('long_term_debt_current', 'NotesPayableCurrent', 4),
    ('cash', 'Cash', 4),
    ('capex', 'PaymentsForCapitalImprovements', 4);

UPDATE silver.metric_tags SET gold_line = false
WHERE (metric, tag) IN (('short_term_investments', 'EquitySecuritiesFvNi'),
                        ('revenue', 'InterestIncomeExpenseNet'),
                        ('revenue', 'NoninterestIncome'));

-- 4.5: before the single tags added here, after the balance sheet tags that were already mapped
INSERT INTO silver.metric_sums (metric, priority, tags) VALUES
    ('short_term_investments', 4.5, ARRAY['DebtSecuritiesCurrent', 'EquitySecuritiesFvNi']),
    ('revenue', 1.5, ARRAY['InterestIncomeExpenseNet', 'NoninterestIncome']);
