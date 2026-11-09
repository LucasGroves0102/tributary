"""Peers by business (filing_parser/business.py): terms, weights, neighbours and competitor names,
on the openings of real 10-K Item 1 sections (fixtures/business, hermes 2026-11-09)."""

import json
from pathlib import Path

import pytest

from filing_parser import business as b

FIX = Path(__file__).parent / "fixtures" / "business"
INDEX = json.loads((FIX / "index.json").read_text())


def text(key: str) -> str:
    return (FIX / f"{key}.txt").read_text()


def test_terms_are_words_and_two_word_phrases_within_a_run():
    c = b.terms("Our data centers use liquid cooling. Products, power distribution; the UPS 3000")
    assert c["data center"] == 1 and c["liquid cooling"] == 1 and c["power distribution"] == 1
    assert c["product"] == 1 and c["center"] == 1 and c["ups"] == 1
    assert "cooling product" not in c          # a full stop ends a run
    assert "our" not in c and "the" not in c   # stop words
    assert "distribution ups" not in c         # nor does a stop word (the)


def test_normal_makes_simple_singulars():
    assert [b.normal(w) for w in ("products", "batteries", "classes", "business", "status",
                                  "gas", "is")] == ["product", "battery", "class", "business",
                                                    "status", "gas", None]


def test_places_and_people_words_are_not_business():
    c = b.terms("Ohio bank serving Texas customers with a performance culture and diversity")
    assert "ohio" not in c and "texas" not in c and "culture" not in c and "diversity" not in c
    assert "ohio bank" not in c and "performance culture" not in c


def test_own_name_and_tickers_never_count():
    own = b.own_words(["Vertiv Holdings Co", "GS Acquisition Holdings Corp II"], ["VRT"])
    assert {"vertiv", "vrt", "acquisition"} <= own


@pytest.fixture
def small(monkeypatch):
    """Thresholds for a ten-document corpus (in it, a name in two documents is 25% of them)."""
    monkeypatch.setattr(b, "MIN_DF", 2)
    monkeypatch.setattr(b, "MAX_DF_SHARE", 0.6)
    monkeypatch.setattr(b, "COMMON_SHARE", 0.9)


def corpus(keys=("vrt", "etn", "nvt", "deck", "crox", "nke", "jpm", "bac")):
    docs = [b.Doc(INDEX[k]["company_id"], INDEX[k]["accession_no"], INDEX[k]["filed"], "rust", None)
            for k in keys]
    texts = {INDEX[k]["company_id"]: text(k) for k in keys}
    names = [(v["company_id"], v["name"]) for v in INDEX.values()]
    tickers = {v["company_id"]: v["tickers"] for v in INDEX.values()}
    return docs, texts, b.Inputs(docs, names, tickers)


def run(keys=None):
    docs, texts, inputs = corpus(*(keys,) if keys else ())
    return b.compute(lambda ds: ((d, texts[d.company_id]) for d in ds), inputs)


def ids(key):
    return INDEX[key]["company_id"]


def test_vectors_have_length_one_and_drop_structure_phrases(small):
    r = run()
    for v in r.vectors.values():
        assert abs(sum(x * x for x in v.values()) - 1) < 1e-9
    vrt = {r.vocab.names[t] for t in r.vectors[ids("vrt")]}
    assert not vrt & b.STRUCTURE
    assert "vertiv" not in vrt


def test_neighbours_follow_the_business_not_the_sic_code(small):
    r = run()
    first = {k: [n.company_id for n in r.similar[ids(k)][:2]] for k in ("vrt", "deck", "jpm")}
    assert set(first["vrt"]) <= {ids("etn"), ids("nvt")}
    assert ids("crox") in first["deck"] and ids("nke") in first["deck"] + [
        n.company_id for n in r.similar[ids("deck")][:3]]
    assert first["jpm"][0] == ids("bac")
    jpm_bac = r.similar[ids("jpm")][0]
    assert 0 < jpm_bac.similarity <= 1 and jpm_bac.terms
    # symmetric: the cosine is the same both ways
    back = next(n for n in r.similar[ids("bac")] if n.company_id == ids("jpm"))
    assert abs(back.similarity - jpm_bac.similarity) < 1e-6


def test_why_terms_do_not_repeat_themselves():
    assert b._distinct(["liquid cooled", "cooled", "rack", "liquid", "rack power"], 4) == [
        "liquid cooled", "rack", "rack power"]


def test_sentences_keep_lists_of_company_names_together():
    s = ("We compete with niche players (e.g., Delta Electronics, Inc., Stulz GmbH, and Socomec "
         "Holding SA). Eaton is large.\nNext line")
    parts = [s[a:z] for a, z in b.sentences(s)]
    assert parts[0].startswith("We compete") and parts[0].endswith("SA).")
    assert parts[1] == "Eaton is large." and parts[2] == "Next line"


def test_competitors_named_in_item_1_are_found_with_their_sentence(small):
    r = run()
    named = {(m.company_id, m.named_id) for m in r.found}
    assert (ids("vrt"), ids("etn")) in named  # "Eaton Corporation Plc"
    m = next(m for m in r.found if (m.company_id, m.named_id) == (ids("vrt"), ids("etn")))
    sentence = text("vrt")[m.char_start:m.char_end]
    assert "competitors" in sentence and "Eaton" in sentence
    assert all(m.company_id != m.named_id for m in r.found)


def test_names_need_capitals_and_must_not_be_ordinary_words():
    names = b.Names([("1", "Eaton Corp plc"), ("2", "Target Corp"), ("3", "Johnson Controls "
                     "International plc"), ("4", "Apple Inc."), ("5", "Apple Hospitality REIT")],
                    common=frozenset({"target"}))
    assert names.find("We compete with Eaton and Johnson Controls.") == {"1", "3"}
    assert names.find("we compete on eaton road") == set()     # one word, not capitalised
    assert names.find("Our Target market competes") == set()    # an ordinary word
    assert "APPLE" in names.by_name                              # unique once suffixes go...
    assert names.find("Competitors include Apple Hospitality REIT") == {"5"}


def test_normalize_name_matches_silver():
    assert b.normalize_name("Eaton Corporation plc") == "EATON"
    assert b.normalize_name("PROCTER & GAMBLE Co") == "PROCTER AND GAMBLE"
    assert b.normalize_name("COSTCO WHOLESALE CORP /NEW") == "COSTCO WHOLESALE"
