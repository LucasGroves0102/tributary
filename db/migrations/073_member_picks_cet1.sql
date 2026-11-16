-- Member picks: one dimensioned fact a company files where others file an undimensioned one (data
-- trust, part 2; db/DESIGN.md "073"). First use: the big banks' CET1 ratio.
--
-- 048 maps cet1_ratio to us-gaap:CommonEquityTierOneCapitalRatio, undimensioned. JPMorgan, Bank of
-- America and Wells Fargo file their CET1 ratio only with dimensions, so companyfacts (which drops
-- dimensional facts) has none and their bank Ratios tab showed no CET1. Each files the ratio for the
-- parent company (srt:ConsolidatedEntitiesAxis = srt:ParentCompanyMember) under each Basel III
-- approach (us-gaap:RiskWeightedAssetsCalculationMethodologyAxis): the standardized ratio is the one
-- a bank leads with, so that is the pick. Read from every stored 10-K/10-Q extract on hermes
-- (bronze.filing_xbrl.raw_gzip, 2018-10 to 2026-11, read-only, 2026-11-16):
--   JPMorgan     jpm:CommonEquityTier1CapitaltoRiskWeightedAssets (its own concept), parent,
--                jpm:BaselIIIStandardizedMember from the Q1 2020 10-Q; before it the 2018-19 filings
--                name jpm:BaselIIIStandardizedTransitionalMember and, in 2019,
--                jpm:BaselIIIStandardizedFullyPhasedInMember: 32 quarter ends 2017-12 to 2026-09
--                (0.140 at 2026-09-30, 0.146 at 2025-12-31), every quarter but 2019-06 and 2019-09
--                without the fully phased-in pick, all of them with it.
--   Bank of America  us-gaap:CommonEquityTierOneCapitalRatio, parent, us-gaap:StandardizedApproachMember,
--                only in its 10-Ks (its 10-Qs tag no capital ratio): nine year ends, 0.114 at
--                2025-12-31.
--   Wells Fargo  the same concept and members in its 10-Qs (its 10-K's ratios are in the annual
--                report exhibit, not the stored document): 31 quarter ends 2018-12 to 2026-09, each
--                December from the next Q1 10-Q's comparatives; 0.1003 at 2026-09-30.
-- A filing never had two values for one pick and date. The subsidiary banks' ratios (dei:LegalEntityAxis,
-- srt:SubsidiariesMember) and the advanced approach are other dimension sets, never read.
--
-- silver.member_picks names, per company, a concept and the exact set of dimensions to read (every
-- axis listed, no other: Bank of America's subsidiary ratio carries a third axis). Its silver tag
-- spells the members out (055's convention for member sums), source = 'filing_extra', and a
-- silver.metric_tags row says which metric it is: the company's own row for its own concept
-- (JPMorgan's), one shared row for a us-gaap concept and standard members (metric_tags' key has no
-- company; no companyfacts or undimensioned fact is ever spelled with members).
-- facts-fetcher-filings projects the picked facts into bronze.filing_facts and
-- silver.refresh_filing_facts (056's body + one INSERT marked "073") maps them.
--
-- How it reaches hermes: the daily filing-facts job's extras pass reads the stored documents of every
-- company with a pick (a company the job never read: every document with an extract; a company read
-- before: every document projected before its latest pick's added_at), then refreshes their gold.
-- Nothing by hand, no rules_version bump (055's precedent: only these three companies' facts change,
-- and the job rebuilds their gold); about 300 documents, one run (FILING_FACTS_LIMIT 500).
--
-- Locks on hermes (lock_timeout 3 s): a new table; INSERTs into silver.metric_tags (row locks; the
-- jobs read it at a run's start); CREATE OR REPLACE of silver.refresh_filing_facts (catalog only).
-- Undo: 056's silver.refresh_filing_facts body, DELETE the metric_tags rows with a '[' in a cet1 tag,
-- DROP TABLE silver.member_picks; the filing-facts job's --rebuild then drops the picked facts.

SET LOCAL lock_timeout = '3s';

-- No FK to silver.companies: test fixtures truncate it (047's reason).
CREATE TABLE silver.member_picks (
    company_id text        NOT NULL,
    tag        text        NOT NULL,  -- the silver tag, e.g. CommonEquityTierOneCapitalRatio[srt:ParentCompanyMember+us-gaap:StandardizedApproachMember]
    concept    text        NOT NULL,  -- us-gaap:CommonEquityTierOneCapitalRatio
    members    jsonb       NOT NULL CHECK (jsonb_typeof(members) = 'object' AND members <> '{}'),
                                      -- axis -> member, exactly the fact's dimensions
    added_at   timestamptz NOT NULL DEFAULT now(),  -- the job re-reads documents projected before it
    PRIMARY KEY (company_id, tag)
);

INSERT INTO silver.member_picks (company_id, tag, concept, members) VALUES
    ('0000019617',
     'CommonEquityTier1CapitaltoRiskWeightedAssets[srt:ParentCompanyMember+jpm:BaselIIIStandardizedMember]',
     'jpm:CommonEquityTier1CapitaltoRiskWeightedAssets',
     '{"srt:ConsolidatedEntitiesAxis": "srt:ParentCompanyMember",
       "us-gaap:RiskWeightedAssetsCalculationMethodologyAxis": "jpm:BaselIIIStandardizedMember"}'),
    ('0000019617',
     'CommonEquityTier1CapitaltoRiskWeightedAssets[srt:ParentCompanyMember+jpm:BaselIIIStandardizedFullyPhasedInMember]',
     'jpm:CommonEquityTier1CapitaltoRiskWeightedAssets',
     '{"srt:ConsolidatedEntitiesAxis": "srt:ParentCompanyMember",
       "us-gaap:RiskWeightedAssetsCalculationMethodologyAxis": "jpm:BaselIIIStandardizedFullyPhasedInMember"}'),
    ('0000019617',
     'CommonEquityTier1CapitaltoRiskWeightedAssets[srt:ParentCompanyMember+jpm:BaselIIIStandardizedTransitionalMember]',
     'jpm:CommonEquityTier1CapitaltoRiskWeightedAssets',
     '{"srt:ConsolidatedEntitiesAxis": "srt:ParentCompanyMember",
       "us-gaap:RiskWeightedAssetsCalculationMethodologyAxis": "jpm:BaselIIIStandardizedTransitionalMember"}'),
    ('0000070858',
     'CommonEquityTierOneCapitalRatio[srt:ParentCompanyMember+us-gaap:StandardizedApproachMember]',
     'us-gaap:CommonEquityTierOneCapitalRatio',
     '{"srt:ConsolidatedEntitiesAxis": "srt:ParentCompanyMember",
       "us-gaap:RiskWeightedAssetsCalculationMethodologyAxis": "us-gaap:StandardizedApproachMember"}'),
    ('0000072971',
     'CommonEquityTierOneCapitalRatio[srt:ParentCompanyMember+us-gaap:StandardizedApproachMember]',
     'us-gaap:CommonEquityTierOneCapitalRatio',
     '{"srt:ConsolidatedEntitiesAxis": "srt:ParentCompanyMember",
       "us-gaap:RiskWeightedAssetsCalculationMethodologyAxis": "us-gaap:StandardizedApproachMember"}');

-- Each pick's metric, the standardized pick before the older names of the same ratio (a period two
-- picks state reads the first). JPMorgan's concept is its own (its rows name it); the us-gaap pick is
-- one tag whoever files it, read only where a company has the pick (no undimensioned fact is ever
-- spelled with members, so no other company's facts carry it).
INSERT INTO silver.metric_tags (metric, taxonomy, tag, priority, company_id) VALUES
    ('cet1_ratio', 'jpm',
     'CommonEquityTier1CapitaltoRiskWeightedAssets[srt:ParentCompanyMember+jpm:BaselIIIStandardizedMember]',
     2, '0000019617'),
    ('cet1_ratio', 'jpm',
     'CommonEquityTier1CapitaltoRiskWeightedAssets[srt:ParentCompanyMember+jpm:BaselIIIStandardizedFullyPhasedInMember]',
     3, '0000019617'),
    ('cet1_ratio', 'jpm',
     'CommonEquityTier1CapitaltoRiskWeightedAssets[srt:ParentCompanyMember+jpm:BaselIIIStandardizedTransitionalMember]',
     4, '0000019617'),
    ('cet1_ratio', 'us-gaap',
     'CommonEquityTierOneCapitalRatio[srt:ParentCompanyMember+us-gaap:StandardizedApproachMember]',
     5, NULL);

-- 056's body with the picks' INSERT marked "073" (header)
CREATE OR REPLACE FUNCTION silver.refresh_filing_facts(p_accessions text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
    total integer := 0;
BEGIN
    DELETE FROM silver.financial_facts
    WHERE accession_no = ANY(p_accessions) AND source IN ('filing', 'filing_extra');

    WITH b AS (
        SELECT b.*, k.company_id
        FROM bronze.filing_facts b
        LEFT JOIN silver.company_ciks k ON k.cik = b.cik
        WHERE b.accession_no = ANY(p_accessions)
    ),
    x AS (
        SELECT b.accession_no, b.cik, b.company_id, b.form, b.filed, b.gap, b.superseded_at,
               split_part(f->>'c', ':', 1) AS taxonomy, split_part(f->>'c', ':', 2) AS tag,
               f->>'u' AS unit, (f->>'s')::date AS period_start,
               coalesce(f->>'e', f->>'i')::date AS period_end, (f->>'v')::numeric AS value
        FROM b CROSS JOIN LATERAL jsonb_array_elements(b.facts) f
        WHERE NOT f ? 'm'
    )
    INSERT INTO silver.financial_facts
        (cik, metric, tag, unit, period_start, period_end, value, accession_no, form, filed,
         taxonomy, source)
    SELECT x.cik, t.metric, x.tag, x.unit, x.period_start, x.period_end, x.value, x.accession_no,
           x.form, x.filed, x.taxonomy,
           CASE WHEN t.company_id IS NULL THEN 'filing' ELSE 'filing_extra' END
    FROM x
    JOIN silver.metric_tags t ON t.taxonomy = x.taxonomy AND t.tag = x.tag
    JOIN silver.metrics m ON m.metric = t.metric AND silver.unit_fits(m.unit, x.unit)  -- 056
    WHERE (t.company_id IS NULL AND x.gap AND x.superseded_at IS NULL
           AND NOT EXISTS (SELECT 1 FROM silver.financial_facts c
                           WHERE c.accession_no = x.accession_no AND c.source = 'companyfacts'))
       OR t.company_id = x.company_id
    ORDER BY x.accession_no, x.tag, t.priority
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;
    total := total + n;

    -- member sums (header): every listed member, no other, and no consolidated fact of the concept
    WITH b AS (
        SELECT b.*, k.company_id
        FROM bronze.filing_facts b
        JOIN silver.company_ciks k ON k.cik = b.cik
        WHERE b.accession_no = ANY(p_accessions)
    ),
    x AS (
        SELECT b.accession_no, b.cik, b.company_id, b.form, b.filed, f->>'c' AS concept,
               f->>'u' AS unit, (f->>'s')::date AS period_start,
               coalesce(f->>'e', f->>'i')::date AS period_end, (f->>'v')::numeric AS value,
               (f->'m') - 'srt:ConsolidationItemsAxis'::text AS m,
               f->'m'->>'srt:ConsolidationItemsAxis' AS consolidation
        FROM b CROSS JOIN LATERAL jsonb_array_elements(b.facts) f
        WHERE b.company_id IN (SELECT s.company_id FROM silver.member_sums s)
    ),
    sums AS (
        SELECT x.accession_no, x.cik, x.form, x.filed, s.tag, s.concept, x.unit, x.period_start,
               x.period_end, sum(x.value) AS value
        FROM silver.member_sums s
        JOIN x ON x.company_id = s.company_id AND x.concept = s.concept
              AND x.m ? s.axis AND (SELECT count(*) FROM jsonb_object_keys(x.m)) = 1
              -- a second dimension that says only "operating segments" (018's rule)
              AND coalesce(x.consolidation, 'us-gaap:OperatingSegmentsMember')
                  = 'us-gaap:OperatingSegmentsMember'
        GROUP BY x.accession_no, x.cik, x.form, x.filed, s.tag, s.concept, s.members, x.unit,
                 x.period_start, x.period_end
        HAVING count(DISTINCT x.m->>s.axis) = cardinality(s.members)
           AND bool_and(x.m->>s.axis = ANY(s.members))
    )
    INSERT INTO silver.financial_facts
        (cik, metric, tag, unit, period_start, period_end, value, accession_no, form, filed,
         taxonomy, source)
    SELECT s.cik, t.metric, s.tag, s.unit, s.period_start, s.period_end, s.value, s.accession_no,
           s.form, s.filed, split_part(s.concept, ':', 1), 'filing_extra'
    FROM sums s
    JOIN silver.company_ciks k ON k.cik = s.cik
    JOIN silver.metric_tags t ON t.company_id = k.company_id AND t.tag = s.tag
    JOIN silver.metrics m ON m.metric = t.metric AND silver.unit_fits(m.unit, s.unit)  -- 056
    WHERE NOT EXISTS (
        SELECT 1 FROM bronze.filing_facts b CROSS JOIN LATERAL jsonb_array_elements(b.facts) f
        WHERE b.accession_no = s.accession_no AND f->>'c' = s.concept AND NOT f ? 'm'
          AND coalesce(f->>'e', f->>'i')::date = s.period_end
          AND (f->>'s')::date IS NOT DISTINCT FROM s.period_start)
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;
    total := total + n;

    -- 073: member picks: the facts whose dimensions are exactly a pick's
    INSERT INTO silver.financial_facts
        (cik, metric, tag, unit, period_start, period_end, value, accession_no, form, filed,
         taxonomy, source)
    SELECT b.cik, t.metric, p.tag, f->>'u', (f->>'s')::date, coalesce(f->>'e', f->>'i')::date,
           (f->>'v')::numeric, b.accession_no, b.form, b.filed, split_part(p.concept, ':', 1),
           'filing_extra'
    FROM bronze.filing_facts b
    JOIN silver.company_ciks k ON k.cik = b.cik
    JOIN silver.member_picks p ON p.company_id = k.company_id
    CROSS JOIN LATERAL jsonb_array_elements(b.facts) f
    JOIN silver.metric_tags t ON t.taxonomy = split_part(p.concept, ':', 1) AND t.tag = p.tag
                             AND (t.company_id IS NULL OR t.company_id = k.company_id)
    JOIN silver.metrics m ON m.metric = t.metric AND silver.unit_fits(m.unit, f->>'u')
    WHERE b.accession_no = ANY(p_accessions)
      AND f->>'c' = p.concept AND f->'m' = p.members
    ORDER BY b.accession_no, p.tag, t.priority
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN total + n;
END
$$;
