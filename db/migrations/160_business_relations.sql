-- Analyst audit, part 2 (Morgan, test 11, findings 11-14): peers that are customers, suppliers or
-- subsidiaries (NVIDIA's list held Super Micro and CoreWeave, Exxon's its 69.6%-owned Imperial Oil,
-- Booking's Southwest, which sells through "Expedia, Priceline"), and Exxon's industry read
-- "Refining & marketing".
--
-- 1. gold.business_relations: other companies a business section names outside competition, what
--    parser-peers (services/parser filing_parser/business.py business-2, spec 6) calls business
--    relations, with the sentence's offsets (like gold.business_mentions) and whether it says one
--    holds a majority of the other. query_api/peers.py decides what a relation does to a ranking.
--    Replaced whole by each run with the other three tables, in one transaction. Same access as
--    gold.business_mentions (047): it derives from filing text, so `filings`, on the company whose
--    text it is. Size on hermes' texts of 2026-11-18 run locally: 32,456 rows.
-- 2. silver.sic_sectors: 2900-2999 (petroleum refining, where the SEC files Exxon, Chevron and
--    ConocoPhillips as well as the refiners) and 5170-5179 (petroleum wholesale) read "Integrated
--    oil, refining & marketing". Same ranges and membership; query_api.screener maps a saved
--    screen's old name to the new one (RENAMED_INDUSTRIES).
--
-- Locks on hermes: a new table; two rows of silver.sic_sectors (read by the screener and peers).

SET LOCAL lock_timeout = '30s';

CREATE TABLE gold.business_relations (
    company_id   text    NOT NULL,       -- whose business section names the other company
    named_id     text    NOT NULL,       -- the company named
    accession_no text    NOT NULL,
    section_key  text    NOT NULL,       -- '1' (10-K) or '4' (20-F)
    char_start   int     NOT NULL,       -- the sentence, in the document's text
    char_end     int     NOT NULL,
    ownership    boolean NOT NULL,       -- the sentence says one holds a majority of the other
    PRIMARY KEY (company_id, named_id, char_start)
);
CREATE INDEX business_relations_named ON gold.business_relations (named_id);

GRANT SELECT ON gold.business_relations TO query_api;

ALTER TABLE gold.business_relations ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON gold.business_relations FOR SELECT TO query_api
    USING (company_id IN (SELECT r FROM authz.readable_companies('filings') r));

DELETE FROM silver.sic_sectors WHERE sic_from = '2900';
DELETE FROM silver.sic_sectors WHERE sic_from = '5170';
INSERT INTO silver.sic_sectors (sic_from, sic_to, sector, industry) VALUES
    ('2900', '2999', 'Energy', 'Integrated oil, refining & marketing'),
    ('5170', '5179', 'Energy', 'Integrated oil, refining & marketing');
