-- The proof layer at silver, part 2: the gate. Every silver fact of a filing that prints inline XBRL
-- is joined to where the filing prints it (166's silver.printed_facts) and its printed value checked.
--
-- 1. silver.printed_value(text, format, scale, sign): the number an ix:nonFraction prints, as its
--    attributes say (ixbrl.py's rule 3, plus what the sample showed it lacked): format by its local
--    name, no format = a plain decimal; num-dot-decimal (and numdotdecimal, numcommadot,
--    num-dot-decimal-apos, numspacedot, num-space-dot) drops commas, spaces and apostrophes;
--    num-comma-decimal (numcommadecimal, numdotcomma, numspacecomma, num-space-comma) drops dots,
--    spaces and apostrophes and reads the comma as the decimal point; zero-dash, fixed-zero,
--    numdash read 0, as a lone dash does under any format; ixt-sec:numwordsen reads "no", "none",
--    "nil", "zero" and the words of 1-99; ".00" is a number. Then x 10^scale, negated for sign
--    "-". Anything else is NULL (unreadable). silver.printed_round(value, decimals): the value at
--    the printed precision (INF or none: as is).
-- 2. silver.gate_facts(accessions): per accession, the served parser's printed facts (rust's row
--    first, as gold.filing_sections serves sections), and every silver.financial_facts row of the
--    accession. A fact's occurrences are the printed facts of its tag (the concept's local name;
--    those of its taxonomy's prefix when any), unit (the measure) and period. Its status:
--      a  agrees: every readable occurrence prints the fact's value at its precision;
--      d  disagrees: one doesn't (silver.fact_disagreements keeps both values and where);
--      n  not located: no occurrence (a hidden fact, or not printed);
--      x  not applicable: a member sum or pick (tag "[...]": no single printed number), or no
--         occurrence is readable (reason 'format').
--    Its line: the occurrence in a section, the disagreeing one first, then in the statements'
--    section (10-K "8" then "15", 10-Q "I-1", 20-F "18" then "17"), then the most precise
--    (decimals), then the first; none when no occurrence is in a section. One silver.fact_gates row
--    per accession: counts and the per-fact results as jsonb
--      lines: [{"c": cik, "t": tag, "u": unit, "s": start, "e": end, "st": status, "r": reason,
--               "k": section key, "a": start, "b": end}, ...]  (document offsets, as 166's)
--    A filing without printed facts (pre-iXBRL, an 8-K, a 6-K, a 40-F) has no row: not applicable.
-- 3. The catch-up kind 'gate' (parser-index, filing_parser/catchup.py): ops.gate_backlog lists
--    accessions with printed facts and no current gate: none yet, another silver.gate_version(), the
--    printed facts rewritten since, or the filer's gold rebuilt since (a rebuild follows every change
--    to a company's facts: the loaders refresh gold for the CIKs they change).
-- 4. ops.check_financials (074's body, the edits marked 167) finds 'printed_mismatch' (warning): a
--    disagreeing fact, one per company, metric and period, with its tag, the factor (fact over
--    printed), the filing and the section; never the values (014's rule). Version 167 (the
--    check_financials catch-up re-checks every company). ops.company_backlog (165's body, the edit
--    marked 167): a company's check is also due when one of its filings was gated since. The
--    status page lists the check (ops.quality_tiers, 074's view with the new row).
--
-- Measured (2026-11-28, docs/test-people/reports/proof-layer/budget.md): a prototype of this rule
-- on 173 documents sampled from hermes and their 15,475 silver facts: 13,721 agree, 1,200 not located
-- (782 in 10-Qs whose statements were outside every section before 166's rule 15; 305 in 40-Fs, whose
-- facts are in exhibits), 537 not applicable, 17 disagreed and every one of the 17 was a reading
-- problem (ixt-sec:numwordsen, ".00") that rule 1 now reads: 0 real disagreements. Size: ~3 KB of
-- jsonb an accession, ~0.25-0.4 GB for every stored iXBRL filing.
--
-- Access: silver.fact_gates and silver.fact_disagreements are filing content (the lines are
-- positions in the filing): the filings tier, silver.filing_tables' policy (067's form), granted to
-- query_api. Backups: their foreign key leads to bronze.filing_documents (SLIM_EXCLUDE_DATA).
--
-- Locks on hermes: new tables and functions; the foreign keys take a SHARE ROW EXCLUSIVE lock on
-- bronze.filing_documents for an instant (writers wait, readers don't); CREATE OR REPLACE of
-- ops.check_financials, ops.company_backlog (catalog only; a running caller keeps its body) and of the
-- view ops.quality_tiers (an ACCESS EXCLUSIVE lock on the view for an instant: the status page's
-- reads wait), hence the short lock_timeout; ops.catchup's CHECK re-created (its 11 rows read).
-- Backout guards: ops.check_financials and ops.company_backlog are in the chain; the guards of the
-- files that start at their head bodies carry this migration's md5(prosrc), computed from the bodies
-- below as 161's were (db/backout/README.md).

SET LOCAL lock_timeout = '3s';

-- 1. The printed value -------------------------------------------------------------------------------

CREATE FUNCTION silver.printed_value(p_text text, p_format text, p_scale text, p_sign text)
RETURNS numeric
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE
    kind text := lower(regexp_replace(btrim(coalesce(p_format, '')), '^.*:', ''));
    t text := btrim(coalesce(p_text, ''), ' ' || chr(9) || chr(10) || chr(13) || chr(160));
    spaces text := ' ''' || chr(9) || chr(10) || chr(13) || chr(160) || chr(8201) || chr(8239);
    units text[] := ARRAY['one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
                          'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
                          'seventeen', 'eighteen', 'nineteen'];
    tens text[] := ARRAY['twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty',
                         'ninety'];
    words text[];
    scale text := btrim(coalesce(p_scale, ''));
    v numeric;
BEGIN
    IF kind IN ('zerodash', 'zero-dash', 'fixed-zero', 'fixedzero', 'numdash') THEN
        v := 0;
    ELSIF kind IN ('numwordsen', 'num-words-en') THEN
        t := lower(btrim(t, ' .'));
        IF t IN ('no', 'none', 'nil', 'zero') THEN
            v := 0;
        ELSIF array_position(units, t) IS NOT NULL THEN
            v := array_position(units, t);
        ELSIF array_position(tens, t) IS NOT NULL THEN
            v := 10 * (array_position(tens, t) + 1);
        ELSE
            words := regexp_split_to_array(t, '[- ]');
            IF cardinality(words) = 2 AND array_position(tens, words[1]) IS NOT NULL
               AND array_position(units, words[2]) BETWEEN 1 AND 9 THEN
                v := 10 * (array_position(tens, words[1]) + 1) + array_position(units, words[2]);
            ELSE
                RETURN NULL;
            END IF;
        END IF;
    ELSIF kind = '' THEN
        t := translate(t, spaces, '');
    ELSIF kind IN ('num-dot-decimal', 'numdotdecimal', 'numcommadot', 'num-dot-decimal-apos',
                   'numspacedot', 'num-space-dot') THEN
        t := translate(t, ',' || spaces, '');
    ELSIF kind IN ('num-comma-decimal', 'numcommadecimal', 'numdotcomma', 'numspacecomma',
                   'num-space-comma') THEN
        t := replace(translate(t, '.' || spaces, ''), ',', '.');
    ELSE
        RETURN NULL;  -- a format this doesn't read
    END IF;
    IF v IS NULL THEN
        IF kind <> '' AND t IN ('-', chr(8211), chr(8212)) THEN
            v := 0;  -- a dash printed for zero
        ELSIF t ~ '^([0-9]+(\.[0-9]*)?|\.[0-9]+)$' THEN
            v := t::numeric;
        ELSE
            RETURN NULL;
        END IF;
    END IF;
    IF scale <> '' THEN
        IF scale !~ '^-?[0-9]{1,2}$' THEN
            RETURN NULL;
        END IF;
        v := v * power(10::numeric, scale::integer);
    END IF;
    IF btrim(coalesce(p_sign, '')) = '-' THEN
        v := -v;
    END IF;
    RETURN v;
END
$$;

-- The value at a printed precision: decimals as an integer, else (INF, none) the value itself.
CREATE FUNCTION silver.printed_round(p_value numeric, p_decimals text) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
BEGIN
    IF btrim(coalesce(p_decimals, '')) ~ '^-?[0-9]{1,2}$' THEN
        RETURN round(p_value, btrim(p_decimals)::integer);
    END IF;
    RETURN p_value;
END
$$;

-- 2. The gate ------------------------------------------------------------------------------------------

-- The gate's rule (header, 2). Replace with the number of the migration that changes it: the
-- catch-up then re-gates every filing.
CREATE FUNCTION silver.gate_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 167';

CREATE TABLE silver.fact_gates (
    accession_no   text        PRIMARY KEY REFERENCES bronze.filing_documents ON DELETE CASCADE,
    cik            text        NOT NULL,  -- bronze.filing_documents.cik (the filer)
    parser         text        NOT NULL,  -- whose printed facts were read
    parser_version text        NOT NULL,
    printed_at     timestamptz NOT NULL,  -- their silver.printed_facts.written_at
    gate_version   integer     NOT NULL,
    gated_at       timestamptz NOT NULL,
    facts          integer     NOT NULL,  -- silver facts of the accession
    agrees         integer     NOT NULL,
    disagrees      integer     NOT NULL,
    not_located    integer     NOT NULL,
    not_applicable integer     NOT NULL,
    lines          jsonb       NOT NULL   -- header, 2
);
CREATE INDEX fact_gates_cik ON silver.fact_gates (cik, gated_at);

-- A fact whose filing prints another number at its line: both values, and where.
CREATE TABLE silver.fact_disagreements (
    accession_no text    NOT NULL REFERENCES bronze.filing_documents ON DELETE CASCADE,
    cik          text    NOT NULL,
    tag          text    NOT NULL,
    unit         text    NOT NULL,
    period_start date,
    period_end   date    NOT NULL,
    metric       text    NOT NULL,
    value        numeric NOT NULL,  -- silver.financial_facts.value
    printed      numeric NOT NULL,  -- what the disagreeing occurrence prints (silver.printed_value)
    section_key  text,              -- where it is printed (document offsets); none outside sections
    char_start   integer,
    char_end     integer,
    UNIQUE NULLS NOT DISTINCT (accession_no, cik, tag, unit, period_start, period_end)
);
CREATE INDEX fact_disagreements_cik ON silver.fact_disagreements (cik);

GRANT SELECT ON silver.fact_gates, silver.fact_disagreements TO query_api;

ALTER TABLE silver.fact_gates ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON silver.fact_gates FOR SELECT TO query_api
    USING ((SELECT k.company_id
            FROM bronze.filing_documents d
            JOIN silver.company_ciks k ON k.cik = d.cik
            WHERE d.accession_no = fact_gates.accession_no)
           IN (SELECT r FROM authz.readable_companies('filings') r));
ALTER TABLE silver.fact_disagreements ENABLE ROW LEVEL SECURITY;
CREATE POLICY granted_filings ON silver.fact_disagreements FOR SELECT TO query_api
    USING ((SELECT k.company_id
            FROM bronze.filing_documents d
            JOIN silver.company_ciks k ON k.cik = d.cik
            WHERE d.accession_no = fact_disagreements.accession_no)
           IN (SELECT r FROM authz.readable_companies('filings') r));

-- Gate these accessions (header, 2): replaces their rows. Returns the facts gated.
CREATE FUNCTION silver.gate_facts(p_accessions text[]) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    acc text;
    v integer := silver.gate_version();
    n integer;
    total integer := 0;
BEGIN
    DELETE FROM silver.fact_disagreements WHERE accession_no = ANY(p_accessions);
    DELETE FROM silver.fact_gates WHERE accession_no = ANY(p_accessions);
    FOREACH acc IN ARRAY p_accessions LOOP
        WITH served AS MATERIALIZED (
            SELECT p.parser, p.parser_version, p.written_at, p.facts, d.form, d.cik
            FROM silver.printed_facts p
            JOIN bronze.filing_documents d ON d.accession_no = p.accession_no
            WHERE p.accession_no = acc
            ORDER BY p.parser = 'rust' DESC, p.written_at DESC
            LIMIT 1
        ), printed AS MATERIALIZED (
            SELECT e.j->>'k' AS section_key,
                   CASE WHEN position(':' IN e.j->>'c') > 0
                        THEN split_part(e.j->>'c', ':', 1) END AS prefix,
                   regexp_replace(e.j->>'c', '^.*:', '') AS tag,
                   e.j->>'m' AS unit, e.j->>'s' AS ps, e.j->>'e' AS pe, e.j->>'d' AS decimals,
                   (e.j->>'a')::integer AS a, (e.j->>'b')::integer AS b,
                   silver.printed_value(e.j->>'v', e.j->>'f', e.j->>'sc', e.j->>'sg') AS pv,
                   s.form
            FROM served s
            CROSS JOIN LATERAL jsonb_array_elements(s.facts) e(j)
        ), facts AS MATERIALIZED (
            SELECT f.cik, f.tag, f.taxonomy, f.unit, f.metric, f.period_start, f.period_end, f.value,
                   to_char(f.period_start, 'YYYY-MM-DD') AS ps,
                   to_char(f.period_end, 'YYYY-MM-DD') AS pe
            FROM silver.financial_facts f
            WHERE f.accession_no = acc AND EXISTS (SELECT 1 FROM served)
        ), matched AS (
            SELECT f.cik, f.tag, f.unit, f.ps, f.pe, f.taxonomy, p.prefix, p.section_key,
                   p.decimals, p.a, p.b, p.pv, p.form,
                   p.pv IS NOT NULL AND p.pv = silver.printed_round(f.value, p.decimals) AS agrees,
                   count(*) FILTER (WHERE p.prefix = f.taxonomy)
                       OVER (PARTITION BY f.cik, f.tag, f.unit, f.ps, f.pe) AS own
            FROM facts f
            JOIN printed p ON p.tag = f.tag AND p.unit = f.unit AND p.pe = f.pe
                          AND p.ps IS NOT DISTINCT FROM f.ps
            WHERE position('[' IN f.tag) = 0
        ), candidates AS (
            SELECT * FROM matched WHERE own = 0 OR prefix = taxonomy
        ), verdicts AS MATERIALIZED (
            SELECT f.cik, f.tag, f.unit, f.metric, f.period_start, f.period_end, f.value,
                   CASE WHEN position('[' IN f.tag) > 0 THEN 'x'
                        WHEN count(c.cik) = 0 THEN 'n'
                        WHEN count(c.pv) = 0 THEN 'x'
                        WHEN bool_or(c.pv IS NOT NULL AND NOT c.agrees) THEN 'd'
                        ELSE 'a' END AS status,
                   CASE WHEN position('[' IN f.tag) > 0 THEN 'member'
                        WHEN count(c.cik) = 0 THEN 'not_printed'
                        WHEN count(c.pv) = 0 THEN 'format' END AS reason,
                   (array_agg(jsonb_build_object('k', c.section_key, 'a', c.a, 'b', c.b)
                              ORDER BY c.pv IS NOT NULL AND NOT c.agrees DESC,
                                       CASE WHEN c.form LIKE '10-K%' AND c.section_key = '8'
                                              OR c.form LIKE '10-Q%' AND c.section_key = 'I-1'
                                              OR c.form LIKE '20-F%' AND c.section_key = '18'
                                            THEN 0
                                            WHEN c.form LIKE '10-K%' AND c.section_key = '15'
                                              OR c.form LIKE '20-F%' AND c.section_key = '17'
                                            THEN 1 ELSE 2 END,
                                       CASE WHEN c.decimals = 'INF' THEN 99
                                            WHEN btrim(c.decimals) ~ '^-?[0-9]{1,2}$'
                                            THEN btrim(c.decimals)::integer
                                            ELSE -99 END DESC,
                                       c.a)
                        FILTER (WHERE c.section_key IS NOT NULL))[1] AS line,
                   (array_agg(c.pv ORDER BY c.section_key IS NULL, c.a)
                        FILTER (WHERE c.pv IS NOT NULL AND NOT c.agrees))[1] AS printed
            FROM facts f
            LEFT JOIN candidates c ON c.cik = f.cik AND c.tag = f.tag AND c.unit = f.unit
                                  AND c.pe = f.pe AND c.ps IS NOT DISTINCT FROM f.ps
            GROUP BY f.cik, f.tag, f.unit, f.metric, f.period_start, f.period_end, f.value
        ), gated AS (
            INSERT INTO silver.fact_gates (accession_no, cik, parser, parser_version, printed_at,
                                           gate_version, gated_at, facts, agrees, disagrees,
                                           not_located, not_applicable, lines)
            SELECT acc, s.cik, s.parser, s.parser_version, s.written_at, v, clock_timestamp(),
                   (SELECT count(*) FROM verdicts),
                   (SELECT count(*) FROM verdicts WHERE status = 'a'),
                   (SELECT count(*) FROM verdicts WHERE status = 'd'),
                   (SELECT count(*) FROM verdicts WHERE status = 'n'),
                   (SELECT count(*) FROM verdicts WHERE status = 'x'),
                   (SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                               'c', x.cik, 't', x.tag, 'u', x.unit, 's', x.period_start,
                               'e', x.period_end, 'st', x.status, 'r', x.reason,
                               'k', x.line->>'k', 'a', (x.line->>'a')::integer,
                               'b', (x.line->>'b')::integer))
                           ORDER BY x.tag, x.unit, x.period_end, x.period_start, x.cik), '[]')
                    FROM verdicts x)
            FROM served s
            RETURNING facts
        )
        INSERT INTO silver.fact_disagreements (accession_no, cik, tag, unit, period_start,
                                               period_end, metric, value, printed, section_key,
                                               char_start, char_end)
        SELECT acc, x.cik, x.tag, x.unit, x.period_start, x.period_end, x.metric, x.value,
               x.printed, x.line->>'k', (x.line->>'a')::integer, (x.line->>'b')::integer
        FROM verdicts x
        WHERE x.status = 'd' AND EXISTS (SELECT 1 FROM gated);
        SELECT coalesce(sum(g.facts), 0) INTO n FROM silver.fact_gates g WHERE g.accession_no = acc;
        total := total + n;
    END LOOP;
    RETURN total;
