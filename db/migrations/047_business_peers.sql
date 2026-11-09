-- Peers by business (F4, H6): what parser-peers (services/parser filing_parser/business.py, its
-- docstring is the spec) derives from each company's latest 10-K Item 1 (Business), for the Query
-- API's peer ranking (query_api/peers.py). Replaced whole by each run, in one transaction; no
-- text is stored, only terms, similarities and the offsets of sentences to cite.
--
--   gold.business_profiles  one row per company with a usable Item 1: the filing and section it
--                           came from (FR6 provenance) and its most distinctive terms
--   gold.business_peers     its NEIGHBOURS (60) most similar companies, cosine similarity of the
--                           two term vectors, and the shared terms that add the most
--   gold.business_mentions  other companies its Item 1 names in a sentence about competition,
--                           with the sentence's offsets in silver.filing_sections' text
--
-- Access: all three derive from Item 1 text, so they read like it (`filings`). A peer row needs
-- both companies readable: its terms are words of both texts. Same policy form as 030 (a single
-- IN over authz.readable_companies, inlined; never a per-row function call). No catalog-sized id
-- list reaches SQL: the API reads one company's rows by primary key.
--
-- Size (hermes' Item 1 texts of 2026-11-09, run locally): 4,888 profiles, 293,280 peer rows,
-- 4,166 mentions; 60 MB with indexes (business_peers 57 MB).
--
-- Locks on hermes: new tables only; nothing existing is touched.

SET LOCAL lock_timeout = '30s';

CREATE TABLE gold.business_profiles (
    company_id   text        PRIMARY KEY,  -- no FK: replaced whole each run
    accession_no text        NOT NULL,   -- the 10-K
    section_key  text        NOT NULL,   -- '1'
    filed        date        NOT NULL,
    parser       text        NOT NULL,   -- the silver.filing_sections row read
    parsed_at    timestamptz,
    terms        text[]      NOT NULL,   -- its most distinctive terms, heaviest first
    algorithm    text        NOT NULL,   -- business.ALGORITHM
    computed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE gold.business_peers (
    company_id text   NOT NULL,
    peer_id    text   NOT NULL,
    similarity real   NOT NULL CHECK (similarity BETWEEN 0 AND 1.0001),
    terms      text[] NOT NULL,          -- shared terms that add the most, heaviest first
    PRIMARY KEY (company_id, peer_id)
);

CREATE TABLE gold.business_mentions (
    company_id   text NOT NULL,          -- whose Item 1 names a competitor
    named_id     text NOT NULL,          -- the company named
    accession_no text NOT NULL,
    section_key  text NOT NULL,
    char_start   int  NOT NULL,          -- the sentence, in that section's text
    char_end     int  NOT NULL,
    PRIMARY KEY (company_id, named_id, char_start)
);
CREATE INDEX business_mentions_named ON gold.business_mentions (named_id);

GRANT SELECT ON gold.business_profiles, gold.business_peers, gold.business_mentions TO query_api;

ALTER TABLE gold.business_profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON gold.business_profiles FOR SELECT TO query_api
    USING (company_id IN (SELECT r FROM authz.readable_companies('filings') r));

ALTER TABLE gold.business_peers ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON gold.business_peers FOR SELECT TO query_api
    USING (company_id IN (SELECT r FROM authz.readable_companies('filings') r)
           AND peer_id IN (SELECT r FROM authz.readable_companies('filings') r));

ALTER TABLE gold.business_mentions ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON gold.business_mentions FOR SELECT TO query_api
    USING (company_id IN (SELECT r FROM authz.readable_companies('filings') r));

-- Companies that file one combined 10-K with this one (#135: PG&E Corp and Pacific Gas &
-- Electric are two registrants, a parent and its utility, not a duplicate). Public filing-index
-- metadata: the 10-K accessions filed in the last 400 days under more than one company's CIKs
-- (submissions index and the live feed, like silver.filings), through the form/date index, then
-- the other companies on those accessions. 10 such filings on hermes on 2026-11-09 (utilities and
-- their subsidiaries, Corteva and EIDP, a REIT and its operating partnership, 26 companies).
-- 190 ms cold on hermes (6,800 index rows), so the Query API reads all pairs at once and keeps
-- them for an hour per process. `filed_by`: the company whose CIK the accession number starts
-- with (the filer agent: usually the parent; NULL when a filing agent submitted it).
CREATE FUNCTION silver.co_registrants()
RETURNS TABLE (company_id text, other_id text, accession_no text, filing_date date,
               filed_by text)
LANGUAGE sql STABLE AS $$
    WITH recent AS (
        SELECT s.accession_no, s.cik, s.filing_date FROM bronze.submission_filings s
        WHERE s.form = '10-K' AND s.filing_date > current_date - 400
        UNION
        SELECT f.accession_no, f.cik, (f.filed_at AT TIME ZONE 'America/New_York')::date
        FROM bronze.filings f
        WHERE f.form_type = '10-K' AND f.filed_at > now() - interval '400 days'
    ),
    shared AS (
        SELECT r.accession_no, max(r.filing_date) AS filing_date,
               array_agg(DISTINCT k.company_id) AS ids
        FROM recent r JOIN silver.company_ciks k ON k.cik = r.cik
        GROUP BY r.accession_no
        HAVING count(DISTINCT k.company_id) > 1
    )
    SELECT DISTINCT ON (me, other) me, other, s.accession_no, s.filing_date,
           (SELECT k.company_id FROM silver.company_ciks k
            WHERE k.cik = left(s.accession_no, 10)) AS filed_by
    FROM shared s, unnest(s.ids) me, unnest(s.ids) other
    WHERE other <> me
    ORDER BY me, other, s.filing_date DESC, s.accession_no DESC
$$;
-- SECURITY INVOKER: query_api reads bronze.filings and bronze.submission_filings' columns through
-- the grants below (public index metadata, like silver.filings and 045's column grant). GRANT
-- updates the catalog only: no lock that blocks readers or the loaders.
GRANT SELECT (accession_no, cik, form_type, filed_at) ON bronze.filings TO query_api;
GRANT SELECT (accession_no, cik, form, filing_date) ON bronze.submission_filings TO query_api;
GRANT EXECUTE ON FUNCTION silver.co_registrants() TO query_api;
