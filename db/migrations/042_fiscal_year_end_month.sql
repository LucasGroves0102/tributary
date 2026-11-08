-- The month a fiscal year ends in, for people. silver.companies.fiscal_year_end is the SEC's MMDD for the
-- company's last 10-K, and a 52/53-week year ends up to a week into the next month: J&J's "0103", L3Harris'
-- "0102", Leidos' "0101", Target's "0201", Disney's "1003". The screener read those as the month after.
-- A day of 1-7 belongs to the month before; anything later is the month it says, so NVIDIA's "0131" and
-- Home Depot's "0131" stay January. Checked on the 640-company sample against each company's own 10-K
-- year ends since 2019: the rule names the month of all 47 first-week filers, the raw MMDD of none. The
-- other 539 keep their month and 534 are right; the five aren't this rule's (a changed fiscal year, Sun
-- Communities' "0513" from the SEC).
--
-- Display and filter only. fiscal_year_end itself and gold.fiscal_year_end, which the fiscal period labels
-- come from, don't change: FY2026 Q4 stays FY2026 Q4. The screener calls this; the web's twin is fyeMonth
-- (services/web/src/companies/screen.ts), change both.
--
-- Locks on hermes: a new function, nothing on any table. Nothing to rebuild.

SET LOCAL lock_timeout = '30s';

CREATE FUNCTION silver.fiscal_year_end_month(p_mmdd text) RETURNS integer
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN right(p_mmdd, 2)::int <= 7 THEN (left(p_mmdd, 2)::int + 10) % 12 + 1
                ELSE left(p_mmdd, 2)::int END
$$;
