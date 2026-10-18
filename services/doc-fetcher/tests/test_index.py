from datetime import date
from pathlib import Path

import pytest

from doc_fetcher.index import parse_index

INDEX = (Path(__file__).parent / "fixtures" / "nvda-10-K-index.htm").read_bytes()


def test_primary_document():
    idx = parse_index(INDEX, "10-K")
    assert idx.document_name == "nvda-20260125.htm"
    assert idx.url == ("https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/"
                       "nvda-20260125.htm")  # inline-XBRL viewer prefix stripped
    assert idx.filed == date(2026, 2, 25)
    assert idx.period_of_report == date(2026, 1, 25)


def test_wrong_form_raises():
    with pytest.raises(ValueError, match="no 10-Q document"):
        parse_index(INDEX, "10-Q")


def test_exhibits_from_8k_index():
    from doc_fetcher.index import parse_exhibits

    raw = (Path(__file__).parent / "fixtures" / "nvda-8-K-index.htm").read_bytes()
    exhibits = parse_exhibits(raw)
    assert [(e.exhibit_type, e.document_name) for e in exhibits] == [
        ("EX-99.1", "q2fy27pr.htm"), ("EX-99.2", "q2fy27cfocommentary.htm")]
    assert exhibits[0].url == ("https://www.sec.gov/Archives/edgar/data/1045810/"
                               "000104581026000073/q2fy27pr.htm")
    assert exhibits[0].description is None  # the index repeats the type


def test_index_url():
    from doc_fetcher.index import index_url

    assert index_url("0001045810", "0001045810-26-000073") == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000073/"
        "0001045810-26-000073-index.htm")
