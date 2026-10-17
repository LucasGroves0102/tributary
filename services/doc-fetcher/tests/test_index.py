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
