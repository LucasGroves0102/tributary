from datetime import datetime
from pathlib import Path

import httpx

from poller.edgar import fetch_feed, parse_feed, parse_page

FIXTURE = (Path(__file__).parent / "fixtures" / "feed.xml").read_bytes()


def test_parses_every_entry():
    assert len(parse_feed(FIXTURE)) == 40


def test_fields():
    filings = parse_feed(FIXTURE)
    whitefiber = next(f for f in filings if f.key == ("0001213900-26-110436", "0002042022"))
    assert whitefiber.company_name == "WhiteFiber, Inc."
    assert whitefiber.role == "Issuer"
    assert whitefiber.form_type == "4"
    assert whitefiber.accession_no == "0001213900-26-110436"
    assert whitefiber.filed_at == datetime.fromisoformat("2026-10-16T21:42:41-04:00")
    assert whitefiber.url.endswith("0001213900-26-110436-index.htm")


def test_same_filing_appears_once_per_filer():
    filings = parse_feed(FIXTURE)
    same = [f for f in filings if f.accession_no == "0001104659-26-116675"]
    assert {f.role for f in same} == {"Reporting", "Issuer"}
    assert len({f.key for f in same}) == 2


def test_oldest_first():
    filings = parse_feed(FIXTURE)
    assert filings == sorted(filings, key=lambda f: f.filed_at)


def test_skips_malformed_entry():
    xml = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
    <entry><title>garbage</title></entry></feed>"""
    assert parse_feed(xml) == []


def test_to_dict_is_json_ready():
    d = parse_feed(FIXTURE)[0].to_dict()
    assert isinstance(d["filed_at"], str)


def test_page_keeps_feed_order_and_the_feed_time():
    page = parse_page(FIXTURE)
    assert page.updated == datetime.fromisoformat("2026-10-16T21:48:40-04:00")
    assert page.filings == list(reversed(parse_feed(FIXTURE)))  # newest first
    assert page.entries == 40


def test_page_counts_malformed_entries():
    # the feed's end is a page with fewer entries than asked, whether or not each one parses
    xml = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
    <entry><title>garbage</title></entry></feed>"""
    page = parse_page(xml)
    assert (page.filings, page.entries, page.updated) == ([], 1, None)


def test_fetch_feed_pages_with_start_and_count():
    urls = []

    def handle(request):
        urls.append(request.url)
        return httpx.Response(200, content=FIXTURE)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    fetch_feed(client)
    fetch_feed(client, start=200)
    first, third = (dict(u.params) for u in urls)
    assert first == {"action": "getcurrent", "output": "atom", "count": "100"}  # as before
    assert third == {**first, "start": "200"}
