"""Layer 2: every query runs as the query_api role with the caller's user_id set, so Postgres
row-level security (db/migrations/007_query_api_rls.sql) filters rows even if layer 1 were
skipped."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

from query_api.formula import Compiled


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
