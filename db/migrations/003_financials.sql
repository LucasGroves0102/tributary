-- XBRL financials (FR2a, FR4, FR6): bronze company facts → silver typed facts → gold quarters.

-- Bronze: each distinct companyfacts payload per CIK, as fetched.
CREATE TABLE bronze.company_facts (
    cik             text        NOT NULL,
    payload_sha256  text        NOT NULL,
    payload         jsonb       NOT NULL,
    fetched_at      timestamptz NOT NULL DEFAULT now(),  -- first fetch of this exact payload
    last_checked_at timestamptz NOT NULL DEFAULT now(),  -- latest fetch that found it unchanged
    PRIMARY KEY (cik, payload_sha256)
);

-- Which metrics we standardize, and which XBRL tags feed each one (lowest priority wins).
CREATE TABLE silver.metrics (
    metric   text    PRIMARY KEY,
    kind     text    NOT NULL CHECK (kind IN ('duration', 'instant')),
    unit     text    NOT NULL,
    additive boolean NOT NULL  -- can a quarter be derived by subtracting year-to-date values?
);

CREATE TABLE silver.metric_tags (
    metric   text NOT NULL REFERENCES silver.metrics,
    taxonomy text NOT NULL DEFAULT 'us-gaap',
    tag      text NOT NULL,
    priority int  NOT NULL,
    PRIMARY KEY (metric, tag),
    UNIQUE (metric, priority)
);

INSERT INTO silver.metrics VALUES
    ('revenue',             'duration', 'USD',        true),
    ('gross_profit',        'duration', 'USD',        true),
    ('operating_income',    'duration', 'USD',        true),
    ('net_income',          'duration', 'USD',        true),
    ('eps_diluted',         'duration', 'USD/shares', false),
    ('operating_cash_flow', 'duration', 'USD',        true),
    ('total_assets',        'instant',  'USD',        false),
    ('cash',                'instant',  'USD',        false);

