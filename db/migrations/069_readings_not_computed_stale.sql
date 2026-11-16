-- Two more ways a figure is read, in the readings family (035): why a blank is blank, and a company
-- whose latest figures are long past (Riley, test 6, 2026-11-15; owner decisions 3 and 4).
--
-- 1. gold.not_computed(metric, sic): why Tributary computes no figure for this kind of company, or
--    NULL. Today one rule, 039's computed gross profit (latest body 043's
--    gold.refresh_quarterly_financials): a filing that prints no gross profit gets revenue less cost
--    of revenue only outside these industries, because there cost of revenue isn't the cost of what
--    revenue sells. Walmart and Costco (5331: revenue includes membership fees), AT&T and Verizon
--    (48xx: network costs and depreciation outside the cost line) read a bare "—" in the screener,
--    the Overview and Analysis' Numbers; now the reading says why. Only for a company whose figures
--    the caller reads (p_latest, gold.latest_period under RLS, isn't NULL): no reading without them. The SIC list must stay 043's:
--    services/query-api/tests/test_readings_db.py `test_not_computed_names_every_industry_039_leaves_out`
--    compares the two for every SIC code in tests/fixtures/sic_codes.csv. A company in these
--    industries that prints its own gross profit (Dollar General, Target: 5331) has a value, and a
--    value is never read this way.
-- 2. Stale: a company whose latest period (gold.latest_period) is far behind today. 035's `old` is a
--    value behind the company's OWN latest period; Aphoenity's figures all end Sep 2024 (none is
--    behind the others), so its 84.7% operating margin topped the screener's sort in Nov 2026, and
--    two-year-old TTM values sorted beside this quarter's. A company is stale when its latest period
--    ended more than
--      270 days ago, for a quarterly filer: the next 10-Q or 10-K is due 40-90 days after a quarter,
--        so 270 days is two reports missed; or
--      500 days ago, for an annual filer: a 20-F is due four months after the year end (485 days after
--        the last year end), plus a late filer's 15 days. Annual = the reading's filing is a 20-F,
--        40-F or 6-K (a foreign filer: 6-K quarters are irregular), or the company has no quarterly
--        revenue or net income within 200 days before its latest period (a 10-K-only filer).
--    Owner decision 4: an annual-only filer stays current until its next annual report is due.
--    Measured on hermes (2026-11-15): 5,398 quarterly filers, 209 stale; 1,219 annual (1,155 by the
--    form), 292 stale; 501 of 6,617 in all. The largest: Ecopetrol, KEPCO, POSCO, TSMC (Dec 2024: its
--    2025 20-F, filed 2026-04-16, isn't in companyfacts' payload yet), Toyota, Honda (Mar 2025),
--    Hub Group (Sep 2025, acquired), EIDP (2019: its figures are Corteva's). Whatever the cause, the
--    figure isn't the company's current one. Stale values keep their figure and date; like `old` they
--    leave sorts, range filters and medians (the screener's k<i> is the value only for no status or
--    `behind`).
--
-- gold.latest_reading: 061's body with edits marked "069": the status order is not_applicable,
-- other_currency, not_computed (no value, or an old one, and a reason above), not_meaningful, old,
-- stale, behind. The stale test is a LATERAL gated on the company's age (`current_date - p_latest >
-- 270`, a one-time filter for each company), so the two quarterly probes run only for companies past
-- 270 days (about 1,000 of 6,617). Still one SQL SELECT, STABLE, INVOKER, no SET: inlinable
-- (test_latest_reading_stays_inlinable); no scalar function with sub-selects is called per row (061).
-- The result columns are 061's: callers are unchanged. "Today" is current_date unless the session sets
-- `tributary.today` (a date): the DB tests pin it, so fixtures dated 2026 don't turn stale as the
-- calendar moves on (an injected clock, the repo's convention). Nothing in the services sets it.
--
-- Locks on hermes: CREATE FUNCTION and CREATE OR REPLACE of one function, catalog only (lock_timeout
-- 10 s). Gold doesn't change: no rebuild. Backout: db/backout/readings_069.sql.

SET LOCAL lock_timeout = '10s';

-- 1. 039's industries, worded (043's SIC tests, copied: keep them equal; header)
CREATE FUNCTION gold.not_computed(p_metric text, p_sic text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN p_metric NOT IN ('gross_profit', 'gross_margin') THEN NULL
        WHEN p_sic IS NULL
            THEN 'the filings print no gross profit, and with no industry code on file Tributary '
                 'doesn''t compute one from revenue and cost of revenue'
        WHEN p_sic = '5331'
            THEN 'a warehouse club''s or variety store''s revenue includes membership fees and other '
                 'income that cost of sales doesn''t carry, so revenue less cost of sales isn''t a '
                 'gross profit, and the filings print none'
        WHEN p_sic ~ '^48'
            THEN 'a telecom''s network costs and depreciation sit outside its cost of revenue, so '
                 'revenue less that cost isn''t a gross profit, and the filings print none'
        WHEN p_sic ~ '^58'
            THEN 'a restaurant''s cost of revenue is food and packaging only, so revenue less that '
                 'cost isn''t a gross profit, and the filings print none'
        WHEN p_sic = '5912'
            THEN 'a drugstore chain''s cost of revenue leaves out its insurance benefit costs, so '
                 'revenue less that cost isn''t a gross profit, and the filings print none'
        WHEN p_sic ~ '^13' OR p_sic = '2911'
            THEN 'an oil and gas company''s purchased crude and products aren''t a cost of revenue '
                 'in the usual sense, so revenue less that cost isn''t a gross profit, and the '
                 'filings print none'
        WHEN p_sic = '4911' OR p_sic ~ '^493.$'
            THEN 'a utility''s fuel and purchased power aren''t its whole cost of revenue, so revenue '
                 'less that cost isn''t a gross profit, and the filings print none'
        WHEN p_sic ~ '^6[0-7]'
            THEN 'a financial company''s revenue isn''t sales with a cost of sales, so there is no '
                 'gross profit, and the filings print none'
    END
$$;

-- 2. 061's body + the edits marked "069"
CREATE OR REPLACE FUNCTION gold.latest_reading(p_company_id text, p_span text, p_metric text, p_sic text,
                                    p_latest date)
RETURNS TABLE (period_end date, value numeric, accession_no text, form text, filed date,
               status text, reason text, unit text)
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER AS $$
    SELECT x.period_end, x.value, x.accession_no, x.form, x.filed,
           CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'not_applicable'
                -- 056: an amount in another currency is never compared with dollars (header, 3)
                WHEN x.unit ~ '^[A-Z]{3}(/shares)?$' AND x.unit !~ '^USD'
                    THEN 'other_currency'
                -- 069: no figure (or only an old one) because Tributary computes none here
                WHEN (x.value IS NULL OR gold.period_status(x.period_end, p_latest) = 'old')
                     AND p_latest IS NOT NULL AND gold.not_computed(p_metric, p_sic) IS NOT NULL
                    THEN 'not_computed'
                WHEN gold.not_meaningful(p_metric, x.value, x.inputs, e.value, coalesce(cu.unit, 'USD')) IS NOT NULL
                    THEN 'not_meaningful'
                -- 069: the company's latest period is long past (header, 2); an old value stays old
                WHEN st.stale AND x.value IS NOT NULL
                     AND gold.period_status(x.period_end, p_latest) IS DISTINCT FROM 'old'
                    THEN 'stale'
                ELSE gold.period_status(x.period_end, p_latest) END,
           CASE WHEN p_metric = ANY(gold.not_applicable_metrics(p_company_id, p_sic))
                    THEN 'this is a bank: deposits are its debt and lending its operations, so '
                         'the figure has no comparable meaning'
                WHEN x.unit ~ '^[A-Z]{3}(/shares)?$' AND x.unit !~ '^USD'
                    THEN 'reported in ' || split_part(x.unit, '/', 1)
                         || ', so it isn''t sorted, filtered or averaged with US-dollar figures'
                -- 069
                WHEN (x.value IS NULL OR gold.period_status(x.period_end, p_latest) = 'old')
                     AND p_latest IS NOT NULL AND gold.not_computed(p_metric, p_sic) IS NOT NULL
                    THEN gold.not_computed(p_metric, p_sic)
                WHEN gold.not_meaningful(p_metric, x.value, x.inputs, e.value, coalesce(cu.unit, 'USD')) IS NOT NULL
                    THEN gold.not_meaningful(p_metric, x.value, x.inputs, e.value, coalesce(cu.unit, 'USD'))
                -- 069
                WHEN st.stale AND x.value IS NOT NULL
                     AND gold.period_status(x.period_end, p_latest) IS DISTINCT FROM 'old'
                    THEN 'this company''s latest figures here are for the period ended '
                         || to_char(p_latest, 'FMMon FMDD, YYYY') || ', and no report since has '
                         'reached Tributary, so they aren''t compared with current figures' END,
           x.unit
    FROM (SELECT 1) one
    LEFT JOIN LATERAL (
        SELECT y.period_end, y.value, y.accession_no, y.form, y.filed, y.inputs, y.unit
        FROM (
            (SELECT q.period_end, q.value, q.accession_no, q.form, q.filed, NULL::jsonb AS inputs,
                    q.unit
             FROM gold.quarterly_financials q
             WHERE q.company_id = p_company_id AND q.metric = p_metric
               AND (p_span = 'quarter'
                    OR (p_span = 'ttm' AND EXISTS (SELECT 1 FROM silver.metrics m
                                                   WHERE m.metric = p_metric AND m.kind = 'instant')))
             ORDER BY q.period_end DESC LIMIT 1)
            UNION ALL
            (SELECT d.period_end, d.value, d.accession_no, d.form, d.filed, d.inputs, d.unit
             FROM gold.derived_financials d
             WHERE d.company_id = p_company_id AND d.span = p_span AND d.metric = p_metric
             ORDER BY d.period_end DESC LIMIT 1)
        ) y
        ORDER BY y.period_end DESC LIMIT 1
    ) x ON true
    LEFT JOIN LATERAL (
        SELECT q.value FROM gold.quarterly_financials q
        WHERE p_metric IN ('debt_to_equity', 'roe')
          AND q.company_id = p_company_id AND q.metric = 'stockholders_equity'
          AND q.period_end <= current_date
        ORDER BY q.period_end DESC LIMIT 1
    ) e ON true
    -- 057: the currency of the amounts a rule's base is in (one per company: 056), only for the
    -- readings that have a base; 060: gold.amount_currency's rule, which also reads an
    -- annual-only filer's TTM rows (one PK probe for a company with quarterly revenue, as before).
    -- 061: the rule's set-returning form, which the planner inlines, so its probes run in this
    -- query under the RLS subplans it already built; LIMIT 1 (it is one row) keeps this a subquery
    -- of its own, so the metric test stays its one-time filter in a generic plan too (header)
    LEFT JOIN LATERAL (
        SELECT c.unit FROM gold.amount_currency_of(p_company_id) c
        WHERE p_metric IN ('gross_margin', 'operating_margin', 'net_margin', 'fcf_margin',
                           'revenue_growth', 'net_income_growth')
        LIMIT 1
    ) cu ON true
    -- 069: stale (header, 2). Only past 270 days (the gate); then 500 for an annual filer, by the
    -- reading's form or by no quarterly revenue or net income within 200 days of the latest period
    LEFT JOIN LATERAL (
        SELECT CASE WHEN t.today - p_latest > 500 THEN true
                    WHEN x.form ~ '^(20-F|40-F|6-K)' THEN false
                    ELSE EXISTS (SELECT 1 FROM gold.quarterly_financials q
                                 WHERE q.company_id = p_company_id
                                   AND q.metric IN ('revenue', 'net_income')
                                   AND q.period_end > p_latest - 200
                                   AND q.period_end <= p_latest) END AS stale
        FROM (SELECT coalesce(nullif(current_setting('tributary.today', true), '')::date,
                              current_date) AS today) t
        WHERE t.today - p_latest > 270
        LIMIT 1
    ) st ON true
$$;
