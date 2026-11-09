"""8-K events and earnings releases: `/v1/companies/{id}/events` (what the company filed under
Form 8-K since its latest 10-Q/10-K period end) and `/v1/companies/{id}/earnings` (each quarter's
earnings release, Item 2.02, with the outlook and adjustments its text states). Both need the
`filings` tier: OpenFGA first (`require`), then Store under RLS, like every route (app.py). The
8-K list and its items are filing-index metadata (migration 045); the excerpts and passages are
section text (RLS on silver.filing_sections).

Rules (the web app and the agent only word what these return):

Items. An 8-K's items come from the SEC's submissions history (silver.company_8ks); until the
nightly load has a new filing, from the item headings the parser found in it (its sections keyed
"2.02"); `items_from` says which ("index", "parsed", or null when neither has any). ITEMS gives
each its SEC title and a kind. AFFECTS: the balance-sheet figures an item can change after a
quarter end: 2.01 a completed acquisition or disposition (cash, debt, net debt, total assets),
2.03 new debt (debt, net debt, debt to equity, cash), 2.04 accelerated debt (debt, net debt, debt
to equity), 2.06 an impairment (total assets, equity, debt to equity), 3.02 an unregistered equity
sale (shares outstanding, equity), 1.03 bankruptcy and 4.02 non-reliance on past statements
(every figure: "*"). Only an original 8-K whose event date (its date of report, else its filing
date) is after the period end counts: an amendment re-reports an earlier event, and an event
dated on or before the period end is in that balance sheet already. A filing whose items are
all ROUTINE (5.07 votes, 5.08 nominations, 9.01 exhibits alone) is listed as not material.

Announced (F1b). Such an 8-K with item 7.01 or 8.01 (and not 2.02, an earnings release) whose
press release or item text announces a completed acquisition or disposition flags 2.01's
figures, and one announcing a new financing (notes priced or issued, a credit facility entered,
a term loan drawn) flags 2.03's, unless the 8-K has that item itself. The words are read by
query_api.announcements (its docstring is the spec); `announced` lists what was read: the kind,
the item, the words that matched and their sentence with a citation.

Since the last report. `since` is the period end of the company's latest original 10-K or 10-Q
(10-KT/10-QT too) in the filing index; events are its 8-K and 8-K/A filed after it, newest first,
at most MAX_EVENTS. With no such report, or one more than RECENT_DAYS old, the window is the last
RECENT_DAYS days and `window` says "recent". `stale` lists each affected metric once, as of that
period end, with the events that may have changed it: a flag, never a recomputed figure. Each
event's excerpt is the opening of the section of its first item in EXCERPT_ORDER that the parser
found (the 8-K's own words, "On October 27, 2026, Vertiv completed ...", after the item's heading
and title lines: `opening`; an item that only points to another, "The information set forth in
Item 1.01 is incorporated by reference", gives way to the next), cut at a sentence end within
EXCERPT_CHARS; `release` names its lowest-numbered EX-99 exhibit (the press release).

Earnings releases. Period ends: the company's original 10-K/10-Q period ends, plus up to two
estimated quarter ends after the latest (gold.expected_filings' rule: + 3 months, snapped to the
month end when the period ended on one, else + 91 days), marked `estimated` until a report
covers them. An original 8-K with item 2.02 reports the latest period end before its filing date,
if at most RELEASE_WINDOW_DAYS earlier. A quarter's release is the last of its 2.02 8-Ks filed on
or before its report's filing date + 1 day (a pre-announcement comes first, the full release
last), else the latest; the others are listed as `other_releases`. Its text is the lowest-numbered
EX-99 exhibit (EX-99.1, EX-99.01), else the 8-K's "2.02" section; headline, outlook and
adjustments come from query_api.releases. Quarters: the latest `quarters` period ends that have a
report or a release, newest first. No beat or miss: Tributary holds no estimates.
"""

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Path, Query

from query_api import announcements
from query_api.releases import read_release
from query_api.shapes import document_url, filing_url
from query_api.store import Store

CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]

