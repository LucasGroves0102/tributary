"""Facts from a filing's own XBRL where SEC companyfacts doesn't carry them (migration 055).

companyfacts misses some filings for good (Citigroup's 2026 10-Qs, Clorox's FY2026 10-K: 52
of 12,006 10-K/10-Qs in 200 days) and never carries a company's own extension tags or dimensional
facts. Each filing's XBRL has them: the stored primary document's inline XBRL, already extracted by
the segments job (bronze.filing_xbrl.raw_gzip, every numeric fact), or else the filing's instance
document, fetched from EDGAR once and stored (bronze.filing_instances). A run:

1. Caught up: filings read here that companyfacts now carries (silver.filing_facts_caught_up) leave
   silver (silver.supersede_filing_facts); their filers' companyfacts payloads are refreshed into
   silver again and the companies' gold rebuilt. No SEC request.
2. Gaps: 10-K and 10-Q filings with XBRL, filed between --since (default 400 days back) and
   --after-days ago (default 3: companyfacts normally has a filing within a day), at a company here,
   with no fact in silver under any CIK. Amendments only when their stored document has statement
   facts (most are Part III alone). The stored extract when it has at least MIN_STATEMENT_FACTS
   undimensioned facts of mapped tags, else the instance: two SEC requests (the folder's index.json
   and the instance), through libs/sec's shared rate limit.
3. Extras: the stored documents of companies with their own tags or member sums (silver.metric_tags
   with a company_id, silver.member_sums) not read yet.
4. --accessions: these filings whatever they are (history before the document crawl: NVIDIA's FY2018
   and FY2020 10-Ks), fetching the instance when no document is stored.

Each filing becomes one bronze.filing_facts row (the projection: undimensioned facts of mapped tags,
and the member facts a sum needs), then silver.refresh_filing_facts maps them into silver and
gold.refresh_financials rebuilds the companies, per batch in one transaction. --rebuild re-projects
every stored row from its source (after a change to metric_tags or member_sums) without the SEC.
"""

import argparse
import gzip
import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

import httpx
import psycopg

from facts_fetcher import instance
from facts_fetcher.ixbrl import EXTRACTOR_VERSION
from facts_fetcher.segments import decompress, fact_dict
from tributary_sec import sec_client
from tributary_telemetry import configure_logging

log = logging.getLogger("facts_fetcher.filings")

GAP_FORMS = ("10-K", "10-Q")
AMENDMENTS = ("10-K/A", "10-Q/A")
SINCE_DAYS = 400
AFTER_DAYS = 3
LIMIT = 500             # filings per run
BATCH = 25              # filings per transaction
MIN_STATEMENT_FACTS = 10
MAX_INSTANCE_BYTES = 200 << 20
CONSOLIDATION = ("srt:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember")
ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/"
_NOT_INSTANCE = re.compile(r"(_cal|_def|_lab|_pre)\.xml$|^FilingSummary\.xml$", re.I)


@dataclass(frozen=True)
class Mapping:
    """What silver can read from a filing: tags every company shares, each company's own tags, and
    the member sums each company has (concept -> axis)."""
    standard: frozenset[tuple[str, str]]
    own: dict[str, frozenset[tuple[str, str]]]
    sums: dict[str, dict[str, str]]


@dataclass(frozen=True)
class Filing:
    accession_no: str
    cik: str              # a filer: the folder on EDGAR
    form: str | None
    filed: date
    gap: bool             # companyfacts has no fact of it


@dataclass(frozen=True)
class Row:
    accession_no: str
    cik: str
    form: str | None
    filed: date
    source: str           # ixbrl | instance
    document: str
    gap: bool
    facts: list[dict]
    fact_count: int


@dataclass
class RunStats:
    filings: int = 0
    instances_fetched: int = 0
    sec_requests: int = 0
    silver_rows: int = 0
    gold_rows: int = 0
    caught_up: int = 0
    skipped: list[str] = field(default_factory=list)


# projection ----------------------------------------------------------------------------------

def split_concept(concept: str) -> tuple[str, str]:
    prefix, _, local = concept.rpartition(":")
    return prefix, local


