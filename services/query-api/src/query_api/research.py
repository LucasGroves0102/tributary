"""Filing text routes: what changed in a section since the comparable filing, and full-text
search over parsed sections (migration 021, built by the parser's `parser-index` job).

Both need the `filings` tier. Same two layers as every route (app.py): OpenFGA first
(`require` for one company, `readable` for the list search may cover), then Store, which runs
every query as query_api with app.user_id set, so RLS on gold.section_search /
gold.section_diffs / gold.section_changes filters again.

What changed has two answers. Without `view` (or `q`, `numbers`), the stored changes as the
parser's diff wrote them, as before F3 (the agent's get_section_changes reads that). With `view`
(`reading` or `ranked`), the materiality pass of query_api.changes (its docstring is the spec):
split paragraphs merged, calendar rollovers and figure-only rows hidden and counted, each
change with its period, figures, score and why; `basis` then defaults to the same quarter a year
earlier for a 10-Q that has one (`comparisons` lists what this filing was compared with), and
`q`, `numbers` and `kind` filter after the pass. The answer echoes `view`: a client that gets
none back is talking to an older API that ignored it.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query

from query_api import changes as material
from query_api.authorizer import Readable
from query_api.shapes import filing_url, newest_windows
from query_api.store import SearchTooBroad, Store

CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]
Accession = Annotated[str, Path(pattern=r"^\d{10}-\d{2}-\d{6}$")]
SECTION = r"^[A-Z0-9.?-]{1,8}$"
FORM = r"^(10-K|10-Q|8-K)(/A)?$"
Kind = Literal["new", "removed", "rewritten"]
View = Literal["reading", "ranked"]
Basis = Literal["prior", "year_ago"]
SNIPPET_MARKS = ("«", "»")  # around matched words in search snippets
# /v1/search over the whole public catalog: rank the matches of the newest window that fills the
# page. Ranking every match of a common phrase (276,000 passages for "supply chain" on hermes)
# can't finish within SEARCH_TIMEOUT; a closed catalog or one company is ranked over all time.
SEARCH_WINDOW_DAYS = (30, 180, 730)


def render_words(old: str, new: str, opcodes: list[list]) -> list[list[str]]:
    """Word-diff opcodes [tag, i1, i2, j1, j2] (tag e/r/d/i over whitespace tokens) as
    [op, text] runs: "=" both, "-" prior only, "+" this filing only. Same as
    filing_parser.diff.render_words, which writes them."""
    a, b = old.split(), new.split()
    runs: list[list[str]] = []
    for tag, i1, i2, j1, j2 in opcodes:
        for op, words in (("=", b[j1:j2]),) if tag == "e" else (("-", a[i1:i2]),
                                                                  ("+", b[j1:j2])):
            if not words:
                continue
            if runs and runs[-1][0] == op:
                runs[-1][1] += " " + " ".join(words)
            else:
                runs.append([op, " ".join(words)])
    return runs


def _paragraph(text: str | None, spans: list[int] | None) -> str | None:
    """A paragraph is its lines' [start, end) slices of the section text, joined by a space."""
    if text is None or not spans:
        return None
    return " ".join(text[spans[i]:spans[i + 1]].strip() for i in range(0, len(spans), 2))


def _citation(side: dict, spans: list[int] | None) -> dict | None:
    if not spans:
        return None
    return {"accession_no": side["accession_no"], "form": side["form"], "filed": side["filed"],
            "period_of_report": side["period_of_report"], "section_key": side["section_key"],
            "document_url": side["document_url"],
            "char_start": side["char_start"] + spans[0],
            "char_end": side["char_start"] + spans[-1]}


