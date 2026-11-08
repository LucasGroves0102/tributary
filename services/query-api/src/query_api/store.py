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
# A recap's one-time-item flags are optional: a slow pass must cost the panel little, and never
# all of it (ui.py answers without them).
FLAGS_TIMEOUT = "2s"
# Data notes: a 10-K/10-Q filed in the last BEHIND_DAYS whose XBRL isn't in companyfacts yet.
BEHIND_DAYS = 120
# silver.filings.filed_on (migration 032) is the date from the row's own base table, so a filter
# on it reaches both UNION branches. Up to this many days from filing_date (a filing accepted
# after hours before a long weekend is dated days later), so bounds on it carry this margin.
FILED_ON_MARGIN_DAYS = 7
# The companies the caller can read at any tier, as their CIKs: a base column of silver.filings
# (company_id comes from a join inside each UNION branch), in the caller's session under RLS.
READABLE_CIKS = """ARRAY(SELECT k.cik FROM silver.company_ciks k WHERE k.company_id IN (
    SELECT r FROM authz.readable_companies('financials') r
    UNION SELECT r FROM authz.readable_companies('filings') r))"""
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
            # These queries read few rows but cost a lot on paper (a LATERAL is charged the RLS
            # policy's hashed subplan on every rescan), so JIT compiled them for 0.7-1.6 s to
            # save nothing (hermes and local plans, migration 032).
            cur.execute("SET LOCAL jit = off")
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

    def search(self, user_id: str, q: str, limit: int = 5, only: list[str] | None = None,
               readable_only: bool = False) -> list[dict]:
        """Best matches for what someone typed: a ticker, a CIK, or part of a name
        (silver.search_companies has the ranking). only: just these companies (the caller's
        readable ones, for a demo that lists nothing else); None: every company. readable_only:
        the same narrowing when the catalog is public (no id list; RLS on the authz tables).
        Narrowed before LIMIT, so a page of unreadable matches can't hide a readable one."""
        narrow = (" AND r.company_id IN (SELECT x FROM authz.readable_companies('financials') x)"
                  if readable_only else "")
        with self._session(user_id) as cur:
            cur.execute(
                "SELECT r.company_id, r.name, r.matched_on, r.matched_alias, r.is_current,"
                "       c.sic, c.sic_description AS industry, c.exchanges, c.tickers"
                " FROM silver.search_companies(%(q)s) WITH ORDINALITY r"
                " JOIN silver.companies c USING (company_id)"
                " WHERE (%(only)s::text[] IS NULL OR r.company_id = ANY(%(only)s::text[]))" + narrow
                + " ORDER BY r.ordinality LIMIT %(limit)s",
                {"q": q, "only": only, "limit": limit})
            return cur.fetchall()

    def catalog_companies(self, user_id: str) -> frozenset[str]:
        """The public catalog's company ids while it is public (authz.public_access, written by
        authz-sync; RLS shows it only to a session with a user). Empty while closed."""
        with self._session(user_id) as cur:
            cur.execute("SELECT DISTINCT company_id FROM authz.public_access")
            return frozenset(r["company_id"] for r in cur.fetchall())

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

    def expected_filings(self, user_id: str, company_ids: list[str] | None, as_of: date,
                         periods: int) -> list[dict]:
        """Estimated next 10-Q/10-K filings (gold.expected_filings; public filing index).
        company_ids None: every company the caller can read (gold.expected_filings_recent)."""
        with self._session(user_id) as cur:
            if company_ids is None:
                cur.execute("SELECT * FROM gold.expected_filings_recent(%s, %s)",
                            (as_of, periods))
            else:
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
                SELECT r.*,
                       gold.not_meaningful(r.metric, r.value, (
                           SELECT d.inputs FROM gold.derived_financials d
                           WHERE d.company_id = %(company)s AND d.span = r.span
                             AND d.metric = r.metric AND d.period_end = r.period_end))
                           AS not_meaningful
                FROM gold.filing_recap(
                    %(company)s,
                    coalesce(%(period_end)s::date,
                             (SELECT max(q.period_end) FROM gold.quarterly_financials q
                              WHERE q.company_id = %(company)s
                                AND q.accession_no = %(accession)s)),
                    %(metrics)s) r
                """, {"company": company_id, "period_end": period_end,
                      "accession": accession_no, "metrics": metrics})
            return cur.fetchall()

    def filing_flags(self, user_id: str, accession_no: str) -> list[dict]:
        """gold.filing_flags rows (RLS: filings tier), under its own short timeout."""
        with self._session(user_id) as cur:
            cur.execute(f"SET LOCAL statement_timeout = '{FLAGS_TIMEOUT}'")
            cur.execute("SELECT * FROM gold.filing_flags(%s)", (accession_no,))
            return cur.fetchall()

    def peers(self, user_id: str, company_id: str, granted: list[str] | None,
              revenue: Decimal | None, limit: int, granted_only: bool = False) -> list[dict]:
        """Ranked peer candidates (gold.peer_candidates): SIC level, then the same revenue size
        band as `revenue` (only companies in `granted` have one; RLS applies too), then the
        same filer category, then revenue closeness, then name. granted_only: only candidates
        in `granted`. granted None: the catalog is public at this tier, RLS alone bounds the
        revenue (query_api.authorizer.Readable)."""
        gate = "" if granted is None else "WHERE p.company_id = ANY(%(granted)s)"
        only = ("p.company_id IN (SELECT x FROM authz.readable_companies('financials') x)"
                if granted is None else "p.company_id = ANY(%(granted)s)")
        with self._session(user_id) as cur:
            cur.execute(
                f"""
                SELECT p.company_id, p.sic_level, p.same_filer_category,
                       gold.revenue_band(r.value) AS revenue_band
                FROM gold.peer_candidates(%(company)s) p
                JOIN silver.companies c ON c.company_id = p.company_id
                LEFT JOIN LATERAL (
                    SELECT x.value FROM gold.latest_value(p.company_id, 'ttm', 'revenue') x
                    {gate}
                ) r ON true
                WHERE NOT %(granted_only)s OR {only}
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
                       ingested_at, gold.not_meaningful(metric, value, inputs) AS not_meaningful,
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

    def annual_per_share(self, user_id: str, company_id: str, years: int) -> list[dict]:
        """The 10-K's own EPS and weighted shares per fiscal year (gold.annual_per_share,
        migration 036): gold's quarters have no annual per-share figure, and these don't add."""
        with self._session(user_id) as cur:
            cur.execute("SELECT * FROM gold.annual_per_share(%s, %s)"
                        " ORDER BY metric, period_end DESC", (company_id, years))
            return cur.fetchall()

    def feed_status(self) -> dict:
        """When the newest filing was accepted by EDGAR and reached bronze (ops.feed_status,
        migration 026): public, no user."""
        with self._session("") as cur:
            cur.execute("SELECT * FROM ops.feed_status()")
            return cur.fetchone() or {"last_accepted_at": None, "last_ingested_at": None}

    def data_status(self) -> dict:
        """The public status page's aggregates (migration 028's SECURITY DEFINER functions):
        counts and timestamps only, no user. {freshness, ingest, completeness, coverage,
        quality: [one row per check]}."""
        with self._session("") as cur:
            out = {}
            cur.execute("SELECT * FROM ops.feed_status()")
            out["freshness"] = cur.fetchone() or {}
            cur.execute("SELECT * FROM ops.public_ingest()")
            out["ingest"] = cur.fetchone() or {}
            cur.execute("SELECT * FROM ops.public_completeness()")
            out["completeness"] = cur.fetchone() or {}
            cur.execute("SELECT * FROM ops.public_coverage()")
            out["coverage"] = cur.fetchone() or {}
            cur.execute("SELECT * FROM ops.public_quality()")
            out["quality"] = cur.fetchall()
            return out

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

    def filing_index(self, user_id: str, company_ids: list[str] | None, forms: list[str],
                     start: date | None, end: date | None, limit: int, offset: int,
                     sections_for: list[str] | None) -> list[dict]:
        """The public SEC filing index (silver.filings) of these companies, newest first, one
        row per accession. `sections` counts parsed sections only for companies in
        `sections_for` (layer 1 said the caller may read their filings); RLS on
        silver.filing_sections filters that count again. NULL for every other company.

        company_ids None: every company the caller can read, without an id list (the catalog
        is public): filtered on their CIKs and a window on filed_on, the two base columns that
        reach silver.filings' UNION branches. Call it with a `start` (ui.py walks windows).
        sections_for None: every company on the page, RLS alone."""
        if company_ids is None:
            scope = f"f.cik = ANY({READABLE_CIKS})"
            if start is not None:
                scope += " AND f.filed_on >= %(start)s::date - %(margin)s"
        else:
            scope = "f.company_id = ANY(%(companies)s)"
        sections = "true" if sections_for is None else "p.company_id = ANY(%(sections_for)s)"
        with self._session(user_id) as cur:
            cur.execute(
                f"""
                WITH f AS (
                    SELECT DISTINCT ON (f.accession_no)
                           f.accession_no, f.company_id, f.cik, f.form, f.filing_date,
                           f.accepted_at, f.report_date, f.primary_document
                    FROM silver.filings f
                    WHERE {scope}
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
                       p.filing_date AS filed, p.accepted_at,
                       -- the SEC's index gives some old 10-Ks and 10-Qs their filing date as the
                       -- period (NVIDIA's 2008 10-K): a report can't cover a period ending that day
                       CASE WHEN p.form ~ '^10-[KQ]' AND p.report_date = p.filing_date
                            THEN NULL ELSE p.report_date END AS period_of_report,
                       p.primary_document,
                       CASE WHEN {sections} THEN (
                           SELECT count(DISTINCT s.section_key) FROM silver.filing_sections s
                           WHERE s.accession_no = p.accession_no) END AS sections
                FROM page p
                LEFT JOIN silver.companies c ON c.company_id = p.company_id
                ORDER BY p.filing_date DESC, p.accepted_at DESC NULLS LAST, p.accession_no DESC
                """, {"companies": company_ids, "forms": forms, "start": start, "end": end,
                      "limit": limit, "offset": offset, "sections_for": sections_for,
                      "margin": FILED_ON_MARGIN_DAYS})
            return cur.fetchall()

    def latest_filings(self, user_id: str, company_ids: list[str] | None,
                       limit: int) -> list[dict]:
        """Newest parsed filings of these companies (no text). company_ids None: of every
        company the caller can read (the catalog is public): walk the documents newest first
        and stop at `limit` with sections, instead of grouping every section under RLS."""
        accessions: list[str] | None = None
        with self._session(user_id) as cur:
            if company_ids is None:
                cur.execute(
                    """
                    SELECT d.accession_no, k.company_id
                    FROM bronze.filing_documents d
                    JOIN silver.company_ciks k ON k.cik = d.cik
                    WHERE k.company_id IN (SELECT r FROM authz.readable_companies('filings') r)
                      AND EXISTS (SELECT 1 FROM silver.filing_sections x
                                  WHERE x.accession_no = d.accession_no)
                    ORDER BY d.filed DESC, d.fetched_at DESC
                    LIMIT %s
                    """, (limit,))
                newest = cur.fetchall()
                # Their companies as bound values too: only company_id reaches the view's DISTINCT
                # ON (025), neither the accession nor a subquery does.
                company_ids = sorted({r["company_id"] for r in newest})
                accessions = [r["accession_no"] for r in newest]
            cur.execute(
                """
                SELECT s.company_id, c.name, s.accession_no, s.form, s.filed,
                       s.period_of_report, s.document_url, count(*) AS sections,
                       max(s.ingested_at) AS ingested_at
                FROM gold.filing_sections s
                JOIN silver.companies c USING (company_id)
                WHERE s.company_id = ANY(%s) AND (%s::text[] IS NULL OR s.accession_no = ANY(%s))
                GROUP BY s.company_id, c.name, s.accession_no, s.form, s.filed,
                         s.period_of_report, s.document_url
                ORDER BY s.filed DESC, max(s.ingested_at) DESC
                LIMIT %s
                """, (company_ids, accessions, accessions, limit))
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

    def search_sections(self, user_id: str, company_ids: list[str] | None, q: str,
                        form: str | None, section_key: str | None, start: date | None,
                        end: date | None, limit: int, offset: int) -> list[dict]:
        """Best passage per section matching websearch_to_tsquery(q), ranked by ts_rank_cd,
        with a ts_headline snippet cut from that passage only (never the whole section).
        company_ids None: every company the caller can read (gold.search_passages, migration
        032, applies the same read rule as RLS; the section text below is still read under RLS).
        Raises SearchTooBroad when ranking the matches takes over SEARCH_TIMEOUT."""
        try:
            with self._session(user_id) as cur:
                cur.execute(f"SET LOCAL statement_timeout = '{SEARCH_TIMEOUT}'")
                cur.execute(
                    """
                    WITH q AS (SELECT websearch_to_tsquery('english', %(q)s) AS q),
                    page AS (
                        SELECT p.*
                        FROM q CROSS JOIN LATERAL gold.search_passages(
                            q.q, %(companies)s::text[], %(form)s::text, %(section)s::text,
                            %(start)s::date, %(end)s::date, %(limit)s::int, %(offset)s::int) p
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

    def data_notes(self, user_id: str, company_ids: list[str] | None, derived: bool,
                   limit: int) -> dict[str, Any]:
        """Raw data notes (query_api.notes): the newest `limit` quality findings, quarters
        derived from year-to-date values per metric (when `derived`), 10-K/10-Q filings on
        EDGAR since the latest one in gold that companyfacts doesn't carry yet, and when the
        companies were last checked. RLS filters findings and gold rows. company_ids None:
        every company the caller can read (the catalog is public), RLS alone; no `derived`."""
        scope = "TRUE" if company_ids is None else "f.company_id = ANY(%(c)s)"
        with self._session(user_id) as cur:
            cur.execute(
                f"""
                SELECT f.company_id, f.check_name, f.severity, f.metric, f.period_end,
                       f.detail, q.checked_at
                FROM ops.quality_findings f
                LEFT JOIN ops.quality_companies q ON q.company_id = f.company_id
                WHERE {scope}
                ORDER BY coalesce((f.detail->>'restated_filed')::date, (f.detail->>'filed')::date,
                                  f.period_end) DESC,
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
                               AS latest_accession,
                           bool_and(source_tag LIKE 'derived:%%') AS computed
                    FROM gold.quarterly_financials
                    WHERE company_id = ANY(%s) AND value_source = 'derived'
                    GROUP BY company_id, metric
                    ORDER BY company_id, metric
                    """, (company_ids,))
                rows = cur.fetchall()
            if company_ids is None:
                # The filings since the BEHIND_DAYS cut without facts are few, so start there and
                # read each one's company's latest gold filing (RLS), not gold for every company.
                cur.execute(
                    """
                    WITH recent AS MATERIALIZED (
                        SELECT f.company_id, f.accession_no, f.form, f.filing_date, f.cik
                        FROM silver.filings f
                        WHERE f.form IN ('10-K', '10-Q')
                          AND f.filing_date >= current_date - %(days)s
                          AND f.filed_on >= current_date - %(days)s - %(margin)s
                          AND f.company_id IS NOT NULL
                          AND NOT EXISTS (SELECT 1 FROM silver.financial_facts x
                                          WHERE x.accession_no = f.accession_no)
                    )
                    SELECT DISTINCT ON (f.accession_no)
                           f.company_id, l.facts_filed, f.accession_no, f.form, f.filing_date,
                           f.cik
                    FROM recent f
                    CROSS JOIN LATERAL (
                        SELECT max(q.filed) AS facts_filed FROM gold.quarterly_financials q
                        WHERE q.company_id = f.company_id
                    ) l
                    WHERE f.filing_date >= l.facts_filed
                    ORDER BY f.accession_no, f.filing_date DESC
                    """, {"days": BEHIND_DAYS, "margin": FILED_ON_MARGIN_DAYS})
                behind = cur.fetchall()
                cur.execute("SELECT max(checked_at) AS checked_at FROM ops.quality_companies")
                checked = (cur.fetchone() or {}).get("checked_at")
                return {"findings": findings, "derived": rows, "behind": behind,
                        "checked_at": checked}
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

    def metric_series(self, user_id: str, company_ids: list[str],
                      metrics: list[str]) -> dict[tuple[str, str], list[tuple[date, float]]]:
        """(company, metric) → its quarters, oldest first (a data note's one-quarter spike, read
        against the other years). A few companies and metrics at most, under RLS."""
        with self._session(user_id) as cur:
            cur.execute("SELECT company_id, metric, period_end, value"
                        " FROM gold.quarterly_financials"
                        " WHERE company_id = ANY(%s) AND metric = ANY(%s)"
                        " ORDER BY company_id, metric, period_end", (company_ids, metrics))
            out: dict[tuple[str, str], list[tuple[date, float]]] = {}
            for r in cur.fetchall():
                out.setdefault((r["company_id"], r["metric"]), []).append(
                    (r["period_end"], float(r["value"])))
            return out

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