def project(facts: Iterable[dict], company_id: str | None, mapping: Mapping,
            gap: bool) -> list[dict]:
    """The facts silver reads from one filing: undimensioned facts of mapped tags (the shared ones
    only for a gap filing), and the member facts of the company's sums (one axis, optionally with
    the operating-segments consolidation member) with the concept's undimensioned facts, which
    stop a sum where the filing states the consolidated figure itself."""
    own = mapping.own.get(company_id or "", frozenset())
    sums = mapping.sums.get(company_id or "", {})
    out = []
    for f in facts:
        members = f.get("m")
        if not members:
            key = split_concept(f["c"])
            if key in own or (gap and key in mapping.standard) or f["c"] in sums:
                out.append(f)
        elif f["c"] in sums:
            rest = {k: v for k, v in members.items() if (k, v) != CONSOLIDATION}
            if list(rest) == [sums[f["c"]]]:
                out.append(f)
    return out


def statement_facts(projected: Iterable[dict], mapping: Mapping) -> int:
    return sum(1 for f in projected if not f.get("m") and split_concept(f["c"]) in mapping.standard)


# EDGAR -----------------------------------------------------------------------------------------

def instance_name(index: dict) -> str | None:
    """The instance among a filing folder's files (index.json): EDGAR's extract of an inline filing
    (`*_htm.xml`), else the one .xml that isn't a linkbase or the filing summary."""
    names = [i.get("name", "") for i in index.get("directory", {}).get("item", [])]
    inline = [n for n in names if n.endswith("_htm.xml")]
    if inline:
        return inline[0]
    xml = [n for n in names if n.lower().endswith(".xml") and not _NOT_INSTANCE.search(n)]
    dated = [n for n in xml if re.search(r"\d{8}\.xml$", n)]
    return (dated or xml or [None])[0]


def fetch_instance(client: httpx.Client, cik: str, accession_no: str,
                   stats: RunStats) -> tuple[str, bytes] | None:
    """(url, content) of a filing's instance, or None when the folder has none."""
    base = ARCHIVES.format(cik=int(cik), folder=accession_no.replace("-", ""))
    stats.sec_requests += 1
    r = client.get(base + "index.json")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    name = instance_name(r.json())
    if name is None:
        return None
    stats.sec_requests += 1
    r = client.get(base + name)
    r.raise_for_status()
    if len(r.content) > MAX_INSTANCE_BYTES:
        raise ValueError(f"{accession_no}: instance of {len(r.content)} bytes")
    return base + name, r.content


# database ------------------------------------------------------------------------------------

def mapping(conn: psycopg.Connection) -> Mapping:
    standard: set[tuple[str, str]] = set()
    own: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for taxonomy, tag, company_id in conn.execute(
            "SELECT taxonomy, tag, company_id FROM silver.metric_tags"):
        (own[company_id] if company_id else standard).add((taxonomy, tag))
    sums: dict[str, dict[str, str]] = defaultdict(dict)
    for company_id, concept, axis in conn.execute(
            "SELECT company_id, concept, axis FROM silver.member_sums"):
        sums[company_id][concept] = axis
    return Mapping(frozenset(standard), {k: frozenset(v) for k, v in own.items()}, dict(sums))


def gaps(conn: psycopg.Connection, since: date, after_days: int, limit: int) -> list[Filing]:
    """Filings companyfacts lacks (module docstring, 2), oldest first."""
    rows = conn.execute(
        """
        WITH s AS (
            SELECT DISTINCT ON (z.accession_no) z.accession_no, z.cik, z.form, z.filing_date
            FROM bronze.submission_filings z
            WHERE z.form = ANY(%(forms)s) AND z.is_xbrl
              AND z.filing_date >= %(since)s AND z.filing_date <= current_date - %(after)s
            ORDER BY z.accession_no,
                     EXISTS (SELECT 1 FROM silver.company_ciks k WHERE k.cik = z.cik) DESC, z.cik
        )
        SELECT s.accession_no, s.cik, s.form, s.filing_date
        FROM s
        WHERE NOT EXISTS (SELECT 1 FROM silver.financial_facts f
                          WHERE f.accession_no = s.accession_no)
          AND NOT EXISTS (SELECT 1 FROM bronze.filing_facts b WHERE b.accession_no = s.accession_no)
          AND EXISTS (SELECT 1 FROM bronze.submission_filings z
                      JOIN silver.company_ciks k ON k.cik = z.cik
                      WHERE z.accession_no = s.accession_no)
          AND (s.form = ANY(%(originals)s)
               OR EXISTS (SELECT 1 FROM bronze.filing_xbrl x
                          WHERE x.accession_no = s.accession_no AND x.status = 'ok'))
        ORDER BY s.filing_date, s.accession_no
        LIMIT %(limit)s
        """,
        {"forms": list(GAP_FORMS + AMENDMENTS), "originals": list(GAP_FORMS), "since": since,
         "after": after_days, "limit": limit}).fetchall()
    return [Filing(a, c, f, d, True) for a, c, f, d in rows]


