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

SEARCH_TIMEOUT = "8s"
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

    def search(self, user_id: str, q: str, limit: int = 5) -> list[dict]:
        with self._session(user_id) as cur:
            cur.execute(
                "SELECT r.company_id, r.name, r.matched_on, r.matched_alias, r.is_current,"
                "       c.sic, c.sic_description AS industry, c.exchanges"
                " FROM silver.resolve_company(%s) WITH ORDINALITY r"
                " JOIN silver.companies c USING (company_id)"
                " ORDER BY r.ordinality LIMIT %s", (q, limit))
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
              limit: int) -> list[dict]:
        """Ranked peer candidates (gold.peer_candidates): SIC level, then the same revenue size
        band as `revenue` (only companies in `granted` have one; RLS applies too), then the
        same filer category, then revenue closeness, then name."""
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
                ORDER BY p.sic_level DESC,
                         coalesce(gold.revenue_band(r.value)
                                  = gold.revenue_band(%(revenue)s::numeric), false) DESC,
                         p.same_filer_category DESC,
                         CASE WHEN r.value > 0 AND %(revenue)s::numeric > 0
                              THEN abs(ln(r.value / %(revenue)s::numeric)) END NULLS LAST,
                         c.name, p.company_id
                LIMIT %(limit)s
                """, {"company": company_id, "granted": granted, "revenue": revenue,
                      "limit": limit})
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

    def filings(self, user_id: str, company_id: str, limit: int) -> list[dict]:
        """Recent filings with the sections each contains (no text)."""
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT accession_no, form, filed, period_of_report, document_url,
                       json_agg(json_build_object('section_key', section_key, 'title', title,
                                                  'chars', length(text))
                                ORDER BY char_start) AS sections
                FROM gold.filing_sections
                WHERE company_id = %s
                GROUP BY accession_no, form, filed, period_of_report, document_url
                ORDER BY filed DESC
                LIMIT %s
                """, (company_id, limit))
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
                           ts_headline('english',
                                       substr(s.text, h.start_char + 1,
                                              h.end_char - h.start_char),
                                       q.q, %(headline)s) AS snippet,
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
