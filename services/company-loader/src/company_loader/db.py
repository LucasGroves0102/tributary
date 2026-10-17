"""Load a Snapshot into silver company tables in one transaction."""

from dataclasses import astuple
from datetime import date

import psycopg

from company_loader.build import Snapshot

_STAGING = """
    DROP TABLE IF EXISTS stg_companies, stg_ciks, stg_aliases, stg_tickers;
    CREATE TEMP TABLE stg_companies (
        company_id text, name text, current_cik text, sic text, sic_description text,
        entity_type text, tracked boolean) ON COMMIT DROP;
    CREATE TEMP TABLE stg_ciks (
        cik text, company_id text, valid_from date, valid_to date) ON COMMIT DROP;
    CREATE TEMP TABLE stg_aliases (
        cik text, alias_type text, alias text, valid_from date, valid_to date, source text
    ) ON COMMIT DROP;
    CREATE TEMP TABLE stg_tickers (alias_type text, alias text, cik text) ON COMMIT DROP;
"""

_MERGE = [
    # 1. companies: upsert; only bump updated_at when something changed
    """
    INSERT INTO silver.companies AS c
        (company_id, name, current_cik, sic, sic_description, entity_type, tracked)
    SELECT * FROM stg_companies
    ON CONFLICT (company_id) DO UPDATE SET
        name = EXCLUDED.name, current_cik = EXCLUDED.current_cik, sic = EXCLUDED.sic,
        sic_description = EXCLUDED.sic_description, entity_type = EXCLUDED.entity_type,
        tracked = EXCLUDED.tracked, updated_at = now()
    WHERE (c.name, c.current_cik, c.sic, c.sic_description, c.entity_type, c.tracked)
        IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.current_cik, EXCLUDED.sic,
                          EXCLUDED.sic_description, EXCLUDED.entity_type, EXCLUDED.tracked)
    """,
    # companies that dropped out of the SEC file stay, but stop being tracked
    """
    UPDATE silver.companies c SET tracked = false, updated_at = now()
    WHERE c.tracked AND NOT EXISTS (SELECT 1 FROM stg_companies s WHERE s.company_id = c.company_id)
    """,
    # 2. CIK → company; a new succession re-points the successor's CIK to the root
    """
    INSERT INTO silver.company_ciks (cik, company_id, valid_from, valid_to)
    SELECT * FROM stg_ciks
    ON CONFLICT (cik) DO UPDATE SET
        company_id = EXCLUDED.company_id, valid_from = EXCLUDED.valid_from,
        valid_to = EXCLUDED.valid_to
    """,
    # 3. aliases follow their CIK to its (possibly new) company
    """
    UPDATE silver.company_aliases a SET company_id = k.company_id
    FROM silver.company_ciks k
    WHERE a.cik = k.cik AND a.company_id <> k.company_id
    """,
    # 4. submissions + curated aliases are fully derived from inputs: replace them
    "DELETE FROM silver.company_aliases WHERE source IN ('sec_submissions', 'curated')",
    """
    INSERT INTO silver.company_aliases
        (company_id, cik, alias_type, alias, valid_from, valid_to, source)
    SELECT k.company_id, s.cik, s.alias_type, s.alias, s.valid_from, s.valid_to, s.source
    FROM stg_aliases s JOIN silver.company_ciks k USING (cik)
    """,
]

# 5. company_tickers.json snapshots: close what disappeared, open what's new.
# On the very first load we don't know when a ticker started, so valid_from is NULL.
_CLOSE_TICKERS = """
    UPDATE silver.company_aliases a SET valid_to = %(today)s
    WHERE a.source = 'sec_tickers' AND a.valid_to IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM stg_tickers s
        WHERE (s.alias_type, s.alias, s.cik) = (a.alias_type, a.alias, a.cik))
"""
_OPEN_TICKERS = """
    INSERT INTO silver.company_aliases
        (company_id, cik, alias_type, alias, valid_from, valid_to, source)
    SELECT k.company_id, s.cik, s.alias_type, s.alias,
           CASE WHEN %(first_load)s THEN NULL ELSE %(today)s::date END, NULL, 'sec_tickers'
    FROM stg_tickers s JOIN silver.company_ciks k USING (cik)
    WHERE NOT EXISTS (
        SELECT 1 FROM silver.company_aliases a
        WHERE a.source = 'sec_tickers' AND a.valid_to IS NULL
          AND (a.alias_type, a.alias, a.cik) = (s.alias_type, s.alias, s.cik))
"""

# 6. companies merged into another lineage no longer own any CIK
_DELETE_MERGED = """
    DELETE FROM silver.companies c
    WHERE NOT EXISTS (SELECT 1 FROM silver.company_ciks k WHERE k.company_id = c.company_id)
"""


def load(conn: psycopg.Connection, snap: Snapshot, today: date) -> dict[str, int]:
    """Apply a snapshot atomically. Returns row counts for logging."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_STAGING)
        _copy(cur, "stg_companies", [astuple(c) for c in snap.companies])
        _copy(cur, "stg_ciks", [astuple(c) for c in snap.ciks])
        _copy(cur, "stg_aliases", [astuple(a) for a in snap.aliases])
        _copy(cur, "stg_tickers", sorted(snap.current_tickers))

        for sql in _MERGE:
            cur.execute(sql)
        cur.execute("SELECT NOT EXISTS (SELECT 1 FROM silver.company_aliases"
                    " WHERE source = 'sec_tickers')")
        first_load = cur.fetchone()[0]
        params = {"today": today, "first_load": first_load}
        cur.execute(_CLOSE_TICKERS, params)
        closed = cur.rowcount
        cur.execute(_OPEN_TICKERS, params)
        opened = cur.rowcount
        cur.execute(_DELETE_MERGED)
        merged = cur.rowcount
    return {
        "companies": len(snap.companies),
        "tracked": sum(c.tracked for c in snap.companies),
        "tickers_opened": opened,
        "tickers_closed": closed,
        "companies_merged": merged,
    }


def _copy(cur: psycopg.Cursor, table: str, rows: list[tuple]) -> None:
    with cur.copy(f"COPY {table} FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