def extras(conn: psycopg.Connection, limit: int) -> list[Filing]:
    """Stored documents of companies with their own tags or member sums, not read for the current
    extractor version (module docstring, 3)."""
    rows = conn.execute(
        """
        WITH curated AS (
            SELECT company_id FROM silver.metric_tags WHERE company_id IS NOT NULL
            UNION SELECT company_id FROM silver.member_sums
        )
        SELECT d.accession_no, d.cik, d.form, d.filed,
               NOT EXISTS (SELECT 1 FROM silver.financial_facts f
                           WHERE f.accession_no = d.accession_no AND f.source = 'companyfacts')
        FROM curated c
        JOIN silver.company_ciks k ON k.company_id = c.company_id
        JOIN bronze.filing_xbrl x ON x.cik = k.cik AND x.status = 'ok'
        JOIN bronze.filing_documents d ON d.accession_no = x.accession_no
        LEFT JOIN bronze.filing_facts b ON b.accession_no = d.accession_no
        WHERE b.accession_no IS NULL OR b.extractor_version <> %s
        ORDER BY d.filed, d.accession_no
        LIMIT %s
        """, (EXTRACTOR_VERSION, limit)).fetchall()
    return [Filing(a, c, f, d, g) for a, c, f, d, g in rows]


def named(conn: psycopg.Connection, accessions: Sequence[str]) -> list[Filing]:
    """These filings, with a filer CIK here when there is one (module docstring, 4)."""
    rows = conn.execute(
        """
        SELECT DISTINCT ON (a) a, z.cik, z.form, z.filing_date,
               NOT EXISTS (SELECT 1 FROM silver.financial_facts f
                           WHERE f.accession_no = a AND f.source = 'companyfacts')
        FROM unnest(%s::text[]) a
        JOIN bronze.submission_filings z ON z.accession_no = a
        ORDER BY a, EXISTS (SELECT 1 FROM silver.company_ciks k WHERE k.cik = z.cik) DESC, z.cik
        """, (list(accessions),)).fetchall()
    found = {r[0] for r in rows}
    for a in accessions:
        if a not in found:
            log.warning("%s: not in bronze.submission_filings, skipped", a)
    return [Filing(a, c, f, d, g) for a, c, f, d, g in rows]


def stored_extract(conn: psycopg.Connection,
                   accession_no: str) -> tuple[str, str | None, list[dict]] | None:
    """(document URL, entity CIK, every fact) of the stored document's extract."""
    row = conn.execute(
        """
        SELECT coalesce(d.url, d.document_name), x.entity_cik, x.raw_gzip
        FROM bronze.filing_xbrl x JOIN bronze.filing_documents d USING (accession_no)
        WHERE x.accession_no = %s AND x.status = 'ok' AND x.raw_gzip IS NOT NULL
        """, (accession_no,)).fetchone()
    if row is None:
        return None
    return row[0], row[1], decompress(bytes(row[2]))


def stored_instance(conn: psycopg.Connection, accession_no: str) -> tuple[str, bytes] | None:
    row = conn.execute(
        "SELECT url, content_gzip FROM bronze.filing_instances WHERE accession_no = %s",
        (accession_no,)).fetchone()
    return (row[0], gzip.decompress(bytes(row[1]))) if row else None


def store_instance(conn: psycopg.Connection, accession_no: str, cik: str, url: str,
                   content: bytes) -> None:
    conn.execute(
        """
        INSERT INTO bronze.filing_instances (accession_no, cik, url, content_gzip, content_sha256,
                                             size_bytes)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (accession_no) DO NOTHING
        """, (accession_no, cik, url, gzip.compress(content, mtime=0),
              hashlib.sha256(content).hexdigest(), len(content)))


def fact_cik(conn: psycopg.Connection, filing: Filing, entity_cik: str | None) -> str:
    """The CIK silver files the facts under: the XBRL's entity when it filed the filing and is a
    company here (050's rule), else the filer."""
    if entity_cik and entity_cik != filing.cik:
        ok = conn.execute(
            "SELECT gold.filed_by(%s, %s)"
            " AND EXISTS (SELECT 1 FROM silver.company_ciks WHERE cik = %s)",
            (filing.accession_no, entity_cik, entity_cik)).fetchone()
        if ok and ok[0]:
            return entity_cik
    return filing.cik


