-- The proof layer at silver, part 3: readers get each figure's printed line, and trust becomes a
-- number.
--
-- 1. gold.printed_lines(accessions, tags, units, starts, ends): for each key (a gold row's filing,
--    its source tag's local name, unit and period), the gate's result for that fact (167's
--    silver.fact_gates: status and, when the filing prints it in a section, the section and the
--    printed figure's document offsets). Read at request time under the caller's RLS (the filings
--    tier), one call per request, every accession's lines unnested once. Gold's rows and picks are
--    unchanged: a figure inherits its line through its source fact, nothing is stored on gold (no
--    rules_version bump). Measured on the NVIDIA 10-Q fixture: 513 printed facts, ~100 gated facts
--    a filing; a company page asks for ~40 filings' lines.
-- 2. Trust as a number: ops.refresh_trust() counts, over gold.quarterly_financials (the company
--    pages' quarters), the figures, those filed since inline XBRL (filed from 2019-06-15, the
--    phase-in's first deadline), those whose source fact links to a printed line, those of them
--    that agree, and the figures sitting on a disagreeing fact (the owner's next decision: "gold
--    prefers gated facts"); and the gate's own fact counts. One ops.trust_runs row per refresh;
--    the gate catch-up refreshes it once a day (filing_parser/catchup.py). ops.public_trust() is
--    the status page's: the latest run's counts only (028's DEFINER pattern), never a company.
--
-- Access: gold.printed_lines is security invoker (silver.fact_gates' policy applies); the trust
-- tables are ops', read by ops_reader and through ops.public_trust (EXECUTE query_api).
--
-- Locks on hermes: new functions and a table; nothing existing is replaced.

SET LOCAL lock_timeout = '3s';

-- 1. Lines ---------------------------------------------------------------------------------------------

CREATE FUNCTION gold.printed_lines(p_accessions text[], p_tags text[], p_units text[],
                                   p_starts date[], p_ends date[])
RETURNS TABLE (n integer, status text, section_key text, char_start integer, char_end integer)
LANGUAGE sql STABLE AS $$
    WITH keys AS MATERIALIZED (
        SELECT k.n::integer AS n, k.accession_no, k.tag, k.unit,
               to_char(k.period_start, 'YYYY-MM-DD') AS ps,
               to_char(k.period_end, 'YYYY-MM-DD') AS pe
        FROM unnest(p_accessions, p_tags, p_units, p_starts, p_ends) WITH ORDINALITY
             AS k(accession_no, tag, unit, period_start, period_end, n)
    ), lines AS MATERIALIZED (
        SELECT g.accession_no, l.j->>'t' AS tag, l.j->>'u' AS unit, l.j->>'s' AS ps,
               l.j->>'e' AS pe, l.j->>'st' AS status, l.j->>'k' AS section_key,
               (l.j->>'a')::integer AS char_start, (l.j->>'b')::integer AS char_end,
               l.j->>'c' AS cik
        FROM silver.fact_gates g
        CROSS JOIN LATERAL jsonb_array_elements(g.lines) l(j)
        WHERE g.accession_no = ANY(p_accessions)
    )
    SELECT DISTINCT ON (k.n) k.n, l.status, l.section_key, l.char_start, l.char_end
    FROM keys k
    JOIN lines l ON l.accession_no = k.accession_no AND l.tag = k.tag AND l.unit = k.unit
                AND l.pe = k.pe AND l.ps IS NOT DISTINCT FROM k.ps
    ORDER BY k.n, l.section_key IS NULL, l.cik
$$;

-- 2. Trust as a number ---------------------------------------------------------------------------------

CREATE TABLE ops.trust_runs (
    run_at         timestamptz PRIMARY KEY DEFAULT clock_timestamp(),
    figures        bigint  NOT NULL,  -- gold.quarterly_financials rows
    since_ixbrl    bigint  NOT NULL,  -- of them, cited from a filing filed from 2019-06-15
    reported       bigint  NOT NULL,  -- of those, reported (one fact; derived ones have none)
    linked         bigint  NOT NULL,  -- of those, whose fact links to a printed line
    agreeing       bigint  NOT NULL,  -- of those, whose printed line agrees
    disagreeing    bigint  NOT NULL,  -- figures (any) on a fact the filing prints otherwise
    filings_gated  bigint  NOT NULL,  -- silver.fact_gates rows
    facts_gated    bigint  NOT NULL,  -- and their facts: agree, disagree, not located, n/a
    facts_agree    bigint  NOT NULL,
    facts_disagree bigint  NOT NULL,
    facts_not_located bigint NOT NULL,
    facts_not_applicable bigint NOT NULL,
    seconds        real
);
GRANT SELECT ON ops.trust_runs TO ops_reader;

-- Count (header, 2) and record one run. Returns the run's time.
CREATE FUNCTION ops.refresh_trust() RETURNS timestamptz
LANGUAGE plpgsql AS $$
DECLARE
    started timestamptz := clock_timestamp();
BEGIN
    INSERT INTO ops.trust_runs (run_at, figures, since_ixbrl, reported, linked, agreeing,
                                disagreeing, filings_gated, facts_gated, facts_agree,
                                facts_disagree, facts_not_located, facts_not_applicable, seconds)
    WITH lines AS MATERIALIZED (  -- one entry per (filing, tag, unit, period): a line first
        SELECT DISTINCT ON (g.accession_no, l.j->>'t', l.j->>'u', l.j->>'s', l.j->>'e')
               g.accession_no, l.j->>'t' AS tag, l.j->>'u' AS unit,
               (l.j->>'s')::date AS period_start, (l.j->>'e')::date AS period_end,
               l.j->>'st' AS status, l.j ? 'k' AS line
        FROM silver.fact_gates g
        CROSS JOIN LATERAL jsonb_array_elements(g.lines) l(j)
        ORDER BY g.accession_no, l.j->>'t', l.j->>'u', l.j->>'s', l.j->>'e', l.j ? 'k' DESC
    ), shown AS (
        SELECT q.filed >= DATE '2019-06-15' AS recent, q.value_source = 'reported' AS reported,
               l.status, coalesce(l.line, false) AS line
        FROM gold.quarterly_financials q
        LEFT JOIN lines l ON l.accession_no = q.accession_no
                         AND l.tag = regexp_replace(q.source_tag, '^.*:', '')
                         AND l.unit = q.unit AND l.period_end = q.period_end
                         AND l.period_start IS NOT DISTINCT FROM q.period_start
                         AND q.value_source = 'reported'
    )
    SELECT started,
           (SELECT count(*) FROM shown),
           (SELECT count(*) FROM shown WHERE recent),
           (SELECT count(*) FROM shown WHERE recent AND reported),
           (SELECT count(*) FROM shown WHERE recent AND reported AND line),
           (SELECT count(*) FROM shown WHERE recent AND reported AND line AND status = 'a'),
           (SELECT count(*) FROM shown WHERE status = 'd'),
           (SELECT count(*) FROM silver.fact_gates),
           (SELECT coalesce(sum(facts), 0) FROM silver.fact_gates),
           (SELECT coalesce(sum(agrees), 0) FROM silver.fact_gates),
           (SELECT coalesce(sum(disagrees), 0) FROM silver.fact_gates),
           (SELECT coalesce(sum(not_located), 0) FROM silver.fact_gates),
           (SELECT coalesce(sum(not_applicable), 0) FROM silver.fact_gates),
           extract(epoch FROM clock_timestamp() - started)::real;
    RETURN started;
END
$$;

-- The status page's trust numbers: the latest run's counts, nothing per company (028's pattern).
CREATE FUNCTION ops.public_trust()
RETURNS TABLE (run_at timestamptz, figures bigint, since_ixbrl bigint, reported bigint,
               linked bigint, agreeing bigint, disagreeing bigint, filings_gated bigint,
               facts_gated bigint, facts_agree bigint, facts_disagree bigint,
               facts_not_located bigint, facts_not_applicable bigint)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    SELECT t.run_at, t.figures, t.since_ixbrl, t.reported, t.linked, t.agreeing, t.disagreeing,
           t.filings_gated, t.facts_gated, t.facts_agree, t.facts_disagree, t.facts_not_located,
           t.facts_not_applicable
    FROM ops.trust_runs t
    ORDER BY t.run_at DESC
    LIMIT 1
$$;
REVOKE ALL ON FUNCTION ops.public_trust() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.public_trust() TO query_api;
