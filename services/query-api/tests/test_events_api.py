"""/v1/companies/{id}/events and /earnings with fake layers: filings tier checked first, the
quarter and event rules of query_api/events.py, shapes. Store rows mirror Store's (real 8-K
items and release texts of Vertiv and Deckers, fixtures/releases)."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.events import (
    affects,
    assign_releases,
    excerpt,
    items_of,
    material,
    next_quarter_end,
    periods,
    release_section,
)

from .conftest import SECRET
from .test_api import NVDA, FakeAuthorizer

VRT = "0001674101"
DECK = "0000910521"
GRANTS = {"bob": {VRT: {"financials", "filings"}, DECK: {"financials", "filings"}},
          "alice": {VRT: {"financials"}, DECK: {"financials"}}}
RELEASES = Path(__file__).parent / "fixtures" / "releases"


def report(acc, form, filed, end):
    return {"accession_no": acc, "form": form, "filing_date": filed, "period_end": end}


def eight_k(acc, filed, items, event=None, form="8-K", cik=VRT, parsed=None):
    return {"accession_no": acc, "cik": cik, "form": form, "filing_date": filed,
            "accepted_at": datetime(filed.year, filed.month, filed.day, 20, 5, tzinfo=UTC),
            "report_date": event or filed, "primary_document": "d.htm",
            "items": items, "parsed_items": parsed}


def section(key, head, title=None, chars=None, start=0, url="https://www.sec.gov/x.htm"):
    return {"section_key": key, "title": title, "head": head, "chars": chars or len(head),
            "char_start": start, "char_end": start + (chars or len(head)), "document_url": url}


# Vertiv on 2026-11-08 (hermes): its 10-Q for Sep 30 and the 8-Ks after it
VRT_REPORTS = [report("0001628280-26-067893", "10-Q", date(2026, 10, 28), date(2026, 9, 30)),
               report("0001628280-26-050609", "10-Q", date(2026, 7, 29), date(2026, 6, 30)),
               report("0001628280-26-030000", "10-K", date(2026, 2, 12), date(2025, 12, 31))]
UIG = "0001628280-26-067393"
VRT_8KS = [
    eight_k("0001628280-26-067707", date(2026, 10, 28), ["2.02", "7.01", "9.01"]),
    eight_k(UIG, date(2026, 10, 27), ["2.01", "7.01", "9.01"]),
    eight_k("0001628280-26-067127", date(2026, 10, 26), ["7.01", "9.01"]),
    eight_k("0001193125-26-379306", date(2026, 9, 2), ["1.01", "7.01", "9.01"],
            event=date(2026, 9, 1)),
    eight_k("0001628280-26-050323", date(2026, 7, 29), ["2.02", "7.01", "9.01"]),
]
UIG_TEXT = ("On October 27, 2026, Vertiv Holdings Co completed its previously announced "
            "acquisition of UIG for approximately $1.45 billion in cash, subject to customary "
            "adjustments, plus contingent consideration of up to $1.15 billion. The acquisition "
            "was funded with cash on hand. Additional details are set out in the press release "
            "furnished as Exhibit 99.1 to this Current Report on Form 8-K, which is incorporated "
            "herein by reference.")


class Store:
    def __init__(self, reports=VRT_REPORTS, rows=VRT_8KS, heads=None, texts=None):
        self.reports, self.rows = reports, rows
        self.heads = heads if heads is not None else {
            UIG: [section("2.01", UIG_TEXT, "Completion of Acquisition", start=1200),
                  section("7.01", "On October 27, 2026, Vertiv issued a press release."),
                  section("EX-99.1", "Vertiv Completes Acquisition of UIG", "PRESS RELEASE")],
            "0001628280-26-067707": [section("2.02", "On October 28, 2026, Vertiv reported."),
                                     section("EX-99.1", "", "Exhibit 99.1", chars=39984)],
        }
        self.texts = texts or {}
        self.calls = []

    def periodic_reports(self, user_id, company_id):
        self.calls.append(("periodic_reports", company_id))
        return self.reports

    def company_8ks(self, user_id, company_id, since):
        self.calls.append(("company_8ks", company_id, since))
        return [r for r in self.rows if r["filing_date"] >= since]

    def section_heads(self, user_id, company_id, accessions, head_chars):
        self.calls.append(("section_heads", company_id, tuple(accessions), head_chars))
        return {a: [{**s, "head": s["head"][:head_chars]} for s in self.heads.get(a, [])]
                for a in accessions if a in self.heads}

    def section_texts(self, user_id, company_id, wanted):
        self.calls.append(("section_texts", company_id, tuple(wanted)))
        return {w: self.texts[w] for w in wanted if w in self.texts}


def client(store, today=date(2026, 11, 8)):
    c = TestClient(create_app(FakeAuthorizer(GRANTS), store, SECRET, today=lambda: today))
    c.store = store  # type: ignore[attr-defined]
    return c


# --- rules ------------------------------------------------------------------------------------

def test_items_come_from_the_index_else_the_parsed_headings():
    assert items_of({"items": ["2.02", "9.01"], "parsed_items": ["2.02"]}) == (["2.02", "9.01"],
                                                                             "index")
    assert items_of({"items": None, "parsed_items": ["2.02", "EX-99.1", "9.01"]}) == (
        ["2.02", "9.01"], "parsed")
    assert items_of({"items": None, "parsed_items": None}) == ([], None)


def test_routine_filings_are_not_material():
    assert not material(["5.07"]) and not material(["9.01"]) and not material(["5.07", "9.01"])
    assert material(["5.07", "8.01"]) and material(["7.01", "9.01"]) and material([])


def test_what_an_event_after_the_quarter_end_affects():
    end = date(2026, 9, 30)
    assert affects(["2.01", "7.01", "9.01"], "8-K", date(2026, 10, 27), end) == [
        "cash", "long_term_debt", "net_debt", "total_assets"]
    assert affects(["1.01", "2.03"], "8-K", date(2026, 10, 2), end) == [
        "long_term_debt", "net_debt", "debt_to_equity", "cash"]
    assert affects(["4.02", "2.03"], "8-K", date(2026, 10, 2), end) == ["*"]
    assert affects(["2.01"], "8-K/A", date(2026, 10, 27), end) == []  # an amendment
    assert affects(["2.03"], "8-K", date(2026, 9, 29), end) == []  # in that balance sheet
    assert affects(["5.02", "7.01"], "8-K", date(2026, 10, 2), end) == []


def test_the_press_release_is_the_lowest_ex99():
    assert release_section(["2.02", "9.01", "EX-99.2", "EX-99.1"]) == "EX-99.1"
    assert release_section(["2.02", "EX-99.01", "EX-99.02"]) == "EX-99.01"
    assert release_section(["2.02", "9.01"]) == "2.02"
    assert release_section(["8.01", "EX-10.1"]) is None


@pytest.mark.parametrize("end,expected", [
    (date(2026, 9, 30), date(2026, 12, 31)), (date(2026, 6, 30), date(2026, 9, 30)),
    (date(2026, 11, 30), date(2027, 2, 28)), (date(2026, 2, 28), date(2026, 5, 31)),
    (date(2026, 7, 26), date(2026, 10, 25)),  # a 52/53-week year: + 91 days
])
def test_next_quarter_end(end, expected):
    assert next_quarter_end(end) == expected


def test_excerpt_cuts_at_a_sentence():
    assert len(UIG_TEXT) > 400
    assert excerpt(UIG_TEXT) == UIG_TEXT[:UIG_TEXT.index("cash on hand.") + 13]
    assert excerpt("short text") == "short text"
    assert excerpt("word " * 200).endswith("word …")


def deck(filed_10q: date | None):
    reports = [report("0000910521-26-000022", "10-Q", date(2026, 7, 30), date(2026, 6, 30))]
    if filed_10q:
        reports.insert(0, report("0001628280-26-068116", "10-Q", filed_10q, date(2026, 9, 30)))
    releases = [eight_k("0000910521-26-000028", date(2026, 10, 22), ["2.02"], cik=DECK),
                eight_k("0000910521-26-000015", date(2026, 7, 23), ["2.02"], cik=DECK)]
    return reports, releases


def test_a_release_before_its_10q_reports_an_estimated_quarter():
    """Deckers on 2026-10-23: the Q2 release is out, its 10-Q isn't."""
    reports, releases = deck(None)
    quarters = periods(reports, releases)
    assign_releases(quarters, releases)
    assert [(q["period_end"], q["estimated"]) for q in quarters[:2]] == [
        (date(2026, 9, 30), True), (date(2026, 6, 30), False)]
    assert quarters[0]["release_row"]["accession_no"] == "0000910521-26-000028"
    assert quarters[1]["release_row"]["accession_no"] == "0000910521-26-000015"