def shape_section(d: dict, changes: list[dict]) -> dict:
    this = {"accession_no": d["accession_no"], "form": d["form"], "filed": d["filed"],
            "period_of_report": d["period_of_report"], "section_key": d["section_key"],
            "document_url": d["document_url"], "char_start": d["char_start"]}
    prior = {"accession_no": d["prior_accession_no"], "form": d["prior_form"],
             "filed": d["prior_filed"], "period_of_report": d["prior_period_of_report"],
             "section_key": d["section_key"], "document_url": d["prior_document_url"],
             "char_start": d["prior_char_start"]}
    stale = d["text"] is None or d["prior_text"] is None  # re-parsed since the diff was made
    out = []
    for c in [] if stale else changes:
        text = _paragraph(d["text"], c["new_spans"])
        prior_text = _paragraph(d["prior_text"], c["prior_spans"])
        out.append({
            "kind": c["kind"], "similarity": c["similarity"],
            "text": text, "prior_text": prior_text,
            "words": render_words(prior_text or "", text or "", c["words"])
                     if c["words"] is not None else None,
            "citation": _citation(this, c["new_spans"]),
            "prior_citation": _citation(prior, c["prior_spans"]),
        })
    return {
        "section_key": d["section_key"], "title": d["title"], "basis": d["basis"],
        "stale": stale,
        "compared_to": {k: v for k, v in prior.items() if k != "char_start"}
                       | {"filing_url": filing_url(d.get("prior_cik"), d["prior_accession_no"])},
        "summary": {k: d[k] for k in ("paragraphs", "prior_paragraphs", "unchanged",
                                      "minor_edits", "numbers_only", "new", "removed",
                                      "rewritten")},
        "changes": out,
        "freshness": {"filed": d["filed"], "ingested_at": d["ingested_at"],
                      "computed_at": d["computed_at"]},
    }


def default_basis(comparisons: list[dict]) -> Basis:
    """A 10-Q against the same quarter a year earlier when that was compared, else the prior
    filing (a 10-K, or a 10-Q with no year-ago 10-Q indexed)."""
    bases = {c["basis"]: c for c in comparisons}
    if "year_ago" in bases and bases["year_ago"].get("form", "").startswith("10-Q"):
        return "year_ago"
    return "prior"


def shape_material(d: dict, rows: list[dict], view: str, terms: list[str],
                   numbers: str | None, kind: str | None) -> dict:
    """One section through the materiality pass (query_api.changes), filtered and ordered."""
    out = shape_section(d, [])
    stale = out["stale"]
    reading = (material.read_section(d["section_key"], d["form"], d["text"], d["prior_text"],
                                     rows)
               if not stale else material.Reading([], dict.fromkeys(material.HIDDEN, 0)))
    this = {"accession_no": d["accession_no"], "form": d["form"], "filed": d["filed"],
            "period_of_report": d["period_of_report"], "section_key": d["section_key"],
            "document_url": d["document_url"], "char_start": d["char_start"]}
    prior = {"accession_no": d["prior_accession_no"], "form": d["prior_form"],
             "filed": d["prior_filed"], "period_of_report": d["prior_period_of_report"],
             "section_key": d["section_key"], "document_url": d["prior_document_url"],
             "char_start": d["prior_char_start"]}
    shown = []
    for c in reading.changes:
        text = c.new.text if c.new else None
        prior_text = c.old.text if c.old else None
        if kind and c.kind != kind:
            continue
        if numbers == "new" and not c.figures_new:
            continue
        if terms and not material.matches_topic(terms, text, prior_text):
            continue
        shown.append({
            "kind": c.kind, "similarity": c.similarity, "text": text, "prior_text": prior_text,
            "words": material.render_words(prior_text or "", text or "", c.words)
                     if c.words is not None else None,
            "citation": _citation(this, c.new.flat()) if c.new else None,
            "prior_citation": _citation(prior, c.old.flat()) if c.old else None,
            "section_key": d["section_key"], "period": c.period, "subsection": c.subsection,
            "figures": {"new": c.figures_new, "removed": c.figures_removed},
            "moved": c.moved, "risk_terms": c.risk, "score": c.score, "why": c.why,
            "merged": (c.new.parts if c.new else 0) + (c.old.parts if c.old else 0),
        })
    if view == "ranked":
        shown.sort(key=lambda x: -x["score"])  # stable: reading order within a score
    counts = {k: sum(1 for c in reading.changes if c.kind == k)
              for k in ("new", "removed", "rewritten")}
    out["material"] = {**counts, "hidden": reading.hidden, "shown": len(shown)}
    out["changes"] = shown
    return out