ITEMS: dict[str, tuple[str, str]] = {
    "1.01": ("Entry into a Material Definitive Agreement", "agreement"),
    "1.02": ("Termination of a Material Definitive Agreement", "agreement"),
    "1.03": ("Bankruptcy or Receivership", "bankruptcy"),
    "1.04": ("Mine Safety - Reporting of Shutdowns and Patterns of Violations", "other"),
    "1.05": ("Material Cybersecurity Incidents", "cybersecurity"),
    "2.01": ("Completion of Acquisition or Disposition of Assets", "deal"),
    "2.02": ("Results of Operations and Financial Condition", "earnings"),
    "2.03": ("Creation of a Direct Financial Obligation or an Obligation under an Off-Balance "
             "Sheet Arrangement of a Registrant", "debt"),
    "2.04": ("Triggering Events That Accelerate or Increase a Direct Financial Obligation or an "
             "Obligation under an Off-Balance Sheet Arrangement", "debt"),
    "2.05": ("Costs Associated with Exit or Disposal Activities", "restructuring"),
    "2.06": ("Material Impairments", "impairment"),
    "3.01": ("Notice of Delisting or Failure to Satisfy a Continued Listing Rule or Standard; "
             "Transfer of Listing", "listing"),
    "3.02": ("Unregistered Sales of Equity Securities", "equity"),
    "3.03": ("Material Modification to Rights of Security Holders", "equity"),
    "4.01": ("Changes in Registrant's Certifying Accountant", "auditor"),
    "4.02": ("Non-Reliance on Previously Issued Financial Statements or a Related Audit Report or "
             "Completed Interim Review", "non_reliance"),
    "5.01": ("Changes in Control of Registrant", "control"),
    "5.02": ("Departure of Directors or Certain Officers; Election of Directors; Appointment of "
             "Certain Officers; Compensatory Arrangements of Certain Officers", "leadership"),
    "5.03": ("Amendments to Articles of Incorporation or Bylaws; Change in Fiscal Year",
             "governance"),
    "5.04": ("Temporary Suspension of Trading Under Registrant's Employee Benefit Plans",
             "governance"),
    "5.05": ("Amendments to the Registrant's Code of Ethics, or Waiver of a Provision of the Code "
             "of Ethics", "governance"),
    "5.06": ("Change in Shell Company Status", "other"),
    "5.07": ("Submission of Matters to a Vote of Security Holders", "vote"),
    "5.08": ("Shareholder Director Nominations", "governance"),
    "6.01": ("ABS Informational and Computational Material", "other"),
    "6.02": ("Change of Servicer or Trustee", "other"),
    "6.03": ("Change in Credit Enhancement or Other External Support", "other"),
    "6.04": ("Failure to Make a Required Distribution", "other"),
    "6.05": ("Securities Act Updating Disclosure", "other"),
    "7.01": ("Regulation FD Disclosure", "disclosure"),
    "8.01": ("Other Events", "other_events"),
    "9.01": ("Financial Statements and Exhibits", "exhibits"),
}
EVERY_FIGURE = "*"
AFFECTS: dict[str, tuple[str, ...]] = {
    "1.03": (EVERY_FIGURE,),
    "2.01": ("cash", "long_term_debt", "net_debt", "total_assets"),
    "2.03": ("long_term_debt", "net_debt", "debt_to_equity", "cash"),
    "2.04": ("long_term_debt", "net_debt", "debt_to_equity"),
    "2.06": ("total_assets", "stockholders_equity", "debt_to_equity"),
    "3.02": ("shares_outstanding", "stockholders_equity"),
    "4.02": (EVERY_FIGURE,),
}
ROUTINE = frozenset({"5.07", "5.08", "9.01"})
# the item an event's excerpt quotes: the first of these it has
EXCERPT_ORDER = ("4.02", "1.03", "2.01", "2.03", "2.04", "2.06", "1.01", "1.02", "2.05", "5.01",
                 "5.02", "3.01", "3.02", "3.03", "4.01", "1.05", "2.02", "7.01", "8.01", "5.03",
                 "5.04", "5.05", "5.06", "5.07", "5.08", "1.04")
ITEM = re.compile(r"^\d\.\d\d$")
ITEM_HEADING = re.compile(r"^item\s+\d\.\d\d\b", re.I)
CROSS_REFERENCE = re.compile(
    r"^(?:the )?(?:information|disclosures?)\s+(?:set forth|contained|included|provided|described)"
    r"\b.{0,200}?\bincorporated\b", re.I | re.S)
