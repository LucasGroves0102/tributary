"""The coverage sweep (migration 076): every exchange-listed company measured for every kind of
gap (no figures, figures behind the latest report, history cut short, a core metric missing
lately, a re-registration not linked, documents missing, statements in an exhibit), each with its
cause and a reason: `fixable` (Tributary's gap), `not_in_sec_data`, `out_of_scope`.
ops.refresh_coverage rebuilds ops.coverage (a gap keeps its first_seen while it lasts); the
nightly financials-bulk job runs it after the extremes report (refresh_quietly). ops.coverage_report
names companies: ops_reader and this CLI only, never public; the status page reads counts.

    make coverage                      # counts per kind, cause and reason
    make coverage KIND=history_short   # that kind's companies (REASON=fixable narrows)
    make coverage REFRESH=1            # recompute first (a minute or two on production)
    make coverage-sample               # this month's 25 random listed companies, as a checklist

The sample is the monthly audit: 25 listed companies picked by a hash of their id and the month
(the same 25 all month, new ones next month), each with what Tributary shows (the latest figures,
their periods and filings, the coverage gaps) and links to its filings on EDGAR, for a person or a
tester to compare with the filings. Nothing here calls a model or the SEC.
"""

import argparse
import json
import logging
import os
import time
from collections.abc import Iterable
from datetime import date

import psycopg
from psycopg.rows import dict_row

from facts_fetcher.extremes import figure

log = logging.getLogger("facts_fetcher.coverage")

SAMPLE = 25
EDGAR = "https://www.sec.gov"
# what the sample shows of each company: TTM flows and the latest balance sheet
SAMPLE_METRICS = ("revenue", "net_income", "operating_cash_flow", "total_assets",
                  "stockholders_equity")


def refresh(conn: psycopg.Connection) -> int:
    """Rebuild ops.coverage in one transaction; returns the gaps open after it."""
    with conn.transaction():
        row = conn.execute("SELECT ops.refresh_coverage()").fetchone()
    return int(row[0]) if row else 0


def refresh_quietly(conn: psycopg.Connection) -> None:
    """refresh() for a nightly job whose own work is done: a failure is logged, never the job's,
    unless the connection broke."""
    started = time.monotonic()
    try:
        gaps = refresh(conn)
    except psycopg.Error:
        if conn.broken:
            raise
        log.exception("coverage report not refreshed; the next night tries again")
        return
    log.info("coverage report refreshed: %d gaps in %.0fs", gaps, time.monotonic() - started)


def summary(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT kind, cause, reason, companies, first_seen, seen_at"
                    " FROM ops.coverage_summary ORDER BY kind, companies DESC, cause")
        return cur.fetchall()


def rows(conn: psycopg.Connection, kind: str, reason: str | None = None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT company_id, ticker, name, kind, cause, reason, detail, first_seen"
            " FROM ops.coverage_report"
            " WHERE kind = %(kind)s AND (%(reason)s::text IS NULL OR reason = %(reason)s)"
            " ORDER BY cause, ticker NULLS LAST, name",
            {"kind": kind, "reason": reason})
        return cur.fetchall()


def render_summary(found: Iterable[dict]) -> str:
    lines, total = [], {}
    for r in found:
        lines.append(f"{r['kind']:<20} {r['cause']:<20} {r['reason']:<16} {r['companies']:>6}")
        total[r["kind"]] = total.get(r["kind"], 0) + r["companies"]
    lines.append("")
    lines += [f"{k:<20} {'(all)':<20} {'':<16} {n:>6}" for k, n in sorted(total.items())]
    return "\n".join(lines)


def render_rows(found: Iterable[dict]) -> str:
    return "\n".join(
        f"{(r['ticker'] or ''):<8} {(r['name'] or '')[:36]:<36} {r['cause']:<20} {r['reason']:<16}"
        f" {r['first_seen']:%Y-%m-%d}  {json.dumps(r['detail'], default=str, sort_keys=True)}"
        for r in found)


# The monthly audit ----------------------------------------------------------------------------

