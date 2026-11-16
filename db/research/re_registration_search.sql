-- Re-registrations: every successor-issuer filing (8-K12B, 8-K12G3) of a CIK mapped here, or of
-- one listed in :extra (a predecessor found in an earlier pass: Broadcom Ltd for Avago), with each
-- candidate predecessor: a CIK whose current or former name (legal suffixes aside), business
-- address or phone matches, that filed a 10-K/10-Q/20-F/40-F within 400 days before the move.
-- Read-only (hermes: about a minute). How re_registrations.csv was made (2026-11-16):
--   psql -XA -F, -v extra='{}' -f db/research/re_registration_search.sql > pairs.csv
-- A pair is linked only when the predecessor stops reporting at the move (nothing after +120 days;
-- last report within 200 days before it, 400 for an annual-only filer; a filing within 30 days of
-- it), the successor didn't report 200+ days before it, the predecessor is no company here and no
-- SPAC, and either the successor's own figures before the move equal the predecessor's (75%+ of 3+
-- shared) or, with none shared (before XBRL), the names match and the address or phone too. Two
-- passing candidates: the one whose name matches, else neither. The rest, with why, went to
-- re_registrations_uncertain.csv.
SET statement_timeout = '400s';
WITH succ AS (
  SELECT DISTINCT f.cik AS s_cik, k.company_id, f.filing_date AS d, f.form
  FROM bronze.submission_filings f LEFT JOIN silver.company_ciks k ON k.cik = f.cik
  WHERE f.form IN ('8-K12B','8-K12G3') AND (k.cik IS NOT NULL OR f.cik = ANY(:'extra'::text[]))
), stems AS (
  SELECT b.cik, b.payload->>'name' AS name, n.nm,
    (SELECT array_to_string(array_agg(t ORDER BY i), ' ')
     FROM unnest(regexp_split_to_array(trim(regexp_replace(regexp_replace(regexp_replace(upper(n.nm), '&', ' AND ', 'g'), '/[^/]*/?', ' ', 'g'), '[^A-Z0-9]+', ' ', 'g')), ' ')) WITH ORDINALITY u(t, i)
     WHERE t <> '' AND t NOT IN ('INC','INCORPORATED','CORP','CORPORATION','CO','COMPANY','LTD','LIMITED','PLC','NV','N','V','SA','S','A','AG','SE','LLC','L','P','LP','THE','HOLDINGS','HOLDING','GROUP','DE','NEW','PTE','OF')) AS stem,
    upper(b.payload->'addresses'->'business'->>'street1') || '|' || coalesce(b.payload->'addresses'->'business'->>'zipCode','') AS addr,
    regexp_replace(coalesce(b.payload->>'phone',''), '[^0-9]', '', 'g') AS phone
  FROM bronze.submissions b
  CROSS JOIN LATERAL (SELECT b.payload->>'name' AS nm UNION SELECT x->>'name' FROM jsonb_array_elements(coalesce(b.payload->'formerNames','[]')) x) n
), skeys AS (
  SELECT DISTINCT st.cik AS s_cik, st.stem, st.addr, st.phone FROM stems st WHERE st.cik IN (SELECT s_cik FROM succ)
), pairs AS (
  SELECT k.s_cik, p.cik AS p_cik,
    bool_or(p.stem = k.stem AND length(k.stem) >= 3) AS by_name,
    bool_or(p.addr = k.addr AND length(k.addr) > 6) AS by_addr,
    bool_or(p.phone = k.phone AND length(k.phone) >= 7) AS by_phone,
    max(p.name) AS p_name
  FROM skeys k JOIN stems p ON p.cik <> k.s_cik
    AND ((p.stem = k.stem AND length(k.stem) >= 3) OR (p.addr = k.addr AND length(k.addr) > 6) OR (p.phone = k.phone AND length(k.phone) >= 7))
  GROUP BY 1, 2
),
candidates AS (
SELECT s.s_cik, s.company_id, coalesce(c.name, (SELECT bb.payload->>'name' FROM bronze.submissions bb WHERE bb.cik = s.s_cik)) AS s_name, c.tickers[1] AS ticker, array_to_string(c.exchanges, ' ') AS exch,
  s.d, s.form, p.p_cik, p.p_name, p.by_name, p.by_addr, p.by_phone, t.p_first, t.p_last, t.p_after, t.p_n, a.p_near,
  (SELECT min(x.filing_date) FROM bronze.submission_filings x WHERE x.cik = s.s_cik AND x.form IN ('10-K','10-Q','20-F','40-F')) AS s_first,
  (SELECT kk.company_id FROM silver.company_ciks kk WHERE kk.cik = p.p_cik) AS p_company,
  (SELECT count(*) FROM silver.company_ciks kk WHERE kk.company_id = s.company_id) AS s_ciks,
  (SELECT bb.payload->>'sic' FROM bronze.submissions bb WHERE bb.cik = p.p_cik) AS p_sic,
  (SELECT max(x.filing_date) FROM bronze.submission_filings x WHERE x.cik = p.p_cik) AS p_last_any,
  (SELECT string_agg(DISTINCT x.form, ' ') FROM bronze.submission_filings x WHERE x.cik = p.p_cik AND x.form IN ('10-K','10-Q','20-F','40-F') AND x.filing_date >= s.d - 730) AS p_forms,
  (SELECT min(q.period_end) FROM gold.quarterly_financials q WHERE q.company_id = s.company_id AND q.metric = 'revenue') AS first_q
FROM succ s
LEFT JOIN silver.companies c ON c.company_id = s.company_id
JOIN pairs p ON p.s_cik = s.s_cik
CROSS JOIN LATERAL (
  SELECT min(x.filing_date) AS p_first, max(x.filing_date) AS p_last,
         count(*) FILTER (WHERE x.filing_date > s.d + 120) AS p_after, count(*) AS p_n
  FROM bronze.submission_filings x WHERE x.cik = p.p_cik AND x.form IN ('10-K','10-Q','20-F','40-F')) t
CROSS JOIN LATERAL (
  SELECT count(*) AS p_near FROM bronze.submission_filings x
  WHERE x.cik = p.p_cik AND x.filing_date BETWEEN s.d - 30 AND s.d + 30) a
WHERE t.p_n > 0 AND t.p_last >= s.d - 400
)
-- the figure agreement: of the successor's own facts for periods ending before the move, how many
-- the candidate also reports (same metric, tag, unit, period) and how many with a value in common
SELECT c.*, m.both_reported, m.equal
FROM candidates c
CROSS JOIN LATERAL (
  SELECT count(*) AS both_reported, count(*) FILTER (WHERE a.vals && b.vals) AS equal
  FROM (SELECT metric, tag, unit, period_start, period_end, array_agg(DISTINCT value) AS vals
        FROM silver.financial_facts
        WHERE cik = c.s_cik AND period_end < c.d AND metric IN ('revenue', 'net_income',
              'total_assets', 'operating_cash_flow', 'stockholders_equity', 'cash')
        GROUP BY 1, 2, 3, 4, 5) a
  JOIN (SELECT metric, tag, unit, period_start, period_end, array_agg(DISTINCT value) AS vals
        FROM silver.financial_facts
        WHERE cik = c.p_cik AND period_end < c.d AND metric IN ('revenue', 'net_income',
              'total_assets', 'operating_cash_flow', 'stockholders_equity', 'cash')
        GROUP BY 1, 2, 3, 4, 5) b USING (metric, tag, unit, period_end)
  WHERE a.period_start IS NOT DISTINCT FROM b.period_start) m
ORDER BY c.d, c.s_cik, c.p_cik;