INSERT INTO silver.metric_tags (metric, tag, priority) VALUES
    -- banks report net revenue here; their plain "Revenues" is gross of interest expense
    ('revenue', 'RevenuesNetOfInterestExpense', 1),
    ('revenue', 'Revenues', 2),
    ('revenue', 'RevenueFromContractWithCustomerExcludingAssessedTax', 3),
    ('revenue', 'RevenueFromContractWithCustomerIncludingAssessedTax', 4),
    ('revenue', 'SalesRevenueNet', 5),  -- pre-2018 filings
    ('gross_profit', 'GrossProfit', 1),
    ('operating_income', 'OperatingIncomeLoss', 1),
    ('net_income', 'NetIncomeLoss', 1),
    ('net_income', 'ProfitLoss', 2),  -- includes noncontrolling interests; fallback only
    ('eps_diluted', 'EarningsPerShareDiluted', 1),
    ('operating_cash_flow', 'NetCashProvidedByUsedInOperatingActivities', 1),
    ('operating_cash_flow', 'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations', 2),
    ('total_assets', 'Assets', 1),
    ('cash', 'CashAndCashEquivalentsAtCarryingValue', 1),
    ('cash', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents', 2),
    ('cash', 'CashAndDueFromBanks', 3);

-- Silver: one row per reported value per filing. Append-only, so restated originals survive.
-- Keyed by CIK, not company: join through silver.company_ciks so lineage changes apply.
CREATE TABLE silver.financial_facts (
    fact_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cik          text    NOT NULL,
    metric       text    NOT NULL REFERENCES silver.metrics,
    tag          text    NOT NULL,
    unit         text    NOT NULL,
    period_start date,            -- NULL for instants (balance-sheet values)
    period_end   date    NOT NULL,
    period_type  text    GENERATED ALWAYS AS (
        CASE
            WHEN period_start IS NULL THEN 'instant'
            -- 80-120 days: 13-week quarters, and 16/17-week ones (Costco's Q4)
            WHEN period_end - period_start BETWEEN 80 AND 120 THEN 'quarter'
            WHEN period_end - period_start BETWEEN 350 AND 380 THEN 'annual'
            ELSE 'ytd'
        END
    ) STORED,
    value        numeric NOT NULL,
    accession_no text    NOT NULL,
    form         text,
    filed        date    NOT NULL,
    frame        text,            -- SEC calendar frame, e.g. CY2026Q2, when assigned
    ingested_at  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX financial_facts_identity ON silver.financial_facts
    (cik, tag, unit, period_start, period_end, accession_no) NULLS NOT DISTINCT;
CREATE INDEX financial_facts_cik_metric ON silver.financial_facts (cik, metric, period_end);

-- Re-derive silver rows for a CIK from its latest bronze payload. Idempotent.
CREATE FUNCTION silver.refresh_financial_facts(p_cik text) RETURNS integer
LANGUAGE sql AS $$
  WITH latest AS (
    SELECT payload FROM bronze.company_facts
    WHERE cik = p_cik ORDER BY fetched_at DESC LIMIT 1
  ),
  inserted AS (
    INSERT INTO silver.financial_facts
        (cik, metric, tag, unit, period_start, period_end, value, accession_no, form, filed, frame)
    SELECT p_cik, t.metric, t.tag, u.key,
           (r->>'start')::date, (r->>'end')::date, (r->>'val')::numeric,
           r->>'accn', r->>'form', (r->>'filed')::date, r->>'frame'
    FROM latest l
    CROSS JOIN silver.metric_tags t
    JOIN silver.metrics m ON m.metric = t.metric
    CROSS JOIN LATERAL jsonb_each(l.payload->'facts'->t.taxonomy->t.tag->'units') u
    CROSS JOIN LATERAL jsonb_array_elements(u.value) r
    WHERE u.key = m.unit
    ON CONFLICT DO NOTHING
    RETURNING 1
  )
  SELECT count(*)::int FROM inserted
$$;

CREATE SCHEMA gold;

-- One value per (company, metric, quarter end) with its provenance (FR6).
CREATE TABLE gold.quarterly_financials (
    company_id        text        NOT NULL REFERENCES silver.companies ON DELETE CASCADE,
    metric            text        NOT NULL REFERENCES silver.metrics,
    period_start      date,       -- NULL for instant metrics
    period_end        date        NOT NULL,
    value             numeric     NOT NULL,
    unit              text        NOT NULL,
    -- reported: the filing states this quarter's value
    -- derived: difference of two year-to-date values (e.g. Q4 = FY - 9 months)
    value_source      text        NOT NULL CHECK (value_source IN ('reported', 'derived')),
    source_tag        text        NOT NULL,  -- XBRL tag, e.g. us-gaap:Revenues
    accession_no      text        NOT NULL,  -- filing to cite (the later one, when derived)
    form              text,
    filed             date        NOT NULL,
    source_accessions text[]      NOT NULL,  -- every filing the value depends on
    ingested_at       timestamptz NOT NULL,
    refreshed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, metric, period_end)
);

-- Rebuild a company's gold rows from silver. Rules:
--   1. per period, take the highest-priority tag, then the most recently filed value
--      (restatements win; originals stay in silver)
--   2. reported quarters and instants are used as-is
--   3. missing quarters of additive metrics are derived from consecutive year-to-date values
--      that share a start date, when they are a quarter (80-120 days) apart
CREATE FUNCTION gold.refresh_quarterly_financials(p_company_id text) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    DELETE FROM gold.quarterly_financials WHERE company_id = p_company_id;

    WITH facts AS (
        SELECT f.*, t.taxonomy, t.priority, m.additive
        FROM silver.financial_facts f
        JOIN silver.company_ciks k ON k.cik = f.cik
        JOIN silver.metric_tags t ON t.metric = f.metric AND t.tag = f.tag
        JOIN silver.metrics m ON m.metric = f.metric AND m.unit = f.unit
        WHERE k.company_id = p_company_id
    ),
    best AS (
        SELECT DISTINCT ON (metric, period_start, period_end) *
        FROM facts
        ORDER BY metric, period_start, period_end, priority, filed DESC, accession_no DESC
    ),
    year_to_date AS (
        SELECT b.*,
               lag(value)        OVER w AS prev_value,
               lag(period_end)   OVER w AS prev_end,
               lag(accession_no) OVER w AS prev_accession,
               lag(ingested_at)  OVER w AS prev_ingested_at
        FROM best b
        WHERE period_type <> 'instant'
        WINDOW w AS (PARTITION BY metric, period_start ORDER BY period_end)
    ),
    candidates AS (
        SELECT metric, period_start, period_end, value, unit, 'reported' AS value_source,
               taxonomy || ':' || tag AS source_tag, accession_no, form, filed, ARRAY[accession_no] AS source_accessions,
               ingested_at
        FROM best
        WHERE period_type IN ('quarter', 'instant')
        UNION ALL
        SELECT metric, prev_end + 1, period_end, value - prev_value, unit, 'derived',
               taxonomy || ':' || tag, accession_no, form, filed, ARRAY[prev_accession, accession_no],
               greatest(ingested_at, prev_ingested_at)
        FROM year_to_date
        WHERE additive AND period_end - prev_end BETWEEN 80 AND 120
    )
    INSERT INTO gold.quarterly_financials
        (company_id, metric, period_start, period_end, value, unit, value_source, source_tag,
         accession_no, form, filed, source_accessions, ingested_at)
    SELECT DISTINCT ON (metric, period_end)
           p_company_id, metric, period_start, period_end, value, unit, value_source,
           source_tag, accession_no, form, filed, source_accessions, ingested_at
    FROM candidates
    -- a reported quarter beats a derived one; then the most recent filing
    ORDER BY metric, period_end, value_source = 'reported' DESC, filed DESC;

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END
$$;
