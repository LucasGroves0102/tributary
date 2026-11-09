-- Sectors and industries over SIC (F4, #136): a small open classification written for
-- Tributary, not a licensed one (no GICS, no ICB). SIC codes are the SEC's 1987 Standard
-- Industrial Classification, assigned per registrant and rarely revised; they stay visible
-- everywhere. This groups them into 12 sectors and 88 industries an analyst would recognise, so
-- the screener can filter and group by them and peers can use them as a weak signal.
--
-- One row per inclusive range of 4-digit SIC codes (text, so '0100' sorts before '1000'). Ranges
-- never overlap (services/query-api/tests/test_sectors.py parses this file and checks that, and
-- that every SIC code the SEC assigned to a company on 2026-11-09, 399 of them, falls in exactly
-- one range). Codes outside every range ('0000', none) have no sector.
--
-- Decisions that are judgment, not SIC structure (change them here, with a new migration):
--   * 2833-2836 pharmaceuticals, biologicals, diagnostics: one industry (biotechs file under 2834
--     and 2836 interchangeably).
--   * 3533 oil & gas field machinery, 4920-4922 gas transmission and 4610 pipelines: Energy.
--   * 3674 semiconductors apart from the other electronic components (3670-3679).
--   * 3826 laboratory analytical instruments and 8731 commercial research: Health Care (life
--     sciences tools); the other instruments (3820-3829) are Technology.
--   * 5330-5399 variety stores (Walmart, Costco, Target, Dollar General) and 5400-5499 food
--     stores: Consumer Staples retail; department stores (5311) stay Consumer Discretionary.
--   * 6324 hospital and medical service plans (UnitedHealth, Elevance): Health Care.
--   * 6220-6229 commodity contract dealers are, at the SEC, almost all commodity and crypto
--     trusts: "Funds & vehicles", like blank checks (6770), asset-backed issuers (6189), ADRs (8880)
--     and non-operating establishments (9995).
--   * 7370-7379 (including Alphabet and Meta at 7370) stay Technology.
--
-- Locks on hermes: a new table and view only; nothing existing is touched.

SET LOCAL lock_timeout = '30s';

CREATE TABLE silver.sic_sectors (
    sic_from text NOT NULL PRIMARY KEY CHECK (sic_from ~ '^\d{4}$'),
    sic_to   text NOT NULL CHECK (sic_to ~ '^\d{4}$' AND sic_to >= sic_from),
    sector   text NOT NULL,
    industry text NOT NULL
);

INSERT INTO silver.sic_sectors (sic_from, sic_to, sector, industry) VALUES
    -- Agriculture, forestry, fishing
    ('0100', '0999', 'Consumer Staples', 'Farming & agricultural products'),
    -- Mining
    ('1000', '1099', 'Materials', 'Metals & mining'),
    ('1200', '1220', 'Materials', 'Metals & mining'),
    ('1221', '1299', 'Energy', 'Coal'),
    ('1300', '1380', 'Energy', 'Oil & gas producers'),
    ('1381', '1389', 'Energy', 'Oil & gas services & equipment'),
    ('1400', '1499', 'Materials', 'Construction materials'),
    -- Construction
    ('1500', '1539', 'Consumer Discretionary', 'Homebuilding'),
    ('1540', '1799', 'Industrials', 'Construction & engineering'),
    -- Manufacturing
    ('2000', '2079', 'Consumer Staples', 'Food products'),
    ('2080', '2089', 'Consumer Staples', 'Beverages'),
    ('2090', '2099', 'Consumer Staples', 'Food products'),
    ('2100', '2199', 'Consumer Staples', 'Tobacco'),
    ('2200', '2299', 'Consumer Discretionary', 'Household durables'),
    ('2300', '2399', 'Consumer Discretionary', 'Apparel, footwear & accessories'),
    ('2400', '2449', 'Materials', 'Forest & paper products'),
    ('2450', '2452', 'Consumer Discretionary', 'Homebuilding'),
    ('2453', '2499', 'Materials', 'Forest & paper products'),
    ('2500', '2519', 'Consumer Discretionary', 'Household durables'),
    ('2520', '2599', 'Industrials', 'Building & office products'),
    ('2600', '2649', 'Materials', 'Forest & paper products'),
    ('2650', '2699', 'Materials', 'Containers & packaging'),
    ('2700', '2749', 'Communications & Media', 'Publishing'),
    ('2750', '2799', 'Industrials', 'Commercial & professional services'),
    ('2800', '2829', 'Materials', 'Chemicals'),
    ('2830', '2836', 'Health Care', 'Pharmaceuticals & biotechnology'),
    ('2837', '2839', 'Materials', 'Chemicals'),
    ('2840', '2849', 'Consumer Staples', 'Household & personal products'),
    ('2850', '2899', 'Materials', 'Chemicals'),
    ('2900', '2999', 'Energy', 'Refining & marketing'),
    ('3000', '3019', 'Consumer Discretionary', 'Auto parts'),
    ('3020', '3029', 'Consumer Discretionary', 'Apparel, footwear & accessories'),
    ('3030', '3099', 'Materials', 'Plastics & rubber products'),
    ('3100', '3199', 'Consumer Discretionary', 'Apparel, footwear & accessories'),
    ('3200', '3299', 'Materials', 'Construction materials'),
    ('3300', '3399', 'Materials', 'Steel & metals'),
    ('3400', '3412', 'Materials', 'Containers & packaging'),
    ('3413', '3429', 'Industrials', 'Machinery & tools'),
    ('3430', '3449', 'Industrials', 'Building & office products'),
    ('3450', '3479', 'Industrials', 'Machinery & tools'),
    ('3480', '3489', 'Industrials', 'Aerospace & defense'),
    ('3490', '3532', 'Industrials', 'Machinery & tools'),
    ('3533', '3533', 'Energy', 'Oil & gas services & equipment'),
    ('3534', '3569', 'Industrials', 'Machinery & tools'),
    ('3570', '3575', 'Technology', 'Computers & hardware'),
    ('3576', '3576', 'Technology', 'Communications equipment'),
    ('3577', '3579', 'Technology', 'Computers & hardware'),
    ('3580', '3584', 'Industrials', 'Machinery & tools'),
    ('3585', '3585', 'Industrials', 'Building & office products'),
    ('3586', '3599', 'Industrials', 'Machinery & tools'),
    ('3600', '3629', 'Industrials', 'Electrical equipment'),
    ('3630', '3639', 'Consumer Discretionary', 'Household durables'),
    ('3640', '3649', 'Industrials', 'Electrical equipment'),
    ('3650', '3659', 'Consumer Discretionary', 'Consumer electronics'),
    ('3660', '3669', 'Technology', 'Communications equipment'),
    ('3670', '3673', 'Technology', 'Electronic components'),
    ('3674', '3674', 'Technology', 'Semiconductors'),
    ('3675', '3679', 'Technology', 'Electronic components'),
    ('3680', '3699', 'Industrials', 'Electrical equipment'),
    ('3700', '3711', 'Consumer Discretionary', 'Automobiles'),
    ('3712', '3713', 'Industrials', 'Commercial vehicles & rail'),
    ('3714', '3714', 'Consumer Discretionary', 'Auto parts'),
    ('3715', '3715', 'Industrials', 'Commercial vehicles & rail'),
    ('3716', '3716', 'Consumer Discretionary', 'Leisure products'),
    ('3717', '3719', 'Consumer Discretionary', 'Auto parts'),
    ('3720', '3739', 'Industrials', 'Aerospace & defense'),
    ('3740', '3749', 'Industrials', 'Commercial vehicles & rail'),
    ('3750', '3759', 'Consumer Discretionary', 'Leisure products'),
    ('3760', '3769', 'Industrials', 'Aerospace & defense'),
    ('3770', '3799', 'Consumer Discretionary', 'Leisure products'),
    ('3800', '3819', 'Industrials', 'Aerospace & defense'),
    ('3820', '3825', 'Technology', 'Electronic instruments & controls'),
    ('3826', '3826', 'Health Care', 'Life sciences tools & services'),
    ('3827', '3839', 'Technology', 'Electronic instruments & controls'),
    ('3840', '3859', 'Health Care', 'Medical devices & supplies'),
    ('3860', '3869', 'Technology', 'Electronic instruments & controls'),
    ('3870', '3929', 'Consumer Discretionary', 'Apparel, footwear & accessories'),
    ('3930', '3989', 'Consumer Discretionary', 'Leisure products'),
    ('3990', '3999', 'Industrials', 'Diversified manufacturing'),
    -- Transportation, communications, utilities
    ('4000', '4499', 'Industrials', 'Transportation & logistics'),
    ('4500', '4599', 'Industrials', 'Airlines & air freight'),
    ('4600', '4699', 'Energy', 'Pipelines & midstream'),
    ('4700', '4799', 'Industrials', 'Transportation & logistics'),
    ('4800', '4829', 'Communications & Media', 'Telecom services'),
    ('4830', '4849', 'Communications & Media', 'Broadcasting & cable'),
    ('4850', '4899', 'Communications & Media', 'Telecom services'),
    ('4900', '4919', 'Utilities', 'Electric utilities'),
    ('4920', '4922', 'Energy', 'Pipelines & midstream'),
    ('4923', '4929', 'Utilities', 'Gas utilities'),
    ('4930', '4939', 'Utilities', 'Multi-utilities'),
    ('4940', '4949', 'Utilities', 'Water utilities'),
    ('4950', '4959', 'Industrials', 'Waste management'),
    ('4960', '4999', 'Utilities', 'Independent power & renewables'),
    -- Wholesale
    ('5000', '5044', 'Industrials', 'Trading & distribution'),
    ('5045', '5045', 'Technology', 'Technology distribution'),
    ('5046', '5046', 'Industrials', 'Trading & distribution'),
    ('5047', '5047', 'Health Care', 'Health care distribution'),
    ('5048', '5062', 'Industrials', 'Trading & distribution'),
    ('5063', '5065', 'Technology', 'Technology distribution'),
    ('5066', '5099', 'Industrials', 'Trading & distribution'),
    ('5100', '5121', 'Consumer Staples', 'Food & staples distribution'),
    ('5122', '5122', 'Health Care', 'Health care distribution'),
    ('5123', '5129', 'Consumer Staples', 'Food & staples distribution'),
    ('5130', '5139', 'Consumer Discretionary', 'Apparel, footwear & accessories'),
    ('5140', '5149', 'Consumer Staples', 'Food & staples distribution'),
    ('5150', '5159', 'Consumer Staples', 'Farming & agricultural products'),
    ('5160', '5169', 'Materials', 'Chemicals'),
    ('5170', '5179', 'Energy', 'Refining & marketing'),
    ('5180', '5189', 'Consumer Staples', 'Beverages'),
    ('5190', '5199', 'Consumer Staples', 'Food & staples distribution'),
    -- Retail
    ('5200', '5299', 'Consumer Discretionary', 'Home improvement retail'),
    ('5300', '5329', 'Consumer Discretionary', 'Department stores'),
    ('5330', '5499', 'Consumer Staples', 'Food & general merchandise retail'),
    ('5500', '5599', 'Consumer Discretionary', 'Automotive retail'),
    ('5600', '5699', 'Consumer Discretionary', 'Apparel & footwear retail'),
    ('5700', '5799', 'Consumer Discretionary', 'Specialty retail'),
    ('5800', '5899', 'Consumer Discretionary', 'Restaurants'),
    ('5900', '5911', 'Consumer Discretionary', 'Specialty retail'),
    ('5912', '5912', 'Consumer Staples', 'Food & general merchandise retail'),
    ('5913', '5959', 'Consumer Discretionary', 'Specialty retail'),
    ('5960', '5969', 'Consumer Discretionary', 'Internet & direct retail'),
    ('5970', '5999', 'Consumer Discretionary', 'Specialty retail'),
    -- Finance, insurance, real estate
    ('6000', '6099', 'Financials', 'Banks & thrifts'),
    ('6100', '6188', 'Financials', 'Consumer & specialty finance'),
    ('6189', '6189', 'Funds & Vehicles', 'Asset-backed & pass-through issuers'),
    ('6190', '6199', 'Financials', 'Consumer & specialty finance'),
    ('6200', '6219', 'Financials', 'Capital markets'),
    ('6220', '6229', 'Funds & Vehicles', 'Commodity & crypto trusts'),
    ('6230', '6281', 'Financials', 'Capital markets'),
    ('6282', '6289', 'Financials', 'Asset management'),
    ('6290', '6299', 'Financials', 'Capital markets'),
    ('6300', '6323', 'Financials', 'Insurance'),
    ('6324', '6324', 'Health Care', 'Managed health care'),
    ('6325', '6399', 'Financials', 'Insurance'),
    ('6400', '6499', 'Financials', 'Insurance brokers'),
    ('6500', '6599', 'Real Estate', 'Real estate operators & developers'),
    ('6700', '6769', 'Financials', 'Holding companies & royalties'),
    ('6770', '6770', 'Funds & Vehicles', 'Blank checks (SPACs)'),
    ('6771', '6791', 'Financials', 'Holding companies & royalties'),
    ('6792', '6792', 'Energy', 'Royalty trusts'),
    ('6793', '6794', 'Financials', 'Holding companies & royalties'),
    ('6795', '6795', 'Energy', 'Royalty trusts'),
    ('6796', '6797', 'Financials', 'Holding companies & royalties'),
    ('6798', '6798', 'Real Estate', 'REITs'),
    ('6799', '6799', 'Financials', 'Holding companies & royalties'),
    -- Services
    ('7000', '7099', 'Consumer Discretionary', 'Hotels, gaming & leisure'),
    ('7200', '7299', 'Consumer Discretionary', 'Consumer services'),
    ('7300', '7319', 'Communications & Media', 'Advertising & marketing'),
    ('7320', '7329', 'Industrials', 'Commercial & professional services'),
    ('7330', '7339', 'Communications & Media', 'Advertising & marketing'),
    ('7340', '7349', 'Industrials', 'Commercial & professional services'),
    ('7350', '7359', 'Industrials', 'Equipment rental & leasing'),
    ('7360', '7369', 'Industrials', 'Commercial & professional services'),
    ('7370', '7371', 'Technology', 'Internet & IT services'),
    ('7372', '7372', 'Technology', 'Software'),
    ('7373', '7373', 'Technology', 'Internet & IT services'),
    ('7374', '7379', 'Technology', 'Internet & IT services'),
    ('7380', '7399', 'Industrials', 'Commercial & professional services'),
    ('7500', '7599', 'Consumer Discretionary', 'Consumer services'),
    ('7600', '7699', 'Industrials', 'Commercial & professional services'),
    ('7800', '7899', 'Communications & Media', 'Entertainment'),
    ('7900', '7999', 'Consumer Discretionary', 'Hotels, gaming & leisure'),
    ('8000', '8099', 'Health Care', 'Health care providers & services'),
    ('8100', '8199', 'Industrials', 'Commercial & professional services'),
    ('8200', '8299', 'Consumer Discretionary', 'Education services'),
    ('8300', '8399', 'Consumer Discretionary', 'Consumer services'),
    ('8700', '8710', 'Industrials', 'Commercial & professional services'),
    ('8711', '8711', 'Industrials', 'Construction & engineering'),
    ('8712', '8730', 'Industrials', 'Commercial & professional services'),
    ('8731', '8731', 'Health Care', 'Life sciences tools & services'),
    ('8732', '8879', 'Industrials', 'Commercial & professional services'),
    ('8880', '8880', 'Funds & Vehicles', 'Depositary receipts'),
    ('8881', '8999', 'Industrials', 'Commercial & professional services'),
    ('9995', '9995', 'Funds & Vehicles', 'Non-operating establishments');

-- A company's sector and industry (none without a SIC code or outside every range). Company
-- identity is public metadata, like the SIC code itself.
CREATE VIEW silver.company_sectors AS
SELECT c.company_id, s.sector, s.industry
FROM silver.companies c
JOIN silver.sic_sectors s ON c.sic BETWEEN s.sic_from AND s.sic_to
WHERE c.sic ~ '^\d{4}$';

GRANT SELECT ON silver.sic_sectors, silver.company_sectors TO query_api;