def test_once_the_10q_is_filed_the_quarter_is_reported():
    reports, releases = deck(date(2026, 10, 29))
    quarters = periods(reports, releases)
    assign_releases(quarters, releases)
    assert quarters[0]["period_end"] == date(2026, 9, 30) and not quarters[0]["estimated"]
    assert quarters[0]["release_row"]["accession_no"] == "0000910521-26-000028"


def test_of_several_releases_the_last_before_the_report_is_shown():
    reports = [report("r-q3", "10-Q", date(2026, 11, 5), date(2026, 9, 30))]
    pre = eight_k("pre", date(2026, 10, 10), ["2.02"])  # a pre-announcement
    full = eight_k("full", date(2026, 11, 4), ["2.02", "9.01"])
    after = eight_k("after", date(2026, 11, 20), ["2.02", "7.01"])  # after the 10-Q
    quarters = periods(reports, [pre, full, after])
    assign_releases(quarters, [pre, full, after])
    assert quarters[0]["release_row"]["accession_no"] == "full"
    assert [r["accession_no"] for r in quarters[0]["others"]] == ["pre", "after"]


def test_a_release_long_after_any_quarter_end_is_left_out():
    reports = [report("r", "10-Q", date(2025, 5, 1), date(2025, 3, 31))]
    late = eight_k("late", date(2026, 3, 1), ["2.02"])
    quarters = periods(reports, [late])
    assign_releases(quarters, [late])
    assert all(q.get("release_row") is None for q in quarters)


