-- Silver: company identity (FR5). One company_id per company across CIKs, tickers and names.
--
-- company_id is the first CIK in the company's lineage (e.g. Alphabet's is Google Inc.'s CIK).
-- Adding a successor CIK later never changes an existing company_id.

CREATE SCHEMA silver;

-- "ELI LILLY & Co" → "ELI LILLY", "COSTCO WHOLESALE CORP /NEW" → "COSTCO WHOLESALE"
CREATE FUNCTION silver.normalize_name(s text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT btrim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
           upper(replace(s, '&', ' AND ')),
           '/[A-Z]{2,3}/?$', ''),                      -- SEC state/status suffixes: /NEW, /DE/
           '[^A-Z0-9 ]', ' ', 'g'),                    -- punctuation → space
           '\m(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LLC|LP|HOLDINGS|THE)\M',
           ' ', 'g'),
           '\s+', ' ', 'g'),
           '( AND)+\s*$', ''))
$$;

CREATE TABLE silver.companies (
    company_id      text        PRIMARY KEY,
    name            text        NOT NULL,  -- current name
    current_cik     text        NOT NULL,  -- the CIK filing today
    sic             text,
    sic_description text,
    entity_type     text,
    tracked         boolean     NOT NULL DEFAULT false,  -- fetch financials and documents
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Every CIK maps to exactly one company; a company can have several over time.
CREATE TABLE silver.company_ciks (
    cik        text PRIMARY KEY,
    company_id text NOT NULL REFERENCES silver.companies,
    valid_from date,  -- NULL = since before we know
    valid_to   date   -- NULL = still in use
);

CREATE TABLE silver.company_aliases (
    alias_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id text   NOT NULL REFERENCES silver.companies,
    cik        text   NOT NULL,  -- the CIK this alias belonged to
    alias_type text   NOT NULL CHECK (alias_type IN ('ticker', 'name')),
    alias      text   NOT NULL,
    normalized text   GENERATED ALWAYS AS (
        CASE alias_type WHEN 'ticker' THEN upper(alias) ELSE silver.normalize_name(alias) END
    ) STORED,
    valid_from date,
    valid_to   date,
    -- sec_tickers: company_tickers.json snapshots, closed (valid_to) when they disappear
    -- sec_submissions: names and former names from data.sec.gov/submissions
    -- curated: hand-maintained ticker history the SEC doesn't publish
    source     text   NOT NULL CHECK (source IN ('sec_tickers', 'sec_submissions', 'curated'))
);

CREATE INDEX company_aliases_lookup ON silver.company_aliases (alias_type, normalized);
CREATE INDEX company_aliases_company ON silver.company_aliases (company_id);
-- at most one open snapshot row per alias
CREATE UNIQUE INDEX company_aliases_open_ticker_snapshot
    ON silver.company_aliases (alias_type, alias, cik)
    WHERE source = 'sec_tickers' AND valid_to IS NULL;

-- Candidates for a CIK, ticker or name (current or former), best match first.
-- matched_on says how it matched; is_current is false for a ticker or name no longer in use.
CREATE FUNCTION silver.resolve_company(q text, as_of date DEFAULT current_date)
RETURNS TABLE (
    company_id    text,
    name          text,
    matched_on    text,
    matched_alias text,
    is_current    boolean,
    tracked       boolean
)
LANGUAGE sql STABLE AS $$
  WITH matches AS (
    SELECT k.company_id, 'cik' AS matched_on, k.cik AS matched_alias, true AS is_current,
           0 AS rank
    FROM silver.company_ciks k
    WHERE btrim(q) ~ '^\d{1,10}$' AND k.cik = lpad(btrim(q), 10, '0')
    UNION ALL
    SELECT a.company_id, a.alias_type, a.alias,
           (a.valid_from IS NULL OR a.valid_from <= as_of)
             AND (a.valid_to IS NULL OR a.valid_to > as_of),
           CASE a.alias_type WHEN 'ticker' THEN 1 ELSE 2 END
    FROM silver.company_aliases a
    WHERE (a.alias_type = 'ticker' AND a.normalized = upper(btrim(q)))
       OR (a.alias_type = 'name' AND a.normalized = silver.normalize_name(q))
  ),
  best AS (
    SELECT DISTINCT ON (m.company_id) m.*
    FROM matches m
    ORDER BY m.company_id, m.rank, m.is_current DESC
  )
  SELECT b.company_id, c.name, b.matched_on, b.matched_alias, b.is_current, c.tracked
  FROM best b JOIN silver.companies c USING (company_id)
  ORDER BY b.rank, b.is_current DESC, c.tracked DESC, b.company_id
$$;
