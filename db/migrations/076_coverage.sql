-- Coverage sweep: every exchange-listed company measured nightly for every kind of gap, each gap
-- with why (db/DESIGN.md "076"). Measured on hermes 2026-11-16, read-only.
--
-- 1. ops.coverage: one row per listed company (silver.companies.exchanges: Nasdaq, NYSE, CBOE,
--    TXSE; 6,068) and kind of gap, with its cause, a reason and when it was first seen. Reasons:
--    fixable (Tributary's gap: a link, a mapping, a pipeline step), not_in_sec_data (the SEC's
--    structured data hasn't it: no XBRL, no reports, an extension tag), out_of_scope (funds,
--    asset-backed issuers, figures filed in a parent's report). detail holds dates, forms,
--    accessions, metric names and counts, never values or text (014's rule). Kinds and causes:
--      no_figures          no gold row at all. fund (N-CSR/N-PORT/485BPOS filers), abs (10-D, or
--                          SIC 6189: structured-product trusts), filed_with_parent (its 10-K/10-Q
--                          are a parent's combined filing), foreign (20-F/40-F; fixable when the
--                          20-F has XBRL: roadmap f), no_xbrl (no XBRL 10-K/10-Q in two years:
--                          royalty trusts), stopped (no 10-K/10-Q for 400 days), awaiting (its first
--                          XBRL 10-K/10-Q is under 30 days old), bank_regulator (a bank on EDGAR
--                          over a year that never filed a periodic report: it files with its bank
--                          regulator), no_reports_yet, no_sec_reports, other (fixable)
--      figures_behind      the latest 10-K/10-Q/20-F/40-F (filed 7+ days ago, 30+ for 20-F/40-F)
--                          covers a period 45+ days after gold's last core figure: pipeline
--                          (companyfacts has the filing; gold left it out), companyfacts_lacks
--                          (055's job should read it), filed_with_parent, no_xbrl, foreign
--                          (detail.in_companyfacts says whether companyfacts carries the filing)
--      history_short       figures start a year or more after the company's first XBRL 10-K/10-Q
--                          (or revenue a year after its net income): re_registration, revenue_tag
--                          (revenue under a tag gold doesn't read as revenue: Church & Dwight's
--                          SalesRevenueGoodsNet to 2018, net income from 2009, revenue from 2017;
--                          not mapped: in a sample where it sits beside a total, goods alone equal
--                          it in 25 of 149 periods), companyfacts (companyfacts starts later than
--                          the filings' XBRL), companyfacts_gold (silver has earlier facts). All
--                          fixable: the figures are in the filings' XBRL.
--      metric_missing      a core metric (revenue, net income, total assets, equity, operating cash
--                          flow) with 4+ quarters, none in the 400 days before the company's latest
--                          figure: gold_dropped (silver has later facts) or not_read (no mapped tag
--                          after then: Booking's net income before 077)
--      re_registration     a CIK that filed an 8-K12B/8-K12G3 (successor issuer) since 2009-06,
--                          figures starting within 400 days of it, and no other CIK of the company
--                          with reports up to the move: its predecessor isn't linked (company-loader
--                          re_registrations.csv)
--      documents_missing   10-K/10-Qs filed since 2016 (the crawl's window) not stored: none, or
--                          under half of 8+ (partial)
--      statements_exhibit  the latest stored 10-K's Item 8 is a short pointer to Exhibit 13 or the
--                          annual report to shareholders, and no EX-13 is stored (doc-fetcher
--                          stores it with a 10-K since this change): ex13
--    Measured on hermes (the whole sweep 34 s): 1,569 gaps at 1,505 of 6,068 listed companies.
--      no_figures 475: out of scope 375 (fund 358, abs 11, filed_with_parent 6); not in the SEC's
--        data 71 (no_reports_yet 30, no_sec_reports 22, no_xbrl 9, foreign without XBRL 7, bank_
--        regulator 3: Bank OZK, Preferred Bank, Georgia Banking); fixable 29 (foreign with XBRL)
--      figures_behind 221: foreign 215 (214 a 20-F/40-F companyfacts lacks: 055's job reads only
--        10-K/10-Q gaps, roadmap f), companyfacts_lacks 3 (investment vehicles 055 read without
--        mapped facts), filed_with_parent 2 (Southern California Edison, EIDP), pipeline 1
--      history_short 652: revenue_tag 639 (a sample of 30: 11 an industry revenue tag gold doesn't
--        map, RealEstateRevenueNet, UtilityRevenue, OilAndGasRevenue...; 6 goods or services
--        lines alone; 13 mapped tags only yearly), companyfacts_gold 8, companyfacts 4,
--        re_registration 1
--      metric_missing 124: revenue 100, equity 14, operating cash flow 10, assets 7, net income 3
--      re_registration 63 (24 linked by this change's re_registrations.csv), statements_exhibit 14
--        (IBM, Wells Fargo...), documents_missing 20 (all without figures: the crawl plans
--        operating companies only)
--    The first pass (docs/runbooks, 2026-11-16) counted 747 without figures (no TTM revenue, net
--    income or assets: 139 of its 166 10-K/10-Q filers are new listings with quarters) and 37
--    figures behind by TTM (25 of them have current quarters: Ferguson, James Hardie, Greif).
--
-- 2. ops.refresh_coverage() rebuilds it (rows not found again go, first_seen stays while a gap
--    stays) and records the run in ops.coverage_runs; the nightly financials-bulk job calls it after
--    the extremes report (facts_fetcher/coverage.py). Read from ops.coverage_found(). The whole
--    sweep reads the listed companies' 10-K/10-Q/20-F/40-F/8-K12B rows of
--    bronze.submission_filings and index probes on gold's primary keys.
--
-- 3. Readers: ops.coverage_report (names companies: ops_reader only, like 072's extremes report),
--    ops.coverage_summary (counts per kind, cause and reason: ops_reader), ops.public_coverage_gaps()
--    (counts per kind and reason for the public status page, never a company), the Query API's data
--    notes (ops.coverage under RLS, financials tier, 030's policy form).
--
-- 4. ops.rebuild_soon(company ids): the company-loader calls it when a company's CIKs change (a
--    re-registration linked): their gold stamp becomes version 0 built at the epoch, which the
--    hourly catch-up (071; catchup.py takes the epoch first) rebuilds before anything else, with the
--    linked CIK's companyfacts. hermes has no gold stamp yet (none since 071), so 077's bump alone
--    would reach Broadcom and Marvell by company id, late.
--
-- Locks on hermes (lock_timeout 3 s): new tables, views and functions only; nothing existing is
-- altered or rebuilt. Undo: drop what this file creates.

SET LOCAL lock_timeout = '3s';

-- 1. The report --------------------------------------------------------------------------------

CREATE TABLE ops.coverage (
    company_id text        NOT NULL,
    kind       text        NOT NULL CHECK (kind IN (
        'no_figures', 'figures_behind', 'history_short', 'metric_missing', 're_registration',
        'documents_missing', 'statements_exhibit')),
    cause      text        NOT NULL,
    reason     text        NOT NULL CHECK (reason IN ('fixable', 'not_in_sec_data', 'out_of_scope')),
    detail     jsonb       NOT NULL DEFAULT '{}',  -- dates, forms, accessions, metrics, counts
    first_seen timestamptz NOT NULL DEFAULT now(),
    seen_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, kind)
);

CREATE TABLE ops.coverage_runs (
    run_at  timestamptz PRIMARY KEY,
    listed  integer     NOT NULL,  -- listed companies measured
    gaps    integer     NOT NULL,  -- rows in ops.coverage after the run
    seconds real
);

GRANT SELECT ON ops.coverage TO query_api;
ALTER TABLE ops.coverage ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_financials ON ops.coverage FOR SELECT TO query_api
    USING (company_id IN (SELECT r FROM authz.readable_companies('financials') r));

-- 2. What the sweep finds now (read-only) ------------------------------------------------------

CREATE FUNCTION ops.coverage_found()
RETURNS TABLE (company_id text, kind text, cause text, reason text, detail jsonb)
LANGUAGE sql STABLE AS $$
    WITH u AS MATERIALIZED (
        SELECT c.company_id, c.sic, c.name
        FROM silver.companies c
        WHERE c.exchanges && ARRAY['Nasdaq', 'NYSE', 'CBOE', 'TXSE']::text[]
    ),
    ck AS MATERIALIZED (
        SELECT k.cik, k.company_id FROM silver.company_ciks k
        WHERE k.company_id IN (SELECT u.company_id FROM u)
    ),
    -- the periodic reports and successor-issuer filings of every listed company's CIKs
    pf AS MATERIALIZED (
        SELECT ck.company_id, f.cik, f.accession_no, f.form, f.filing_date, f.report_date,
               f.is_xbrl
        FROM ck JOIN bronze.submission_filings f ON f.cik = ck.cik
        WHERE f.form IN ('10-K', '10-Q', '20-F', '40-F', '8-K12B', '8-K12G3')
    ),
    rep AS MATERIALIZED (
        SELECT pf.company_id,
               min(pf.filing_date) FILTER (WHERE pf.form IN ('10-K', '10-Q')) AS first_10x,
               max(pf.filing_date) FILTER (WHERE pf.form IN ('10-K', '10-Q')) AS last_10x,
               min(pf.filing_date) FILTER (WHERE pf.form IN ('10-K', '10-Q') AND pf.is_xbrl)
                   AS first_10x_xbrl,
               count(*) FILTER (WHERE pf.form IN ('10-K', '10-Q') AND pf.is_xbrl
                                  AND pf.filing_date >= current_date - 730) AS n_10x_xbrl,
               max(pf.filing_date) FILTER (WHERE pf.form IN ('20-F', '40-F')) AS last_foreign,
               bool_or(pf.is_xbrl) FILTER (WHERE pf.form IN ('20-F', '40-F')) AS foreign_xbrl
        FROM pf GROUP BY pf.company_id
    ),
    -- core figures: each metric's first and last quarter and last TTM (probes on gold's keys)
    fig AS MATERIALIZED (
        SELECT u.company_id, m.metric, q.first_end, q.last_end, q.n, t.last_ttm
        FROM u
        CROSS JOIN (VALUES ('revenue'), ('net_income'), ('total_assets'),
                           ('stockholders_equity'), ('operating_cash_flow')) m(metric)
        CROSS JOIN LATERAL (
            SELECT min(g.period_end) AS first_end, max(g.period_end) AS last_end, count(*) AS n
            FROM gold.quarterly_financials g
            WHERE g.company_id = u.company_id AND g.metric = m.metric) q
        CROSS JOIN LATERAL (
            SELECT max(d.period_end) AS last_ttm FROM gold.derived_financials d
            WHERE d.company_id = u.company_id AND d.span = 'ttm' AND d.metric = m.metric) t
    ),
    figs AS MATERIALIZED (
        SELECT fig.company_id,
               -- where the figures start: the first quarter of a flow (an equity statement's
               -- opening balance reaches years before the first income statement)
               min(fig.first_end) FILTER (WHERE fig.metric IN ('revenue', 'net_income',
                                                               'operating_cash_flow')) AS first_any,
               min(fig.first_end) FILTER (WHERE fig.metric = 'revenue') AS first_revenue,
               min(fig.first_end) FILTER (WHERE fig.metric = 'net_income') AS first_net_income,
               greatest(max(fig.last_end), max(fig.last_ttm)) AS last_any,
               EXISTS (SELECT 1 FROM gold.quarterly_financials g WHERE g.company_id = fig.company_id)
               OR EXISTS (SELECT 1 FROM gold.derived_financials d WHERE d.company_id = fig.company_id)
                   AS any_figures
        FROM fig GROUP BY fig.company_id
    ),

    -- no_figures -------------------------------------------------------------------------------
    nofig AS (
        SELECT u.company_id, u.sic, u.name, r.first_10x, r.last_10x, r.n_10x_xbrl,
               r.first_10x_xbrl, r.last_foreign, r.foreign_xbrl,
               (SELECT max(z.filing_date) FROM ck JOIN bronze.submission_filings z ON z.cik = ck.cik
                WHERE ck.company_id = u.company_id
                  AND z.form IN ('N-CSR', 'N-CSRS', 'NPORT-P', 'N-PORT', 'N-Q', '485BPOS', '497',
                                 'N-CEN', 'N-2', 'N-1A', '497K', '24F-2NT', 'N-30D')) AS last_fund,
               (SELECT max(z.filing_date) FROM ck JOIN bronze.submission_filings z ON z.cik = ck.cik
                WHERE ck.company_id = u.company_id AND z.form IN ('10-D', '10-D/A', 'ABS-15G'))
                   AS last_abs,
               (SELECT min(z.filing_date) FROM ck JOIN bronze.submission_filings z ON z.cik = ck.cik
                WHERE ck.company_id = u.company_id) AS first_filing,
               -- the latest 10-K/10-Q is also another company's filing (a parent's combined report)
               (SELECT o.company_id
                FROM pf p
                JOIN bronze.submission_filings z ON z.accession_no = p.accession_no AND z.cik <> p.cik
                JOIN silver.company_ciks o ON o.cik = z.cik AND o.company_id <> p.company_id
                WHERE p.company_id = u.company_id AND p.form IN ('10-K', '10-Q')
                ORDER BY p.filing_date DESC LIMIT 1) AS parent
        FROM u
        JOIN figs fg ON fg.company_id = u.company_id AND NOT fg.any_figures
        LEFT JOIN rep r ON r.company_id = u.company_id
    ),
    nofig_found AS (
        SELECT n.company_id, 'no_figures'::text AS kind, x.cause,
               CASE WHEN x.cause IN ('fund', 'abs', 'filed_with_parent') THEN 'out_of_scope'
                    WHEN x.cause IN ('foreign') AND n.foreign_xbrl THEN 'fixable'
                    WHEN x.cause IN ('awaiting', 'other') THEN 'fixable'
                    ELSE 'not_in_sec_data' END AS reason,
               jsonb_strip_nulls(jsonb_build_object(
                   'last_10x', n.last_10x, 'last_foreign', n.last_foreign,
                   'parent', CASE WHEN x.cause = 'filed_with_parent' THEN n.parent END,
                   'first_filing', n.first_filing)) AS detail
        FROM nofig n
        CROSS JOIN LATERAL (SELECT CASE
            WHEN n.last_fund IS NOT NULL THEN 'fund'
            WHEN n.last_abs IS NOT NULL OR n.sic = '6189' THEN 'abs'
            WHEN n.parent IS NOT NULL THEN 'filed_with_parent'
            WHEN n.last_foreign IS NOT NULL AND n.last_foreign >= coalesce(n.last_10x, '-infinity')
                THEN 'foreign'
            WHEN n.last_10x IS NOT NULL AND n.last_10x < current_date - 400 THEN 'stopped'
            WHEN n.last_10x IS NOT NULL AND n.n_10x_xbrl = 0 THEN 'no_xbrl'
            WHEN n.first_10x_xbrl >= current_date - 30 THEN 'awaiting'
            WHEN n.last_10x IS NOT NULL THEN 'other'
            WHEN n.first_filing < current_date - 365
                 AND (n.sic IN ('6021', '6022', '6029', '6035', '6036') OR n.name ~* '\mbank\M')
                THEN 'bank_regulator'
            WHEN n.first_filing >= current_date - 548 THEN 'no_reports_yet'
            ELSE 'no_sec_reports' END AS cause) x
    ),

    -- figures_behind ---------------------------------------------------------------------------
    latest AS (
        SELECT DISTINCT ON (pf.company_id) pf.company_id, pf.accession_no, pf.form,
               pf.filing_date, pf.report_date, pf.is_xbrl, pf.form IN ('20-F', '40-F') AS foreign_form
        FROM pf
        WHERE pf.form IN ('10-K', '10-Q', '20-F', '40-F') AND pf.report_date IS NOT NULL
          AND pf.filing_date <= current_date - 7
        ORDER BY pf.company_id, pf.filing_date DESC, pf.accession_no DESC
    ),
    behind_found AS (
        SELECT l.company_id, 'figures_behind'::text AS kind, x.cause,
               CASE WHEN x.cause = 'no_xbrl' OR (x.cause = 'foreign' AND NOT l.is_xbrl)
                    THEN 'not_in_sec_data'
                    WHEN x.cause = 'filed_with_parent' THEN 'out_of_scope'
                    ELSE 'fixable' END AS reason,
               jsonb_build_object('accession_no', l.accession_no, 'form', l.form,
                                  'filed', l.filing_date, 'period_end', l.report_date,
                                  'figures_to', fg.last_any,
                                  -- the SEC's companyfacts carries the filing (silver has its facts)
                                  'in_companyfacts', EXISTS (
                                      SELECT 1 FROM silver.financial_facts s
                                      WHERE s.accession_no = l.accession_no)) AS detail
        FROM latest l
        JOIN figs fg ON fg.company_id = l.company_id AND fg.any_figures AND fg.last_any IS NOT NULL
        CROSS JOIN LATERAL (SELECT CASE
            WHEN l.foreign_form THEN 'foreign'
            WHEN NOT l.is_xbrl THEN 'no_xbrl'
            -- the filing is also another company's (a parent's combined report): gold reads it
            -- as the company its XBRL names (050)
            WHEN EXISTS (SELECT 1 FROM bronze.submission_filings z
                         JOIN silver.company_ciks o ON o.cik = z.cik
                         WHERE z.accession_no = l.accession_no AND o.company_id <> l.company_id)
                THEN 'filed_with_parent'
            WHEN EXISTS (SELECT 1 FROM silver.financial_facts s WHERE s.accession_no = l.accession_no)
                THEN 'pipeline'
            ELSE 'companyfacts_lacks' END AS cause) x
        WHERE l.report_date > fg.last_any + 45
          AND l.filing_date >= current_date - 500
          AND (NOT l.foreign_form OR l.filing_date <= current_date - 30)
    ),

    -- re_registration --------------------------------------------------------------------------
    moves AS (
        SELECT pf.company_id, pf.cik, min(pf.filing_date) AS moved_on,
               (array_agg(pf.form ORDER BY pf.filing_date))[1] AS form
        FROM pf WHERE pf.form IN ('8-K12B', '8-K12G3')
        GROUP BY pf.company_id, pf.cik
    ),
    rereg AS (
        SELECT DISTINCT ON (m.company_id) m.company_id, m.cik, m.moved_on, m.form, fg.first_any
        FROM moves m
        JOIN figs fg ON fg.company_id = m.company_id
        WHERE m.moved_on >= '2009-06-01' AND fg.first_any >= m.moved_on - 400
          AND NOT EXISTS (SELECT 1 FROM pf p
                          WHERE p.company_id = m.company_id AND p.cik <> m.cik
                            AND p.form IN ('10-K', '10-Q', '20-F', '40-F')
                            AND p.filing_date <= m.moved_on + 120)
        ORDER BY m.company_id, m.moved_on DESC
    ),
    rereg_found AS (
        SELECT r.company_id, 're_registration'::text AS kind, 'unlinked'::text AS cause,
               'fixable'::text AS reason,
               jsonb_build_object('cik', r.cik, 'form', r.form, 'moved_on', r.moved_on,
                                  'figures_from', r.first_any) AS detail
        FROM rereg r
    ),

    -- history_short ----------------------------------------------------------------------------
    short_found AS (
        SELECT fg.company_id, 'history_short'::text AS kind, x.cause,
               -- each is in the SEC's data (a filing's XBRL): Tributary's to read
               'fixable'::text AS reason,
               jsonb_strip_nulls(jsonb_build_object(
                   'first_xbrl_report', r.first_10x_xbrl, 'figures_from', fg.first_any,
                   'revenue_from', fg.first_revenue,
                   'moved_on', (SELECT rr.moved_on FROM rereg rr WHERE rr.company_id = fg.company_id)))
                   AS detail
        FROM figs fg
        JOIN rep r ON r.company_id = fg.company_id
        CROSS JOIN LATERAL (SELECT
            fg.first_any > greatest(r.first_10x_xbrl, '2009-06-30'::date) + 400 AS late_figures,
            fg.first_revenue > fg.first_net_income + 400 AS late_revenue) t
        CROSS JOIN LATERAL (SELECT CASE
            WHEN EXISTS (SELECT 1 FROM rereg rr WHERE rr.company_id = fg.company_id)
                THEN 're_registration'
            WHEN NOT t.late_figures THEN 'revenue_tag'
            -- silver has earlier facts gold doesn't use: a gold rule, else companyfacts starts late
            WHEN EXISTS (SELECT 1 FROM ck JOIN silver.financial_facts s ON s.cik = ck.cik
                         WHERE ck.company_id = fg.company_id
                           AND s.metric IN ('revenue', 'net_income', 'total_assets')
                           AND s.period_end < fg.first_any - 200
                           AND s.form NOT LIKE 'DEF%')
                THEN 'companyfacts_gold'
            ELSE 'companyfacts' END AS cause) x
        WHERE fg.any_figures AND r.first_10x_xbrl IS NOT NULL
          AND (t.late_figures OR t.late_revenue)
    ),

    -- metric_missing ---------------------------------------------------------------------------
    missing AS (
        SELECT f.company_id, f.metric, greatest(f.last_end, f.last_ttm) AS last_end,
               EXISTS (SELECT 1 FROM ck JOIN silver.financial_facts s ON s.cik = ck.cik
                       WHERE ck.company_id = f.company_id AND s.metric = f.metric
                         AND s.period_end > greatest(f.last_end, f.last_ttm) + 45
                         AND s.form NOT LIKE 'DEF%') AS in_silver
        FROM fig f
        JOIN figs fg ON fg.company_id = f.company_id
        WHERE f.n >= 4 AND greatest(f.last_end, f.last_ttm) < fg.last_any - 400
    ),
    missing_found AS (
        SELECT m.company_id, 'metric_missing'::text AS kind,
               CASE WHEN bool_or(m.in_silver) THEN 'gold_dropped' ELSE 'not_read' END AS cause,
               'fixable'::text AS reason,
               jsonb_build_object('metrics', jsonb_agg(jsonb_build_object(
                   'metric', m.metric, 'last', m.last_end) ORDER BY m.metric)) AS detail
        FROM missing m GROUP BY m.company_id
    ),

    -- documents_missing ------------------------------------------------------------------------
    docs AS (
        SELECT pf.company_id,
               count(*) AS expected,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM bronze.filing_documents d
                                              WHERE d.accession_no = pf.accession_no)) AS stored,
               max(pf.filing_date) AS last_filed
        FROM pf
        WHERE pf.form IN ('10-K', '10-Q') AND pf.filing_date >= '2016-01-01'
          AND pf.filing_date <= current_date - 7
        GROUP BY pf.company_id
    ),
    docs_found AS (
        SELECT d.company_id, 'documents_missing'::text AS kind,
               CASE WHEN d.stored = 0 THEN 'none' ELSE 'partial' END AS cause,
               CASE WHEN fg.any_figures THEN 'fixable' ELSE 'out_of_scope' END AS reason,
               jsonb_build_object('expected', d.expected, 'stored', d.stored,
                                  'last_filed', d.last_filed) AS detail
        FROM docs d
        JOIN figs fg ON fg.company_id = d.company_id
        WHERE d.stored = 0 OR (d.expected >= 8 AND d.stored < d.expected / 2)
    ),

    -- statements_exhibit -----------------------------------------------------------------------
    last_10k AS (
        SELECT DISTINCT ON (pf.company_id) pf.company_id, pf.accession_no, pf.filing_date
        FROM pf
        WHERE pf.form = '10-K' AND pf.filing_date >= current_date - 500
          AND EXISTS (SELECT 1 FROM bronze.filing_documents d WHERE d.accession_no = pf.accession_no)
        ORDER BY pf.company_id, pf.filing_date DESC, pf.accession_no DESC
    ),
    ex_found AS (
        SELECT l.company_id, 'statements_exhibit'::text AS kind,
               'ex13'::text AS cause,
               'fixable'::text AS reason,
               jsonb_build_object('accession_no', l.accession_no, 'filed', l.filing_date) AS detail
        FROM last_10k l
        CROSS JOIN LATERAL (
            SELECT x.text, x.char_end - x.char_start AS length
            FROM silver.filing_sections x
            WHERE x.accession_no = l.accession_no AND x.section_key = '8'
            ORDER BY x.parser = 'rust' DESC LIMIT 1) s
        WHERE s.length < 1500
          AND s.text ~* '(exhibit\s*13|annual\s+report\s+to\s+(share|stock)holders)'
          -- doc-fetcher stores a 10-K's EX-13 since 076: then the statements are here
          AND NOT EXISTS (SELECT 1 FROM bronze.filing_exhibits e
                          WHERE e.accession_no = l.accession_no AND e.exhibit_type ~ '^EX-13')
    )

    SELECT * FROM nofig_found
    UNION ALL SELECT * FROM behind_found
    UNION ALL SELECT * FROM rereg_found
    UNION ALL SELECT * FROM short_found
    UNION ALL SELECT * FROM missing_found
    UNION ALL SELECT * FROM docs_found
    UNION ALL SELECT * FROM ex_found
