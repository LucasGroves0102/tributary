-- silver.search_companies: within a rank, the companies a search means first (leftover from the UI audit).
--
-- "bank of" listed Bank of Chile, Bank of Hawaii and Butterfield before Bank of America: within rank 4
-- (a prefix of a current name) the tie-break after filer category and exchange was the raw name's
-- length, and "BANK OF AMERICA CORP /DE/" is long for its SEC suffixes. Two changes, after tracked:
--   - companies with financials here first (gold.financials_coverage, the screener's public list of
--     company ids with gold rows, no values): Bank of Chile, Montreal and Nova Scotia file 20-F/40-F
--     and have none, so they go after the US banks a search here can show figures for;
--   - the normalized name's length before the raw name's ("BANK OF AMERICA", 15).
-- Ranks, matching and everything else are 034's, copied. Identity stays public metadata: coverage is
-- whether a company has figures, never a figure. No locks beyond replacing the function.

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
           -- 044: the name as compared, not its SEC suffixes ("BANK OF AMERICA CORP /DE/")
           length(silver.normalize_name(c.name)), length(c.name), b.company_id
$$;
