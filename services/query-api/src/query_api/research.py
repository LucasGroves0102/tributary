"""Filing text routes: what changed in a section since the comparable filing, and full-text
search over parsed sections (migration 021, built by the parser's `parser-index` job).

Both need the `filings` tier. Same two layers as every route (app.py): OpenFGA first
(`require` for one company, `readable` for the list search may cover), then Store, which runs
every query as query_api with app.user_id set, so RLS on gold.section_search /
gold.section_diffs / gold.section_changes filters again.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query

from query_api.shapes import filing_url
from query_api.store import SearchTooBroad, Store

CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]
Accession = Annotated[str, Path(pattern=r"^\d{10}-\d{2}-\d{6}$")]
SECTION = r"^[A-Z0-9.?-]{1,8}$"
FORM = r"^(10-K|10-Q|8-K)(/A)?$"
Kind = Literal["new", "removed", "rewritten"]
SNIPPET_MARKS = ("«", "»")  # around matched words in search snippets


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


def add_research_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                        require: Callable[[str, str, str], None],
                        readable: Callable[[str], dict[str, set[str]]]) -> None:
    User = Annotated[str, Depends(current_user)]

    @app.get("/v1/companies/{company_id}/filings/{accession_no}/changes")
    def section_changes(
        user_id: User, company_id: CompanyId, accession_no: Accession,
        section: Annotated[str | None, Query(
            pattern=SECTION, description='10-K "1A" or "7", 10-Q "II-1A" or "I-2"; default: all'
        )] = None,
        basis: Annotated[Literal["prior", "year_ago"], Query(
            description="prior: the previous 10-K / 10-Q; year_ago: the 10-Q for the same"
                        " quarter a year earlier")] = "prior",
        kind: Annotated[Kind | None, Query(description="only this kind of change")] = None,
    ) -> dict:
        """Paragraphs of Risk Factors and MD&A that are new, removed or rewritten since the
        comparable earlier filing, each cited in both filings. Unchanged paragraphs (numbers
        masked, so "fiscal 2025" → "2026" alone is unchanged) are only counted."""
        require(user_id, "filings", company_id)
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
        between « and ») and a citation to the passage."""
        if company is not None:
            require(user_id, "filings", company)
            granted = [company]
        else:
            granted = sorted(c for c, tiers in readable(user_id).items() if "filings" in tiers)
        rows: list[dict] = []
        if granted:
            try:
                rows = store.search_sections(user_id, granted, q, form, section, start, end,
                                             limit + 1, offset)
            except SearchTooBroad:
                raise HTTPException(422, "search too broad: add a company, form, section or"
                                         " date filter, or more specific words") from None
        return {
            "query": q, "as_of": _now(), "limit": limit, "offset": offset,
            "has_more": len(rows) > limit,
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
            } for r in rows[:limit]],
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()