END
$$;

-- 3. The catch-up kind 'gate' --------------------------------------------------------------------------

-- Accessions with printed facts and no current gate (header, 3): 079's anti-join shape.
CREATE FUNCTION ops.gate_backlog(p_version integer)
RETURNS TABLE (accession_no text, filed date)
LANGUAGE sql STABLE AS $$
    SELECT d.accession_no, d.filed
    FROM (SELECT p.accession_no, max(p.written_at) AS written_at
          FROM silver.printed_facts p GROUP BY p.accession_no) p
    JOIN bronze.filing_documents d ON d.accession_no = p.accession_no
    WHERE NOT EXISTS (
          SELECT 1 FROM silver.fact_gates g
          WHERE g.accession_no = p.accession_no AND g.gate_version = p_version
            AND g.printed_at >= p.written_at
            AND NOT EXISTS (SELECT 1 FROM silver.company_ciks k
                            JOIN ops.company_builds b ON b.kind = 'gold'
                                                     AND b.company_id = k.company_id
                            WHERE k.cik = d.cik AND b.built_at > g.gated_at))
$$;

ALTER TABLE ops.catchup DROP CONSTRAINT catchup_kind_check;
ALTER TABLE ops.catchup ADD CONSTRAINT catchup_kind_check
    CHECK (kind IN ('parser', 'passages', 'diffs', 'gold', 'check_financials', 'check_segments',
                    'check_sections', 'statements', 'known', 'releases', 'gate'));
