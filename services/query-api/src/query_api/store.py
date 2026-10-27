"""Layer 2: every query runs as the query_api role with the caller's user_id set, so Postgres
row-level security (db/migrations/007_query_api_rls.sql) filters rows even if layer 1 were
skipped."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from query_api.formula import Compiled
from query_api.screener import Compiled as Screen

# Every query: SET LOCAL ROLE doesn't apply the role's own settings (those apply at login), so
# a timeout on query_api would never reach these sessions. Search sets its own, longer one.
QUERY_TIMEOUT = "5s"
SEARCH_TIMEOUT = "8s"
# Data notes: a 10-K/10-Q filed in the last BEHIND_DAYS whose XBRL isn't in companyfacts yet.
BEHIND_DAYS = 120
HEADLINE = ("StartSel=«, StopSel=», MaxWords=35, MinWords=15, MaxFragments=2,"
            " FragmentDelimiter=\" … \"")


class SearchTooBroad(Exception):
    """Ranking every match took longer than SEARCH_TIMEOUT."""


class Pool(Protocol):
    def connection(self) -> Any: ...  # context manager yielding a psycopg.Connection


class Store:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @contextmanager
    def _session(self, user_id: str) -> Iterator[psycopg.Cursor]:
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SET LOCAL ROLE query_api")
            cur.execute(f"SET LOCAL statement_timeout = '{QUERY_TIMEOUT}'")
            cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            try:
                yield cur
            finally:  # don't leak the role or user into an enclosing transaction
                # (a failed transaction is rolled back, which undoes both anyway)
                if conn.info.transaction_status != TransactionStatus.INERROR:
                    cur.execute("RESET ROLE")
                    cur.execute("SELECT set_config('app.user_id', '', true)")

    def metric_names(self) -> dict[str, list[str]]:
        """{metrics: silver.metrics, derived_metrics: gold.derived_metrics}, by name."""
        with self._session("") as cur:
            cur.execute(
                "SELECT ARRAY(SELECT metric FROM silver.metrics ORDER BY 1) AS metrics,"
                "       ARRAY(SELECT metric FROM gold.derived_metrics ORDER BY 1)"
                "           AS derived_metrics")
            return dict(cur.fetchone() or {})

    def search(self, user_id: str, q: str, limit: int = 5,
               only: list[str] | None = None) -> list[dict]:
        """Best matches for a name, ticker or CIK. only: just these companies (the caller's
        readable ones, for a demo that lists nothing else); None: every company."""
        with self._session(user_id) as cur:
            cur.execute(
                "SELECT r.company_id, r.name, r.matched_on, r.matched_alias, r.is_current,"
                "       c.sic, c.sic_description AS industry, c.exchanges"
                " FROM silver.resolve_company(%(q)s) WITH ORDINALITY r"
                " JOIN silver.companies c USING (company_id)"
                " WHERE %(only)s::text[] IS NULL OR r.company_id = ANY(%(only)s::text[])"
                " ORDER BY r.ordinality LIMIT %(limit)s", {"q": q, "only": only, "limit": limit})
            return cur.fetchall()

    def profiles(self, user_id: str, company_ids: list[str]) -> dict[str, dict]:
        """company_id → name, sic, industry, exchanges."""
        with self._session(user_id) as cur:
            cur.execute("SELECT company_id, name, sic, sic_description AS industry, exchanges"
                        " FROM silver.companies WHERE company_id = ANY(%s)", (company_ids,))
            return {r.pop("company_id"): r for r in cur.fetchall()}

    def resolve_tickers(self, user_id: str, tickers: list[str]) -> dict[str, str]:
        """ticker → company_id, by silver.resolve_company's best ticker match (public identity).
        Tickers that match nothing are absent."""
        with self._session(user_id) as cur:
            cur.execute(
                "SELECT t.ticker,"
                "       (SELECT r.company_id FROM silver.resolve_company(t.ticker)"
                "        WITH ORDINALITY r WHERE r.matched_on = 'ticker'"
                "        ORDER BY r.ordinality LIMIT 1) AS company_id"
                " FROM unnest(%s::text[]) t(ticker)", (tickers,))
            return {r["ticker"]: r["company_id"] for r in cur.fetchall() if r["company_id"]}

    def tickers(self, user_id: str, company_ids: list[str]) -> dict[str, list[str]]:
        """company_id → its current tickers."""
        with self._session(user_id) as cur:
            cur.execute("SELECT company_id, array_agg(DISTINCT upper(alias) ORDER BY upper(alias))"
                        "  AS tickers"
                        " FROM silver.company_aliases"
                        " WHERE alias_type = 'ticker' AND valid_to IS NULL"
                        "   AND company_id = ANY(%s)"
                        " GROUP BY company_id", (company_ids,))
            return {r["company_id"]: r["tickers"] for r in cur.fetchall()}

    def formula(self, user_id: str, compiled: Compiled) -> list[dict]:
        """Points of a compiled formula (query_api.formula.compile)."""
        with self._session(user_id) as cur:
            cur.execute(compiled.sql, compiled.params)  # pyright: ignore[reportArgumentType]
            return cur.fetchall()

    def screen(self, user_id: str, compiled: Screen) -> list[dict]:
        """Rows of a compiled screen (query_api.screener)."""
        with self._session(user_id) as cur:
            cur.execute(compiled.sql, compiled.params)  # pyright: ignore[reportArgumentType]
            return cur.fetchall()

    def expected_filings(self, user_id: str, company_ids: list[str], as_of: date,
                         periods: int) -> list[dict]:
        """Estimated next 10-Q/10-K filings (gold.expected_filings; public filing index)."""
        with self._session(user_id) as cur:
            cur.execute("SELECT * FROM gold.expected_filings(%s, %s, %s)",
                        (company_ids, as_of, periods))
            return cur.fetchall()

    def last_reports(self, user_id: str, company_ids: list[str]) -> dict[str, dict]:
        """company_id → its latest 10-K/10-Q (or amendment) from the public filing index."""
        with self._session(user_id) as cur:
            cur.execute("SELECT DISTINCT ON (company_id) company_id, accession_no, form,"
                        "       filing_date, period_end"
                        " FROM silver.company_reports(%s)"
                        " ORDER BY company_id, filing_date DESC, accession_no DESC",
                        (company_ids,))
            return {r.pop("company_id"): r for r in cur.fetchall()}

    def filing(self, user_id: str, accession_no: str) -> dict | None:
        """A filing's company, form, dates and period from the public filing index."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT f.company_id, f.cik, f.form, f.filing_date,
                       coalesce(f.report_date, d.period_of_report) AS period_end
                FROM silver.filings f
                LEFT JOIN bronze.filing_documents d ON d.accession_no = f.accession_no
                WHERE f.accession_no = %s AND f.company_id IS NOT NULL
                ORDER BY f.report_date IS NULL, f.cik
                LIMIT 1
                """, (accession_no,))
            return cur.fetchone()

    def filings_meta(self, user_id: str, accession_nos: list[str]) -> dict[str, dict]:
        """accession_no → form, filed, period_end from the public filing index."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (accession_no) accession_no, form, filing_date AS filed,
                       report_date AS period_end
                FROM silver.filings
                WHERE accession_no = ANY(%s)
                ORDER BY accession_no, report_date IS NULL, cik
                """, (accession_nos,))
            return {r.pop("accession_no"): r for r in cur.fetchall()}

    def recap(self, user_id: str, company_id: str, accession_no: str, period_end: date | None,
              metrics: list[str]) -> list[dict]:
        """gold.filing_recap rows. Without a period of report, the latest period this filing
        is cited for (under RLS like the rest)."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT * FROM gold.filing_recap(
                    %(company)s,
                    coalesce(%(period_end)s::date,
                             (SELECT max(q.period_end) FROM gold.quarterly_financials q
                              WHERE q.company_id = %(company)s
                                AND q.accession_no = %(accession)s)),
                    %(metrics)s)
                """, {"company": company_id, "period_end": period_end,
                      "accession": accession_no, "metrics": metrics})
            return cur.fetchall()

    def filing_flags(self, user_id: str, accession_no: str) -> list[dict]:
        """gold.filing_flags rows (RLS: filings tier)."""
        with self._session(user_id) as cur:
            cur.execute("SELECT * FROM gold.filing_flags(%s)", (accession_no,))
            return cur.fetchall()

    def peers(self, user_id: str, company_id: str, granted: list[str], revenue: Decimal | None,
              limit: int, granted_only: bool = False) -> list[dict]:
        """Ranked peer candidates (gold.peer_candidates): SIC level, then the same revenue size
        band as `revenue` (only companies in `granted` have one; RLS applies too), then the
        same filer category, then revenue closeness, then name. granted_only: only candidates
        in `granted`."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT p.company_id, p.sic_level, p.same_filer_category,
                       gold.revenue_band(r.value) AS revenue_band
                FROM gold.peer_candidates(%(company)s) p
                JOIN silver.companies c ON c.company_id = p.company_id
                LEFT JOIN gold.latest_financials r
                       ON r.company_id = p.company_id AND r.company_id = ANY(%(granted)s)
                      AND r.span = 'ttm' AND r.metric = 'revenue'
                WHERE NOT %(granted_only)s OR p.company_id = ANY(%(granted)s)
                ORDER BY p.sic_level DESC,
                         coalesce(gold.revenue_band(r.value)
                                  = gold.revenue_band(%(revenue)s::numeric), false) DESC,
                         p.same_filer_category DESC,
                         CASE WHEN r.value > 0 AND %(revenue)s::numeric > 0
                              THEN abs(ln(r.value / %(revenue)s::numeric)) END NULLS LAST,
                         c.name, p.company_id
                LIMIT %(limit)s
                """, {"company": company_id, "granted": granted, "revenue": revenue,
                      "limit": limit, "granted_only": granted_only})
            return cur.fetchall()

    def financials(self, user_id: str, company_id: str, metrics: list[str], quarters: int,
                   view: str = "quarter", basis: str = "fiscal") -> list[dict]:
        """view: quarter | ttm. basis: fiscal (every fiscal period) | calendar (one row per
        calendar quarter)."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT metric, period_start, period_end, fiscal_year, fiscal_quarter,
                       calendar_year, calendar_quarter, value, unit, value_source,
                       source_tag, inputs, accession_no, form, filed, source_accessions,
                       ingested_at,
                       (SELECT f.cik FROM silver.financial_facts f
                        WHERE f.accession_no = q.accession_no LIMIT 1) AS filer_cik
                FROM (
                    SELECT *, row_number() OVER (PARTITION BY metric ORDER BY period_end DESC)
                    FROM gold.financials
                    WHERE company_id = %s AND span = %s AND metric = ANY(%s)
                      AND (%s = 'fiscal' OR calendar_primary)
                ) q
                WHERE row_number <= %s
                ORDER BY metric, period_end DESC
                """, (company_id, view, metrics, basis, quarters))
            return cur.fetchall()

    def segment_metric_names(self) -> list[str]:
        """silver.segment_metrics, by name."""
        with self._session("") as cur:
            cur.execute("SELECT ARRAY(SELECT metric FROM silver.segment_metrics ORDER BY 1)"
                        " AS metrics")
            row = cur.fetchone()
            return list(row["metrics"]) if row else []

    def segments(self, user_id: str, company_id: str, axis: str | None, metric: str | None,
                 periods: int, view: str = "quarter", basis: str = "fiscal") -> list[dict]:
        """gold.segments rows of the latest `periods` period ends (calendar basis: calendar
        quarters, one row each) per axis and metric. view: quarter | ttm."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT axis, member, member_label, member_aliases, metric, period_start,
                       period_end, fiscal_year, fiscal_quarter, calendar_year,
                       calendar_quarter, value, unit, value_source, source_tag, axis_qname,
                       accession_no, filer_cik, form, filed, source_accessions, ingested_at
                FROM (
                    SELECT *, dense_rank() OVER (
                        PARTITION BY axis, metric
                        ORDER BY CASE WHEN %(basis)s = 'calendar'
                                      THEN make_date(calendar_year, calendar_quarter * 3 - 2, 1)
                                      ELSE period_end END DESC) AS recency
                    FROM gold.segments
                    WHERE company_id = %(company)s AND span = %(view)s
                      AND (%(axis)s::text IS NULL OR axis = %(axis)s)
                      AND (%(metric)s::text IS NULL OR metric = %(metric)s)
                      AND (%(basis)s = 'fiscal' OR calendar_primary)
                ) s
                WHERE recency <= %(periods)s
                ORDER BY axis, metric, member_label, member, period_end DESC
                """, {"company": company_id, "view": view, "axis": axis, "metric": metric,
                      "basis": basis, "periods": periods})
            return cur.fetchall()

    def filings(self, user_id: str, company_id: str, limit: int,
                accession_no: str | None = None) -> list[dict]:
        """Recent filings (or one filing) with the sections each contains (no text)."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT accession_no, form, filed, period_of_report,
                       (array_agg(document_url ORDER BY section_key LIKE 'EX-%%', char_start))[1]
                           AS document_url,
                       json_agg(json_build_object('section_key', section_key, 'title', title,
                                                  'chars', length(text))
                                ORDER BY section_key LIKE 'EX-%%', char_start) AS sections
                FROM gold.filing_sections
                WHERE company_id = %s AND (%s::text IS NULL OR accession_no = %s)
                GROUP BY accession_no, form, filed, period_of_report
                ORDER BY filed DESC
                LIMIT %s
                """, (company_id, accession_no, accession_no, limit))
            return cur.fetchall()

    def filing_index(self, user_id: str, company_ids: list[str], forms: list[str],
                     start: date | None, end: date | None, limit: int, offset: int,
                     sections_for: list[str]) -> list[dict]:
        """The public SEC filing index (silver.filings) of these companies, newest first, one
        row per accession. `sections` counts parsed sections only for companies in
        `sections_for` (layer 1 said the caller may read their filings); RLS on
        silver.filing_sections filters that count again. NULL for every other company."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                WITH f AS (
                    SELECT DISTINCT ON (f.accession_no)
                           f.accession_no, f.company_id, f.cik, f.form, f.filing_date,
                           f.accepted_at, f.report_date, f.primary_document
                    FROM silver.filings f
                    WHERE f.company_id = ANY(%(companies)s)
                      AND (cardinality(%(forms)s::text[]) = 0 OR f.form = ANY(%(forms)s))
                      AND (%(start)s::date IS NULL OR f.filing_date >= %(start)s)
                      AND (%(end)s::date IS NULL OR f.filing_date <= %(end)s)
                    ORDER BY f.accession_no, f.report_date IS NULL, f.cik
                ),
                page AS (
                    SELECT * FROM f
                    ORDER BY filing_date DESC, accepted_at DESC NULLS LAST, accession_no DESC
                    LIMIT %(limit)s OFFSET %(offset)s
                )
                SELECT p.accession_no, p.company_id, c.name, p.cik, p.form,
                       p.filing_date AS filed, p.accepted_at, p.report_date AS period_of_report,
                       p.primary_document,
                       CASE WHEN p.company_id = ANY(%(sections_for)s) THEN (
                           SELECT count(DISTINCT s.section_key) FROM silver.filing_sections s
                           WHERE s.accession_no = p.accession_no) END AS sections
                FROM page p
                LEFT JOIN silver.companies c ON c.company_id = p.company_id
                ORDER BY p.filing_date DESC, p.accepted_at DESC NULLS LAST, p.accession_no DESC
                """, {"companies": company_ids, "forms": forms, "start": start, "end": end,
                      "limit": limit, "offset": offset, "sections_for": sections_for})
            return cur.fetchall()

    def latest_filings(self, user_id: str, company_ids: list[str], limit: int) -> list[dict]:
        """Newest parsed filings of these companies (no text)."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT s.company_id, c.name, s.accession_no, s.form, s.filed,
                       s.period_of_report, s.document_url, count(*) AS sections,
                       max(s.ingested_at) AS ingested_at
                FROM gold.filing_sections s
                JOIN silver.companies c USING (company_id)
                WHERE s.company_id = ANY(%s)
                GROUP BY s.company_id, c.name, s.accession_no, s.form, s.filed,
                         s.period_of_report, s.document_url
                ORDER BY s.filed DESC, max(s.ingested_at) DESC
                LIMIT %s
                """, (company_ids, limit))
            return cur.fetchall()

    def sections(self, user_id: str, company_id: str, section_key: str, form: str | None,
                 accession_no: str | None, limit: int, max_chars: int) -> list[dict]:
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT accession_no, form, filed, period_of_report, section_key, title,
                       left(text, %s) AS text, length(text) AS total_chars,
                       document_url, char_start, char_end, parser, ingested_at
                FROM gold.filing_sections
                WHERE company_id = %s AND section_key = %s
                  AND (%s::text IS NULL OR form = %s)
                  AND (%s::text IS NULL OR accession_no = %s)
                ORDER BY filed DESC
                LIMIT %s
                """, (max_chars, company_id, section_key, form, form, accession_no,
                      accession_no, limit))
            return cur.fetchall()

    def section_diffs(self, user_id: str, company_id: str, accession_no: str,
                      section_key: str | None, basis: str) -> list[dict]:
        """gold.section_diffs rows of a filing with both section texts. A side whose section
        was re-parsed after the diff (parser or parsed_at differ) comes back with text NULL."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT d.*, doc.period_of_report, doc.url AS document_url,
                       doc.fetched_at AS ingested_at, s.title, s.text, s.char_start,
                       pdoc.cik AS prior_cik, pdoc.period_of_report AS prior_period_of_report,
                       pdoc.url AS prior_document_url, ps.text AS prior_text,
                       ps.char_start AS prior_char_start
                FROM gold.section_diffs d
                JOIN bronze.filing_documents doc ON doc.accession_no = d.accession_no
                JOIN bronze.filing_documents pdoc ON pdoc.accession_no = d.prior_accession_no
                LEFT JOIN silver.filing_sections s
                       ON s.accession_no = d.accession_no AND s.section_key = d.section_key
                      AND s.parser = d.parser AND s.parsed_at = d.parsed_at
                LEFT JOIN silver.filing_sections ps
                       ON ps.accession_no = d.prior_accession_no
                      AND ps.section_key = d.section_key
                      AND ps.parser = d.prior_parser AND ps.parsed_at = d.prior_parsed_at
                WHERE d.company_id = %s AND d.accession_no = %s AND d.basis = %s
                  AND (%s::text IS NULL OR d.section_key = %s)
                ORDER BY d.section_key
                """, (company_id, accession_no, basis, section_key, section_key))
            return cur.fetchall()

    def section_changes(self, user_id: str, accession_no: str, section_keys: list[str],
                        basis: str, kind: str | None) -> list[dict]:
        """gold.section_changes rows in reading order."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT section_key, ordinal, kind, similarity, new_spans, prior_spans, words
                FROM gold.section_changes
                WHERE accession_no = %s AND section_key = ANY(%s) AND basis = %s
                  AND (%s::text IS NULL OR kind = %s)
                ORDER BY section_key, ordinal
                """, (accession_no, section_keys, basis, kind, kind))
            return cur.fetchall()

    def has_filing(self, user_id: str, company_id: str, accession_no: str) -> bool:
        """Whether the caller can read parsed sections of this filing of this company."""
        with self._session(user_id) as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM gold.filing_sections"
                        " WHERE company_id = %s AND accession_no = %s) AS found",
                        (company_id, accession_no))
            row = cur.fetchone()
            return bool(row and row["found"])

    def search_sections(self, user_id: str, company_ids: list[str], q: str, form: str | None,
                        section_key: str | None, start: date | None, end: date | None,
                        limit: int, offset: int) -> list[dict]:
        """Best passage per section matching websearch_to_tsquery(q), ranked by ts_rank_cd,
        with a ts_headline snippet cut from that passage only (never the whole section).
        Raises SearchTooBroad when ranking the matches takes over SEARCH_TIMEOUT."""
        try:
            with self._session(user_id) as cur:
                cur.execute(f"SET LOCAL statement_timeout = '{SEARCH_TIMEOUT}'")
                cur.execute(
                    """
                    WITH q AS (SELECT websearch_to_tsquery('english', %(q)s) AS q),
                    matches AS (
                        SELECT p.*, ts_rank_cd(p.tsv, q.q) AS rank,
                               count(*) OVER (PARTITION BY p.accession_no, p.section_key)
                                   AS passages_matched
                        FROM gold.section_search p, q
                        WHERE p.tsv @@ q.q AND p.company_id = ANY(%(companies)s)
                          AND (%(form)s::text IS NULL OR p.form = %(form)s)
                          AND (%(section)s::text IS NULL OR p.section_key = %(section)s)
                          AND (%(start)s::date IS NULL OR p.filed >= %(start)s)
                          AND (%(end)s::date IS NULL OR p.filed <= %(end)s)
                    ),
                    best AS (
                        SELECT DISTINCT ON (accession_no, section_key) *
                        FROM matches
                        ORDER BY accession_no, section_key, rank DESC, passage
                    ),
                    page AS (
                        SELECT * FROM best
                        ORDER BY rank DESC, filed DESC, accession_no, section_key
                        LIMIT %(limit)s OFFSET %(offset)s
                    )
                    SELECT h.company_id, c.name, h.accession_no, h.form, h.filed,
                           h.section_key, h.rank, h.passages_matched, s.title,
                           s.char_start + h.start_char AS char_start,
                           s.char_start + h.end_char AS char_end,
                           regexp_replace(
                               ts_headline('english',
                                           substr(s.text, h.start_char + 1,
                                                  h.end_char - h.start_char),
                                           q.q, %(headline)s),
                               '\\s+', ' ', 'g') AS snippet,
                           coalesce(e.url, d.url) AS document_url, d.cik,
                           d.period_of_report, d.fetched_at AS ingested_at
                    FROM page h
                    CROSS JOIN q
                    JOIN silver.filing_sections s
                      ON s.accession_no = h.accession_no AND s.section_key = h.section_key
                     AND s.parser = h.parser
                    JOIN bronze.filing_documents d ON d.accession_no = h.accession_no
                    LEFT JOIN bronze.filing_exhibits e
                           ON e.accession_no = h.accession_no AND e.exhibit_type = h.section_key
                    JOIN silver.companies c ON c.company_id = h.company_id
                    ORDER BY h.rank DESC, h.filed DESC, h.accession_no, h.section_key
                    """, {"q": q, "companies": company_ids, "form": form,
                          "section": section_key, "start": start, "end": end,
                          "limit": limit, "offset": offset, "headline": HEADLINE})
                return cur.fetchall()
        except psycopg.errors.QueryCanceled:
            raise SearchTooBroad from None

    def data_notes(self, user_id: str, company_ids: list[str], derived: bool,
                   limit: int) -> dict[str, Any]:
        """Raw data notes (query_api.notes): the newest `limit` quality findings, quarters
        derived from year-to-date values per metric (when `derived`), 10-K/10-Q filings on
        EDGAR since the latest one in gold that companyfacts doesn't carry yet, and when the
        companies were last checked. RLS filters findings and gold rows."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT f.company_id, f.check_name, f.severity, f.metric, f.period_end,
                       f.detail, q.checked_at
                FROM ops.quality_findings f
                LEFT JOIN ops.quality_companies q ON q.company_id = f.company_id
                WHERE f.company_id = ANY(%(c)s)
                ORDER BY coalesce((f.detail->>'restated_filed')::date, f.period_end) DESC,
                         f.company_id, f.check_name, f.metric
                LIMIT %(limit)s
                """, {"c": company_ids, "limit": limit})
            findings = cur.fetchall()
            rows: list[dict] = []
            if derived:
                cur.execute(
                    """
                    SELECT company_id, metric, count(*) AS quarters,
                           min(period_end) AS first_period_end,
                           max(period_end) AS latest_period_end,
                           (array_agg(accession_no ORDER BY period_end DESC))[1]
                               AS latest_accession
                    FROM gold.quarterly_financials
                    WHERE company_id = ANY(%s) AND value_source = 'derived'
                    GROUP BY company_id, metric
                    ORDER BY company_id, metric
                    """, (company_ids,))
                rows = cur.fetchall()
            # silver.filings gets company_id from a join inside each UNION branch, so a filter on
            # it can't reach the 27M-row bronze.submission_filings index: filter on the CIKs
            # (a base column) as literal values instead (5.4 s → 0.07 s for 20 companies).
            # Companyfacts lags by days, so only the last BEHIND_DAYS count as "behind".
            cur.execute("SELECT array_agg(cik) AS ciks FROM silver.company_ciks"
                        " WHERE company_id = ANY(%s)", (company_ids,))
            ciks = (cur.fetchone() or {}).get("ciks") or []
            cur.execute(
                """
                WITH latest AS (
                    SELECT company_id, max(filed) AS facts_filed
                    FROM gold.quarterly_financials
                    WHERE company_id = ANY(%(c)s)
                    GROUP BY company_id
                )
                SELECT DISTINCT ON (f.accession_no)
                       l.company_id, l.facts_filed, f.accession_no, f.form, f.filing_date,
                       f.cik
                FROM silver.filings f
                JOIN latest l ON l.company_id = f.company_id
                WHERE f.cik = ANY(%(ciks)s) AND f.form IN ('10-K', '10-Q')
                  AND f.filing_date >= l.facts_filed
                  AND f.filing_date >= current_date - %(days)s
                  AND NOT EXISTS (SELECT 1 FROM silver.financial_facts x
                                  WHERE x.accession_no = f.accession_no)
                ORDER BY f.accession_no, f.filing_date DESC
                """, {"c": company_ids, "ciks": ciks, "days": BEHIND_DAYS})
            behind = cur.fetchall()
            cur.execute("SELECT max(checked_at) AS checked_at FROM ops.quality_companies"
                        " WHERE company_id = ANY(%s)", (company_ids,))
            checked = (cur.fetchone() or {}).get("checked_at")
            return {"findings": findings, "derived": rows, "behind": behind,
                    "checked_at": checked}

    def filing_refs(self, user_id: str, accessions: list[str]) -> dict[str, dict]:
        """accession_no → form, filed, cik (the filer, for EDGAR links), from the public filing
        index; the facts' filer CIK when the index doesn't have it."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT a.accession_no, i.form, i.filing_date AS filed,
                       coalesce(i.cik, (SELECT x.cik FROM silver.financial_facts x
                                        WHERE x.accession_no = a.accession_no LIMIT 1)) AS cik
                FROM unnest(%s::text[]) a(accession_no)
                LEFT JOIN LATERAL (
                    SELECT f.form, f.filing_date, f.cik FROM silver.filings f
                    WHERE f.accession_no = a.accession_no
                    ORDER BY f.filing_date LIMIT 1
                ) i ON true
                """, (accessions,))
            return {r.pop("accession_no"): r for r in cur.fetchall()}
