"""query_api.text_kpis (F8b, H11): backlog and orders from the filings' words, one case per rule,
each on real text (fixtures/text_kpis: trimmed sections of production's 10-K/10-Q/EX-99.1; the
inline sentences are quoted from production sections, named per case), and the route with fake
layers: filings tier first, releases dated by their quarter, bounded, no `rows` key."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.text_kpis import MAX_RELEASES, Doc, extract, sentences, series

from .conftest import SECRET
from .test_api import FakeAuthorizer

FIXTURES = Path(__file__).parent / "fixtures" / "text_kpis"
VRT, NOC = "0001674101", "0001133421"


def doc(text, form="10-Q", end=date(2025, 9, 30), key="I-2", filed=date(2025, 10, 28),
        acc="0000000001-25-000001"):
    return Doc(acc, form, filed, key, end, text)


def found(text, name=None, **kw):
    return [(f.metric, f.period_end, f.value, f.basis) for f in extract(doc(text, **kw), name)]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# --- rule 1: passages --------------------------------------------------------------------

def test_sentences_keep_abbreviations_and_join_a_line_broken_mid_sentence():
    text = ("Backlog was $1.0 billion at Dec. 31, 2025. Orders rose.\n"
            "Robust demand continued with order growth and\nrecord backlog of $2.4 billion.")
    got = [t for _, _, t in sentences(text)]
    assert got == ["Backlog was $1.0 billion at Dec. 31, 2025.", "Orders rose.",
                   "Robust demand continued with order growth and\nrecord backlog of $2.4 billion."]
    for a, b, t in sentences(text):
        assert text[a:b] == t


# --- rules 3-4: backlog in a sentence ---------------------------------------------------------

def test_vertiv_states_two_years_respectively():
    # Vertiv's 10-K Item 1, filed 2026-02-13
    text = ("Vertiv’s estimated combined order backlog was $15.0 billion and $7.2 billion as of "
            "December 31, 2025 and 2024, respectively, as continued strong demand has "
            "contributed to an increase in customer orders being placed in advance of our ability "
            "to fulfill them.")
    assert found(text, "Vertiv Holdings Co", form="10-K", key="1", end=date(2025, 12, 31)) == [
        ("backlog", date(2025, 12, 31), 15.0e9, None),
        ("backlog", date(2024, 12, 31), 7.2e9, None)]


def test_the_release_dates_backlog_by_its_quarter():
    # Vertiv's Q3 2025 release (fixture): backlog at the quarter end, order growth for it
    text = fixture("vrt-8k-2025-10-22-ex99-1.txt")
    got = found(text, "Vertiv Holdings Co", form="8-K", key="EX-99.1", end=date(2025, 9, 30))
    assert ("backlog", date(2025, 9, 30), 9.5e9, None) in got
    assert ("orders_growth", date(2025, 9, 30), 60.0, "organic") in got
    # the headline's "+60%" has no year-on-year words, the TTM figure is a year's: neither read
    assert [g for g in got if g[0] == "orders_growth"] == [
        ("orders_growth", date(2025, 9, 30), 60.0, "organic")] * 2


def test_caterpillar_quarter_end_and_its_parts():
    text = fixture("cat-10q-2021-11-03-I-2.txt")
    assert found(text, "CATERPILLAR INC", end=date(2021, 9, 30)) == [
        ("backlog", date(2021, 9, 30), 20.6e9, None)]
    part = ("Of the total backlog at September 30, 2021, approximately $3.9 billion was not "
            "expected to be filled in the following twelve months.")
    assert found(part, end=date(2021, 9, 30)) == []


def test_a_year_after_the_amount_is_a_comparison():
    # Caterpillar's Q1 2023 10-Q
    text = ("At the end of the first quarter of 2023, the dollar amount of backlog believed to be "
            "firm was approximately $30.4 billion, which was about flat to the fourth quarter "
            "of 2022.")
    assert found(text, end=date(2023, 3, 31)) == [("backlog", date(2023, 3, 31), 30.4e9, None)]
    # but another year before the amount is another period's figure
    assert found("Backlog at the end of fiscal 2023 was $3.0 billion.", end=date(2025, 3, 31)) == []


def test_each_amount_takes_its_own_date():
    # Caterpillar's 10-K, Lockheed Martin's 10-K
    cat = ("The dollar amount of backlog believed to be firm was approximately $23.1 billion at "
           "December 31, 2021 and $14.2 billion at December 31, 2020.")
    assert found(cat, form="10-K", key="1", end=date(2021, 12, 31)) == [
        ("backlog", date(2021, 12, 31), 23.1e9, None),
        ("backlog", date(2020, 12, 31), 14.2e9, None)]
    lmt = ("At December 31, 2021, our backlog was $135.4 billion compared with $147.1 billion at "
           "December 31, 2020.")
    assert found(lmt, form="10-K", key="7", end=date(2021, 12, 31)) == [
        ("backlog", date(2021, 12, 31), 135.4e9, None),
        ("backlog", date(2020, 12, 31), 147.1e9, None)]
    both = ("Backlog was approximately $135.4 billion and $147.1 billion at December 31, 2021 "
            "and 2020.")
    assert [g[1] for g in found(both, form="10-K", key="7")] == [date(2021, 12, 31),
                                                                 date(2020, 12, 31)]


def test_a_date_in_the_subject_dates_the_figure():
    # Xylem's Q3 2025 10-Q: the year-end figure is a comparison, dated as such
    text = ("Backlog decreased $251 million, or 5.0%, at September 30, 2025, as compared to "
            "December 31, 2024 backlog of $5,070 million.")
    assert found(text) == [("backlog", date(2024, 12, 31), 5070e6, None)]


@pytest.mark.parametrize("text", [
    # General Dynamics' 10-Q: a segment's
    "The Aerospace segment ended the first quarter of 2026 with backlog of $22.3 billion.",
    # Boeing's release and RTX's 10-K: a segment named before the verb
    "Backlog at Defense, Space & Security was $58 billion, of which 33 percent represents orders "
    "from customers outside the U.S.",
    "Backlog and Bookings – Backlog was $16 billion at December 31, 2022 compared to $18 billion "
    "at December 31, 2021.",
    "Defense Backlog and Bookings – Backlog was $72 billion as of September 30, 2025 and $63 "
    "billion as of December 31, 2024.",
    # Lockheed Martin's 10-K: a kind of backlog
    "Funded backlog was $88.5 billion at December 31, 2021, as compared to $102.3 billion at "
    "December 31, 2020.",
    # Quanta's 10-Q: two figures listed, the first is RPO
    "Our remaining performance obligations and backlog were $14.37 billion and $31.31 billion as "
    "of June 30, 2024, representing increases of 3.4% and 4.0% relative to December 31, 2023.",
    # Vertiv's October 2022 pre-announcement: dated a month before the quarter end
    "Record backlog of $4.7 billion as of the end of August, a 46% increase since the end of 2021.",
    # Boeing's 10-Q: a line broken mid-sentence ("... resulted in a reduction to")
    "Changes resulted in a reduction to\nbacklog of $3,846 million primarily due to 777X aircraft.",
    # GE Vernova's investor update: RPO described as a measure of backlog
    "About 65% of GE Vernova’s total RPO (remaining performance obligation, a measure of backlog) "
    "of $116 billion at the end of 2023 was for services.",
    # guidance and bounds
    "We expect backlog to reach $20 billion by the end of the year.",
    "Backlog of more than $5 billion supports our outlook.",
])
def test_what_is_not_the_companys_backlog_is_not_read(text):
    assert found(text, "GE Vernova Inc.", form="10-K", key="7", end=date(2025, 12, 31)) == []


# --- rule 5: orders -------------------------------------------------------------------------

def test_orders_need_a_company_subject_and_the_quarter():
    # GE Vernova's Q1 2024 release
    gev = "In the quarter, GE Vernova orders of $9.7 billion were flat, down (1)% organically."
    assert found(gev, "GE Vernova Inc.", form="8-K", key="EX-99.1", end=date(2024, 3, 31)) == [
        ("orders", date(2024, 3, 31), 9.7e9, None)]
    segment = ("Orders of $5.0 billion increased +25%, +24% organically, with higher equipment "
               "orders from increased HA and Aeroderivative gas turbines.")
    assert found(segment, "GE Vernova Inc.", form="8-K", key="EX-99.1") == []
    year = "Total orders of $44.1 billion for the full year increased 9%."
    assert found(year, form="8-K", key="EX-99.1") == []
    # a 10-K's orders are a year's
    assert found(gev, "GE Vernova Inc.", form="10-K", key="7") == []


def test_order_growth_for_the_quarter_year_on_year():
    cases = [
        # Vertiv's releases
        ("Third quarter organic orders (excluding foreign exchange) increased ~17% compared to "
         "last year’s third quarter.", (17.0, "organic")),
        ("•Third quarter orders up 11% (excluding foreign exchange) from last year.",
         (11.0, "organic")),
        ("Vertiv’s fourth quarter orders increased 51.2% compared with the prior year quarter, "
         "benefiting from large project orders.", (51.2, "reported")),
        ("•Strong first quarter trailing twelve-month (TTM) orders growth of ~20%, with first "
         "quarter orders up ~13% compared to last year’s first quarter and up ~21% sequentially",
         (13.0, "reported")),
        ("Third quarter orders were down (4)% compared to the prior year quarter.",
         (-4.0, "reported")),
    ]
    for text, (value, basis) in cases:
        assert found(text, "Vertiv Holdings Co", form="8-K", key="EX-99.1") == [
            ("orders_growth", date(2025, 9, 30), value, basis)], text


@pytest.mark.parametrize("text", [
    # Honeywell's release, in a segment's paragraph: whose orders isn't said
    "Orders grew 19% year over year, the third consecutive quarter of double-digit orders growth.",
    # sequential and trailing figures
    "Third quarter organic orders up 20% sequentially from second quarter 2025.",
    "Strong third quarter trailing twelve-month (TTM) organic orders up 21% compared to prior year "
    "TTM period.",
    # no year-on-year words
    "Q4 orders up 9% (10% organic) driven by strong demand across Industrial Process",
])
def test_order_growth_that_is_not_read(text):
    assert found(text, "Vertiv Holdings Co", form="8-K", key="EX-99.1") == []


# --- rule 6: tables ---------------------------------------------------------------------------

def test_a_total_row_in_the_first_column():
    boeing = fixture("ba-10k-2022-01-31-7.txt")
    assert found(boeing, "BOEING CO", form="10-K", key="7", end=date(2021, 12, 31)) == [
        ("backlog", date(2021, 12, 31), 377_499e6, None)]
    lmt = fixture("lmt-8k-2023-10-17-ex99-1.txt")  # the scale note is two lines above the title
    assert found(lmt, form="8-K", key="EX-99.1", end=date(2023, 9, 24)) == [
        ("backlog", date(2023, 9, 24), 156_034e6, None)]


def test_tables_whose_first_column_isnt_the_total_now():
    # Northrop Grumman: funded, unfunded, total columns
    assert found(fixture("noc-10q-2026-10-20-I-2.txt"), end=date(2026, 9, 30)) == []
    # Emerson: last year's column first; its sentence says both years
    emr = found(fixture("emr-10k-2025-11-10-1.txt"), "EMERSON ELECTRIC CO", form="10-K", key="1",
                end=date(2025, 9, 30))
    assert emr == [("backlog", date(2025, 9, 30), 8.6e9, None),
                   ("backlog", date(2024, 9, 30), 8.4e9, None)]
    # Eaton: a segment table's backlog line, no total
    assert found(fixture("etn-10q-2025-11-04-I-2.txt"), "Eaton Corp plc") == []


# --- rules 8-9: verbatim, one value per date --------------------------------------------------

def test_every_value_is_written_in_its_passage():
    for name in sorted(FIXTURES.glob("*.txt")):
        for f in extract(doc(name.read_text(), end=date(2025, 9, 30))):
            assert f.written in f.passage


def test_a_table_total_beats_a_segment_sentence():
    table = extract(doc(fixture("ba-10k-2022-01-31-7.txt"), form="10-K", key="7",
                        end=date(2021, 12, 31)), "BOEING CO")
    bds = extract(doc(fixture("ba-10k-2022-01-31-7-bds.txt"), form="10-K", key="7",
                      end=date(2021, 12, 31)), "BOEING CO")
    assert [f.value for f in bds] == [59_828e6]  # the defense segment's, worded as a total
    [s, *_] = series(table + bds)
    assert [p["value"] for p in s.points] == [377_499e6]
    assert s.conflicts == [{"period_end": date(2021, 12, 31), "values": [59_828e6, 377_499e6],
                            "kept": 377_499e6}]


def test_agreeing_figures_are_one_point_with_every_source():
    lmt_10k = extract(doc("At December 31, 2023, our backlog was $160.6 billion.", form="10-K",
                          key="7", acc="0000936468-24-000009", filed=date(2024, 1, 23)))
    lmt_8k = extract(doc(fixture("lmt-8k-2023-10-17-ex99-1.txt").replace("156,034", "160,567")
                         .replace("Sept. 24,\n2023", "Dec. 31,\n2023"), form="8-K",
                         key="EX-99.1", end=date(2023, 12, 31), acc="0000936468-24-000004",
                         filed=date(2024, 1, 23)))
    [s, *_] = series(lmt_10k + lmt_8k)
    [p] = s.points
    assert p["value"] == 160_567e6 and p["precision"] == 1e6  # the most precise
    assert [src["written"] for src in p["sources"]] == ["160.6", "160,567"]
    assert p["sources"][0]["citation"]["accession_no"] == "0000936468-24-000009"


def test_equal_figures_that_disagree_leave_the_date_out():
    a = extract(doc("Backlog of $2.9 billion at September 30, 2025 increased 3.8%.",
                    acc="0000000001-25-000001"))
    b = extract(doc("Backlog of $2.0 billion at September 30, 2025 increased 3.9%.",
                    acc="0000000001-25-000001"))
    [s, *_] = series(a + b)
    assert s.points == []
    assert s.conflicts[0]["kept"] is None
    # the same figure in a second filing settles it
    c = extract(doc("Backlog of $2.9 billion, up 20% year over year.", form="8-K", key="EX-99.1",
                    acc="0000000001-25-000002"))
    [s, *_] = series(a + b + c)
    assert [p["value"] for p in s.points] == [2.9e9]


# --- the route --------------------------------------------------------------------------------

VRT_RELEASE = "0001628280-25-049000"
VRT_10Q = "0001628280-25-049100"


class Store:
    def __init__(self, sections=None, n_8ks=1):
        self.calls: list[tuple[str, Any]] = []
        self.sections = sections
        self.n_8ks = n_8ks

    def periodic_reports(self, user_id, company_id):
        self.calls.append(("periodic_reports", company_id))
        return [{"accession_no": VRT_10Q, "form": "10-Q", "filing_date": date(2025, 10, 22),
                 "period_end": date(2025, 9, 30)},
                {"accession_no": "0001628280-25-030000", "form": "10-Q",
                 "filing_date": date(2025, 7, 30), "period_end": date(2025, 6, 30)}]

    def company_8ks(self, user_id, company_id, since):
        self.calls.append(("company_8ks", since))
        rows = [{"accession_no": VRT_RELEASE, "cik": VRT, "form": "8-K",
                 "filing_date": date(2025, 10, 22), "accepted_at": None, "report_date": None,
                 "primary_document": "d.htm", "items": ["2.02", "7.01", "9.01"],
                 "parsed_items": None}]
        rows += [{**rows[0], "accession_no": f"0001628280-24-{n:06d}",
                  "filing_date": date(2024, 1, 1)} for n in range(self.n_8ks - 1)]
        return rows

    def kpi_sections(self, user_id, company_id, since, releases, limit):
        self.calls.append(("kpi_sections", (since, releases, limit)))
        rows = self.sections if self.sections is not None else [
            {"accession_no": VRT_RELEASE, "form": "8-K", "filed": date(2025, 10, 22),
             "period_of_report": date(2025, 10, 22), "section_key": "EX-99.1", "char_start": 120,
             "document_url": "https://www.sec.gov/ex991.htm",
             "text": fixture("vrt-8k-2025-10-22-ex99-1.txt")},
            {"accession_no": VRT_10Q, "form": "10-Q", "filed": date(2025, 10, 22),
             "period_of_report": date(2025, 10, 22), "section_key": "I-2", "char_start": 0,
             "document_url": "https://www.sec.gov/q.htm",
             "text": "Backlog was $9.5 billion at the end of the quarter."}]
        return {"name": "Vertiv Holdings Co", "sections": rows}


def client(store):
    auth = FakeAuthorizer({"bob": {VRT: {"financials", "filings"}}, "alice": {VRT: {"financials"}}})
    return TestClient(create_app(auth, store, SECRET, today=lambda: date(2025, 11, 9)))


def test_route_needs_filings_first(token):
    store = Store()
    r = client(store).get(f"/v1/companies/{VRT}/text-kpis", headers=token("alice"))
    assert r.status_code == 403 and store.calls == []


def test_route_reads_the_release_as_its_quarter(token):
    store = Store()
    r = client(store).get(f"/v1/companies/{VRT}/text-kpis", headers=token("bob"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "rows" not in body and "sections" not in body  # verify.py's keys
    backlog = body["metrics"]["backlog"]
    assert backlog["label"] == "Backlog" and backlog["unit"] == "USD"
    [p] = backlog["points"]
    assert (p["period_end"], p["value"], p["span"]) == ("2025-09-30", 9.5e9, "instant")
    [src] = p["sources"]
    assert src["citation"]["accession_no"] == VRT_RELEASE
    assert src["citation"]["document_url"] == "https://www.sec.gov/ex991.htm"
    assert src["citation"]["char_start"] >= 120 and src["written"] == "9.5"
    # the 10-Q's period equals its filing date (the SEC index quirk): no period, nothing read
    growth = body["metrics"]["orders_growth"]["points"]
    assert [(g["period_end"], g["value"], g["basis"]) for g in growth] == [
        ("2025-09-30", 60.0, "organic")]
    assert body["read"] == {"sections": 2, "filings": 2, "from": "2025-10-22"}
    since, releases, limit = dict(store.calls)["kpi_sections"]
    assert since == date(2015, 11, 9) and releases == [VRT_RELEASE] and limit == 160


def test_route_bounds_the_releases_and_years(token):
    store = Store(n_8ks=200)
    c = client(store)
    assert c.get(f"/v1/companies/{VRT}/text-kpis?years=3", headers=token("bob")).status_code == 200
    _, releases, _ = dict(store.calls)["kpi_sections"]
    assert len(releases) <= MAX_RELEASES < 100
    assert c.get(f"/v1/companies/{VRT}/text-kpis?years=16",
                 headers=token("bob")).status_code == 422


def test_route_with_nothing_stated(token):
    r = client(Store(sections=[])).get(f"/v1/companies/{VRT}/text-kpis", headers=token("bob"))
    body = r.json()
    assert all(m["points"] == [] for m in body["metrics"].values())
    assert body["read"] == {"sections": 0, "filings": 0, "from": None}
