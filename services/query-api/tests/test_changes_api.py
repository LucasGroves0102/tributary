"""/changes with view=reading|ranked (F3, query_api.changes) with fake layers: filings tier
first, a 10-Q against a year ago by default, the pass and the filters, the old answer unchanged
without a view."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app

from .conftest import SECRET
from .test_api import NVDA, TSLA, FakeAuthorizer
from .test_research_api import GRANTS, Store

Q_NEW = ("Item 2. MD&A\nThree Months Ended September 30, 2026\n"
         "Gross Profit. Gross margin increased to 60.5% from 56.2%, due to IEEPA tariff net "
         "refunds of $46,000.\n"
         "Net sales were $4,312.8 in the third quarter of 2026, up from the third quarter of "
         "2025 and strong.\n"
         "We are a global leader in footwear and apparel for everyday use.")
Q_OLD = ("Item 2. MD&A\nThree Months Ended June 30, 2026\n"
         "Gross Profit. Gross margin increased to 56.2% from 55.9%, due to price increases and "
         "mix.\n"
         "Net sales were $3,274.3 in the second quarter of 2026, up from the second quarter of "
         "2025 and strong.\n"
         "Our brands compete across the fashion and casual lifestyle markets.")
Q_ACC, Q_PRIOR, Q_YEAR_AGO = "0000910521-26-068116", "0000910521-26-000022", "0000910521-25-000044"


def _line(text: str, starts: str) -> list[int]:
    a = text.index(starts)
    end = text.find("\n", a)
    return [a, end if end >= 0 else len(text)]


class QStore(Store):
    """A 10-Q compared with the prior 10-Q and the same quarter a year earlier."""

    def section_bases(self, user_id, company_id, accession_no):
        self.calls.append(("section_bases", company_id, accession_no))
        return [{"basis": b, "form": "10-Q", "prior_accession_no": acc, "prior_form": "10-Q",
                 "prior_filed": filed, "prior_period_of_report": end}
                for b, acc, filed, end in (
                    ("prior", Q_PRIOR, date(2026, 7, 30), date(2026, 6, 30)),
                    ("year_ago", Q_YEAR_AGO, date(2025, 10, 31), date(2025, 9, 30)))]

    def section_diffs(self, user_id, company_id, accession_no, section, basis):
        [d] = super().section_diffs(user_id, company_id, accession_no, section, basis)
        return [d | {"section_key": "I-2", "form": "10-Q", "title": "MD&A", "text": Q_NEW,
                     "prior_text": Q_OLD, "accession_no": Q_ACC}]

    def section_changes(self, user_id, accession_no, keys, basis, kind):
        self.calls.append(("section_changes", accession_no, tuple(keys), basis, kind))
        rows = [("rewritten", _line(Q_NEW, "Gross Profit."), _line(Q_OLD, "Gross Profit.")),
                ("rewritten", _line(Q_NEW, "Net sales"), _line(Q_OLD, "Net sales")),
                ("new", _line(Q_NEW, "We are"), None),
                ("removed", None, _line(Q_OLD, "Our brands"))]
        return [{"section_key": "I-2", "ordinal": i, "kind": k, "similarity": 0.5,
                 "new_spans": n, "prior_spans": o, "words": None}
                for i, (k, n, o) in enumerate(rows)]


def client():
    store = QStore()
    c = TestClient(create_app(FakeAuthorizer(GRANTS), store, SECRET))
    c.store = store  # type: ignore[attr-defined]
    return c


URL = f"/v1/companies/{NVDA}/filings/{Q_ACC}/changes"


def test_a_10q_is_read_against_a_year_ago_by_default_with_the_ranking_explained(token):
    c = client()
    r = c.get(f"{URL}?view=ranked", headers=token("bob"))
    assert r.status_code == 200
    body = r.json()
    assert (body["view"], body["basis"]) == ("ranked", "year_ago")
    assert [x["basis"] for x in body["comparisons"]] == ["prior", "year_ago"]
    assert body["comparisons"][1] == {"basis": "year_ago", "accession_no": Q_YEAR_AGO,
                                      "form": "10-Q", "filed": "2025-10-31",
                                      "period_of_report": "2025-09-30"}
    assert body["ranking"].startswith("Ranked by what changed")
    assert c.store.calls[:2] == [  # type: ignore[attr-defined]
        ("section_bases", NVDA, Q_ACC), ("section_diffs", "bob", NVDA, Q_ACC, None, "year_ago")]
    [s] = body["sections"]
    # "second quarter" → "third quarter", June → September: a calendar rollover, hidden
    assert s["material"]["hidden"]["calendar"] == 1
    gross, *rest = s["changes"]
    assert gross["kind"] == "rewritten" and gross["period"] == "three months"
    assert gross["figures"]["new"] == ["60.5%", "$46,000"]
    assert "tariff" in gross["risk_terms"]
    assert gross["score"] == max(x["score"] for x in s["changes"])
    assert gross["why"][0].startswith("rewritten") and gross["citation"]["section_key"] == "I-2"
    assert gross["words"][0] == ["=", "Gross Profit. Gross margin increased to"]
    assert {x["kind"] for x in rest} == {"new", "removed"}
    assert body["matched"] == s["material"]["shown"] == 3 and body["total"] == 3


def test_topic_and_new_figures_filter_after_the_pass(token):
    c = client()
    body = c.get(f"{URL}?q=tariffs", headers=token("bob")).json()  # q implies view=reading
    assert body["view"] == "reading" and body["filters"]["terms"] == ["tariff"]
    assert [x["text"][:13] for x in body["sections"][0]["changes"]] == ["Gross Profit."]
    assert body["matched"] == 1 and body["total"] == 3
    body = c.get(f"{URL}?view=reading&numbers=new&basis=prior", headers=token("bob")).json()
    assert body["basis"] == "prior" and body["filters"]["numbers"] == "new"
    assert [x["figures"]["new"] for x in body["sections"][0]["changes"]] == [
        ["60.5%", "$46,000"]]
    body = c.get(f'{URL}?view=ranked&q="footwear and apparel"&kind=new',
                 headers=token("bob")).json()
    assert [x["kind"] for x in body["sections"][0]["changes"]] == ["new"]


@pytest.mark.parametrize("query", ["view=sorted", "numbers=old", "q=x", "q=" + "x" * 101,
                                   "view=ranked&basis=forever"])
def test_bad_parameters_are_refused_before_the_store(token, query):
    c = client()
    assert c.get(f"{URL}?{query}", headers=token("bob")).status_code == 422
    assert c.store.calls == []  # type: ignore[attr-defined]


def test_the_view_needs_the_filings_tier_first(token):
    c = client()
    for user, company in (("alice", NVDA), ("bob", TSLA)):
        r = c.get(f"/v1/companies/{company}/filings/{Q_ACC}/changes?view=ranked&q=tariffs",
                  headers=token(user))
        assert r.status_code == 403
    assert c.store.calls == []  # type: ignore[attr-defined]


def test_without_a_view_the_answer_is_as_before(token):
    """The agent's get_section_changes and an older web app read this shape."""
    c = client()
    body = c.get(URL, headers=token("bob")).json()
    assert set(body) == {"company_id", "accession_no", "basis", "as_of", "sections"}
    assert body["basis"] == "prior"
    assert ("section_bases", NVDA, Q_ACC) not in c.store.calls  # type: ignore[attr-defined]
    assert set(body["sections"][0]["changes"][0]) == {
        "kind", "similarity", "text", "prior_text", "words", "citation", "prior_citation"}