EXHIBIT_99 = re.compile(r"^EX-99(?:\.(\d{1,2}))?$")
PERIODIC = ("10-K", "10-Q", "10-KT", "10-QT")
MAX_EVENTS = 25
RECENT_DAYS = 400
RELEASE_WINDOW_DAYS = 120
EXCERPT_CHARS = 400
HEAD_CHARS = 1200  # of each section read for excerpts (the full text only for a release)
MAX_QUARTERS = 8
METHOD_EVENTS = (
    "8-K and 8-K/A filings from the SEC filing index filed after the period end of the latest "
    "10-K or 10-Q, newest first, with their items (the SEC's submissions history; a filing too "
    "new for it shows the items the parser found). Figures as of that period end that an event "
    "after it may have changed are listed in `stale`: completed acquisitions or dispositions "
    "(2.01), new or accelerated debt (2.03, 2.04), impairments (2.06), unregistered equity sales "
    "(3.02), and bankruptcy or non-reliance on past statements (1.03, 4.02: every figure). An "
    "8-K under Item 7.01 or 8.01 whose press release or text says an acquisition or disposition "
    "was completed, or notes were priced or issued, a credit facility entered or a term loan "
    "drawn, flags the same figures as 2.01 or 2.03; `announced` quotes the words. A flag, not a "
    "recomputed figure.")
METHOD_EARNINGS = (
    "Each quarter's earnings release is the 8-K with Item 2.02 filed after that quarter's end "
    f"(within {RELEASE_WINDOW_DAYS} days, before the next quarter ends), the last one on or "
    "before the 10-Q/10-K for the quarter when there are several; its text is the first EX-99 "
    "exhibit. Outlook passages are the blocks under the release's own Outlook or Guidance "
    "headings; adjustments are its reconciliation tables and the notes under its figures that "
    "state a dollar amount. Quoted as written, found by fixed rules: a release without such a "
    "heading or table shows none. No beat or miss: Tributary holds no estimates.")


# --- items and events ------------------------------------------------------------------------

def items_of(row: dict) -> tuple[list[str], str | None]:
    """An 8-K's items and where they come from: the filing index, else the parsed sections."""
    index = [i for i in (row.get("items") or []) if i]
    if index:
        return index, "index"
    parsed = sorted(k for k in (row.get("parsed_items") or []) if ITEM.match(k))
    return (parsed, "parsed") if parsed else ([], None)


def item_info(code: str) -> dict:
    title, kind = ITEMS.get(code, (f"Item {code}", "other"))
    return {"item": code, "title": title, "kind": kind}


def material(items: list[str]) -> bool:
    """False only when every item is routine (a vote, nominations, exhibits alone)."""
    return any(i not in ROUTINE for i in items) or not items


def affects(items: list[str], form: str, event_date: date, period_end: date | None) -> list[str]:
    """AFFECTS of an original 8-K whose event comes after the period end."""
    if form != "8-K" or period_end is None or event_date <= period_end:
        return []
    out: list[str] = []
    for i in items:
        for m in AFFECTS.get(i, ()):
            if m not in out:
                out.append(m)
    return [EVERY_FIGURE] if EVERY_FIGURE in out else out


def announced(items: list[str], form: str, event_date: date, period_end: date | None,
              sections: list[dict]) -> list[announcements.Announcement]:
    """What an original 8-K after the period end announces under 7.01 / 8.01 (F1b), read from
    its sections' heads."""
    if form != "8-K" or period_end is None or event_date <= period_end:
        return []
    return announcements.read(items, {s["section_key"]: s["head"] for s in sections})


def with_announced(hit: list[str], found: list[announcements.Announcement]) -> list[str]:
    """`hit` plus the figures of the item each announcement stands for (2.01, 2.03)."""
    out = list(hit)
    for a in found:
        for m in AFFECTS[announcements.ALREADY[a.kind]]:
            if m not in out:
                out.append(m)
    return [EVERY_FIGURE] if EVERY_FIGURE in out else out


def exhibit_rank(key: str) -> tuple[int, int] | None:
    """EX-99 → (0, 0), EX-99.1 / EX-99.01 → (0, 1), ...; None for other sections."""
    m = EXHIBIT_99.match(key)
    return (0, int(m.group(1) or 0)) if m else None


