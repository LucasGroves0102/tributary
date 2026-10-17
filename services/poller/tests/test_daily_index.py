import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from poller.backfill import days_in_range, quarters
from poller.daily_index import available_days, parse_master

FIXTURES = Path(__file__).parent / "fixtures"
MASTER = (FIXTURES / "master.20261016.idx").read_bytes()


def test_parses_every_row():
    filings = parse_master(MASTER)
    rows = [line for line in MASTER.decode().splitlines()[7:] if line]
    assert len(filings) == len(rows) == 19


def test_fields():
    f = next(f for f in parse_master(MASTER) if f.cik == "0002042022")
    assert f.accession_no == "0001213900-26-110436"
    assert f.company_name == "WhiteFiber, Inc."
    assert f.form_type == "4"
    assert f.role == "Unknown"
    assert f.source == "daily-index"
    assert f.filed_at == datetime(2026, 10, 16, tzinfo=ZoneInfo("America/New_York"))
    assert f.url == ("https://www.sec.gov/Archives/edgar/data/2042022/000121390026110436/"
                     "0001213900-26-110436-index.htm")


def test_one_row_per_filer_matches_feed_identity():
    # the same Form 4 as the feed fixture: insider and issuer, same accession
    keys = {f.key for f in parse_master(MASTER) if f.accession_no == "0001213900-26-110436"}
    assert keys == {("0001213900-26-110436", "0002042022"),
                    ("0001213900-26-110436", "0002058414")}


def test_pipe_in_company_name():
    raw = b"header\n--------\n123|A|B Corp|8-K|20261016|edgar/data/123/0000000123-26-000001.txt\n"
    f = parse_master(raw)[0]
    assert (f.company_name, f.form_type, f.cik) == ("A|B Corp", "8-K", "0000000123")


def test_not_an_index():
    with pytest.raises(ValueError, match="not a master index"):
        parse_master(b"<html>Access Denied</html>")


def test_available_days_from_listing():
    listing = json.loads((FIXTURES / "daily-index-qtr4.json").read_text())
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, json=listing))
    client = httpx.Client(transport=transport)
    days = available_days(client, 2026, 4)
    assert date(2026, 10, 1) in days
    assert date(2026, 10, 3) not in days  # Saturday


def test_range_spanning_quarters():
    days = days_in_range(date(2026, 9, 30), date(2026, 10, 2))
    assert len(days) == 3
    assert quarters(days) == {(2026, 3), (2026, 4)}