$$;

CREATE FUNCTION ops.refresh_coverage() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    started timestamptz := clock_timestamp();
    n integer;
BEGIN
    INSERT INTO ops.coverage AS c (company_id, kind, cause, reason, detail, first_seen, seen_at)
    SELECT f.company_id, f.kind, f.cause, f.reason, f.detail, started, started
    FROM ops.coverage_found() f
    ON CONFLICT (company_id, kind) DO UPDATE
    SET cause = EXCLUDED.cause, reason = EXCLUDED.reason, detail = EXCLUDED.detail,
        seen_at = EXCLUDED.seen_at;
    DELETE FROM ops.coverage c WHERE c.seen_at < started;  -- not found again: closed
    SELECT count(*) INTO n FROM ops.coverage;
    INSERT INTO ops.coverage_runs (run_at, listed, gaps, seconds)
    SELECT started, count(*), n, extract(epoch FROM clock_timestamp() - started)
    FROM silver.companies c
    WHERE c.exchanges && ARRAY['Nasdaq', 'NYSE', 'CBOE', 'TXSE']::text[];
    DELETE FROM ops.coverage_runs WHERE run_at < started - interval '90 days';
    RETURN n;
END
$$;

-- 3. Readers -----------------------------------------------------------------------------------

-- Names companies: ops_reader (and `make coverage`) only, never the Query API.
CREATE VIEW ops.coverage_report AS
SELECT v.company_id, c.tickers[1] AS ticker, c.name, v.kind, v.cause, v.reason, v.detail,
       v.first_seen, v.seen_at
