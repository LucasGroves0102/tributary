"""Changes and search routes with fake layers: checked first, filings tier only, shapes."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.research import render_words
from query_api.store import SearchTooBroad

from .conftest import SECRET
from .test_api import NVDA, TSLA, FakeAuthorizer

ACC, PRIOR = "0001045810-26-000021", "0001045810-25-000023"
GRANTS = {"bob": {NVDA: {"financials", "filings"}, TSLA: {"financials"}},
          "alice": {NVDA: {"financials"}}}
NEW_TEXT = "Item 1A. Risk Factors\nOur Groq license is new.\nSales rose 15% in fiscal 2026."
OLD_TEXT = "Item 1A. Risk Factors\nSales rose 12% in fiscal 2025 and fell.\nAn old risk."


class Store:
    def __init__(self, broad=False):
        self.calls, self.broad = [], broad

    def section_diffs(self, user_id, company_id, accession_no, section, basis):
        self.calls.append(("section_diffs", user_id, company_id, accession_no, section, basis))
        return [{
            "company_id": company_id, "accession_no": ACC, "section_key": "1A", "basis": basis,
            "form": "10-K", "filed": date(2026, 2, 25), "period_of_report": date(2026, 1, 25),
            "document_url": "https://sec/new.htm", "ingested_at": "2026-10-20T00:00:00Z",
            "title": "Risk Factors", "text": NEW_TEXT, "char_start": 1000,
            "prior_accession_no": PRIOR, "prior_form": "10-K", "prior_filed": date(2025, 2, 26),
            "prior_period_of_report": date(2025, 1, 26), "prior_cik": "0001045810",
            "prior_document_url": "https://sec/old.htm", "prior_text": OLD_TEXT,
            "prior_char_start": 2000, "paragraphs": 3, "prior_paragraphs": 3, "unchanged": 1,
            "minor_edits": 0, "numbers_only": 0, "rewritten": 1, "new": 1, "removed": 1,
            "computed_at": "2026-10-20T01:00:00Z"}]

    def section_changes(self, user_id, accession_no, keys, basis, kind):
        self.calls.append(("section_changes", accession_no, tuple(keys), basis, kind))
        start = NEW_TEXT.index("Our")
        sales, old_sales = NEW_TEXT.index("Sales"), OLD_TEXT.index("Sales")
        old_risk = OLD_TEXT.index("An old")
        return [
            {"section_key": "1A", "ordinal": 0, "kind": "new", "similarity": None,
             "new_spans": [start, start + 24], "prior_spans": None, "words": None},
            {"section_key": "1A", "ordinal": 1, "kind": "rewritten", "similarity": 0.6,
             "new_spans": [sales, len(NEW_TEXT)],
             "prior_spans": [old_sales, old_risk - 1],
             "words": [["e", 0, 2, 0, 2], ["r", 2, 3, 2, 3], ["e", 3, 5, 3, 5],
                       ["r", 5, 8, 5, 6]]},
            {"section_key": "1A", "ordinal": 2, "kind": "removed", "similarity": None,
             "new_spans": None, "prior_spans": [old_risk, len(OLD_TEXT)], "words": None},
        ]

    def has_filing(self, user_id, company_id, accession_no):
        self.calls.append(("has_filing", company_id, accession_no))
        return False

    def search_sections(self, user_id, companies, q, form, section, start, end, limit, offset):
        self.calls.append(("search", user_id, tuple(companies), q, form, section, start, end,
                           limit, offset))
        if self.broad:
            raise SearchTooBroad
        return [{"company_id": NVDA, "name": "NVIDIA CORP", "accession_no": ACC, "form": "10-K",
                 "filed": date(2026, 2, 25), "period_of_report": date(2026, 1, 25),
                 "section_key": "1A", "title": "Risk Factors", "rank": 0.25,
                 "passages_matched": 2, "snippet": "our «Groq» license",
                 "char_start": 1010, "char_end": 1300, "document_url": "https://sec/new.htm",
                 "cik": "0001045810", "ingested_at": "2026-10-20T00:00:00Z"}] * min(limit, 3)


def client(grants=GRANTS, down=False, broad=False):
    store = Store(broad)
    c = TestClient(create_app(FakeAuthorizer(grants, down), store, SECRET))
    c.store = store  # type: ignore[attr-defined]
    return c


def test_changes_with_texts_word_diff_and_citations(token):
    c = client()
    r = c.get(f"/v1/companies/{NVDA}/filings/{ACC}/changes?section=1A", headers=token("bob"))
    assert r.status_code == 200
    [s] = r.json()["sections"]
    assert s["compared_to"]["accession_no"] == PRIOR
    assert s["compared_to"]["filing_url"].endswith("/1045810/000104581025000023/")
    new, rewritten, removed = s["changes"]
    assert (new["text"], new["prior_text"], new["prior_citation"]) == (
        "Our Groq license is new.", None, None)
    assert new["citation"]["char_start"] == 1000 + NEW_TEXT.index("Our")
    assert rewritten["words"] == [["=", "Sales rose"], ["-", "12%"], ["+", "15%"],
                                  ["=", "in fiscal"], ["-", "2025 and fell."],
                                  ["+", "2026."]]
    assert rewritten["prior_citation"]["char_start"] == 2000 + OLD_TEXT.index("Sales")
    assert (removed["text"], removed["prior_text"]) == (None, "An old risk.")
    assert c.store.calls[0] == ("section_diffs", "bob", NVDA, ACC, "1A", "prior")  # type: ignore[attr-defined]


def test_changes_need_the_filings_tier_first(token):
    c = client()
    for user, company in (("alice", NVDA), ("bob", TSLA)):
        r = c.get(f"/v1/companies/{company}/filings/{ACC}/changes", headers=token(user))
        assert r.status_code == 403
    assert c.store.calls == []  # type: ignore[attr-defined]


def test_changes_reject_bad_parameters(token):
    c = client()
    for q in ("section=1A;DROP", "basis=forever", "kind=moved"):
        r = c.get(f"/v1/companies/{NVDA}/filings/{ACC}/changes?{q}", headers=token("bob"))
        assert r.status_code == 422
    assert c.get(f"/v1/companies/{NVDA}/filings/nope/changes",
                 headers=token("bob")).status_code == 422
    assert c.store.calls == []  # type: ignore[attr-defined]


def test_changes_unknown_filing_is_404(token):
    c = client()
    c.store.section_diffs = lambda *a: []  # type: ignore[attr-defined]
    r = c.get(f"/v1/companies/{NVDA}/filings/{ACC}/changes", headers=token("bob"))
    assert r.status_code == 404


def test_search_covers_only_filings_tier_companies(token):
    c = client()
    r = c.get("/v1/search?q=groq&limit=2", headers=token("bob"))
    assert r.status_code == 200
    body = r.json()
    assert body["has_more"] is True and len(body["results"]) == 2
    assert body["results"][0]["citation"]["char_start"] == 1010
    assert body["results"][0]["snippet"] == "our «Groq» license"
    assert c.store.calls == [("search", "bob", (NVDA,), "groq", None, None, None, None, 3, 0)]  # type: ignore[attr-defined]


def test_search_without_filings_access_skips_the_database(token):
    c = client()
    r = c.get("/v1/search?q=groq", headers=token("alice"))
    assert r.json()["results"] == [] and c.store.calls == []  # type: ignore[attr-defined]


def test_search_company_filter_is_checked(token):
    c = client()
    assert c.get(f"/v1/search?q=groq&company={TSLA}", headers=token("bob")).status_code == 403
    assert c.store.calls == []  # type: ignore[attr-defined]
    r = c.get(f"/v1/search?q=groq&company={NVDA}&form=10-K&section=1A&from=2026-01-01"
              "&to=2026-12-31&offset=10", headers=token("bob"))
    assert r.status_code == 200
    assert c.store.calls == [("search", "bob", (NVDA,), "groq", "10-K", "1A",  # type: ignore[attr-defined]
                              date(2026, 1, 1), date(2026, 12, 31), 11, 10)]


def test_search_rejects_bad_parameters(token):
    c = client()
    for q in ("q=a", "q=groq&company=NVDA", "q=groq&form=S-1", "q=groq&limit=500",
              "q=groq&section=1A;DROP", "q=groq&from=yesterday"):
        assert c.get(f"/v1/search?{q}", headers=token("bob")).status_code == 422, q
    assert c.store.calls == []  # type: ignore[attr-defined]


def test_search_too_broad_and_openfga_down(token):
    r = client(broad=True).get("/v1/search?q=revenue", headers=token("bob"))
    assert r.status_code == 422 and "too broad" in r.json()["detail"]
    assert client(down=True).get("/v1/search?q=groq", headers=token("bob")).status_code == 503


def test_render_words_matches_the_writer():
    diff = pytest.importorskip("filing_parser.diff")
    writer, word_opcodes = diff.render_words, diff.word_opcodes
    old, new = "Sales rose 12% in fiscal 2025 and fell.", "Sales rose 15% in fiscal 2026."
    ops = word_opcodes(old, new)
    assert render_words(old, new, ops) == writer(old, new, ops)