def company_of(conn: psycopg.Connection, cik: str) -> str | None:
    row = conn.execute("SELECT company_id FROM silver.company_ciks WHERE cik = %s",
                       (cik,)).fetchone()
    return row[0] if row else None


def store_rows(conn: psycopg.Connection, rows: Sequence[Row]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO bronze.filing_facts (accession_no, cik, form, filed, source, document, gap,
                                             facts, fact_count, extractor_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (accession_no) DO UPDATE SET
                cik = EXCLUDED.cik, form = EXCLUDED.form, filed = EXCLUDED.filed,
                source = EXCLUDED.source, document = EXCLUDED.document,
                gap = bronze.filing_facts.gap OR EXCLUDED.gap, facts = EXCLUDED.facts,
                fact_count = EXCLUDED.fact_count, extractor_version = EXCLUDED.extractor_version,
                projected_at = now()
            """,
            [(r.accession_no, r.cik, r.form, r.filed, r.source, r.document, r.gap,
              json.dumps(r.facts, separators=(",", ":")), r.fact_count, EXTRACTOR_VERSION)
             for r in rows])


def refresh(conn: psycopg.Connection, accessions: Sequence[str],
            ciks: Iterable[str]) -> tuple[int, int]:
    """Silver rows from these filings, then gold for every company their CIKs reach (050)."""
    silver = conn.execute("SELECT silver.refresh_filing_facts(%s)",
                          (list(accessions),)).fetchone()[0]  # type: ignore[index]
    companies = [r[0] for r in conn.execute(
        "SELECT DISTINCT c FROM gold.companies_for_ciks(%s::text[]) c ORDER BY 1",
        (sorted(set(ciks)),))]
    gold = 0
    if companies:
        gold = conn.execute("SELECT gold.refresh_financials(%s::text[])",
                            (companies,)).fetchone()[0]  # type: ignore[index]
    return silver, gold


# reading one filing ------------------------------------------------------------------------------

Fetch = Callable[[str, str], tuple[str, bytes] | None]


def read_filing(conn: psycopg.Connection, filing: Filing, m: Mapping, fetch: Fetch | None,
                stats: RunStats) -> Row | None:
    """The bronze.filing_facts row of one filing: its stored extract when that has the statements,
    else its instance (stored, or fetched when `fetch` is given)."""
    with conn.transaction():
        stored = stored_extract(conn, filing.accession_no)
    if stored is not None:
        document, entity, facts = stored
        with conn.transaction():
            cik = fact_cik(conn, filing, entity)
            company = company_of(conn, cik)
        projected = project(facts, company, m, filing.gap)
        if not filing.gap or statement_facts(projected, m) >= MIN_STATEMENT_FACTS:
            return Row(filing.accession_no, cik, filing.form, filing.filed, "ixbrl", document,
                       filing.gap, projected, len(facts))
    if filing.form and filing.form.endswith("/A") and stored is None:
        return None  # an amendment without a stored document: rarely statements, never fetched
    with conn.transaction():
        got = stored_instance(conn, filing.accession_no)
    if got is None and fetch is not None:
        got = fetch(filing.cik, filing.accession_no)
        if got is not None:
            stats.instances_fetched += 1
            with conn.transaction():
                store_instance(conn, filing.accession_no, filing.cik, got[0], got[1])
    if got is None:
        return None
    url, content = got
    ex = instance.extract_bytes(content)
    facts = [fact_dict(f) for f in ex.facts]
    with conn.transaction():
        cik = fact_cik(conn, filing, ex.entity_cik)
        company = company_of(conn, cik)
    return Row(filing.accession_no, cik, filing.form, filing.filed, "instance", url, filing.gap,
               project(facts, company, m, filing.gap), len(facts))


def load(conn: psycopg.Connection, filings: Sequence[Filing], m: Mapping, fetch: Fetch | None,
         stats: RunStats, batch_size: int = BATCH) -> None:
    batch: list[Row] = []

    def flush() -> None:
        if not batch:
            return
        with conn.transaction():
            store_rows(conn, batch)
            s, g = refresh(conn, [r.accession_no for r in batch], [r.cik for r in batch])
        stats.silver_rows += s
        stats.gold_rows += g
        stats.filings += len(batch)
        log.info("%d filings read (%d instances fetched), %d silver rows", stats.filings,
                 stats.instances_fetched, stats.silver_rows)
        batch.clear()

    for filing in filings:
        try:
            row = read_filing(conn, filing, m, fetch, stats)
        except (httpx.HTTPError, ValueError, SyntaxError) as e:  # one bad filing: not the run
            log.warning("%s: %s", filing.accession_no, e)
            row = None
        if row is None:
            stats.skipped.append(filing.accession_no)
            continue
        batch.append(row)
        if len(batch) >= batch_size:
            flush()
    flush()


def catch_up(conn: psycopg.Connection, stats: RunStats) -> None:
    """Module docstring, 1."""
    with conn.transaction():
        done = [r[0] for r in conn.execute(
            "SELECT a FROM silver.filing_facts_caught_up() a ORDER BY 1")]
    for i in range(0, len(done), BATCH):
        chunk = done[i:i + BATCH]
        with conn.transaction():
            ciks = [r[0] for r in conn.execute(
                "SELECT c FROM silver.supersede_filing_facts(%s) c ORDER BY 1", (chunk,))]
            conn.execute("SELECT silver.refresh_financial_facts(%s::text[])", (ciks,))
            _, gold = refresh(conn, chunk, ciks)
        stats.caught_up += len(chunk)
        stats.gold_rows += gold
    if done:
        log.info("%d filings now in companyfacts: their own facts left silver", len(done))


def rebuild(conn: psycopg.Connection, stats: RunStats) -> None:
    """Re-project every stored row from its source with the current tags and sums; no SEC."""
    m = mapping(conn)
    with conn.transaction():
        rows = conn.execute(
            "SELECT accession_no, cik, form, filed, gap AND superseded_at IS NULL"
            " FROM bronze.filing_facts ORDER BY filed, accession_no").fetchall()
    filings = [Filing(a, c, f, d, g) for a, c, f, d, g in rows]
    load(conn, filings, m, None, stats)


# job -----------------------------------------------------------------------------------------

def run(conn: psycopg.Connection, fetch: Fetch | None, since: date, after_days: int = AFTER_DAYS,
        limit: int = LIMIT, accessions: Sequence[str] = (),
        stats: RunStats | None = None) -> RunStats:
    stats = stats or RunStats()
    catch_up(conn, stats)
    with conn.transaction():
        m = mapping(conn)
        todo = named(conn, accessions) if accessions else []
        todo += gaps(conn, since, after_days, limit)
        todo += extras(conn, limit)
    seen: set[str] = set()
    unique = [f for f in todo if not (f.accession_no in seen or seen.add(f.accession_no))]
    load(conn, unique, m, fetch, stats)
    return stats


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", type=date.fromisoformat,
                        default=os.environ.get("FILING_FACTS_SINCE") or None,
                        help=f"oldest filing date for gaps (default: {SINCE_DAYS} days back)")
    parser.add_argument("--after-days", type=int,
                        default=int(os.environ.get("FILING_FACTS_AFTER_DAYS", AFTER_DAYS)),
                        help=f"a gap is a filing at least this old (default {AFTER_DAYS})")
    parser.add_argument("--limit", type=int,
                        default=int(os.environ.get("FILING_FACTS_LIMIT", LIMIT)),
                        help=f"gap and extra filings per run, each (default {LIMIT})")
    parser.add_argument("--accessions", default=os.environ.get("FILING_FACTS_ACCESSIONS", ""),
                        help="comma-separated filings to read whatever they are")
    parser.add_argument("--rebuild", action="store_true",
                        help="re-project every stored row from its source; no SEC requests")
    args = parser.parse_args(None if argv is None else list(argv))
    configure_logging()
    since = args.since or date.fromordinal(date.today().toordinal() - SINCE_DAYS)
    started = time.monotonic()
    stats = RunStats()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        if args.rebuild:
            rebuild(conn, stats)
        else:
            with sec_client(os.environ["SEC_USER_AGENT"]) as client:
                def fetch(cik: str, accession_no: str) -> tuple[str, bytes] | None:
                    return fetch_instance(client, cik, accession_no, stats)
                accessions = [a.strip() for a in args.accessions.split(",") if a.strip()]
                run(conn, fetch, since, args.after_days, args.limit, accessions, stats)
    log.info("%d filings read, %d instances fetched (%d SEC requests), %d caught up,"
             " %d silver rows,"
             " %d gold rows, %d skipped in %.0fs", stats.filings, stats.instances_fetched,
             stats.sec_requests, stats.caught_up, stats.silver_rows, stats.gold_rows,
             len(stats.skipped), time.monotonic() - started)
    if stats.skipped:
        log.info("skipped (no XBRL found): %s", ", ".join(stats.skipped[:50]))


if __name__ == "__main__":
    main()
