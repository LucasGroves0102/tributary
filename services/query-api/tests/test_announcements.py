"""query_api.announcements over real 8-Ks (fixtures/announcements: the openings events.py reads,
from production on 2026-11-09): a completed deal or a new financing announced under 7.01 / 8.01
flags; an agreement, an earnings release, a buyback, a dividend, a proposed offering don't."""

import json
from pathlib import Path

import pytest

from query_api.announcements import read, read_sentence, sentences

FIXTURES = Path(__file__).parent / "fixtures" / "announcements"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def found(name: str) -> list[tuple[str, str, str, str]]:
    f = fixture(name)
    return [(a.kind, a.item, a.section_key, a.words) for a in read(f["items"], f["heads"])]


@pytest.mark.parametrize("name, expected", [
    # Vertiv's 8-K of Oct 26, 2026: Item 7.01 only, announcing the closed KES acquisition
    ("vrt-2026-10-26-kes-completed", [("deal", "7.01", "EX-99.1", "Completes Acquisition")]),
    ("vrt-2026-04-27-stl-acquired", [("deal", "7.01", "EX-99.1", "has acquired")]),
    ("bx-2026-10-27-notes-priced",
     [("financing", "8.01", "EX-99.1", "Prices $1.1 Billion Senior Notes")]),
    ("lvs-2026-10-28-notes-priced",
     [("financing", "8.01", "EX-99.1", "PRICING OF $1.0 BILLION OF SENIOR UNSECURED NOTES")]),
    # "the pricing of the Offering" after the 8.01 text named the notes
    ("tko-2026-11-05-offering-priced",
     [("financing", "8.01", "8.01", "pricing of the Offering")]),
])
def test_completed_deals_and_new_financings_flag(name, expected):
    assert found(name) == expected


@pytest.mark.parametrize("name", [
    "vrt-2026-09-24-kes-agreement",  # "entered into an agreement to acquire" the same company
    "vrt-2026-09-02-uig-agreement",  # agreement and plan of merger (1.01 + 7.01)
    "vrt-2026-03-24-thermokey-agreement",  # "Vertiv to Acquire", "Upon closing, ..."
    "vrt-2026-10-28-earnings",  # 2.02 + 7.01: an earnings release is never read
    "uthr-2026-10-22-buyback",  # a new $2 billion share repurchase authorization (8.01)
    "vrt-2026-09-02-dividend",
    "bwxt-2026-11-04-offering-proposed",  # "launched an offering", tender offers
    "nfg-2026-10-separation-planned",  # "expected to be completed in the second half of 2027"
])
def test_agreements_releases_buybacks_and_plans_do_not(name):
    assert found(name) == []


def test_the_answer_quotes_the_sentence_with_its_offsets():
    f = fixture("vrt-2026-10-26-kes-completed")
    [a] = read(f["items"], f["heads"])
    text = f["heads"]["EX-99.1"]
    assert text[a.start:a.end].startswith("Vertiv Completes Acquisition of King Environmental")
    assert a.sentence == " ".join(text[a.start:a.end].split())
    assert a.to_dict() == {"kind": "deal", "item": "7.01", "section_key": "EX-99.1",
                           "words": "Completes Acquisition", "sentence": a.sentence}


def test_an_item_the_8k_has_already_flags_its_kind():
    f = fixture("vrt-2026-10-26-kes-completed")
    assert read(["2.01", "7.01", "9.01"], f["heads"]) == []  # 2.01 flags it already
    assert read(["2.03", "7.01", "9.01"], f["heads"])[0].kind == "deal"
    assert read(["9.01"], f["heads"]) == []  # neither 7.01 nor 8.01


@pytest.mark.parametrize("sentence, kind, words", [
    ("Acme has completed its previously announced acquisition of Widget Co.", "deal",
     "has completed its previously announced acquisition"),
    ("Acme closed the sale of its industrial segment to Buyer for $2.0 billion.", "deal",
     "closed the sale"),
    ("Acme closed the sale of $500 million of 5.0% senior notes due 2031.", "financing",
     "closed the sale of $500 million of 5.0% senior notes"),
    ("On May 1, Acme entered into a new $750 million term loan facility.", "financing",
     "entered into a new $750 million term loan facility"),
    ("Acme borrowed $300 million under its revolving credit facility.", "financing",
     "borrowed $300 million under its revolving"),
])
def test_phrases_that_count(sentence, kind, words):
    assert read_sentence(sentence, kind) == words


@pytest.mark.parametrize("sentence, kind", [
    ("The acquisition is expected to close in the fourth quarter.", "deal"),
    ("Upon completion of the acquisition, Widget will become part of Acme.", "deal"),
    ("Acme expects to complete the acquisition by year end.", "deal"),
    ("The completion of the merger is subject to customary closing conditions.", "deal"),
    ("Acme announced the proposed offering of senior notes.", "financing"),
    ("Acme intends to use the net proceeds from the issuance of the notes to repay debt.",
     "financing"),
    ("Acme entered into an amendment to its credit agreement extending the maturity.",
     "financing"),
    ("Acme raised its full-year outlook and expects free cash flow of $1.2 billion.",
     "financing"),
    ("Acme completed the sale of 5,000,000 shares of common stock.", "deal"),
])
def test_phrases_that_do_not(sentence, kind):
    assert read_sentence(sentence, kind) is None


def test_sentences_keep_a_headline_apart_and_join_wrapped_lines():
    text = ("Vertiv Completes Acquisition of KES\nAcquisition expands Vertiv\n"
            "Acme today announced the pricing of $900 million\naggregate principal amount of "
            "notes. The offering is expected to close on May 3.")
    got = [" ".join(text[a:b].split()) for a, b in sentences(text)]
    assert got == ["Vertiv Completes Acquisition of KES", "Acquisition expands Vertiv",
                   "Acme today announced the pricing of $900 million aggregate principal "
                   "amount of notes.", "The offering is expected to close on May 3."]