def release_section(keys: list[str]) -> str | None:
    """The press release of an 8-K: its lowest-numbered EX-99 exhibit, else its "2.02" item."""
    ranked = sorted((r, k) for k in keys if (r := exhibit_rank(k)) is not None)
    if ranked:
        return ranked[0][1]
    return "2.02" if "2.02" in keys else None


def opening(head: str, item: str) -> int:
    """Where an item's own words start in its section: after its heading line ("Item 2.03" or
    "Item 2.02 Results of Operations and Financial Condition.", up to 250 characters) and up to 3
    title lines after it (one that starts like the item's SEC title, or of up to 30 characters, or
    up to 80 not ending in "."), never the last line read (prose cut short). 0 when the section
    doesn't start with an Item heading."""
    lines = head.split("\n")
    if not lines or len(lines[0]) > 250 or not ITEM_HEADING.match(lines[0]):
        return 0
    title = ITEMS.get(item, ("",))[0].lower()[:20]
    at = len(lines[0]) + 1
    for ln in lines[1:min(4, len(lines) - 1)]:  # the last line may be prose cut short
        bare = ln.strip().rstrip("*")
        if len(ln) <= 160 and ((title and bare.lower().startswith(title)) or len(bare) <= 30
                               or (len(bare) <= 80 and not bare.endswith("."))):
            at += len(ln) + 1
        else:
            break
    return at if at < len(head) else 0


def excerpt_section(items: list[str], sections: list[dict]) -> tuple[dict | None, int]:
    """The section an event's excerpt quotes and where its own words start: the first of its items
    in EXCERPT_ORDER (else any item section) with words that aren't only a cross-reference to
    another item ("The information set forth in Item 1.01 is incorporated by reference"), else
    the first such section anyway."""
    by_key = {s["section_key"]: s for s in sections if s["head"].strip()}
    keys = [i for i in EXCERPT_ORDER if i in items and i in by_key]
    keys += [k for k in by_key if ITEM.match(k) and k not in keys]
    found = [(by_key[k], opening(by_key[k]["head"], k)) for k in keys]
    for s, start in found:
        if not CROSS_REFERENCE.match(s["head"][start:].lstrip()):
            return s, start
    return found[0] if found else (None, 0)


def excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    """The opening of a section, whitespace collapsed, cut at a sentence end (else a word)."""
    t = " ".join(text.split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    end = max(cut.rfind(". "), cut.rfind(".” "))
    if end > limit // 3:
        return cut[:end + 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip(" ,;:") + " …"


def latest_report(reports: list[dict]) -> dict | None:
    """The original 10-K/10-Q with the latest period end (the first filed for it)."""
    usable = [r for r in reports if r["form"] in PERIODIC and r.get("period_end")]
    if not usable:
        return None
    return min(usable, key=lambda r: (-r["period_end"].toordinal(), r["filing_date"],
                                      r["accession_no"]))


def _citation(company_id: str, cik: str | None, row: dict, section: dict | None,
              start: int = 0, end: int | None = None) -> dict:
    out = {"accession_no": row["accession_no"], "form": row["form"],
           "filed": row["filing_date"], "filing_url": filing_url(cik, row["accession_no"])}
    if section is not None:
        base = section.get("char_start") or 0
        out |= {"section_key": section["section_key"], "document_url": section["document_url"],
                "char_start": base + start,
                "char_end": base + (end if end is not None else section["chars"])}
    return out


def shape_events(company_id: str, report: dict | None, since: date, window: str,
                 rows: list[dict], sections: dict[str, list[dict]]) -> dict:
    """The /events answer from the 8-K rows (newest first) and their sections' heads."""
    period_end = report["period_end"] if report and window == "since_report" else None
    events, stale = [], {}
    for r in rows[:MAX_EVENTS]:
        items, source = items_of(r)
        secs = sections.get(r["accession_no"], [])
        by_key = {s["section_key"]: s for s in secs}
        event_date = r.get("report_date") or r["filing_date"]
        found = announced(items, r["form"], event_date, period_end, secs)
        hit = with_announced(affects(items, r["form"], event_date, period_end), found)
        quoted, start = excerpt_section(items, secs)
        rel = release_section(list(by_key))
        events.append({
            "accession_no": r["accession_no"], "form": r["form"], "filed": r["filing_date"],
            "accepted_at": r.get("accepted_at"), "event_date": event_date,
            "items": [item_info(i) for i in items], "items_from": source,
            "material": material(items), "amendment": r["form"] != "8-K", "affects": hit,
            "announced": [a.to_dict() | {"citation": _citation(company_id, r.get("cik"), r,
                                                               by_key[a.section_key], a.start,
                                                               a.end)} for a in found],
            "sections_available": bool(secs),
            "excerpt": {"item": quoted["section_key"], "text": excerpt(quoted["head"][start:]),
                        "citation": _citation(company_id, r.get("cik"), r, quoted, start,
                                              len(quoted["head"]))}
            if quoted else None,
            "release": {"section_key": rel, "title": by_key[rel].get("title")} if rel else None,
            "filing_url": filing_url(r.get("cik"), r["accession_no"]),
            "document_url": _document_url(r),
        })
        for m in hit:
            stale.setdefault(m, []).append(r["accession_no"])
    return {
        "company_id": company_id, "as_of": _now(),
        "latest_report": {"accession_no": report["accession_no"], "form": report["form"],
                          "filed": report["filing_date"], "period_end": report["period_end"]}
        if report else None,
        "since": since, "window": window, "events": events,
        "more": len(rows) > MAX_EVENTS,
        "stale": [{"metric": m, "as_of": period_end, "events": accs}
                  for m, accs in stale.items()],
        "method": METHOD_EVENTS,
    }


def _document_url(r: dict) -> str | None:
    return document_url(r.get("cik"), r["accession_no"], r.get("primary_document"))


# --- earnings releases ----------------------------------------------------------------------

def next_quarter_end(d: date) -> date:
    """gold.expected_filings' next period end: + 3 months on a month end, else + 91 days."""
    if (d + timedelta(days=1)).day == 1:
        y, m = divmod(d.month - 1 + 3, 12)
        first_after = date(d.year + y, m + 1, 1)
        y2, m2 = divmod(first_after.month, 12)
        return date(first_after.year + y2, m2 + 1, 1) - timedelta(days=1)
    return d + timedelta(days=91)


def periods(reports: list[dict], releases: list[dict]) -> list[dict]:
    """Period ends newest first: {period_end, report (or None), estimated}. Estimated quarter
    ends follow the latest reported one while a release was filed after them (at most two)."""
    by_end: dict[date, dict] = {}
    for r in sorted((r for r in reports if r["form"] in PERIODIC and r.get("period_end")),
                    key=lambda r: (r["filing_date"], r["accession_no"])):
        by_end.setdefault(r["period_end"], r)
    out = [{"period_end": end, "report": rep, "estimated": False}
           for end, rep in sorted(by_end.items(), reverse=True)]
    if out:
        newest_release = max((r["filing_date"] for r in releases), default=None)
        end = out[0]["period_end"]
        for _ in range(2):
            end = next_quarter_end(end)
            if newest_release is None or newest_release <= end:
                break
            out.insert(0, {"period_end": end, "report": None, "estimated": True})
    return out


def assign_releases(quarters: list[dict], releases: list[dict]) -> None:
    """Each 2.02 8-K to the latest period end before its filing date (within
    RELEASE_WINDOW_DAYS); then each quarter's `release` and `other_releases`."""
    ends = sorted((q["period_end"] for q in quarters), reverse=True)
    mapped: dict[date, list[dict]] = {}
    for r in releases:
        end = next((e for e in ends if e < r["filing_date"]), None)
        if end is not None and (r["filing_date"] - end).days <= RELEASE_WINDOW_DAYS:
            mapped.setdefault(end, []).append(r)
    for q in quarters:
        found = sorted(mapped.get(q["period_end"], []),
                       key=lambda r: (r["filing_date"], str(r.get("accepted_at") or ""),
                                      r["accession_no"]))
        report = q["report"]
        before = [r for r in found
                  if report and r["filing_date"] <= report["filing_date"] + timedelta(days=1)]
        chosen = (before or found)[-1] if found else None
        q["release_row"] = chosen
        q["others"] = [r for r in found if r is not chosen]


def shape_quarter(company_id: str, q: dict, sections: dict[str, list[dict]],
                  texts: dict[tuple[str, str], str]) -> dict:
    report, row = q["report"], q.get("release_row")
    out = {
        "period_end": q["period_end"], "estimated": q["estimated"],
        "report": {"accession_no": report["accession_no"], "form": report["form"],
                   "filed": report["filing_date"]} if report else None,
        "release": None, "headline": None, "outlook": [], "adjustments": [],
        "other_releases": [{"accession_no": r["accession_no"], "filed": r["filing_date"],
                            "filing_url": filing_url(r.get("cik"), r["accession_no"])}
                           for r in q.get("others", [])],
    }
    if row is None:
        return out
    items, _ = items_of(row)
    secs = {s["section_key"]: s for s in sections.get(row["accession_no"], [])}
    key = release_section(list(secs))
    section = secs.get(key) if key else None
    out["release"] = {
        "accession_no": row["accession_no"], "form": row["form"], "filed": row["filing_date"],
        "accepted_at": row.get("accepted_at"), "items": [item_info(i) for i in items],
        "section_key": key, "title": section.get("title") if section else None,
        "total_chars": section["chars"] if section else None,
        "document_url": section["document_url"] if section else _document_url(row),
        "filing_url": filing_url(row.get("cik"), row["accession_no"]),
    }
    text = texts.get((row["accession_no"], key)) if key else None
    if section is None or text is None:
        return out
    reading = read_release(text)

    def cite(p) -> dict:
        return {**p.to_dict(),
                "citation": _citation(company_id, row.get("cik"), row, section, p.start, p.end)}

    out["headline"] = cite(reading.headline) if reading.headline else None
    out["outlook"] = [cite(p) for p in reading.outlook]
    out["adjustments"] = [cite(p) for p in reading.adjustments]
    return out


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --- routes ---------------------------------------------------------------------------------

def add_event_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                     require: Callable[[str, str, str], None],
                     today: Callable[[], date]) -> None:
    User = Annotated[str, Depends(current_user)]

    @app.get("/v1/companies/{company_id}/events", response_model=dict)
    def events(user_id: User, company_id: CompanyId) -> dict:
        """8-K filings since the latest 10-Q/10-K period end, each with its items, an excerpt of
        the 8-K's own words and the figures at that period end it may have changed (`stale`).
        Rules: query_api/events.py."""
        require(user_id, "filings", company_id)
        report = latest_report(store.periodic_reports(user_id, company_id))
        now = today()
        recent = now - timedelta(days=RECENT_DAYS)
        if report is not None and report["period_end"] >= recent:
            since, window = report["period_end"], "since_report"
        else:
            since, window = recent, "recent"
        rows = store.company_8ks(user_id, company_id, since + timedelta(days=1))
        heads = store.section_heads(user_id, company_id,
                                    [r["accession_no"] for r in rows[:MAX_EVENTS]], HEAD_CHARS)
        return shape_events(company_id, report, since, window, rows, heads)

    @app.get("/v1/companies/{company_id}/earnings", response_model=dict)
    def earnings(
        user_id: User, company_id: CompanyId,
        quarters: Annotated[int, Query(ge=1, le=MAX_QUARTERS)] = 4,
    ) -> dict:
        """Each quarter's earnings release (8-K Item 2.02), newest first: the release, its
        headline, the outlook it states and management's adjustments, each passage quoted with
        its offsets in the release. No beat or miss. Rules: query_api/events.py, releases.py."""
        require(user_id, "filings", company_id)
        reports = store.periodic_reports(user_id, company_id)
        since = today() - timedelta(days=quarters * 92 + RELEASE_WINDOW_DAYS + 31)
        rows = store.company_8ks(user_id, company_id, since)
        releases = [r for r in rows if r["form"] == "8-K" and "2.02" in items_of(r)[0]]
        found = periods(reports, releases)
        assign_releases(found, releases)
        shown = [q for q in found if q["report"] or q.get("release_row")][:quarters]
        chosen = [q["release_row"] for q in shown if q.get("release_row")]
        sections = store.section_heads(user_id, company_id,
                                       [r["accession_no"] for r in chosen], 0) if chosen else {}
        wanted = []
        for r in chosen:
            key = release_section([s["section_key"] for s in sections.get(r["accession_no"], [])])
            if key:
                wanted.append((r["accession_no"], key))
        texts = store.section_texts(user_id, company_id, wanted) if wanted else {}
        return {"company_id": company_id, "as_of": _now(),
                "quarters": [shape_quarter(company_id, q, sections, texts) for q in shown],
                "method": METHOD_EARNINGS}
