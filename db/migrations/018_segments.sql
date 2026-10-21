-- Segment and KPI data (Phase 5): revenue, operating income and other metrics by business
-- segment, geography and product/service, from the inline XBRL of the 10-K/10-Q documents
-- already stored in bronze.filing_documents (no new SEC requests). companyfacts drops every
-- dimensional fact, so these come from the documents themselves.
--
--   bronze.filing_xbrl        one row per 10-K/10-Q document: extracted facts (facts-fetcher's
--                             facts-fetcher-segments job, facts_fetcher/ixbrl.py rules)
--   silver.segment_facts      one row per segment fact per filing (refresh_segment_facts)
--   gold.segment_financials   quarters (reported or derived) and TTM per member, with
--                             provenance (refresh_segment_financials); gold.segments adds
--                             fiscal/calendar labels like gold.financials
--   ops.check_segments        members vs consolidated total, into ops.quality_findings
--
-- Independent of 019/020 (it may be applied after them): it only reads objects from 001-017.

-- Bronze -------------------------------------------------------------------------------------

-- Facts of each stored 10-K/10-Q document. The checkpoint of the loader: a document is done
-- when it has a row with its current content hash and the current extractor version.
CREATE TABLE bronze.filing_xbrl (
    accession_no      text        PRIMARY KEY
                                  REFERENCES bronze.filing_documents ON DELETE CASCADE,
    cik               text        NOT NULL,  -- bronze.filing_documents.cik
    entity_cik        text,                  -- the XBRL contexts' entity identifier
    document_sha256   text        NOT NULL,  -- bronze.filing_documents.content_sha256 read
    extractor_version int         NOT NULL,
    status            text        NOT NULL CHECK (status IN ('ok', 'no_ixbrl', 'error')),
    -- what SQL reads: facts dimensioned only by axes in silver.segment_axes (at most two), plus
    -- the undimensioned facts of the same concepts; [{c, v, u, d, s, e | i, m: {axis: member}}]
    facts             jsonb       NOT NULL DEFAULT '[]',
    labels            jsonb       NOT NULL DEFAULT '{}',  -- member QName → readable label
    raw_gzip          bytea,      -- every numeric fact, gzip JSON, for re-projection
    fact_count        int         NOT NULL DEFAULT 0,     -- numeric facts in the document
    stats             jsonb       NOT NULL DEFAULT '{}',  -- contexts, unsupported, conflicts
    error             text,
    extracted_at      timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE bronze.filing_xbrl ALTER COLUMN raw_gzip SET STORAGE EXTERNAL;  -- gzip already

-- Silver -------------------------------------------------------------------------------------

-- XBRL axes we read, and what each means.
CREATE TABLE silver.segment_axes (
    axis_qname text PRIMARY KEY,
    axis       text NOT NULL CHECK (axis IN ('business_segment', 'geography', 'product_service',
                                             'reconciliation'))
);
INSERT INTO silver.segment_axes VALUES
    ('us-gaap:StatementBusinessSegmentsAxis', 'business_segment'),
    ('srt:StatementGeographicalAxis',         'geography'),
    ('srt:ProductOrServiceAxis',              'product_service'),
    -- corporate, eliminations and other reconciling items between segments and the total
    ('srt:ConsolidationItemsAxis',            'reconciliation');

CREATE TABLE silver.segment_metrics (
    metric     text    PRIMARY KEY,
    unit       text    NOT NULL,
    additive   boolean NOT NULL,  -- can a quarter be derived from year-to-date values?
    definition text    NOT NULL
);
INSERT INTO silver.segment_metrics VALUES
    ('revenue',                   'USD', true, 'revenue of the member'),
    ('operating_income',          'USD', true, 'segment operating income (loss)'),
    ('gross_profit',              'USD', true, 'gross profit of the member'),
    ('depreciation_amortization', 'USD', true, 'depreciation and amortization of the member'),
    ('capex',                     'USD', true, 'capital expenditure of the member');

-- Concepts feeding each metric (lowest priority wins). Any QName, so a company's own KPI tag
-- (e.g. a custom subscriber count) can be mapped by a later migration.
CREATE TABLE silver.segment_metric_tags (
    metric   text NOT NULL REFERENCES silver.segment_metrics,
    tag      text NOT NULL,  -- QName, e.g. us-gaap:Revenues
    priority int  NOT NULL,
    PRIMARY KEY (metric, tag),
    UNIQUE (metric, priority)
);
INSERT INTO silver.segment_metric_tags VALUES
    ('revenue', 'us-gaap:RevenuesNetOfInterestExpense', 1),
    ('revenue', 'us-gaap:Revenues', 2),
    ('revenue', 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax', 3),
    ('revenue', 'us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax', 4),
    ('revenue', 'us-gaap:SalesRevenueNet', 5),
    ('operating_income', 'us-gaap:OperatingIncomeLoss', 1),
    ('operating_income', 'us-gaap:SegmentReportingInformationOperatingIncomeLoss', 2),
    ('gross_profit', 'us-gaap:GrossProfit', 1),
    ('depreciation_amortization', 'us-gaap:DepreciationDepletionAndAmortization', 1),
    ('depreciation_amortization', 'us-gaap:DepreciationAndAmortization', 2),
    ('capex', 'us-gaap:PaymentsToAcquirePropertyPlantAndEquipment', 1),
    ('capex', 'us-gaap:SegmentExpenditureAdditionToLongLivedAssets', 2);

-- One row per segment fact per filing. axis 'total' holds the undimensioned (consolidated)
-- value of the same concept, member ''. Keyed by CIK: join through silver.company_ciks.
CREATE TABLE silver.segment_facts (
    fact_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cik          text        NOT NULL,
    accession_no text        NOT NULL REFERENCES bronze.filing_xbrl ON DELETE CASCADE,
    form         text,
    filed        date        NOT NULL,
    metric       text        NOT NULL REFERENCES silver.segment_metrics,
    concept      text        NOT NULL,
    axis         text        NOT NULL,  -- business_segment | geography | product_service |
                                        -- reconciliation | total
    axis_qname   text        NOT NULL,  -- '' for total
    member       text        NOT NULL,  -- QName; '' for total
    member_label text        NOT NULL,
    unit         text        NOT NULL,
    period_start date        NOT NULL,
    period_end   date        NOT NULL,
    period_type  text        GENERATED ALWAYS AS (
        CASE
            WHEN period_end - period_start BETWEEN 80 AND 120 THEN 'quarter'
            WHEN period_end - period_start BETWEEN 350 AND 380 THEN 'annual'
            ELSE 'ytd'
        END
    ) STORED,
    value        numeric     NOT NULL,
    decimals     text,
    ingested_at  timestamptz NOT NULL,  -- when the document was fetched
    UNIQUE (accession_no, concept, unit, axis_qname, member, period_start, period_end)
);
CREATE INDEX segment_facts_cik ON silver.segment_facts (cik, metric);

-- The segment a fact's dimensions ({axis: member}) put it in: none → total; one axis from
-- silver.segment_axes → that axis; two when one of them is ConsolidationItemsAxis =
-- OperatingSegmentsMember (filers tag segment figures both ways) → the other. Anything else
-- (two real axes, unknown axes) → no row. ConsolidationItemsAxis = OperatingSegmentsMember on
-- its own is the sum of the segments, not a member, and is skipped too.
CREATE FUNCTION silver.segment_of(m jsonb)
RETURNS TABLE (axis text, axis_qname text, member text)
LANGUAGE sql STABLE AS $$
    WITH d AS (
        SELECT e.key, e.value #>> '{}' AS member, a.axis
        FROM jsonb_each(coalesce(m, '{}')) e
        LEFT JOIN silver.segment_axes a ON a.axis_qname = e.key
    ),
    s AS (
        SELECT * FROM d
        WHERE NOT (key = 'srt:ConsolidationItemsAxis' AND member = 'us-gaap:OperatingSegmentsMember')
    )
    SELECT 'total', '', '' WHERE NOT EXISTS (SELECT 1 FROM d)
    UNION ALL
    SELECT s.axis, s.key, s.member FROM s
    WHERE s.axis IS NOT NULL AND (SELECT count(*) FROM s) = 1
$$;

-- Re-derive the silver rows of these filings from bronze. Idempotent. Returns rows written.
CREATE FUNCTION silver.refresh_segment_facts(p_accessions text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    DELETE FROM silver.segment_facts WHERE accession_no = ANY(p_accessions);
    INSERT INTO silver.segment_facts
        (cik, accession_no, form, filed, metric, concept, axis, axis_qname, member,
         member_label, unit, period_start, period_end, value, decimals, ingested_at)
    SELECT x.cik, x.accession_no, d.form, d.filed, t.metric, f->>'c', s.axis, s.axis_qname,
           s.member, coalesce(x.labels->>s.member, ''), f->>'u', (f->>'s')::date,
           (f->>'e')::date, (f->>'v')::numeric, f->>'d', d.fetched_at
    FROM bronze.filing_xbrl x
    JOIN bronze.filing_documents d ON d.accession_no = x.accession_no
    CROSS JOIN LATERAL jsonb_array_elements(x.facts) f
    JOIN silver.segment_metric_tags t ON t.tag = f->>'c'
    JOIN silver.segment_metrics m ON m.metric = t.metric AND m.unit = f->>'u'
    CROSS JOIN LATERAL silver.segment_of(f->'m') s
    WHERE x.accession_no = ANY(p_accessions) AND x.status = 'ok'
      AND f ? 's' AND f ? 'e' AND (f->>'e')::date > (f->>'s')::date
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END
$$;

-- Gold ---------------------------------------------------------------------------------------

-- One value per (company, span, axis, member, metric, period end), with provenance (FR6).
CREATE TABLE gold.segment_financials (
    company_id        text        NOT NULL REFERENCES silver.companies ON DELETE CASCADE,
    span              text        NOT NULL CHECK (span IN ('quarter', 'ttm')),
    axis              text        NOT NULL,
    member            text        NOT NULL,  -- QName as last filed
    member_label      text        NOT NULL,
    member_aliases    text[]      NOT NULL DEFAULT '{}',  -- earlier QNames merged into it
    metric            text        NOT NULL REFERENCES silver.segment_metrics,
    period_start      date        NOT NULL,
    period_end        date        NOT NULL,
    value             numeric     NOT NULL,
    unit              text        NOT NULL,
    -- reported: the filing states this quarter; derived: difference of two year-to-date
    -- values (Q4 = FY - 9 months); ttm: sum of four contiguous quarters
    value_source      text        NOT NULL CHECK (value_source IN ('reported', 'derived', 'ttm')),
    source_tag        text        NOT NULL,  -- XBRL concept (TTM: the latest quarter's)
    axis_qname        text        NOT NULL,
    accession_no      text        NOT NULL,  -- filing to cite (the later one, when derived)
    filer_cik         text        NOT NULL,  -- its filer, for the EDGAR link
    form              text,
    filed             date        NOT NULL,
    source_accessions text[]      NOT NULL,  -- every filing the value depends on
    ingested_at       timestamptz NOT NULL,
    refreshed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, span, axis, member, metric, period_end)
);

-- Rebuild these companies' segment quarters and TTM from silver. Rules:
--   1. Members are matched across filings (renames):
--      a. per company and axis, members whose labels fold to the same key (case, spaces,
--         punctuation, "&" = "and"; e.g. nvda:ComputeAndNetworkingMember and
--         nvda:ComputeAndNetworkingSegmentMember, or a prefix change) are one member, under
--         the QName filed most recently;
--      b. a member X whose last filing precedes the first filing of member Y, where both
--         report the same non-zero value for the same metric and period (the new filing's
--         restated comparatives), was renamed to Y; X takes the Y with the most such matches
--         (then the latest filed), and chains X → Y → Z resolve to Z. Splits and merges
--         (no equal values) are not matched: the old member's history just ends.
--      The merged QNames are kept in member_aliases; the label is the latest filing's.
--   2-5. As gold.refresh_quarterly_financials (015): per period the highest-priority tag,
--      then the latest filing; reported quarters as-is; quarters of additive metrics derived
--      from consecutive year-to-date values sharing a start date 80-120 days apart (Q4 = FY -
--      9 months; 12/16-week quarters fit); per quarter the preferred tag, then reported over
--      derived, then the latest filing; deterministic tie-breaks.
--   6. TTM: four contiguous quarters (each starting 0-7 days after the previous ends) spanning
--      350-380 days, as gold.refresh_derived_financials (014).
CREATE FUNCTION gold.refresh_segment_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('gold.segment_financials'), hashtext(id))
    FROM (SELECT DISTINCT id FROM unnest(p_company_ids) id ORDER BY id) ids;

    DELETE FROM gold.segment_financials WHERE company_id = ANY(p_company_ids);

    WITH RECURSIVE facts AS (
        SELECT k.company_id, f.*, t.priority, m.additive,
               regexp_replace(lower(replace(f.member_label, '&', ' and ')), '[^a-z0-9]+', '',
                              'g') AS label_key
        FROM silver.company_ciks k
        JOIN silver.segment_facts f ON f.cik = k.cik
        JOIN silver.segment_metric_tags t ON t.metric = f.metric AND t.tag = f.concept
        JOIN silver.segment_metrics m ON m.metric = f.metric AND m.unit = f.unit
        WHERE k.company_id = ANY(p_company_ids) AND f.axis <> 'total'
    ),
    by_key AS (  -- 1a: one QName per label key, the one filed most recently
        SELECT DISTINCT ON (company_id, axis, label_key)
               company_id, axis, label_key, member AS canon
        FROM facts
        ORDER BY company_id, axis, label_key, filed DESC, accession_no DESC, member
    ),
    keyed AS (
        SELECT f.*, b.canon
        FROM facts f
        JOIN by_key b USING (company_id, axis, label_key)
    ),
    spans AS (
        SELECT company_id, axis, canon, min(filed) AS first_filed, max(filed) AS last_filed
        FROM keyed
        GROUP BY company_id, axis, canon
    ),
    matches AS (  -- 1b: equal values for the same period, the old member gone before the new
        SELECT a.company_id, a.axis, a.canon AS old, b.canon AS new, count(*) AS n,
               max(sb.last_filed) AS new_last_filed
        FROM keyed a
        JOIN keyed b ON b.company_id = a.company_id AND b.axis = a.axis
                    AND b.metric = a.metric AND b.period_start = a.period_start
                    AND b.period_end = a.period_end AND b.value = a.value
                    AND b.canon <> a.canon
        JOIN spans sa ON sa.company_id = a.company_id AND sa.axis = a.axis
                     AND sa.canon = a.canon
        JOIN spans sb ON sb.company_id = b.company_id AND sb.axis = b.axis
                     AND sb.canon = b.canon
        WHERE a.value <> 0 AND sa.last_filed < sb.first_filed
        GROUP BY a.company_id, a.axis, a.canon, b.canon
    ),
    renames AS (
        SELECT DISTINCT ON (company_id, axis, old) company_id, axis, old, new
        FROM matches
        ORDER BY company_id, axis, old, n DESC, new_last_filed DESC, new
    ),
    chain (company_id, axis, old, new, depth) AS (
        SELECT company_id, axis, old, new, 1 FROM renames
        UNION ALL
        SELECT c.company_id, c.axis, c.old, r.new, c.depth + 1
        FROM chain c
        JOIN renames r ON r.company_id = c.company_id AND r.axis = c.axis AND r.old = c.new
        WHERE c.depth < 20
    ),
    final AS (
        SELECT DISTINCT ON (company_id, axis, old) company_id, axis, old, new
        FROM chain
        ORDER BY company_id, axis, old, depth DESC
    ),
    resolved AS (
        SELECT k.*, coalesce(fn.new, k.canon) AS final_member
        FROM keyed k
        LEFT JOIN final fn ON fn.company_id = k.company_id AND fn.axis = k.axis
                          AND fn.old = k.canon
    ),
    members AS (  -- label of the latest filing, and every QName merged in
        SELECT DISTINCT ON (company_id, axis, final_member)
               company_id, axis, final_member, member_label,
               ARRAY(SELECT DISTINCT r2.member FROM resolved r2
                     WHERE r2.company_id = r.company_id AND r2.axis = r.axis
                       AND r2.final_member = r.final_member
                       AND r2.member <> r.final_member ORDER BY 1) AS aliases
        FROM resolved r
        ORDER BY company_id, axis, final_member, (member = final_member) DESC, filed DESC,
                 accession_no DESC
    ),
    best AS (
        SELECT DISTINCT ON (company_id, axis, final_member, metric, period_start, period_end) *
        FROM resolved
        ORDER BY company_id, axis, final_member, metric, period_start, period_end, priority,
                 filed DESC, accession_no DESC, cik, member
    ),
    year_to_date AS (
        SELECT b.*,
               lag(value)        OVER w AS prev_value,
               lag(period_end)   OVER w AS prev_end,
               lag(accession_no) OVER w AS prev_accession,
               lag(ingested_at)  OVER w AS prev_ingested_at,
               lag(priority)     OVER w AS prev_priority
        FROM best b
        WINDOW w AS (PARTITION BY company_id, axis, final_member, metric, period_start
                     ORDER BY period_end)
    ),
    candidates AS (
        SELECT company_id, axis, final_member, metric, period_start, period_end, value, unit,
               'reported' AS value_source, concept, axis_qname, accession_no, cik, form, filed,
               ARRAY[accession_no] AS source_accessions, ingested_at, priority
        FROM best
        WHERE period_type = 'quarter'
        UNION ALL
        SELECT company_id, axis, final_member, metric, prev_end + 1, period_end,
               value - prev_value, unit, 'derived', concept, axis_qname, accession_no, cik,
               form, filed, ARRAY[prev_accession, accession_no],
               greatest(ingested_at, prev_ingested_at), greatest(priority, prev_priority)
        FROM year_to_date
        WHERE additive AND period_end - prev_end BETWEEN 80 AND 120
    ),
    quarters AS (
        SELECT DISTINCT ON (company_id, axis, final_member, metric, period_end) *
        FROM candidates
        ORDER BY company_id, axis, final_member, metric, period_end, priority,
                 value_source = 'reported' DESC, filed DESC, accession_no DESC,
                 source_accessions DESC, period_start DESC, concept, value, ingested_at DESC,
                 cik
    )
    INSERT INTO gold.segment_financials
        (company_id, span, axis, member, member_label, member_aliases, metric, period_start,
         period_end, value, unit, value_source, source_tag, axis_qname, accession_no,
         filer_cik, form, filed, source_accessions, ingested_at)
    SELECT q.company_id, 'quarter', q.axis, q.final_member, m.member_label, m.aliases, q.metric,
           q.period_start, q.period_end, q.value, q.unit, q.value_source, q.concept,
           q.axis_qname, q.accession_no, q.cik, q.form, q.filed, q.source_accessions,
           q.ingested_at
    FROM quarters q
    JOIN members m ON m.company_id = q.company_id AND m.axis = q.axis
                  AND m.final_member = q.final_member;

    WITH quarters AS (
        SELECT g.*,
               coalesce(g.period_start - lag(g.period_end) OVER w BETWEEN 0 AND 7, false)
                   AS follows
        FROM gold.segment_financials g
        JOIN silver.segment_metrics m ON m.metric = g.metric
        WHERE g.company_id = ANY(p_company_ids) AND g.span = 'quarter' AND m.additive
        WINDOW w AS (PARTITION BY g.company_id, g.axis, g.member, g.metric ORDER BY g.period_end)
    ),
    windows AS (
        SELECT q.*,
               count(*) OVER w4 AS n_quarters,
               bool_and(follows) OVER w3 AS contiguous,
               first_value(period_start) OVER w4 AS ttm_start,
               sum(value) OVER w4 AS ttm_value,
               max(ingested_at) OVER w4 AS ttm_ingested_at,
               source_accessions || lag(source_accessions, 1) OVER w
                   || lag(source_accessions, 2) OVER w || lag(source_accessions, 3) OVER w
                   AS accessions
        FROM quarters q
        WINDOW w AS (PARTITION BY company_id, axis, member, metric ORDER BY period_end),
               w4 AS (w ROWS 3 PRECEDING),
               w3 AS (w ROWS 2 PRECEDING)
    )
    INSERT INTO gold.segment_financials
        (company_id, span, axis, member, member_label, member_aliases, metric, period_start,
         period_end, value, unit, value_source, source_tag, axis_qname, accession_no,
         filer_cik, form, filed, source_accessions, ingested_at)
    SELECT company_id, 'ttm', axis, member, member_label, member_aliases, metric, ttm_start,
           period_end, ttm_value, unit, 'ttm', source_tag, axis_qname, accession_no, filer_cik,
           form, filed, ARRAY(SELECT DISTINCT a FROM unnest(accessions) a ORDER BY 1),
           ttm_ingested_at
    FROM windows
    WHERE n_quarters = 4 AND contiguous AND period_end - ttm_start + 1 BETWEEN 350 AND 380;

    SELECT count(*) INTO n FROM gold.segment_financials
    WHERE company_id = ANY(p_company_ids) AND span = 'quarter';
    RETURN n;
END
$$;

-- Segment rows with fiscal and calendar labels, by the rules of gold.financials (014): fiscal
-- year from silver.companies.fiscal_year_end, calendar quarter = the one containing
-- period_end - 35 days, calendar_primary = the row to show per calendar quarter (most overlap,
-- then the latest end).
CREATE VIEW gold.segments WITH (security_invoker = true) AS
WITH labeled AS (
    SELECT s.*,
           gold.fiscal_year_end(s.period_end, c.fiscal_year_end) AS fiscal_year_end,
           gold.calendar_quarter(s.period_end) AS calendar_start
    FROM gold.segment_financials s
    JOIN silver.companies c ON c.company_id = s.company_id
)
SELECT company_id, span, axis, member, member_label, member_aliases, metric, period_start,
       period_end,
       extract(year FROM fiscal_year_end - 7)::int AS fiscal_year,
       CASE WHEN fiscal_year_end IS NOT NULL
            THEN greatest(1, least(4, 4 - round((fiscal_year_end - period_end) / 91.31)::int))
       END AS fiscal_quarter,
       extract(year FROM calendar_start)::int AS calendar_year,
       extract(quarter FROM calendar_start)::int AS calendar_quarter,
       row_number() OVER (
           PARTITION BY company_id, span, axis, member, metric, calendar_start
           ORDER BY least(period_end, (calendar_start + interval '3 months')::date - 1)
                    - greatest(period_start, calendar_start) DESC,
                    period_end DESC
       ) = 1 AS calendar_primary,
       value, unit, value_source, source_tag, axis_qname, accession_no, filer_cik, form, filed,
       source_accessions, ingested_at
FROM labeled;

-- Query API: RLS like gold.quarterly_financials (financials tier).
GRANT SELECT ON gold.segment_financials, gold.segments, silver.segment_metrics,
    silver.segment_axes TO query_api;

ALTER TABLE gold.segment_financials ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_financials ON gold.segment_financials FOR SELECT TO query_api
    USING (EXISTS (
        SELECT 1 FROM authz.user_access a
        WHERE a.user_id = current_setting('app.user_id', true)
          AND a.company_id = segment_financials.company_id
          AND a.tier = 'financials'
    ));

-- Data quality --------------------------------------------------------------------------------

-- Check these companies' segment quarters against their consolidated gold quarters; replaces
-- their segment_* findings (check_financials keeps them, see below). Check (warning):
--   segment_sum_<axis>: the members of an axis don't add up to gold.quarterly_financials'
--     value for the same metric and period end within 1% (revenue on every axis; operating
--     income on business segments only, where reconciliation members - corporate,
--     eliminations - are added in). Members that overlap by one subtotal (Apple's Products
--     next to iPhone/Mac/iPad; NVIDIA's Data Center next to its parts) pass: the sum minus
--     any one member may match instead. Needs at least two members.
-- detail holds the axis, member count, relative deviation and filing, never values.
CREATE FUNCTION ops.check_segments(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    run bigint;
    n integer;
BEGIN
    INSERT INTO ops.quality_runs (companies)
    SELECT count(DISTINCT id) FROM unnest(p_company_ids) id
    RETURNING run_id INTO run;

    DELETE FROM ops.quality_findings
    WHERE company_id = ANY(p_company_ids) AND check_name LIKE 'segment\_%';

    WITH members AS (
        SELECT company_id,
               CASE WHEN axis = 'reconciliation' THEN 'business_segment' ELSE axis END
                   AS checked_axis,
               axis, metric, period_end, value, accession_no
        FROM gold.segment_financials
        WHERE company_id = ANY(p_company_ids) AND span = 'quarter'
          AND (metric = 'revenue'
               OR (metric = 'operating_income' AND axis IN ('business_segment',
                                                            'reconciliation')))
    ),
    sums AS (
        SELECT company_id, checked_axis, metric, period_end,
               sum(value) AS total, array_agg(value) AS parts,
               count(*) FILTER (WHERE axis <> 'reconciliation') AS n_members,
               max(accession_no) AS accession_no
        FROM members
        GROUP BY company_id, checked_axis, metric, period_end
    ),
    findings AS (
        SELECT s.company_id, 'segment_sum_' || s.checked_axis AS check_name, s.metric,
               s.period_end, 'warning' AS severity,
               jsonb_build_object('axis', s.checked_axis, 'members', s.n_members,
                                  'deviation', round((s.total - g.value) / abs(g.value), 4),
                                  'accession_no', s.accession_no,
                                  'total_accession', g.accession_no) AS detail
        FROM sums s
        JOIN gold.quarterly_financials g
          ON g.company_id = s.company_id AND g.metric = s.metric
         AND g.period_end = s.period_end
        WHERE s.n_members >= 2 AND g.value <> 0
          AND abs(s.total - g.value) > 0.01 * abs(g.value)
          AND NOT EXISTS (SELECT 1 FROM unnest(s.parts) p
                          WHERE abs(s.total - p - g.value) <= 0.01 * abs(g.value))
    )
    INSERT INTO ops.quality_findings
        (company_id, check_name, metric, period_end, severity, detail, run_id)
    SELECT company_id, check_name, metric, period_end, severity, detail, run
    FROM findings
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;

    UPDATE ops.quality_runs SET findings = n, by_check = coalesce((
        SELECT jsonb_object_agg(check_name, c)
        FROM (SELECT check_name, count(*) AS c FROM ops.quality_findings
              WHERE run_id = run GROUP BY check_name) x), '{}')
    WHERE run_id = run;

    INSERT INTO ops.quality_companies (company_id, run_id, checked_at, errors, warnings)
    SELECT c.company_id, run, now(),
           count(f.*) FILTER (WHERE f.severity = 'error'),
           count(f.*) FILTER (WHERE f.severity = 'warning')
    FROM silver.companies c
    LEFT JOIN ops.quality_findings f ON f.company_id = c.company_id
    WHERE c.company_id = ANY(p_company_ids)
    GROUP BY c.company_id
    ON CONFLICT (company_id) DO UPDATE SET run_id = EXCLUDED.run_id,
        checked_at = EXCLUDED.checked_at, errors = EXCLUDED.errors,
        warnings = EXCLUDED.warnings;

    RETURN n;
END
$$;

-- Rebuild these companies' segment gold rows, then check them. Returns the quarterly rows.
CREATE FUNCTION gold.refresh_segments(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    n := gold.refresh_segment_financials(p_company_ids);
    PERFORM ops.check_segments(p_company_ids);
    RETURN n;
END
$$;

-- 014's check, unchanged except that it no longer deletes the segment_* findings (they belong
-- to ops.check_segments, which runs on its own schedule).
CREATE OR REPLACE FUNCTION ops.check_financials(p_company_ids text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    run bigint;
    n integer;
BEGIN
    INSERT INTO ops.quality_runs (companies)
    SELECT count(DISTINCT id) FROM unnest(p_company_ids) id
    RETURNING run_id INTO run;

    DELETE FROM ops.quality_findings
    WHERE company_id = ANY(p_company_ids) AND check_name NOT LIKE 'segment\_%';

    WITH q AS (
        SELECT g.company_id, g.metric, g.period_start, g.period_end, g.value, m.kind,
               m.additive, g.accession_no,
               g.period_start - lag(g.period_end) OVER w AS gap,
               lag(g.value) OVER w AS prev_value,
               lead(g.value) OVER w AS next_value
        FROM gold.quarterly_financials g
        JOIN silver.metrics m ON m.metric = g.metric
        WHERE g.company_id = ANY(p_company_ids)
        WINDOW w AS (PARTITION BY g.company_id, g.metric ORDER BY g.period_end)
    ),
    runs AS (  -- durations: are the three quarters before this one contiguous?
        SELECT q.*,
               count(*) FILTER (WHERE gap BETWEEN 0 AND 7) OVER (
                   PARTITION BY company_id, metric ORDER BY period_end
                   ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS contiguous_before
        FROM q
        WHERE kind = 'duration'
    ),
    pairs AS (
        SELECT company_id, period_end,
               max(value) FILTER (WHERE metric = 'revenue') AS revenue,
               max(value) FILTER (WHERE metric = 'gross_profit') AS gross_profit,
               max(value) FILTER (WHERE metric = 'current_assets') AS current_assets,
               max(value) FILTER (WHERE metric = 'total_assets') AS total_assets,
               max(value) FILTER (WHERE metric = 'current_liabilities') AS current_liabilities,
               max(value) FILTER (WHERE metric = 'total_liabilities') AS total_liabilities
        FROM q
        GROUP BY company_id, period_end
    ),
    annual AS (  -- each reported fiscal year, picked like gold picks quarters
        SELECT DISTINCT ON (k.company_id, f.metric, f.period_start, f.period_end)
               k.company_id, f.metric, f.period_start, f.period_end, f.value, f.accession_no
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.metric = f.metric AND t.tag = f.tag
        JOIN silver.metrics m ON m.metric = f.metric AND m.unit = f.unit
        WHERE k.company_id = ANY(p_company_ids) AND f.period_type = 'annual' AND m.additive
        ORDER BY k.company_id, f.metric, f.period_start, f.period_end, t.priority,
                 f.filed DESC, f.accession_no DESC
    ),
    findings AS (
        SELECT company_id, 'impossible_value' AS check_name, metric, period_end,
               'error' AS severity, jsonb_build_object('rule', 'negative',
                                                       'accession_no', accession_no) AS detail
        FROM q
        WHERE value < 0 AND metric IN (
            'revenue', 'total_assets', 'current_assets', 'total_liabilities',
            'current_liabilities', 'cash', 'short_term_investments', 'inventory',
            'receivables', 'shares_outstanding', 'weighted_shares_basic',
            'weighted_shares_diluted')
        UNION ALL
        SELECT company_id, 'impossible_value', r.metric, period_end, 'error',
               jsonb_build_object('rule', r.rule)
        FROM pairs
        CROSS JOIN LATERAL (VALUES
            ('current_assets', 'current_assets > total_assets', current_assets > total_assets),
            ('current_liabilities', 'current_liabilities > total_liabilities',
             current_liabilities > total_liabilities),
            ('gross_profit', 'gross_profit > revenue', revenue > 0 AND gross_profit > revenue)
        ) r(metric, rule, failed)
        WHERE r.failed
        UNION ALL
        SELECT company_id, 'overlapping_quarters', metric, period_end, 'error',
               jsonb_build_object('overlap_days', -gap, 'accession_no', accession_no)
        FROM runs
        WHERE gap < 0
        UNION ALL
        SELECT company_id, 'calendar_collision', metric, max(period_end), 'warning',
               jsonb_build_object('calendar_quarter', to_char(cq, 'YYYY"Q"Q'),
                                  'period_ends', array_agg(period_end ORDER BY period_end))
        FROM (SELECT r.*, gold.calendar_quarter(period_end) AS cq FROM runs r) c
        GROUP BY company_id, metric, cq
        HAVING count(*) > 1
        UNION ALL
        SELECT d.company_id, 'annual_sum', d.metric, d.period_end, 'warning',
               jsonb_build_object('deviation', round((d.value - a.value) / abs(a.value), 4),
                                  'annual_accession', a.accession_no,
                                  'quarter_accessions', d.source_accessions)
        FROM gold.derived_financials d
        JOIN annual a ON a.company_id = d.company_id AND a.metric = d.metric
                     AND a.period_end = d.period_end
                     AND abs(a.period_start - d.period_start) <= 7
        WHERE d.company_id = ANY(p_company_ids) AND d.span = 'ttm' AND d.value_source = 'ttm'
          AND a.value <> 0
          AND abs(d.value - a.value) > 0.01 * abs(a.value)
        UNION ALL
        SELECT company_id, 'series_gap', metric, period_end, 'warning',
               jsonb_build_object('missing_from', period_start - gap + 1,
                                  'missing_to', period_start - 1)
        FROM runs
        WHERE additive AND gap > 7 AND contiguous_before = 3
        UNION ALL
        SELECT company_id, 'sign_flip', metric, period_end, 'warning',
               jsonb_build_object('accession_no', accession_no)
        FROM q
        WHERE metric IN ('cost_of_revenue', 'research_and_development', 'sga',
                         'interest_expense', 'capex', 'depreciation_amortization',
                         'stock_based_compensation', 'dividends_paid', 'share_buybacks')
          AND value <> 0 AND prev_value <> 0 AND next_value <> 0
          AND sign(value) <> sign(prev_value) AND sign(prev_value) = sign(next_value)
        UNION ALL
        SELECT company_id, 'outlier', metric, period_end, 'warning',
               jsonb_build_object('vs_neighbours',
                                  round(value / greatest(abs(prev_value), abs(next_value)), 1),
                                  'accession_no', accession_no)
        FROM q
        WHERE prev_value <> 0 AND next_value <> 0 AND sign(prev_value) = sign(next_value)
          AND greatest(abs(prev_value), abs(next_value))
              <= 2 * least(abs(prev_value), abs(next_value))
          AND abs(value) >= 10 * greatest(abs(prev_value), abs(next_value))
    )
    INSERT INTO ops.quality_findings
        (company_id, check_name, metric, period_end, severity, detail, run_id)
    SELECT company_id, check_name, metric, period_end, severity, detail, run
    FROM findings
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;

    UPDATE ops.quality_runs SET findings = n, by_check = coalesce((
        SELECT jsonb_object_agg(check_name, c)
        FROM (SELECT check_name, count(*) AS c FROM ops.quality_findings
              WHERE run_id = run GROUP BY check_name) x), '{}')
    WHERE run_id = run;

    INSERT INTO ops.quality_companies (company_id, run_id, checked_at, errors, warnings)
    SELECT c.company_id, run, now(),
           count(f.*) FILTER (WHERE f.severity = 'error'),
           count(f.*) FILTER (WHERE f.severity = 'warning')
    FROM silver.companies c
    LEFT JOIN ops.quality_findings f ON f.company_id = c.company_id
    WHERE c.company_id = ANY(p_company_ids)
    GROUP BY c.company_id
    ON CONFLICT (company_id) DO UPDATE SET run_id = EXCLUDED.run_id,
        checked_at = EXCLUDED.checked_at, errors = EXCLUDED.errors,
        warnings = EXCLUDED.warnings;

    RETURN n;
END
$$;
-- 014's summary, with the segment checks.
CREATE OR REPLACE VIEW ops.quality_summary AS
SELECT ch.check_name, ch.severity,
       count(f.company_id) AS findings,
       count(DISTINCT f.company_id) AS companies,
       (SELECT count(*) FROM ops.quality_companies) AS companies_checked,
       (SELECT max(checked_at) FROM ops.quality_runs) AS last_checked_at
FROM (VALUES ('impossible_value', 'error'), ('overlapping_quarters', 'error'),
             ('calendar_collision', 'warning'), ('annual_sum', 'warning'),
             ('series_gap', 'warning'), ('sign_flip', 'warning'), ('outlier', 'warning'),
             ('segment_sum_business_segment', 'warning'), ('segment_sum_geography', 'warning'),
             ('segment_sum_product_service', 'warning'))
     ch(check_name, severity)
LEFT JOIN ops.quality_findings f ON f.check_name = ch.check_name
GROUP BY ch.check_name, ch.severity;

-- Loader progress, one row per form: stored 10-K/10-Q documents, how many are extracted with
-- the current content, by outcome, and the latest extraction. Counts only.
CREATE VIEW ops.segment_load_status AS
SELECT d.form,
       count(*) AS documents,
       count(x.accession_no) FILTER (WHERE x.document_sha256 = d.content_sha256) AS extracted,
       count(*) FILTER (WHERE x.accession_no IS NULL
                          OR x.document_sha256 <> d.content_sha256) AS pending,
       count(*) FILTER (WHERE x.status = 'ok') AS ok,
       count(*) FILTER (WHERE x.status = 'no_ixbrl') AS no_ixbrl,
       count(*) FILTER (WHERE x.status = 'error') AS errors,
       min(x.extractor_version) AS min_extractor_version,
       max(x.extracted_at) AS last_extracted_at
FROM bronze.filing_documents d
LEFT JOIN bronze.filing_xbrl x ON x.accession_no = d.accession_no
WHERE d.form IN ('10-K', '10-K/A', '10-Q', '10-Q/A')
GROUP BY d.form;

GRANT SELECT ON ops.segment_load_status TO ops_reader;
