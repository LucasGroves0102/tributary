"""Layer 2: every query runs as the query_api role with the caller's user_id set, so Postgres
row-level security (db/migrations/007_query_api_rls.sql) filters rows even if layer 1 were
skipped."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row


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

    def search(self, user_id: str, q: str, limit: int = 5) -> list[dict]:
        with self._session(user_id) as cur:
            cur.execute(
                "SELECT company_id, name, matched_on, matched_alias, is_current"
                " FROM silver.resolve_company(%s) LIMIT %s", (q, limit))
            return cur.fetchall()

    def names(self, user_id: str, company_ids: list[str]) -> dict[str, str]:
        with self._session(user_id) as cur:
            cur.execute("SELECT company_id, name FROM silver.companies"
                        " WHERE company_id = ANY(%s)", (company_ids,))
            return {r["company_id"]: r["name"] for r in cur.fetchall()}

    def financials(self, user_id: str, company_id: str, metrics: list[str],
                   quarters: int) -> list[dict]:
        with self._session(user_id) as cur:
            cur.execute(
                """
                SELECT metric, period_start, period_end, value, unit, value_source,
                       source_tag, accession_no, form, filed, source_accessions, ingested_at,
                       (SELECT f.cik FROM silver.financial_facts f
                        WHERE f.accession_no = q.accession_no LIMIT 1) AS filer_cik
                FROM (
                    SELECT *, row_number() OVER (PARTITION BY metric ORDER BY period_end DESC)
                    FROM gold.quarterly_financials
                    WHERE company_id = %s AND metric = ANY(%s)
                ) q
                WHERE row_number <= %s
                ORDER BY metric, period_end DESC
                """, (company_id, metrics, quarters))
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
