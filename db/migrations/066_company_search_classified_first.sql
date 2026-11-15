-- silver.search_companies: a company the SEC classifies before one it doesn't, within a rank (analyst
-- test 4, regression 3).
--
-- "Goldman Sachs" listed Goldman Sachs BDC above The Goldman Sachs Group in the palette and Compare's
-- picker: both are rank 4 (a prefix of a current name), not tracked, with figures here, large
-- accelerated and on the NYSE, so 044's next key, the normalized name's length, decided ("GOLDMAN SACHS
-- BDC" 17 < "GOLDMAN SACHS GROUP" 19). One key goes before the length: a company with a SIC code first.
-- The SEC assigns an industry code to the businesses it classifies; business development companies,
-- funds and trusts named after a company commonly have none (Goldman Sachs BDC's is blank; GS's is
-- 6211). Public identity metadata only, like 044's keys: never a value (search is public identity; an
-- order by revenue would be an oracle with the catalog closed).
--
-- Measured on hermes (2026-11-14, read-only, the body below inlined over a list of queries): the first
-- two words of every large accelerated filer's normalized name (2,268 queries) plus the report's cases
-- (Deckers, Sprouts, bank of, Apple, Vertiv, Morgan Stanley, BlackRock, Blackstone, Ares, Apollo, KKR,
-- Carlyle, Oaktree, JPMorgan, Franklin, Invesco). The first result changes for three: "Goldman Sachs"
-- (either case) to GOLDMAN SACHS GROUP INC, and "Blue Owl" from Blue Owl Capital Corp (a BDC) to BLUE
-- OWL CAPITAL INC. (the asset manager). Nothing else moves to the top. No cost: the column is read
-- already. Ranks, matching and every other key are 044's, copied.
--
-- Locks on hermes: CREATE OR REPLACE FUNCTION, catalog only; no table is locked or rewritten.

CREATE OR REPLACE FUNCTION silver.search_companies(q text, as_of date DEFAULT current_date)
RETURNS TABLE (
    company_id    text,
    name          text,
    matched_on    text,
    matched_alias text,
    is_current    boolean,
    tracked       boolean
)
LANGUAGE sql STABLE AS $$
  WITH needle AS (
    SELECT n.ticker, n.name, replace(n.name, ' ', '') AS squashed
    FROM (SELECT replace(upper(btrim(q)), '.', '-') AS ticker,
                 -- a query that is only "Inc" or "The" normalizes to nothing: use it as typed
                 coalesce(nullif(silver.normalize_name(q), ''),
                          btrim(regexp_replace(upper(q), '[^A-Z0-9 ]', ' ', 'g'))) AS name) n
  ),
  aliases AS (
    SELECT a.company_id, a.alias_type, a.alias, a.normalized,
           (a.valid_from IS NULL OR a.valid_from <= as_of)
             AND (a.valid_to IS NULL OR a.valid_to > as_of) AS is_current
    FROM silver.company_aliases a
  ),
  matches AS (
    SELECT k.company_id, 'cik' AS matched_on, k.cik AS matched_alias, true AS is_current, 1 AS rank
    FROM silver.company_ciks k
    WHERE btrim(q) ~ '^\d{1,10}$' AND k.cik = lpad(btrim(q), 10, '0')
    UNION ALL
    SELECT a.company_id, 'ticker', a.alias, a.is_current,
           CASE WHEN a.normalized = n.ticker THEN (CASE WHEN a.is_current THEN 0 ELSE 3 END)
                WHEN a.is_current THEN 4
                ELSE 6 END
    FROM aliases a, needle n
    WHERE a.alias_type = 'ticker' AND n.ticker <> '' AND starts_with(a.normalized, n.ticker)
    UNION ALL
    SELECT a.company_id, 'name', a.alias, a.is_current,
           CASE WHEN a.normalized = n.name THEN (CASE WHEN a.is_current THEN 2 ELSE 3 END)
                WHEN starts_with(a.normalized, n.name)
                  THEN (CASE WHEN a.is_current THEN 4 ELSE 6 END)
                WHEN starts_with(replace(a.normalized, ' ', ''), n.squashed)
                     OR (length(n.squashed) >= 2
                         AND position(' ' || n.name IN ' ' || a.normalized) > 0)
                  THEN (CASE WHEN a.is_current THEN 5 ELSE 6 END)
                ELSE (CASE WHEN a.is_current THEN 7 ELSE 8 END) END
    FROM aliases a, needle n
    WHERE a.alias_type = 'name' AND n.squashed <> ''
      AND (starts_with(replace(a.normalized, ' ', ''), n.squashed)
           OR (length(n.squashed) >= 2 AND position(' ' || n.name IN ' ' || a.normalized) > 0)
           OR (length(n.squashed) >= 3
               AND position(n.squashed IN replace(a.normalized, ' ', '')) > 0))
  ),
  best AS (
    SELECT DISTINCT ON (m.company_id) m.*
    FROM matches m
    ORDER BY m.company_id, m.rank, m.is_current DESC, length(m.matched_alias)
  )
  SELECT b.company_id, c.name, b.matched_on, b.matched_alias, b.is_current, c.tracked
  FROM best b JOIN silver.companies c USING (company_id)
  ORDER BY b.rank, c.tracked DESC,
           -- 044: a company with financials here before one without (a foreign filer's 20-F/40-F)
           EXISTS (SELECT 1 FROM gold.financials_coverage v WHERE v.company_id = b.company_id) DESC,
           CASE gold.filer_category(c.filer_categories)
               WHEN 'large_accelerated' THEN 0 WHEN 'accelerated' THEN 1 ELSE 2 END,
           c.exchanges && ARRAY['NYSE', 'Nasdaq'] DESC NULLS LAST,
           -- 066: a company the SEC classifies (it has a SIC code) before one it doesn't (a BDC,
           -- fund or trust named after it: Goldman Sachs BDC before The Goldman Sachs Group)
           coalesce(c.sic, '') <> '' DESC,
           -- 044: the name as compared, not its SEC suffixes ("BANK OF AMERICA CORP /DE/")
           length(silver.normalize_name(c.name)), length(c.name), b.company_id
$$;
