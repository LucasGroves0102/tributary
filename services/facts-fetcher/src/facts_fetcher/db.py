"""Bronze writes and silver/gold refreshes. The transforms themselves live in SQL."""

from collections import defaultdict
from collections.abc import Sequence

import psycopg

from facts_fetcher.payload import Payload, Tag, prepare


def tracked_ciks(conn: psycopg.Connection) -> dict[str, list[str]]:
    """company_id → every CIK in its lineage, for tracked companies."""
    rows = conn.execute(
        """
        SELECT c.company_id, k.cik
        FROM silver.companies c JOIN silver.company_ciks k USING (company_id)
        WHERE c.tracked
        ORDER BY c.company_id, k.cik
        """
    ).fetchall()
    out: dict[str, list[str]] = defaultdict(list)
    for company_id, cik in rows:
        out[company_id].append(cik)
    return dict(out)


def metric_tags(conn: psycopg.Connection) -> set[Tag]:
    return {(t, g) for t, g in conn.execute("SELECT taxonomy, tag FROM silver.metric_tags")}


def latest_hashes(conn: psycopg.Connection) -> dict[str, str]:
    """CIK → SHA-256 of its latest stored payload."""
    rows = conn.execute(
        """
        SELECT DISTINCT ON (cik) cik, payload_sha256 FROM bronze.company_facts
        ORDER BY cik, last_checked_at DESC, fetched_at DESC
        """
    ).fetchall()
    return dict(rows)


def store_payloads(conn: psycopg.Connection, payloads: Sequence[Payload], source: str) -> list[str]:
    """Insert each payload unless already stored, and drop the raw copy of superseded ones.
    A payload seen again is re-projected and becomes its CIK's latest. Returns the CIKs whose
    payload was new. Call inside a transaction."""
    if not payloads:
        return []
    conn.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS company_facts_in (
            cik text, payload_sha256 text, payload jsonb, raw_gzip bytea, size_bytes bigint
        ) ON COMMIT DELETE ROWS
        """
    )
    conn.execute("TRUNCATE company_facts_in")
    with conn.cursor().copy("COPY company_facts_in FROM STDIN") as copy:
        for p in payloads:
            copy.write_row((p.cik, p.sha256, p.facts_json, p.raw_gzip, p.size_bytes))
    rows = conn.execute(
        """
        INSERT INTO bronze.company_facts (cik, payload_sha256, payload, raw_gzip, size_bytes,
                                          source, fetched_at, last_checked_at)
        -- clock time, not transaction time: the latest payload must win within a transaction
        SELECT cik, payload_sha256, payload, raw_gzip, size_bytes, %s,
               clock_timestamp(), clock_timestamp()
        FROM company_facts_in
        ON CONFLICT (cik, payload_sha256) DO UPDATE
            SET payload = EXCLUDED.payload, raw_gzip = EXCLUDED.raw_gzip,
                size_bytes = EXCLUDED.size_bytes, last_checked_at = clock_timestamp()
        RETURNING cik, (xmax = 0)  -- true on insert, false on update
        """,
        (source,),
    ).fetchall()
    conn.execute(
        """
        UPDATE bronze.company_facts f SET raw_gzip = NULL
        FROM company_facts_in p
        WHERE f.cik = p.cik AND f.payload_sha256 <> p.payload_sha256 AND f.raw_gzip IS NOT NULL
        """
    )
    return [cik for cik, new in rows if new]


def store_payload(conn: psycopg.Connection, cik: str, raw: bytes, source: str = "api") -> bool:
    """Store one fetched payload. Returns True if it was new."""
    return bool(store_payloads(conn, [prepare(cik, raw, metric_tags(conn))], source))


def touch(conn: psycopg.Connection, ciks: Sequence[str], shas: Sequence[str]) -> None:
    """Record that these payloads were seen again unchanged."""
    conn.execute(
        """
        UPDATE bronze.company_facts f SET last_checked_at = clock_timestamp()
        FROM unnest(%s::text[], %s::text[]) AS p(cik, sha)
        WHERE f.cik = p.cik AND f.payload_sha256 = p.sha
        """,
        (list(ciks), list(shas)),
    )


def latest_raw(conn: psycopg.Connection, ciks: Sequence[str]) -> list[tuple[str, str, bytes]]:
    """(cik, sha256, raw_gzip) of each CIK's latest payload that still has its raw copy."""
    return conn.execute(
        """
        SELECT cik, payload_sha256, raw_gzip FROM (
            SELECT DISTINCT ON (cik) cik, payload_sha256, raw_gzip FROM bronze.company_facts
            WHERE cik = ANY(%s)
            ORDER BY cik, last_checked_at DESC, fetched_at DESC
        ) latest
        WHERE raw_gzip IS NOT NULL
        """,
        (list(ciks),),
    ).fetchall()


def set_facts(conn: psycopg.Connection, cik: str, sha256: str, facts_json: str) -> None:
    conn.execute(
        "UPDATE bronze.company_facts SET payload = %s::jsonb"
        " WHERE cik = %s AND payload_sha256 = %s",
        (facts_json, cik, sha256),
    )


def company_ids(conn: psycopg.Connection, ciks: Sequence[str]) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT company_id FROM silver.company_ciks WHERE cik = ANY(%s) ORDER BY 1",
        (list(ciks),),
    ).fetchall()
    return [r[0] for r in rows]


def pending_companies(conn: psycopg.Connection) -> list[str]:
    """Companies with silver facts but no gold rows yet."""
    rows = conn.execute("SELECT company_id FROM gold.financials_pending ORDER BY 1").fetchall()
    return [r[0] for r in rows]


def refresh_silver(conn: psycopg.Connection, ciks: Sequence[str]) -> int:
    """New silver facts from the latest bronze payload of each CIK."""
    if not ciks:
        return 0
    return conn.execute(
        "SELECT silver.refresh_financial_facts(%s::text[])", (list(ciks),)
    ).fetchone()[0]


def refresh_gold(conn: psycopg.Connection, company_ids: Sequence[str]) -> int:
    """Rebuild these companies' gold quarters, TTM and derived metrics, and run the
    data-quality checks on them. Returns how many quarterly rows there are now."""
    if not company_ids:
        return 0
    return conn.execute(
        "SELECT gold.refresh_financials(%s::text[])", (list(company_ids),)
    ).fetchone()[0]
