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


def write_zip(path: Path, entries: dict[str, bytes]) -> Path:
    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


@pytest.fixture
def bulk_entries() -> dict[str, bytes]:
    """Trimmed real submissions.zip entries: Apple (with one page), an insider, an empty filer."""
    return {p.name: p.read_bytes() for p in sorted((FIXTURES / "bulk").glob("CIK*.json"))}


@pytest.fixture
def bulk_zip(tmp_path, bulk_entries) -> Path:
    return write_zip(tmp_path / "submissions.zip", bulk_entries)
