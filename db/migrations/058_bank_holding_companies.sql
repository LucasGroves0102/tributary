-- Four more banks (owner's decision, 2026-11-09): Charles Schwab, American Express, Synchrony and Ally
-- are bank holding companies, so the bank rule (035) applies to them too: margins before interest, free
-- cash flow, net debt, debt to equity, the current ratio and cash and investments read n/a, and their
-- pages, peers and comps use the bank template (F4, 048). The SEC files Schwab under brokers (6211) and
-- American Express and Synchrony under "finance services" (6199), SICs whose other filers aren't banks,
-- so they join by company id, like Goldman Sachs and Morgan Stanley. Ally's SIC is already a depository
-- one (6022); it is listed too, so a SIC change at the SEC can't drop it. Ids are silver.companies' on
-- hermes (2026-11-09):
--   0000316709 SCHWAB CHARLES CORP    6211
--   0000004962 AMERICAN EXPRESS CO    6199
--   0001601712 Synchrony Financial    6199
--   0000040729 Ally Financial Inc.    6022
--
-- One rule everywhere: this function is the only list of banks. The screener's `not_applicable` and
-- gold.latest_reading call it; the Query API's peers pick BANK_PEER_COLUMNS from that row, and the web
-- app's bank template (isBankRow, isBank) reads the same field. Nothing in Python or TypeScript lists a
-- bank (test_readings_db checks the four ids through the screener).
--
-- Gold: gold.usable_facts (056) drops the fallback debt tags (LongTermDebt, LongTermNotesPayable,
-- LongTermNotesAndLoans) at a bank (039's rule: deposits are a bank's debt). American Express and
-- Synchrony tag their debt only as LongTermDebt (hermes, 2026-11-09: 71 and 42 quarters, to 2026-09-30)
-- and Schwab did until 2013 (17 quarters), so their next gold refresh (a new filing, or the
-- financials-rebuild job) leaves long_term_debt out for them, as for JPMorgan, and net debt and debt to
-- equity with it; all three already read n/a from this migration on. Ally (LongTermDebtNoncurrent and
-- LongTermDebtCurrent) keeps its debt lines.
--
-- gold.not_applicable_metrics below is 049's body with one edit marked "058" (the company ids).
-- Locks on hermes: CREATE OR REPLACE FUNCTION of one plain SQL function, nothing on any table; its
-- owner and grants (EXECUTE to PUBLIC, as 035 created it) stay. Calls already running finish with the
-- old body. No rebuild needed for the readings. Backout: db/backout/banks_058.sql (049's body).

SET LOCAL lock_timeout = '30s';

CREATE OR REPLACE FUNCTION gold.not_applicable_metrics(p_company_id text, p_sic text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN p_sic IN ('6021', '6022', '6029', '6035', '6036', '6099')
                     OR p_company_id IN ('0000886982', '0000895421',  -- GS, MS (SIC 6211)
                                         -- 058: Schwab (6211), American Express, Synchrony (6199),
                                         -- Ally (6022 already)
                                         '0000316709', '0000004962', '0001601712', '0000040729')
                THEN ARRAY['gross_margin', 'operating_margin', 'free_cash_flow', 'fcf_margin',
                           'net_debt', 'debt_to_equity', 'current_ratio',
                           'cash_and_investments']  -- 049
                ELSE ARRAY[]::text[] END
$$;
