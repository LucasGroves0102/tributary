-- Coverage notes that say what is missing after 080 (db/DESIGN.md "081"). ops.coverage_found (076's
-- body, edits marked "081"); measured on hermes 2026-11-16, read-only, from raw companyfacts.
--
-- 1. history_short: revenue_yearly (reason not_in_sec_data) where gold reads the company's revenue
--    for years (TTM rows from the 10-Ks' stated years) 400+ days before its first quarter: the
--    10-Qs carry revenue under no standard tag (in the raw companyfacts of the 639 companies whose
--    revenue started late, 99 had no revenue line of any kind in their quarters, only the 10-K's
--    year). revenue_tag stays for the rest: an industry line Tributary doesn't take as the total
--    (ContractsRevenue, LicensesRevenue, OilAndGasRevenue...: 080's header), the company's own tag,
--    or no revenue yet (139 of the 639 tagged no revenue line in those years: a biotech before its
--    first sale). detail gains revenue_years_from. On hermes' gold today (rules 64, before 080):
--    revenue_yearly 70, revenue_tag 569. revenue_excise (not_in_sec_data: Brown-Forman,
--    Constellation Brands) and revenue_predecessor (out_of_scope: Skyline Champion) for 080's
--    silver.industry_total_exclusions.
--
-- 2. metric_missing (124 listed companies on hermes):
--    - gold_dropped only when silver's later facts are under a tag gold reads as the line, not a
--      bank's sum parts (InterestIncomeExpenseNet, NoninterestIncome: gold_line false), which
--      biotechs and mortgage REITs carry as net interest income. gold_dropped 5 (was 41 with the
--      parts): Aqua Metals' zero, Perspective Therapeutics' one 10-K year, Booking (077 reads it),
--      Southern California Edison (filed with its parent), Vale (its 20-Fs tag only cash generated
--      by operations, before interest and taxes).
--    - trust (out_of_scope), 13: a commodity pool or trust (SIC 6221: iShares Gold and Silver,
--      abrdn's metal ETFs, Teucrium, USCF) states net assets, no balance-sheet totals or cash flow
--      statement, and its "revenue" is gains on the metal.
--    - partners_capital, 8: equity alone missing at a partnership or LLC (MPLX, Plains All American
--      and GP, KNOT, Navios, Icahn Enterprises, Seapeak, Belpointe): filed as partners' or members'
--      capital, which gold doesn't read as stockholders' equity (080's header: not measured safe).
--    not_read 98: no revenue lately (biotechs after a collaboration ends, explorers, shells), a
--    bank's net revenue part under its own tag (Arrow, Banner, Magyar), mortgage REITs' interest
--    lines, a REIT's lease income alone (American Homes 4 Rent), an extension tag (Acadian's
--    equity), a combined filing's dimensioned facts (Cinemark since 2022).

-- Web words: services/web/src/company/coverage.ts. Locks on hermes (lock_timeout 3 s): CREATE OR
-- REPLACE of one function (catalog only); ops.coverage changes at the next nightly refresh. Undo:
-- 076's body.

SET LOCAL lock_timeout = '3s';

CREATE OR REPLACE FUNCTION ops.coverage_found()
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
        SELECT u.company_id, m.metric, q.first_end, q.last_end, q.n, t.first_ttm, t.last_ttm
        FROM u
        CROSS JOIN (VALUES ('revenue'), ('net_income'), ('total_assets'),
                           ('stockholders_equity'), ('operating_cash_flow')) m(metric)
        CROSS JOIN LATERAL (
            SELECT min(g.period_end) AS first_end, max(g.period_end) AS last_end, count(*) AS n
            FROM gold.quarterly_financials g
            WHERE g.company_id = u.company_id AND g.metric = m.metric) q
        CROSS JOIN LATERAL (
            SELECT min(d.period_end) AS first_ttm, max(d.period_end) AS last_ttm  -- 081
            FROM gold.derived_financials d
            WHERE d.company_id = u.company_id AND d.span = 'ttm' AND d.metric = m.metric) t
    ),
    figs AS MATERIALIZED (
        SELECT fig.company_id,
               -- where the figures start: the first quarter of a flow (an equity statement's
               -- opening balance reaches years before the first income statement)
               min(fig.first_end) FILTER (WHERE fig.metric IN ('revenue', 'net_income',
                                                               'operating_cash_flow')) AS first_any,
               min(fig.first_end) FILTER (WHERE fig.metric = 'revenue') AS first_revenue,
               -- 081: revenue's first year (a TTM row: four quarters or a stated year)
               min(fig.first_ttm) FILTER (WHERE fig.metric = 'revenue') AS first_revenue_year,
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
               -- each is in the SEC's data (a filing's XBRL): Tributary's to read; 081: but a
               -- revenue tagged only in the 10-K, the quarters under no standard revenue tag, or
               -- net sales under the company's own tag (the standard one holds sales before excise
               -- taxes); and a predecessor's filings aren't this business's
               CASE WHEN x.cause IN ('revenue_yearly', 'revenue_excise') THEN 'not_in_sec_data'
                    WHEN x.cause = 'revenue_predecessor' THEN 'out_of_scope'
                    ELSE 'fixable' END AS reason,
               jsonb_strip_nulls(jsonb_build_object(
                   'first_xbrl_report', r.first_10x_xbrl, 'figures_from', fg.first_any,
                   'revenue_from', fg.first_revenue,
                   'revenue_years_from', CASE WHEN x.cause = 'revenue_yearly'  -- 081
                                              THEN fg.first_revenue_year END,
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
            -- 081: an industry total 080 never reads at this company (silver.industry_total_
            -- exclusions: sales before excise taxes, a predecessor's figures)
            WHEN NOT t.late_figures AND EXISTS (
                    SELECT 1 FROM silver.industry_total_exclusions e
                    WHERE e.company_id = fg.company_id AND e.cause = 'excise')
                THEN 'revenue_excise'
            WHEN NOT t.late_figures AND EXISTS (
                    SELECT 1 FROM silver.industry_total_exclusions e
                    WHERE e.company_id = fg.company_id AND e.cause = 'predecessor')
                THEN 'revenue_predecessor'
            -- 081: revenue read for years (stated in 10-Ks) long before its first quarter
            WHEN NOT t.late_figures AND fg.first_revenue_year <= fg.first_revenue - 400
                THEN 'revenue_yearly'
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
                       -- 081: under a tag gold reads as the line, not a sum's part (a biotech's
                       -- net interest income on its cash is stored under revenue for banks' sum)
                       JOIN silver.metric_tags mt ON mt.metric = s.metric AND mt.tag = s.tag
                                                 AND mt.taxonomy = s.taxonomy AND mt.gold_line
                       WHERE ck.company_id = f.company_id AND s.metric = f.metric
                         AND s.period_end > greatest(f.last_end, f.last_ttm) + 45
                         AND s.form NOT LIKE 'DEF%') AS in_silver
        FROM fig f
        JOIN figs fg ON fg.company_id = f.company_id
        WHERE f.n >= 4 AND greatest(f.last_end, f.last_ttm) < fg.last_any - 400
    ),
    missing_found AS (
        SELECT m.company_id, 'metric_missing'::text AS kind, x.cause,
               CASE WHEN x.cause = 'trust' THEN 'out_of_scope' ELSE 'fixable' END AS reason,
               m.detail
        FROM (SELECT m.company_id, bool_or(m.in_silver) AS in_silver,
                     bool_and(m.metric = 'stockholders_equity') AS equity_only,
                     jsonb_build_object('metrics', jsonb_agg(jsonb_build_object(
                         'metric', m.metric, 'last', m.last_end) ORDER BY m.metric)) AS detail
              FROM missing m GROUP BY m.company_id) m
        JOIN u ON u.company_id = m.company_id
        CROSS JOIN LATERAL (SELECT CASE
            -- 081: a commodity pool or trust (SIC 6221) states net assets: no balance-sheet totals
            -- or cash flow statement
            WHEN u.sic = '6221' THEN 'trust'
            WHEN m.in_silver THEN 'gold_dropped'
            -- 081: a partnership's or LLC's equity, filed as partners' or members' capital
            WHEN m.equity_only
                 AND u.name ~* '(\mL\.?\s?P\.?$|\mpartners\M|\mLLC$|\mL\.L\.C\.$)'
                THEN 'partners_capital'
            ELSE 'not_read' END AS cause) x
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