def sample_companies(conn: psycopg.Connection, month: str, n: int = SAMPLE) -> list[dict]:
    """n listed companies picked by md5(company id, month): the same all month, new ones next."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT c.company_id, c.name, c.tickers[1] AS ticker, c.current_cik, c.sic,"
            "       c.sic_description"
            " FROM silver.companies c"
            " WHERE c.exchanges && ARRAY['Nasdaq', 'NYSE', 'CBOE', 'TXSE']::text[]"
            " ORDER BY md5(c.company_id || %s) LIMIT %s", (month, n))
        return cur.fetchall()


def shown(conn: psycopg.Connection, company_id: str) -> dict:
    """What Tributary shows of one company: its latest TTM flows and balance sheet (with period
    and filing), its latest 10-K/10-Q/20-F/40-F, and its coverage gaps."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT m.metric, x.span, x.period_end, x.value, x.unit, x.accession_no
            FROM unnest(%(metrics)s::text[]) m(metric)
            LEFT JOIN LATERAL (
                SELECT 'ttm' AS span, d.period_end, d.value, d.unit, d.accession_no
                FROM gold.derived_financials d
                WHERE d.company_id = %(c)s AND d.span = 'ttm' AND d.metric = m.metric
                UNION ALL
                SELECT 'quarter', q.period_end, q.value, q.unit, q.accession_no
                FROM gold.quarterly_financials q
                WHERE q.company_id = %(c)s AND q.metric = m.metric AND q.period_start IS NULL
                ORDER BY 2 DESC LIMIT 1) x ON true
            """, {"c": company_id, "metrics": list(SAMPLE_METRICS)})
        figures = cur.fetchall()
        cur.execute(
            "SELECT f.form, f.filing_date, f.report_date, f.accession_no, f.cik,"
            "       f.primary_document"
            " FROM silver.company_ciks k JOIN bronze.submission_filings f ON f.cik = k.cik"
            " WHERE k.company_id = %s AND f.form IN ('10-K', '10-Q', '20-F', '40-F')"
            " ORDER BY f.filing_date DESC, f.accession_no DESC LIMIT 2", (company_id,))
        filings = cur.fetchall()
        cur.execute("SELECT kind, cause, reason, detail FROM ops.coverage"
                    " WHERE company_id = %s ORDER BY kind", (company_id,))
        gaps = cur.fetchall()
    return {"figures": figures, "filings": filings, "gaps": gaps}


def filing_url(cik: str, accession_no: str, document: str | None) -> str:
    folder = f"{EDGAR}/Archives/edgar/data/{int(cik)}/{accession_no.replace('-', '')}"
    return f"{folder}/{document}" if document else f"{folder}/{accession_no}-index.htm"


def checklist(month: str, companies: list[dict], seen: dict[str, dict]) -> str:
    """The audit as Markdown: one section per company, a box per thing to compare."""
    out = [f"# Coverage audit {month}", "",
           f"{len(companies)} listed companies picked at random for {month}. For each, open its "
           "filings and tick what matches; note what doesn't (a wrong figure, a gap the coverage "
           "notes don't explain, a gap that isn't real).", ""]
    for i, c in enumerate(companies, 1):
        s = seen[c["company_id"]]
        out.append(f"## {i}. {c['name']} ({c['ticker'] or 'no ticker'}, company {c['company_id']})")
        out.append(f"SIC {c['sic'] or '-'} {c['sic_description'] or ''}. EDGAR: "
                   f"{EDGAR}/cgi-bin/browse-edgar?action=getcompany&CIK={c['current_cik']}"
                   "&type=10-&dateb=&owner=include&count=40")
        out.append("")
        for f in s["filings"]:
            url = filing_url(f["cik"], f["accession_no"], f["primary_document"])
            out.append(f"- Latest filing: {f['form']} for {f['report_date'] or '-'}, filed "
                       f"{f['filing_date']}: {url}")
        figures = [f for f in s["figures"] if f["value"] is not None]
        if not figures:
            out.append("- [ ] Tributary shows no figures: is that right for what it files?")
        for f in figures:
            what = "TTM to" if f["span"] == "ttm" else "at"
            shown_as = figure(f["value"], f["unit"])
            out.append(f"- [ ] {f['metric']} {what} {f['period_end']}: {shown_as}"
                       f" (filing {f['accession_no'] or '-'})")
        for g in s["gaps"]:
            detail = json.dumps(g["detail"], default=str, sort_keys=True)
            out.append(f"- [ ] Coverage note {g['kind']} / {g['cause']} ({g['reason']}): {detail}"
                       " is true?")
        if not s["gaps"]:
            out.append("- [ ] No coverage gap noted: nothing missing from the figures?")
        out.append("")
    return "\n".join(out)


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="recompute the report first")
    parser.add_argument("--kind", help="one kind's companies, e.g. history_short")
    parser.add_argument("--reason", choices=("fixable", "not_in_sec_data", "out_of_scope"))
    parser.add_argument("--sample", action="store_true", help="the monthly audit checklist")
    parser.add_argument("--month", default=date.today().strftime("%Y-%m"),
                        help="the audit's month, YYYY-MM (default: this month)")
    parser.add_argument("--out", help="write the checklist here (default: stdout)")
    args = parser.parse_args(None if argv is None else list(argv))
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        if args.refresh:
            log.info("%d gaps", refresh(conn))
        if args.sample:
            companies = sample_companies(conn, args.month)
            text = checklist(args.month, companies,
                             {c["company_id"]: shown(conn, c["company_id"]) for c in companies})
            if args.out:
                with open(args.out, "w") as f:
                    f.write(text + "\n")
                log.info("wrote %s", args.out)
            else:
                print(text)
            return
        if args.kind:
            print(render_rows(rows(conn, args.kind, args.reason)) or "no gaps of that kind")
            return
        found = summary(conn)
    print(render_summary(found) if found else "no report yet: run with REFRESH=1")


if __name__ == "__main__":
    main()
