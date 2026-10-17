from datetime import date

import pytest

from company_loader import curated
from company_loader.build import Lineage, build, tracked_ciks

GOOGLE_INC, ALPHABET = "0001288776", "0001652044"
EXXON, EXXON_HOLDINGS = "0000034088", "0002115436"
META = "0001326801"


def company(snap, company_id):
    return next(c for c in snap.companies if c.company_id == company_id)


def test_lineage():
    lineage = Lineage(curated.successions())
    assert lineage.root(ALPHABET) == GOOGLE_INC
    assert lineage.members(ALPHABET) == [GOOGLE_INC, ALPHABET]
    assert lineage.valid_range(GOOGLE_INC) == (None, date(2015, 10, 2))
    assert lineage.valid_range(ALPHABET) == (date(2015, 10, 2), None)
    assert lineage.root(META) == META


def test_successor_keeps_predecessors_id(inputs):
    snap = build(**inputs)
    alphabet = company(snap, GOOGLE_INC)
    assert alphabet.current_cik == ALPHABET
    assert alphabet.name == "Alphabet Inc."
    assert alphabet.tracked
    exxon = company(snap, EXXON)
    assert exxon.current_cik == EXXON_HOLDINGS
    assert not any(c.company_id in (ALPHABET, EXXON_HOLDINGS) for c in snap.companies)


def test_every_cik_maps_to_its_root(inputs):
    snap = build(**inputs)
    by_cik = {k.cik: k.company_id for k in snap.ciks}
    assert by_cik[ALPHABET] == by_cik[GOOGLE_INC] == GOOGLE_INC
    assert by_cik[EXXON_HOLDINGS] == by_cik[EXXON] == EXXON


def test_former_names_and_ticker_history(inputs):
    snap = build(**inputs)
    meta_aliases = {(a.alias_type, a.alias): a for a in snap.aliases if a.cik == META}
    facebook = meta_aliases[("name", "Facebook Inc")]
    assert (facebook.valid_from, facebook.valid_to) == (date(2005, 5, 6), date(2021, 10, 27))
    fb = meta_aliases[("ticker", "FB")]
    assert fb.source == "curated"
    assert fb.valid_to == date(2022, 6, 9)


def test_predecessor_name_closes_at_succession(inputs):
    snap = build(**inputs)
    google = next(a for a in snap.aliases if a.cik == GOOGLE_INC and a.alias == "GOOGLE INC.")
    assert google.valid_to == date(2015, 10, 2)


def test_untracked_company(inputs):
    snap = build(**inputs)
    amd = company(snap, "0000002488")
    assert not amd.tracked
    assert amd.sic is None  # submissions are only fetched for tracked companies


def test_current_tickers_snapshot(inputs):
    snap = build(**inputs)
    assert ("ticker", "GOOG", ALPHABET) in snap.current_tickers
    assert ("ticker", "XOM", EXXON_HOLDINGS) in snap.current_tickers
    assert ("name", "Meta Platforms, Inc.", META) in snap.current_tickers


def test_tracked_ciks_include_predecessors(tickers):
    ciks = tracked_ciks(tickers, ["GOOGL", "XOM"], Lineage(curated.successions()))
    assert ciks == {GOOGLE_INC, ALPHABET, EXXON, EXXON_HOLDINGS}


def test_unknown_tracked_ticker_raises(tickers):
    with pytest.raises(ValueError, match="NOPE"):
        tracked_ciks(tickers, ["AAPL", "NOPE"], Lineage([]))