def test_a_filing_compared_with_nothing_yet_says_so(token):
    c = client()
    c.store.section_bases = lambda *a: []  # type: ignore[attr-defined]
    c.store.section_diffs = lambda *a: []  # type: ignore[attr-defined]
    c.store.has_filing = lambda *a: True  # type: ignore[attr-defined]
    body = c.get(f"{URL}?view=ranked", headers=token("bob")).json()
    assert body["sections"] == [] and body["basis"] == "prior" and "note" in body
    c.store.has_filing = lambda *a: False  # type: ignore[attr-defined]
    assert c.get(f"{URL}?view=ranked", headers=token("bob")).status_code == 404


def test_the_pass_copies_the_writers_rules():
    diff = pytest.importorskip("filing_parser.diff")
    from query_api import changes
    old, new = "Gross margin “rose” to 56.2% — fell.", "Gross margin ‘rose’ to 60.5% – rose."
    assert changes.word_opcodes(old, new) == diff.word_opcodes(old, new)
    assert changes.normalize(new) == diff.normalize(new)
    ops = diff.word_opcodes(old, new)
    assert changes.render_words(old, new, ops) == diff.render_words(old, new, ops)
    assert (changes.MIN_WORDS, changes.ALIGN_MIN, changes.REWRITE_BELOW) == (
        diff.MIN_WORDS, diff.ALIGN_MIN, diff.REWRITE_BELOW)
