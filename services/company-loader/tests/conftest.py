import json
from pathlib import Path

import pytest

from company_loader import curated
from company_loader.build import parse_tickers

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tickers():
    return parse_tickers(json.loads((FIXTURES / "company_tickers.json").read_text()))


@pytest.fixture
def submissions():
    return {
        p.stem.removeprefix("CIK"): json.loads(p.read_text())
        for p in (FIXTURES / "submissions").glob("CIK*.json")
    }


@pytest.fixture
def inputs(tickers, submissions):
    """Real SEC data (trimmed) plus the real curated files."""
    return dict(
        tickers=tickers,
        submissions=submissions,
        successions=curated.successions(),
        history=curated.ticker_history(),
        tracked=curated.tracked_tickers(),
    )