INSERT INTO ops.catchup (kind, paused) VALUES ('gate', false);

-- 4. The finding ---------------------------------------------------------------------------------------

-- 074's body with the edits marked "167" (header, 4).
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
    -- 072: what gold believes, once: the annual comparison and the tag check read it
    best AS MATERIALIZED (
        SELECT * FROM gold.best_facts(p_company_ids)
    ),
    annual AS (  -- each reported fiscal year, picked as gold picks its facts
        SELECT b.company_id, b.metric, b.period_start, b.period_end, b.value, b.accession_no,
               b.tag, b.unit, b.filed
        FROM best b
        WHERE b.period_type = 'annual' AND b.additive
    ),
    -- 072: the metric's other gold tags in the believed fact's own filing and period, positive
    -- (interest expense and debt without their sign, as gold reads them)
    tag_facts AS MATERIALIZED (
        SELECT k.company_id, t.metric, f.tag, f.unit, f.period_start, f.period_end,
               f.accession_no,
               CASE WHEN t.metric IN ('interest_expense', 'long_term_debt',
                                      'long_term_debt_current')
                    THEN abs(f.value) ELSE f.value END AS value
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.taxonomy = f.taxonomy AND t.tag = f.tag AND t.gold_line
                                 AND (t.company_id IS NULL OR t.company_id = k.company_id)
        WHERE k.company_id = ANY(p_company_ids) AND f.value <> 0
    ),
    -- 072: two tags of one line in one filing and period that can't both be right: the figure gold
    -- reads under a fifth of a part of it (ops.tag_relations 'part'), two names of one line 5x
    -- apart ('same'), or any two 1,000 or 1,000,000 times apart (one figure in two scales; revenue's
    -- 064 resolves in best_facts, so a revenue pair that far apart is no finding at all). One per
    -- company, metric and period end: the shortest period, then the largest gap.
    disagree AS (
        SELECT DISTINCT ON (b.company_id, b.metric, b.period_end)
               b.company_id, b.metric, b.period_start, b.period_end, b.tag, b.accession_no,
               b.form, b.filed, o.tag AS other_tag, o.value / b.value AS factor,
               CASE WHEN x.scale THEN 'scale'
                    WHEN r.relation = 'same' THEN 'same_line'
                    ELSE 'total_below_part' END AS rule
        FROM best b
        JOIN tag_facts o ON o.company_id = b.company_id AND o.metric = b.metric
                        AND o.accession_no = b.accession_no AND o.unit = b.unit
                        AND o.period_start IS NOT DISTINCT FROM b.period_start
                        AND o.period_end = b.period_end AND o.tag <> b.tag
        LEFT JOIN ops.tag_relations r
          ON r.metric = b.metric
         AND ((r.tag = b.tag AND r.other = o.tag)
              OR (r.relation = 'same' AND r.tag = o.tag AND r.other = b.tag))
        CROSS JOIN LATERAL (
            SELECT greatest(o.value, b.value) / least(o.value, b.value) AS gap
        ) g
        CROSS JOIN LATERAL (
            SELECT abs(g.gap - 1000) <= 5 OR abs(g.gap - 1000000) <= 5000 AS scale
        ) x
        WHERE b.value > 0 AND o.value > 0
          AND CASE WHEN x.scale THEN b.metric <> 'revenue'
                   ELSE (r.relation = 'same' AND g.gap > 5)
                        OR (r.relation = 'part' AND r.tag = b.tag AND b.value * 5 < o.value) END
        ORDER BY b.company_id, b.metric, b.period_end,
                 b.period_end - coalesce(b.period_start, b.period_end), g.gap DESC, o.tag
    ),
    -- 072: quarters whose first filing (same tag, unit, dates) said something else; the
    -- restatement finding, and what explains a year whose quarters don't add up
    restated AS MATERIALIZED (
        SELECT g.company_id, g.metric, g.period_start, g.period_end, g.accession_no, g.filed,
               o.accession_no AS original_accession, o.filed AS original_filed,
               round((g.value - o.value) / abs(o.value), 4) AS change
        FROM gold.quarterly_financials g
        CROSS JOIN LATERAL (
            -- 050: the metrics gold reads without their sign are compared without it
            SELECT f.accession_no, f.filed,
                   CASE WHEN g.metric IN ('interest_expense', 'long_term_debt',
                                          'long_term_debt_current')
                        THEN abs(f.value) ELSE f.value END AS value
            FROM silver.company_ciks k
            JOIN silver.financial_facts f ON f.cik = k.cik
            WHERE k.company_id = g.company_id AND f.metric = g.metric
              AND f.period_end = g.period_end
              AND f.period_start IS NOT DISTINCT FROM g.period_start
              AND f.tag = split_part(g.source_tag, ':', 2) AND f.unit = g.unit
            ORDER BY f.filed, f.accession_no
            LIMIT 1
        ) o
        WHERE g.company_id = ANY(p_company_ids) AND g.value_source = 'reported'
          AND o.filed < g.filed AND o.value <> 0
          AND abs(g.value - o.value) > 0.005 * abs(o.value)
    ),
    -- what gold.usable_facts drops, for the reader: facts of mapped tags that can't be a figure of
    -- the period. Never the value (014's rule for detail).
    mistagged AS (  -- one per company and metric, its latest, with how many periods
        SELECT DISTINCT ON (x.company_id, x.metric) x.*, count(*) OVER (PARTITION BY x.company_id, x.metric) AS periods
        FROM (
            SELECT DISTINCT ON (k.company_id, t.metric, f.period_end)
                   k.company_id, t.metric, f.period_end, f.tag, f.accession_no, f.form, f.filed
            FROM silver.company_ciks k
            JOIN silver.financial_facts f ON f.cik = k.cik
            JOIN silver.metric_tags t ON t.tag = f.tag AND t.gold_line
            WHERE k.company_id = ANY(p_company_ids)
              AND f.period_end > f.filed + 7
              AND coalesce(f.form, '') !~ '^(DEF|PRE)'
            ORDER BY k.company_id, t.metric, f.period_end, f.filed DESC, f.accession_no DESC
        ) x
        ORDER BY x.company_id, x.metric, x.period_end DESC
    ),
    debt_scale AS (  -- a debt fact over five times the same date's total liabilities and total assets
        SELECT DISTINCT ON (k.company_id, t.metric, f.period_end)
               k.company_id, t.metric, f.period_end, f.tag, f.accession_no, f.form, f.filed
        FROM silver.company_ciks k
        JOIN silver.financial_facts f ON f.cik = k.cik
        JOIN silver.metric_tags t ON t.tag = f.tag AND t.metric IN ('long_term_debt', 'long_term_debt_current')
        JOIN LATERAL (
            SELECT x.value FROM silver.financial_facts x
            WHERE x.cik = f.cik AND x.metric = 'total_liabilities' AND x.tag = 'Liabilities'
              AND x.period_end = f.period_end AND x.period_start IS NULL
              AND x.unit = f.unit  -- 057: in the debt's own currency (header, 4)
            ORDER BY x.filed DESC, x.accession_no DESC LIMIT 1
        ) l ON f.value > l.value * 5
        JOIN LATERAL (
            SELECT x.value FROM silver.financial_facts x
            WHERE x.cik = f.cik AND x.metric = 'total_assets' AND x.tag = 'Assets'
              AND x.period_end = f.period_end AND x.period_start IS NULL
              AND x.unit = f.unit  -- 057
            ORDER BY x.filed DESC, x.accession_no DESC LIMIT 1
        ) a ON f.value > a.value * 5
        WHERE k.company_id = ANY(p_company_ids) AND f.period_start IS NULL
        ORDER BY k.company_id, t.metric, f.period_end, f.filed DESC, f.accession_no DESC
    ),
    -- 050: debt parts adding up to 1,000 or 1,000,000 times the same filing's total for the date
    -- (gold.usable_facts leaves them out); one per company and date, its latest filing
    parts_scale AS (
        SELECT DISTINCT ON (k.company_id, p.period_end)
               k.company_id, p.period_end, p.accession_no, p.form, p.filed,
               round(p.parts / p.total) AS factor
        FROM silver.company_ciks k
        CROSS JOIN LATERAL (
            SELECT f.period_end, f.accession_no, max(f.form) AS form, max(f.filed) AS filed,
                   sum(abs(f.value)) FILTER (WHERE f.tag IN ('LongTermDebtCurrent',
                                                             'LongTermDebtNoncurrent')) AS parts,
                   max(abs(f.value)) FILTER (WHERE f.tag = 'LongTermDebt') AS total
            FROM silver.financial_facts f
            WHERE f.cik = k.cik AND f.metric IN ('long_term_debt', 'long_term_debt_current')
              AND f.period_start IS NULL
              AND f.tag IN ('LongTermDebtCurrent', 'LongTermDebtNoncurrent', 'LongTermDebt')
            GROUP BY f.period_end, f.accession_no, f.unit  -- 057: parts and total in one unit
        ) p
        WHERE k.company_id = ANY(p_company_ids) AND p.total > 0
          AND (abs(p.parts / p.total - 1000) <= 5 OR abs(p.parts / p.total - 1000000) <= 5000)
        ORDER BY k.company_id, p.period_end, p.filed DESC, p.accession_no DESC
    ),
    -- net debt left out: the latest balance sheet with debt and cash has no figure (040's rule)
    net_debt_gap AS (
        SELECT b.company_id, b.period_end,
               (SELECT max(s.period_end) FROM q s
                WHERE s.company_id = b.company_id AND s.metric = 'short_term_investments'
                  AND s.period_end < b.period_end AND s.value > 0
                  AND s.period_end >= b.period_end - 730
                  AND NOT EXISTS (SELECT 1 FROM q x
                                  WHERE x.company_id = b.company_id AND x.period_end = b.period_end
                                    AND x.metric = 'short_term_investments')) AS investments_end
        FROM (
            SELECT DISTINCT ON (d.company_id) d.company_id, d.period_end
            FROM q d
            JOIN q c ON c.company_id = d.company_id AND c.metric = 'cash' AND c.period_end = d.period_end
            WHERE d.metric = 'long_term_debt'
            ORDER BY d.company_id, d.period_end DESC
        ) b
        WHERE NOT EXISTS (SELECT 1 FROM gold.derived_financials n
                          WHERE n.company_id = b.company_id AND n.span = 'quarter'
                            AND n.metric = 'net_debt' AND n.period_end = b.period_end)
    ),
    -- the same quarter missing year after year: the 10-K tags the year in a way the 10-Qs don't, so
    -- no fourth quarter and no annual total (NVIDIA's capex, FY2017-21). series_gap needs four contiguous
    -- quarters first, which a hole every fourth quarter never has. One finding, the latest.
    repeated_gap AS (
        SELECT DISTINCT ON (company_id, metric) company_id, metric, period_end, period_start, gap,
               count(*) OVER (PARTITION BY company_id, metric) AS years
        FROM runs
        WHERE additive AND gap > 7 AND contiguous_before = 2
        ORDER BY company_id, metric, period_end DESC
    ),
    -- 074: each filing's statement lines against the figure gold holds for the period
    stmt AS MATERIALIZED (
        SELECT * FROM ops.statement_comparisons(p_company_ids)
    ),
    -- 167: a fact whose filing prints another number at its line (silver.fact_disagreements), one
    -- per metric and period: the latest filing's, with how many filings and tags disagree
    misprinted AS (
        SELECT DISTINCT ON (k.company_id, x.metric, x.period_end)
               k.company_id, x.metric, x.period_end, x.period_start, x.tag, x.accession_no,
               x.section_key, x.value / nullif(x.printed, 0) AS factor, d.form, d.filed,
               count(*) OVER (PARTITION BY k.company_id, x.metric, x.period_end) AS facts
        FROM silver.company_ciks k
        JOIN silver.fact_disagreements x ON x.cik = k.cik
        JOIN bronze.filing_documents d ON d.accession_no = x.accession_no
        WHERE k.company_id = ANY(p_company_ids)
        ORDER BY k.company_id, x.metric, x.period_end, d.filed DESC, x.accession_no DESC, x.tag
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
            'weighted_shares_diluted', 'long_term_debt', 'long_term_debt_current')
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
        -- 072: a year whose own figure a later filing changed, or with a restated quarter, is
        -- info and says which (restated: year | quarter); otherwise a warning as before
        SELECT d.company_id, 'annual_sum', d.metric, d.period_end,
               CASE WHEN y.restated IS NOT NULL THEN 'info' ELSE 'warning' END,
               jsonb_build_object('deviation', round((d.value - a.value) / abs(a.value), 4),
                                  'annual_accession', a.accession_no,
                                  'quarter_accessions', d.source_accessions)
               || CASE WHEN y.restated IS NOT NULL
                       THEN jsonb_build_object('restated', y.restated) ELSE '{}'::jsonb END
        FROM gold.derived_financials d
        JOIN annual a ON a.company_id = d.company_id AND a.metric = d.metric
                     AND a.period_end = d.period_end
                     AND abs(a.period_start - d.period_start) <= 7
        CROSS JOIN LATERAL (
            SELECT CASE
                WHEN EXISTS (
                    SELECT 1 FROM (
                        SELECT f.filed,
                               CASE WHEN a.metric IN ('interest_expense', 'long_term_debt',
                                                      'long_term_debt_current')
                                    THEN abs(f.value) ELSE f.value END AS value
                        FROM silver.company_ciks k
                        JOIN silver.financial_facts f ON f.cik = k.cik
                        WHERE k.company_id = a.company_id AND f.tag = a.tag
                          AND f.unit = a.unit AND f.period_start = a.period_start
                          AND f.period_end = a.period_end
                        ORDER BY f.filed, f.accession_no
                        LIMIT 1) o
                    WHERE o.filed < a.filed AND o.value <> 0
                      AND abs(a.value - o.value) > 0.005 * abs(o.value)) THEN 'year'
                WHEN EXISTS (SELECT 1 FROM restated r
                             WHERE r.company_id = d.company_id AND r.metric = d.metric
                               AND r.period_end > d.period_start
                               AND r.period_end <= d.period_end) THEN 'quarter'
                END AS restated
        ) y
        WHERE d.company_id = ANY(p_company_ids) AND d.span = 'ttm' AND d.value_source = 'ttm'
          AND a.value <> 0
          AND abs(d.value - a.value) > 0.01 * abs(a.value)
        UNION ALL
        SELECT company_id, 'series_gap', metric, period_end, 'info',  -- 072: coverage
               jsonb_build_object('missing_from', period_start - gap + 1,
                                  'missing_to', period_start - 1)
        FROM runs
        WHERE additive AND gap > 7 AND contiguous_before = 3
        UNION ALL
        -- 050: a negative stock-based compensation quarter is a benefit as filed (forfeitures
        -- reversing earlier expense, liability awards remeasured), not a slip, unless another filing
        -- reports the same quarter positive: info, rule 'benefit'
        SELECT company_id, 'sign_flip', metric, period_end, s.severity,
               jsonb_build_object('accession_no', accession_no)
               || CASE WHEN s.severity = 'info' THEN '{"rule": "benefit"}'::jsonb
                       ELSE '{}'::jsonb END
        FROM q
        CROSS JOIN LATERAL (
            SELECT CASE WHEN q.metric = 'stock_based_compensation' AND q.value < 0
                             AND NOT EXISTS (
                                 SELECT 1 FROM silver.company_ciks k
                                 JOIN silver.financial_facts f ON f.cik = k.cik
                                 WHERE k.company_id = q.company_id
                                   AND f.metric = 'stock_based_compensation'
                                   AND f.period_end = q.period_end
                                   AND f.period_start = q.period_start AND f.value > 0)
                        THEN 'info' ELSE 'warning' END AS severity
        ) s
        WHERE metric IN ('cost_of_revenue', 'research_and_development', 'sga',
                         'interest_expense', 'capex', 'depreciation_amortization',
                         'stock_based_compensation', 'dividends_paid', 'share_buybacks')
          AND value <> 0 AND prev_value <> 0 AND next_value <> 0
          AND sign(value) <> sign(prev_value) AND sign(prev_value) = sign(next_value)
        UNION ALL
        -- 072: a spike on a line that swings by nature (cash flow subtotals, the change in cash,
        -- buybacks, dividends, income tax) is info, rule 'lumpy'; the test is 041's
        SELECT company_id, 'outlier', metric, period_end,
               CASE WHEN metric IN ('financing_cash_flow', 'investing_cash_flow', 'change_in_cash',
                                    'share_buybacks', 'dividends_paid', 'income_tax')
                    THEN 'info' ELSE 'warning' END,
               jsonb_build_object('vs_neighbours',
                                  round(value / greatest(abs(prev_value), abs(next_value)), 1),
                                  'accession_no', accession_no)
               || CASE WHEN metric IN ('financing_cash_flow', 'investing_cash_flow',
                                       'change_in_cash', 'share_buybacks', 'dividends_paid',
                                       'income_tax')
                       THEN '{"rule": "lumpy"}'::jsonb ELSE '{}'::jsonb END
        FROM q
        WHERE prev_value <> 0 AND next_value <> 0 AND sign(prev_value) = sign(next_value)
          AND greatest(abs(prev_value), abs(next_value))
              <= 2 * least(abs(prev_value), abs(next_value))
          AND abs(value) >= 10 * greatest(abs(prev_value), abs(next_value))
        UNION ALL
        -- restatement: the period's first filing (same tag, unit, dates) said something else
        SELECT company_id, 'restatement', metric, period_end, 'info',  -- 072: from `restated`
               jsonb_build_object('original_accession', original_accession,
                                  'original_filed', original_filed,
                                  'restated_accession', accession_no,
                                  'restated_filed', filed,
                                  'change', change)
        FROM restated
        UNION ALL
        SELECT company_id, 'mistagged_fact', metric, period_end, 'info',  -- 072: kept out
               jsonb_build_object('rule', 'period_after_filing', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'tag', tag, 'periods', periods)
        FROM mistagged
        UNION ALL
        SELECT company_id, 'mistagged_fact', metric, period_end, 'info',  -- 072: kept out
               jsonb_build_object('rule', 'debt_above_liabilities', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'tag', tag)
        FROM debt_scale
        UNION ALL
        SELECT company_id, 'mistagged_fact', 'long_term_debt_current', period_end, 'info',
               jsonb_build_object('rule', 'debt_parts_scale', 'filed', filed, 'form', form,
                                  'accession_no', accession_no, 'factor', factor)
        FROM parts_scale
        UNION ALL
        SELECT company_id, 'series_gap', metric, period_end, 'info',  -- 072: coverage
               jsonb_build_object('missing_from', period_start - gap + 1,
                                  'missing_to', period_start - 1, 'repeated', years)
        FROM repeated_gap
        WHERE years >= 3
        UNION ALL
        SELECT company_id, 'net_debt_gap', 'net_debt', period_end, 'info',  -- 072: coverage
               jsonb_build_object('investments_end', investments_end)
        FROM net_debt_gap
        UNION ALL
        -- 072: the tags, the factor (the other tag's figure over gold's) and the filing; never the
        -- values (014's rule for detail)
        SELECT company_id, 'tag_disagreement', metric, period_end, 'warning',
               jsonb_build_object('rule', rule, 'tag', tag, 'other_tag', other_tag,
                                  'factor', round(factor, 6)::float8, 'accession_no', accession_no,
                                  'form', form, 'filed', filed, 'period_start', period_start)
        FROM disagree
        UNION ALL
        -- 074: the filing's own statement prints, unambiguously, another figure than the one gold
        -- holds and the filing states (ops.statement_comparisons: own, clear). The line, the
        -- factor (gold's over the printed), the filing; never the values (014's rule).
        SELECT company_id, 'statement_mismatch',
               CASE WHEN line = 'liabilities_and_equity' THEN 'total_assets' ELSE line END,
               gold_end, 'warning',
               jsonb_build_object('rule', rule, 'line', line,
                                  'factor', round(gold / printed, 6)::float8,
                                  'accession_no', accession_no, 'form', form, 'filed', filed)
        FROM stmt
        WHERE NOT agree AND own AND clear AND printed <> 0
        UNION ALL
        -- 167: the filing's inline XBRL prints, at the fact's own line, a number the fact doesn't
        -- equal (to the printed precision). The tag, the factor (the fact over the printed), the
        -- filing and the section; never the values (014's rule).
        SELECT company_id, 'printed_mismatch', metric, period_end, 'warning',
               jsonb_build_object('tag', tag, 'factor', round(factor, 6)::float8,
                                  'accession_no', accession_no, 'form', form, 'filed', filed,
                                  'section_key', section_key, 'period_start', period_start,
                                  'facts', facts)
        FROM misprinted
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

    -- 072: and the gold figures checked (quarters, TTM and computed), for findings per 1,000
    -- 074: and the confidence mark's inputs (gold.company_confidence): statement lines compared
    -- and agreeing, the core figures at the latest period, what kind of entity it is
    INSERT INTO ops.quality_companies (company_id, run_id, checked_at, errors, warnings, figures,
                                       statements_checked, statements_agreed,
                                       statements_differ_at, core_figures, entity)
    SELECT c.company_id, run, now(),
           count(f.*) FILTER (WHERE f.severity = 'error'),
           count(f.*) FILTER (WHERE f.severity = 'warning'),
           (SELECT count(*) FROM gold.quarterly_financials g WHERE g.company_id = c.company_id)
           + (SELECT count(*) FROM gold.derived_financials g WHERE g.company_id = c.company_id),
           s.checked, s.agreed,
           max(f.period_end) FILTER (WHERE f.check_name = 'statement_mismatch'),
           ops.core_figures(c.company_id),
           ops.entity_kind(c.company_id, c.sic)
    FROM silver.companies c
    CROSS JOIN LATERAL (
        SELECT count(*) FILTER (WHERE x.agree OR (x.own AND x.clear)) AS checked,
               count(*) FILTER (WHERE x.agree) AS agreed
        FROM ops.statement_comparisons(ARRAY[c.company_id]) x
    ) s
    LEFT JOIN ops.quality_findings f ON f.company_id = c.company_id
    WHERE c.company_id = ANY(p_company_ids)
    GROUP BY c.company_id, c.sic, s.checked, s.agreed
    ON CONFLICT (company_id) DO UPDATE SET run_id = EXCLUDED.run_id,
        checked_at = EXCLUDED.checked_at, errors = EXCLUDED.errors,
        warnings = EXCLUDED.warnings, figures = EXCLUDED.figures,
        statements_checked = EXCLUDED.statements_checked,
        statements_agreed = EXCLUDED.statements_agreed,
        statements_differ_at = EXCLUDED.statements_differ_at,
        core_figures = EXCLUDED.core_figures, entity = EXCLUDED.entity;

    RETURN n;
END
$$;

-- 167: the hourly catch-up re-checks every company (071).
CREATE OR REPLACE FUNCTION ops.check_financials_version() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS 'SELECT 167';

-- 165's body with the edit marked "167" (header, 4).
CREATE OR REPLACE FUNCTION ops.company_backlog(p_kind text)
RETURNS TABLE (company_id text, built_at timestamptz, failed boolean)
LANGUAGE sql STABLE AS $$
    SELECT c.company_id, b.built_at,
           b.failed_version IS NOT DISTINCT FROM ops.company_rule_version(p_kind)
    FROM silver.companies c
    JOIN ops.catchup k ON k.kind = p_kind
    LEFT JOIN ops.company_builds b ON b.kind = p_kind AND b.company_id = c.company_id
    WHERE (coalesce(b.version, k.baseline) < ops.company_rule_version(p_kind)
           -- 074
           OR (p_kind = 'check_financials' AND EXISTS (
                  SELECT 1 FROM silver.company_ciks x
                  JOIN silver.statement_reads r ON r.cik = x.cik
                  WHERE x.company_id = c.company_id
                    AND r.read_at > coalesce(b.built_at, '-infinity'::timestamptz)))
           -- 165: a results release read since the company's gold was built
           OR (p_kind = 'gold' AND EXISTS (
                  SELECT 1 FROM silver.company_ciks x
                  JOIN silver.release_reads r ON r.cik = x.cik
                  WHERE x.company_id = c.company_id
                    AND r.read_at > coalesce(b.built_at, '-infinity'::timestamptz)))
           -- 167: a filing of the company gated (silver.fact_gates) since its check
           OR (p_kind = 'check_financials' AND EXISTS (
                  SELECT 1 FROM silver.company_ciks x
                  JOIN silver.fact_gates g ON g.cik = x.cik
                  WHERE x.company_id = c.company_id
                    AND g.gated_at > coalesce(b.built_at, '-infinity'::timestamptz)))
           -- 086
           OR (p_kind = 'known' AND NOT EXISTS (
                  SELECT 1 FROM gold.known_companies kc WHERE kc.company_id = c.company_id)))
      AND (p_kind NOT IN ('check_financials', 'known') OR c.company_id NOT IN (  -- 086
              SELECT g.company_id FROM ops.company_backlog('gold') g WHERE NOT g.failed))
$$;

-- 074's view, with ('printed_mismatch', 'warning') in the expected list (167).
CREATE OR REPLACE VIEW ops.quality_tiers AS
WITH found AS (
    SELECT f.check_name, f.severity, count(*) AS findings,
           count(DISTINCT f.company_id) AS companies,
           count(*) FILTER (WHERE q.figures IS NOT NULL) AS counted
    FROM ops.quality_findings f
    LEFT JOIN ops.quality_companies q ON q.company_id = f.company_id
    GROUP BY f.check_name, f.severity
),
checks AS (
    SELECT check_name, severity
    FROM (VALUES ('impossible_value', 'error'), ('overlapping_quarters', 'error'),
                 ('tag_disagreement', 'warning'), ('statement_mismatch', 'warning'),
                 ('printed_mismatch', 'warning'),  -- 167
                 ('outlier', 'warning'), ('sign_flip', 'warning'),
                 ('annual_sum', 'warning'), ('calendar_collision', 'warning'),
                 ('segment_sum_business_segment', 'warning'), ('segment_sum_geography', 'warning'),
                 ('segment_sum_product_service', 'warning'),
                 ('mistagged_fact', 'info'), ('series_gap', 'info'), ('net_debt_gap', 'info'),
                 ('outlier', 'info'), ('sign_flip', 'info'), ('annual_sum', 'info'),
                 ('restatement', 'info')) v(check_name, severity)
    UNION
    SELECT check_name, severity FROM found
),
figures AS (
    SELECT sum(figures) AS figures FROM ops.quality_companies
)
SELECT c.check_name, c.severity,
       coalesce(f.findings, 0) AS findings,
       coalesce(f.companies, 0) AS companies,
       (SELECT count(*) FROM ops.quality_companies) AS companies_checked,
       (SELECT max(checked_at) FROM ops.quality_runs) AS last_checked_at,
       ops.quality_tier(c.check_name, c.severity) AS tier,
       g.figures,
       round(1000.0 * coalesce(f.counted, 0) / nullif(g.figures, 0), 3) AS per_1000
FROM checks c
LEFT JOIN found f ON f.check_name = c.check_name AND f.severity = c.severity
CROSS JOIN figures g;