# --- routes -----------------------------------------------------------------------------------

@pytest.mark.parametrize("route", ["events", "earnings"])
def test_filings_tier_is_checked_before_the_store(token, route):
    store = Store()
    r = client(store).get(f"/v1/companies/{VRT}/{route}", headers=token("alice"))
    assert r.status_code == 403 and store.calls == []
    r = client(store).get(f"/v1/companies/{NVDA}/{route}", headers=token("bob"))
    assert r.status_code == 403 and store.calls == []


def test_events_since_the_last_report_flag_the_figures_they_make_stale(token):
    """Tester 1's case: the UIG acquisition closed after Vertiv's Sep 30 balance sheet."""
    store = Store()
    body = client(store).get(f"/v1/companies/{VRT}/events", headers=token("bob")).json()
    assert body["since"] == "2026-09-30" and body["window"] == "since_report"
    assert body["latest_report"] == {"accession_no": "0001628280-26-067893", "form": "10-Q",
                                     "filed": "2026-10-28", "period_end": "2026-09-30"}
    assert ("company_8ks", VRT, date(2026, 10, 1)) in store.calls
    assert [e["filed"] for e in body["events"]] == ["2026-10-28", "2026-10-27", "2026-10-26"]
    uig = body["events"][1]
    assert [i["item"] for i in uig["items"]] == ["2.01", "7.01", "9.01"]
    assert uig["items"][0] == {"item": "2.01", "kind": "deal",
                               "title": "Completion of Acquisition or Disposition of Assets"}
    assert uig["items_from"] == "index" and uig["material"] and not uig["amendment"]
    assert uig["affects"] == ["cash", "long_term_debt", "net_debt", "total_assets"]
    assert uig["excerpt"]["item"] == "2.01"
    assert uig["excerpt"]["text"].endswith("funded with cash on hand.")
    assert uig["excerpt"]["citation"] == {
        "accession_no": UIG, "form": "8-K", "filed": "2026-10-27",
        "filing_url": "https://www.sec.gov/Archives/edgar/data/1674101/000162828026067393/",
        "section_key": "2.01", "document_url": "https://www.sec.gov/x.htm",
        "char_start": 1200, "char_end": 1200 + len(UIG_TEXT)}
    assert uig["release"] == {"section_key": "EX-99.1", "title": "PRESS RELEASE"}
    assert body["stale"] == [{"metric": m, "as_of": "2026-09-30", "events": [UIG]}
                             for m in ("cash", "long_term_debt", "net_debt", "total_assets")]
    # the earnings release and the Reg FD 8-K change no figure
    assert body["events"][0]["affects"] == [] and body["events"][2]["affects"] == []
    assert body["events"][2]["excerpt"] is None and not body["events"][2]["sections_available"]


