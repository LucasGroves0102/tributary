-- silver.search_companies: what a search box should do with what people type.
--
-- silver.resolve_company compares for equality: right for "which company is this exact ticker
-- or name" (it stays, the sandbox and formulas use it) and wrong for a box: "Deckers", "Sprouts"
-- and "Goldman Sachs" found nothing, because their normalized names go on ("DECKERS OUTDOOR").
--
-- Candidates, best first (one row per company, its best match):
--   0 exact current ticker              AAPL; BRK.B and BRK-B are the same ticker
--   1 exact CIK                         320193
--   2 exact current name                Apple Inc. (compared normalized: Apple)
--   3 exact retired ticker, former name FB, Facebook
--   4 prefix of a current ticker or     AAP finds AAPL, Goldman Sachs finds Goldman Sachs Group
--     current name
--   5 a word of a current name starts   Sachs finds Goldman Sachs Group (2+ characters)
--     with it, or the name does with    JP Morgan finds JPMorgan Chase
--     spaces ignored
--   6 the same rules on former names and retired tickers
--   7 contained in a current name       3+ characters, spaces ignored
--   8 contained in a former name
-- Within a rank: tracked companies, then by filer category (the SEC's public-float size proxy),
-- then listed on a national exchange, then the shortest name. Company_id last: a total order.
--
-- matched_on/matched_alias say how it matched (cik | ticker | name), as in resolve_company, so a
-- caller that wants only an exact ticker keeps filtering on them. Identity is public metadata:
-- the Query API narrows with access=financials on top of this, never inside.
--
-- Nothing typed reaches a pattern: comparisons use starts_with/position, never LIKE or a regex.
-- No index: 33,000 aliases are read in full each call, 25-38 ms on hermes (timed once per query)
-- behind a 200 ms debounce. A trigram index (pg_trgm, a few MB) could not serve the spaces-ignored
-- matching and would save little on a table this small, so none was added. No locks
-- beyond creating the function.

CREATE FUNCTION silver.search_companies(q text, as_of date DEFAULT current_date)
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
           CASE gold.filer_category(c.filer_categories)
               WHEN 'large_accelerated' THEN 0 WHEN 'accelerated' THEN 1 ELSE 2 END,
           c.exchanges && ARRAY['NYSE', 'Nasdaq'] DESC NULLS LAST,
           length(c.name), b.company_id
$$;
