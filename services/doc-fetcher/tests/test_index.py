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


def test_a_10k_stores_its_annual_report_exhibit_only():
    """A 10-K's EX-13 (the annual report to shareholders, where IBM and Wells Fargo print their
    statements) and nothing else; an 8-K's EX-99.x only; a 10-Q none. The EX-13 page is NVIDIA's
    real FY2026 10-K index with its EX-19.1 row relabelled EX-13 (the shape IBM's index has)."""
    from doc_fetcher.index import parse_exhibits

    fixtures = Path(__file__).parent / "fixtures"
    assert parse_exhibits(INDEX, "10-K") == []  # EX-19.1, 21.1, 23.1, 31, 32, 101: none stored
    raw = (fixtures / "nvda-10-K-index-ex13.htm").read_bytes()
    exhibits = parse_exhibits(raw, "10-K")
    assert [(e.exhibit_type, e.document_name) for e in exhibits] == [
        ("EX-13", "nvda-2026xex13.htm")]
    assert parse_exhibits(raw, "10-Q") == [] and parse_exhibits(raw, "8-K") == []
    assert parse_exhibits((fixtures / "nvda-8-K-index.htm").read_bytes(), "10-K") == []


def test_index_url():
    from doc_fetcher.index import index_url

    assert index_url("0001045810", "0001045810-26-000073") == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000073/"
        "0001045810-26-000073-index.htm")