def test_events_without_a_recent_report_cover_the_last_400_days(token):
    old = [report("r", "10-K", date(2024, 3, 1), date(2023, 12, 31))]
    store = Store(reports=old, rows=[])
    body = client(store).get(f"/v1/companies/{VRT}/events", headers=token("bob")).json()
    assert body["window"] == "recent" and body["since"] == "2025-10-04"
    assert body["events"] == [] and body["stale"] == []
    assert ("company_8ks", VRT, date(2025, 10, 5)) in store.calls


def test_events_too_new_for_the_index_use_the_parsed_items(token):
    rows = [eight_k("0001628280-26-070001", date(2026, 11, 6), None,
                    parsed=["2.03", "9.01", "EX-99.1"])]
    store = Store(rows=rows, heads={})
    body = client(store).get(f"/v1/companies/{VRT}/events", headers=token("bob")).json()
    [event] = body["events"]
    assert event["items_from"] == "parsed"
    assert [i["item"] for i in event["items"]] == ["2.03", "9.01"]
    assert event["affects"] == ["long_term_debt", "net_debt", "debt_to_equity", "cash"]


def test_earnings_quotes_the_release_with_offsets_into_it(token):
    text = (RELEASES / "deck-2026-10-22-ex99-1.txt").read_text(encoding="utf-8")
    acc = "0000910521-26-000028"
    reports, releases = deck(date(2026, 10, 29))
    store = Store(reports=reports, rows=releases, heads={
        acc: [section("2.02", "Results"), section("EX-99.1", "", "Exhibit 99.1",
                                                    chars=len(text))]},
        texts={(acc, "EX-99.1"): text})
    body = client(store).get(f"/v1/companies/{DECK}/earnings?quarters=2",
                             headers=token("bob")).json()
    latest, prior = body["quarters"]
    assert latest["period_end"] == "2026-09-30" and not latest["estimated"]
    assert latest["report"] == {"accession_no": "0001628280-26-068116", "form": "10-Q",
                                "filed": "2026-10-29"}
    assert latest["release"]["accession_no"] == acc
    assert latest["release"]["section_key"] == "EX-99.1"
    assert [i["item"] for i in latest["release"]["items"]] == ["2.02"]
    [outlook] = latest["outlook"]
    cite = outlook["citation"]
    assert cite["section_key"] == "EX-99.1" and cite["accession_no"] == acc
    assert text[cite["char_start"]:cite["char_end"]].startswith("Full Fiscal Year 2027 Outlook")
    assert "$7.80 to $7.95" in outlook["text"]
    [note] = latest["adjustments"]
    assert note["kind"] == "note" and "$46.0 million" in note["text"]
    assert latest["headline"]["text"].startswith("DECKERS BRANDS REPORTS SECOND QUARTER")
    # the July release: its text wasn't read here, so nothing is quoted, and it says which
    assert prior["release"]["accession_no"] == "0000910521-26-000015"
    assert prior["outlook"] == [] and prior["headline"] is None
    assert ("section_texts", DECK, ((acc, "EX-99.1"),)) in store.calls
    assert "No beat or miss" in body["method"]


def test_a_quarter_without_a_release_says_so(token):
    store = Store(reports=VRT_REPORTS[:1], rows=[], heads={})
    body = client(store).get(f"/v1/companies/{VRT}/earnings", headers=token("bob")).json()
    [quarter] = body["quarters"]
    assert quarter["release"] is None and quarter["outlook"] == []
    assert quarter["report"]["accession_no"] == "0001628280-26-067893"
    assert not any(c[0] == "section_texts" for c in store.calls)