def add_research_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                        require: Callable[[str, str, str], None],
                        readable: Callable[[str], Readable]) -> None:
    User = Annotated[str, Depends(current_user)]

    @app.get("/v1/companies/{company_id}/filings/{accession_no}/changes")
    def section_changes(
        user_id: User, company_id: CompanyId, accession_no: Accession,
        section: Annotated[str | None, Query(
            pattern=SECTION, description='10-K "1A" or "7", 10-Q "II-1A" or "I-2"; default: all'
        )] = None,
        basis: Annotated[Basis | None, Query(
            description="prior: the previous 10-K / 10-Q; year_ago: the 10-Q for the same"
                        " quarter a year earlier. Default: prior; with `view`, year_ago for a"
                        " 10-Q that has one")] = None,
        kind: Annotated[Kind | None, Query(description="only this kind of change")] = None,
        view: Annotated[View | None, Query(
            description="the materiality pass (split paragraphs merged, calendar rollovers"
                        " hidden, scored): reading order, or ranked by score")] = None,
        q: Annotated[str | None, Query(
            min_length=2, max_length=100,
            description='a topic: passages with every word (a prefix: "tariffs" finds'
                        ' "tariff") or "a phrase"; implies view')] = None,
        numbers: Annotated[Literal["new"] | None, Query(
            description="new: only passages that state a figure the compared passage"
                        " doesn't; implies view")] = None,
    ) -> dict:
        """Paragraphs of Risk Factors and MD&A that are new, removed or rewritten since the
        comparable earlier filing, each cited in both filings. Unchanged paragraphs (numbers
        masked, so "fiscal 2025" → "2026" alone is unchanged) are only counted. With `view`,
        read for materiality (query_api/changes.py)."""
        require(user_id, "filings", company_id)
        if view is None and (q is not None or numbers is not None):
            view = "reading"
        if view is not None:
            return _material_changes(user_id, company_id, accession_no, section, basis, kind,
                                     view, q, numbers)
        basis = basis or "prior"
        diffs = store.section_diffs(user_id, company_id, accession_no, section, basis)
        if not diffs and not store.has_filing(user_id, company_id, accession_no):
            raise HTTPException(404, "no parsed filing with this accession for this company")
        changes = store.section_changes(user_id, accession_no, [d["section_key"] for d in diffs],
                                        basis, kind) if diffs else []
        by_section: dict[str, list[dict]] = {}
        for c in changes:
            by_section.setdefault(c["section_key"], []).append(c)
        return {
            "company_id": company_id, "accession_no": accession_no, "basis": basis,
            "as_of": _now(),
            "sections": [shape_section(d, by_section.get(d["section_key"], [])) for d in diffs],
            **({} if diffs else {"note": "no comparable earlier filing with these sections is"
                                         " indexed yet"}),
        }

    def _material_changes(user_id: str, company_id: str, accession_no: str,
                          section: str | None, basis: Basis | None, kind: str | None,
                          view: str, q: str | None, numbers: str | None) -> dict:
        comparisons = store.section_bases(user_id, company_id, accession_no)
        chosen: Basis = basis or default_basis(comparisons)
        diffs = store.section_diffs(user_id, company_id, accession_no, section, chosen)
        if not diffs and not comparisons \
                and not store.has_filing(user_id, company_id, accession_no):
            raise HTTPException(404, "no parsed filing with this accession for this company")
        rows = store.section_changes(user_id, accession_no, [d["section_key"] for d in diffs],
                                     chosen, None) if diffs else []
        by_section: dict[str, list[dict]] = {}
        for c in rows:
            by_section.setdefault(c["section_key"], []).append(c)
        terms = material.topic_terms(q)
        sections = [shape_material(d, by_section.get(d["section_key"], []), view, terms,
                                   numbers, kind) for d in diffs]
        return {
            "company_id": company_id, "accession_no": accession_no, "basis": chosen,
            "as_of": _now(), "view": view,
            "comparisons": [{"basis": c["basis"], "accession_no": c["prior_accession_no"],
                             "form": c["prior_form"], "filed": c["prior_filed"],
                             "period_of_report": c["prior_period_of_report"]}
                            for c in comparisons],
            "filters": {"q": q, "terms": terms, "numbers": numbers, "kind": kind,
                        "section": section},
            "ranking": material.RANKING,
            "matched": sum(s["material"]["shown"] for s in sections),
            "total": sum(sum(s["material"][k] for k in ("new", "removed", "rewritten"))
                         for s in sections),
            "sections": sections,
            **({} if diffs else {"note": "no comparable earlier filing with these sections is"
                                         " indexed yet"}),
        }

    @app.get("/v1/search")
    def search_sections(
        user_id: User,
        q: Annotated[str, Query(min_length=2, max_length=200, description=(
            'web-search syntax: words, "a phrase", or, -excluded'))],
        company: Annotated[str | None, Query(pattern=r"^\d{10}$",
                                             description="company_id")] = None,
        form: Annotated[str | None, Query(pattern=FORM)] = None,
        section: Annotated[str | None, Query(pattern=SECTION)] = None,
        start: Annotated[date | None, Query(alias="from", description="filed on or after")]
            = None,
        end: Annotated[date | None, Query(alias="to", description="filed on or before")] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        offset: Annotated[int, Query(ge=0, le=1000)] = 0,
    ) -> dict:
        """Full-text search over the parsed sections of the companies whose filings the caller
        can read: the best-matching passage per section, ranked, with a snippet (matches
        between « and ») and a citation to the passage. With the catalog public and no `company`,
        `searched_from` says how far back the ranked filings go: the newest window that fills
        the page, wider only when there are few matches."""
        if company is not None:
            require(user_id, "filings", company)
            granted: list[str] | None = [company]
            allows = {company}
        else:
            access = readable(user_id)
            granted = access.ids("filings")
            allows = {c for c, tiers in access.items() if "filings" in tiers}
        rows: list[dict] = []
        windowed: dict[str, date | None] = {}
        try:
            if granted is None:
                today = datetime.now(UTC).date()  # EDGAR dates are US Eastern, never later
                for lower in newest_windows(SEARCH_WINDOW_DAYS, start, end, today):
                    rows = store.search_sections(user_id, None, q, form, section, lower, end,
                                                 limit + 1, offset)
                    windowed = {"searched_from": lower}
                    if len(rows) > limit:
                        break
            elif granted:
                rows = store.search_sections(user_id, granted, q, form, section, start, end,
                                             limit + 1, offset)
        except SearchTooBroad:
            raise HTTPException(422, "search too broad: add a company, form, section or"
                                     " date filter, or more specific words") from None
        return {
            "query": q, "as_of": _now(), "limit": limit, "offset": offset, **windowed,
            "has_more": len(rows) > limit,  # layer 1 on every row below, also without an id list
            "results": [{
                "company_id": r["company_id"], "name": r["name"],
                "section_key": r["section_key"], "title": r["title"],
                "rank": round(float(r["rank"]), 6), "snippet": r["snippet"],
                "passages_matched": r["passages_matched"],
                "citation": {"accession_no": r["accession_no"], "form": r["form"],
                             "filed": r["filed"], "period_of_report": r["period_of_report"],
                             "section_key": r["section_key"], "document_url": r["document_url"],
                             "filing_url": filing_url(r.get("cik"), r["accession_no"]),
                             "char_start": r["char_start"], "char_end": r["char_end"]},
                "freshness": {"filed": r["filed"], "ingested_at": r["ingested_at"]},
            } for r in rows[:limit] if r["company_id"] in allows],
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()
