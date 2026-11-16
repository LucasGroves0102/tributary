-- Net income as one line (db/DESIGN.md "077"; coverage sweep, 076's metric_missing). Measured on
-- hermes 2026-11-16, read-only.
--
-- 1. NetIncomeLossAvailableToCommonStockholdersBasic is net income's last fallback (priority 5,
--    after every us-gaap and IFRS tag): read only for a period with no NetIncomeLoss or ProfitLoss
--    fact. Booking tags its income statement's "Net income" that way in every 10-K and 10-Q since
--    2016 (no preferred stock: the same figure); its only NetIncomeLoss facts since are the
--    proxies' (024 drops them), so gold had no net income after 2015 (reference set: 5,404 printed
--    for 2025, none in gold). Of the listed companies whose net income stops while their figures
--    go on (076's sweep, 3), Booking and Trupanion carry this tag every period since; Energy
--    Services of America also reports preferred dividends, so its figure reads net income less
--    them. Elsewhere it fills only a period with neither tag.
--
-- 2. gold.best_facts (064's body, edits marked "077"): inside a year whose annual net income is
--    tagged ProfitLoss and never NetIncomeLoss (a year: 350-380 days), NetIncomeLoss for its
--    periods only in later filings' comparatives (first filed 100+ days after the year ended),
--    ProfitLoss reads first, so the year's quarters, year-to-date figures and the fiscal Q4
--    derived from them are one line. PNC tags its bottom line ProfitLoss in every filing; two 2026
--    10-Qs added NetIncomeLoss for their 2025 comparatives, so gold's 2025 read Q1 ProfitLoss,
--    Q2-Q3 NetIncomeLoss and a fiscal Q4 of the year's ProfitLoss less nine months of
--    NetIncomeLoss: TTM 7,015 against the printed 6,997. Now 6,997. Tried first and dropped:
--    ProfitLoss first wherever it covers more of the company's periods (Kroger, Prologis and Duke
--    Energy would read the figure with noncontrolling interests where they print the attributable
--    one; the reference set caught it). On hermes' silver: 2,765 such years at 626 companies (most
--    with no NetIncomeLoss in them: nothing changes); 417 periods at 129 companies hold both tags,
--    192 with two values, 69 by more than 10% (large noncontrolling interests: the year's quarters
--    now all include them, where its derived Q4 alone used to). Without the comparatives
--    condition: 2,163 periods at 328 companies.
--
-- gold.rules_version() 64 -> 77: the hourly catch-up (071) re-projects every company's stored
-- companyfacts with the new tag and rebuilds its gold, oldest build first; nothing by hand. The
-- backouts' guards pin best_facts' new body (scripts/backout_guards.py).
--
-- Locks on hermes (lock_timeout 3 s): one row into silver.metric_tags (read by the hourly jobs:
-- ROW EXCLUSIVE), CREATE OR REPLACE of gold.best_facts and gold.rules_version (catalog only; a
-- running caller keeps the body it started with). Nothing is rebuilt here. Undo:
-- db/backout/net_income_077.sql (064's best_facts, the tag and its silver facts gone), then the
-- financials-rebuild job.

SET LOCAL lock_timeout = '3s';

INSERT INTO silver.metric_tags (metric, tag, priority) VALUES
    ('net_income', 'NetIncomeLossAvailableToCommonStockholdersBasic', 5);

CREATE OR REPLACE FUNCTION gold.best_facts(p_company_ids text[])
RETURNS TABLE (company_id text, metric text, tag text, taxonomy text, unit text, period_start date,
               period_end date, period_type text, value numeric, accession_no text, form text,
               filed date, ingested_at timestamptz, cik text, priority numeric, additive boolean)
LANGUAGE sql STABLE AS $$
    WITH tag_latest AS (
        SELECT DISTINCT ON (u.company_id, u.metric, u.tag, u.period_start, u.period_end) u.*,
               -- 077: when the tag first gave this period (a later filing's comparative or not)
               min(u.filed) OVER (PARTITION BY u.company_id, u.metric, u.tag, u.period_start,
                                               u.period_end) AS first_filed
        FROM gold.usable_facts(p_company_ids) u
        ORDER BY u.company_id, u.metric, u.tag, u.period_start, u.period_end, u.filed DESC,
                 u.accession_no DESC, u.cik
    ),
    -- 077: a year whose net income the company's own filings of the year tag ProfitLoss only (the
    -- annual figure ProfitLoss, never NetIncomeLoss; a year: 350-380 days), NetIncomeLoss for its
    -- periods coming only from later filings' comparatives (first filed 100+ days after the year
    -- ended). Inside it ProfitLoss reads first, so the year's quarters, its year-to-date figures and
    -- the fiscal Q4 derived from them are one line. PNC's 2025: ProfitLoss in every filing of the
    -- year, NetIncomeLoss only in two 2026 10-Qs' comparatives (Q2, Q3, six and nine months): gold
    -- read Q1 ProfitLoss, Q2-Q3 NetIncomeLoss and a Q4 of the year's ProfitLoss less nine months of
    -- NetIncomeLoss.
    profit_loss_years AS MATERIALIZED (
        SELECT y.company_id, y.period_start, y.period_end
        FROM (SELECT t.company_id, t.period_start, t.period_end
              FROM tag_latest t
              WHERE t.metric = 'net_income' AND t.taxonomy = 'us-gaap'
                AND t.tag IN ('NetIncomeLoss', 'ProfitLoss')
                AND t.period_end - t.period_start BETWEEN 350 AND 380
              GROUP BY t.company_id, t.period_start, t.period_end
              HAVING NOT bool_or(t.tag = 'NetIncomeLoss')) y
        WHERE NOT EXISTS (SELECT 1 FROM tag_latest n
                          WHERE n.company_id = y.company_id AND n.metric = 'net_income'
                            AND n.taxonomy = 'us-gaap' AND n.tag = 'NetIncomeLoss'
                            AND n.period_start >= y.period_start
                            AND n.period_end <= y.period_end
                            AND n.first_filed <= y.period_end + 100)
    ),
    ranked AS (  -- 077: the order tags are believed in; priority, but for that rule
        SELECT t.*,
               CASE WHEN t.metric = 'net_income' AND t.taxonomy = 'us-gaap' AND t.tag = 'ProfitLoss'
                         AND EXISTS (SELECT 1 FROM profit_loss_years y
                                     WHERE y.company_id = t.company_id
                                       AND t.period_start >= y.period_start
                                       AND t.period_end <= y.period_end)
                    THEN 0 ELSE t.priority END AS rank
        FROM tag_latest t
    ),
    -- 064: the period's positive revenue values, and the largest of its other money figures (any
    -- other metric with the same start and end), for the scale test below
    scales AS (
        SELECT t.*,
               array_agg(t.value) FILTER (WHERE t.metric = 'revenue' AND t.value > 0) OVER wm
                   AS revenue_values,
               max(abs(t.value)) FILTER (WHERE t.metric <> 'revenue' AND t.unit ~ '^[A-Z]{3}$')
                   OVER wp AS other_max
        FROM ranked t  -- 077
        WINDOW wp AS (PARTITION BY t.company_id, t.period_start, t.period_end),
               wm AS (PARTITION BY t.company_id, t.period_start, t.period_end, t.metric)
    ),
    -- 064: two revenue tags of a period 1,000 or 1,000,000 times apart (within 0.5%) are one figure
    -- in two scales. The larger is the slip when it is over 100 times every other money figure of
    -- the period, else the smaller is (also when the period has no other figure). A slip is no
    -- evidence for revenue's rules below and sorts after every other tag.
    slips AS (
        SELECT s.*,
               CASE WHEN s.metric = 'revenue' AND s.value > 0 THEN EXISTS (
                   SELECT 1 FROM unnest(s.revenue_values) v
                   WHERE ((s.value BETWEEN v * 995 AND v * 1005
                           OR s.value BETWEEN v * 995000 AND v * 1005000)
                          AND s.value > 100 * s.other_max)
                      OR ((v BETWEEN s.value * 995 AND s.value * 1005
                           OR v BETWEEN s.value * 995000 AND s.value * 1005000)
                          AND (s.other_max IS NULL OR v <= 100 * s.other_max)))
               ELSE false END AS slipped
        FROM scales s
    ),
    weighed AS (  -- revenue's rules (039's and 064's headers) read the period's other tags
        SELECT t.*,
               -- 064: a slip counts for none of these
               max(t.filed) FILTER (WHERE t.value >= 1000000 AND NOT t.slipped) OVER w AS large_filed,
               max(t.filed) FILTER (WHERE t.value > 0 AND t.value < 1000000 AND NOT t.slipped) OVER w
                   AS small_filed,
               max(t.value) FILTER (WHERE NOT t.slipped AND t.tag IN (
                   'RevenueFromContractWithCustomerExcludingAssessedTax',
                   'RevenueFromContractWithCustomerIncludingAssessedTax')) OVER w AS contract_value,
               -- 064: the gross line a net revenue figure is compared with
               max(t.value) FILTER (WHERE NOT t.slipped AND t.tag IN ('Revenues',
                   'RevenueFromContractWithCustomerExcludingAssessedTax',
                   'RevenueFromContractWithCustomerIncludingAssessedTax')) OVER w AS gross_value
        FROM slips t  -- 064
        WINDOW w AS (PARTITION BY t.company_id, t.metric, t.period_start, t.period_end)
    )
    SELECT DISTINCT ON (x.company_id, x.metric, x.period_start, x.period_end)
           x.company_id, x.metric, x.tag, x.taxonomy, x.unit, x.period_start, x.period_end,
           x.period_type, x.value, x.accession_no, x.form, x.filed, x.ingested_at, x.cik, x.priority,
           x.additive
    FROM weighed x
    ORDER BY x.company_id, x.metric, x.period_start, x.period_end,
             x.slipped,  -- 064: a scale slip after every other tag
             (x.metric = 'revenue' AND x.period_start IS NOT NULL
              AND ((x.value = 0 AND (x.large_filed >= x.filed OR x.small_filed > x.filed))
                   OR (x.tag = 'Revenues' AND x.value > 0 AND x.value < 0.2 * x.contract_value)
                   -- 064: net revenue under 20% of the gross line is a part too (Rectitude 0.2%)
                   OR (x.tag = 'RevenuesNetOfInterestExpense' AND x.value > 0
                       AND x.value < 0.2 * x.gross_value)))
                 IS TRUE,
             x.rank, x.filed DESC, x.accession_no DESC, x.cik  -- 077: rank, not priority
$$;

CREATE OR REPLACE FUNCTION gold.rules_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 77';