FROM ops.coverage v
JOIN silver.companies c ON c.company_id = v.company_id;

CREATE VIEW ops.coverage_summary AS
SELECT v.kind, v.cause, v.reason, count(*) AS companies, min(v.first_seen) AS first_seen,
       max(v.seen_at) AS seen_at
FROM ops.coverage v
GROUP BY v.kind, v.cause, v.reason;

GRANT SELECT ON ops.coverage_report, ops.coverage_summary TO ops_reader;

-- The public status page's counts: per kind and reason, plus the companies measured and when.
CREATE FUNCTION ops.public_coverage_gaps()
RETURNS TABLE (kind text, reason text, companies bigint, listed integer, refreshed_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
    SELECT v.kind, v.reason, count(*), r.listed, r.run_at
    FROM ops.coverage v
    CROSS JOIN (SELECT x.listed, x.run_at FROM ops.coverage_runs x
                ORDER BY x.run_at DESC LIMIT 1) r
    GROUP BY v.kind, v.reason, r.listed, r.run_at
    ORDER BY v.kind, v.reason
$$;
REVOKE ALL ON FUNCTION ops.public_coverage_gaps() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.public_coverage_gaps() TO query_api;

-- 4. A company's CIKs changed: rebuild its gold first ------------------------------------------

-- Their gold reads as built by version 0 at the epoch (1970): due whatever the rules, and the
-- catch-up (catchup.py) takes the epoch first, before the companies never stamped. Not -infinity:
-- psycopg can't read it back (the catch-up reports the oldest build date).
CREATE FUNCTION ops.rebuild_soon(p_company_ids text[]) RETURNS integer
LANGUAGE sql AS $$
    WITH due AS (
        INSERT INTO ops.company_builds AS b (kind, company_id, version, built_at)
        SELECT DISTINCT 'gold', id, 0, 'epoch'::timestamptz
        FROM unnest(p_company_ids) id WHERE id IS NOT NULL
        ON CONFLICT (kind, company_id) DO UPDATE
        SET version = 0, built_at = 'epoch', failed_version = NULL, failed_at = NULL,
            error = NULL
        RETURNING 1
    )
    SELECT count(*)::integer FROM due
$$;
