-- Fundamentals beyond the first eight metrics: full income statement, balance sheet and
-- cash flow lines. Run `make financials-rebuild` afterwards to fill silver/gold from bronze.
--
-- Tags within a metric share one meaning and sign; lower priority wins per period.
-- Outflows (capex, dividends, buybacks) are positive amounts, as filed.
-- us-gaap only: ifrs-full filers (20-F/40-F) report mostly non-USD and at most
-- half-yearly, so they would add few quarters and need per-metric currencies.

INSERT INTO silver.metrics VALUES
    ('cost_of_revenue',           'duration', 'USD',        true),
    ('research_and_development',  'duration', 'USD',        true),
    ('sga',                       'duration', 'USD',        true),
    ('interest_expense',          'duration', 'USD',        true),
    ('pretax_income',             'duration', 'USD',        true),
    ('income_tax',                'duration', 'USD',        true),
    ('eps_basic',                 'duration', 'USD/shares', false),
    ('weighted_shares_basic',     'duration', 'shares',     false),
    ('weighted_shares_diluted',   'duration', 'shares',     false),
    ('short_term_investments',    'instant',  'USD',        false),
    ('receivables',               'instant',  'USD',        false),
    ('inventory',                 'instant',  'USD',        false),
    ('current_assets',            'instant',  'USD',        false),
    ('current_liabilities',       'instant',  'USD',        false),
    ('long_term_debt',            'instant',  'USD',        false),
    ('long_term_debt_current',    'instant',  'USD',        false),
    ('total_liabilities',         'instant',  'USD',        false),
    ('stockholders_equity',       'instant',  'USD',        false),
    ('shares_outstanding',        'instant',  'shares',     false),
    ('capex',                     'duration', 'USD',        true),
    ('depreciation_amortization', 'duration', 'USD',        true),
    ('stock_based_compensation',  'duration', 'USD',        true),
    ('dividends_paid',            'duration', 'USD',        true),
    ('share_buybacks',            'duration', 'USD',        true);

INSERT INTO silver.metric_tags (metric, tag, priority) VALUES
    ('cost_of_revenue', 'CostOfRevenue', 1),
    ('cost_of_revenue', 'CostOfGoodsAndServicesSold', 2),
    ('cost_of_revenue', 'CostOfGoodsSold', 3),  -- pre-2018 filings
    ('research_and_development', 'ResearchAndDevelopmentExpense', 1),
    ('research_and_development', 'ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost', 2),
    -- filers that split selling and G&A (Microsoft, Alphabet, Netflix) have no total
    ('sga', 'SellingGeneralAndAdministrativeExpense', 1),
    -- InterestExpense was split into Nonoperating/Operating in the 2024 taxonomy;
    -- banks' interest on deposits is Operating
    ('interest_expense', 'InterestExpenseNonoperating', 1),
    ('interest_expense', 'InterestExpense', 2),
    ('interest_expense', 'InterestExpenseDebt', 3),
    ('interest_expense', 'InterestExpenseOperating', 4),
    ('pretax_income', 'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest', 1),
    ('pretax_income', 'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments', 2),
    ('income_tax', 'IncomeTaxExpenseBenefit', 1),
    ('income_tax', 'IncomeTaxExpenseBenefitContinuingOperations', 2),
    ('eps_basic', 'EarningsPerShareBasic', 1),
    ('eps_basic', 'EarningsPerShareBasicAndDiluted', 2),
    ('eps_diluted', 'EarningsPerShareBasicAndDiluted', 2),
    ('weighted_shares_basic', 'WeightedAverageNumberOfSharesOutstandingBasic', 1),
    ('weighted_shares_basic', 'WeightedAverageNumberOfShareOutstandingBasicAndDiluted', 2),
    ('weighted_shares_diluted', 'WeightedAverageNumberOfDilutedSharesOutstanding', 1),
    ('weighted_shares_diluted', 'WeightedAverageNumberOfShareOutstandingBasicAndDiluted', 2),
    ('short_term_investments', 'ShortTermInvestments', 1),
    ('short_term_investments', 'MarketableSecuritiesCurrent', 2),
    ('short_term_investments', 'AvailableForSaleSecuritiesDebtSecuritiesCurrent', 3),
    ('short_term_investments', 'OtherShortTermInvestments', 4),
    -- broadest first: the balance-sheet line; narrower tags often appear only in notes
    ('receivables', 'ReceivablesNetCurrent', 1),
    ('receivables', 'AccountsNotesAndLoansReceivableNetCurrent', 2),
    ('receivables', 'AccountsReceivableNetCurrent', 3),
    ('inventory', 'InventoryNet', 1),
    ('current_assets', 'AssetsCurrent', 1),
    ('current_liabilities', 'LiabilitiesCurrent', 1),
    -- noncurrent portion; unclassified balance sheets (banks, insurers, REITs) have none
    ('long_term_debt', 'LongTermDebtNoncurrent', 1),
    ('long_term_debt', 'LongTermDebtAndCapitalLeaseObligations', 2),
    ('long_term_debt_current', 'LongTermDebtCurrent', 1),
    ('long_term_debt_current', 'LongTermDebtAndCapitalLeaseObligationsCurrent', 2),
    ('total_liabilities', 'Liabilities', 1),
    ('stockholders_equity', 'StockholdersEquity', 1),
    -- includes noncontrolling interests; fallback only
    ('stockholders_equity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest', 2),
    -- at the balance-sheet date; dei:EntityCommonStockSharesOutstanding is dated at the
    -- cover page, weeks after the period end, so it would add off-quarter rows
    ('shares_outstanding', 'CommonStockSharesOutstanding', 1),
    ('capex', 'PaymentsToAcquirePropertyPlantAndEquipment', 1),
    ('capex', 'PaymentsToAcquireProductiveAssets', 2),  -- includes intangibles (NVIDIA, Amazon)
    ('capex', 'PaymentsToAcquireOtherPropertyPlantAndEquipment', 3),
    ('depreciation_amortization', 'DepreciationDepletionAndAmortization', 1),
    ('depreciation_amortization', 'DepreciationAmortizationAndAccretionNet', 2),
    ('depreciation_amortization', 'DepreciationAndAmortization', 3),
    ('depreciation_amortization', 'Depreciation', 4),  -- excludes amortization; fallback only
    ('stock_based_compensation', 'ShareBasedCompensation', 1),
    ('stock_based_compensation', 'AllocatedShareBasedCompensationExpense', 2),
    ('dividends_paid', 'PaymentsOfDividendsCommonStock', 1),
    ('dividends_paid', 'PaymentsOfOrdinaryDividends', 2),
    ('dividends_paid', 'PaymentsOfDividends', 3),  -- may include preferred
    ('share_buybacks', 'PaymentsForRepurchaseOfCommonStock', 1),
    ('share_buybacks', 'PaymentsForRepurchaseOfEquity', 2);
