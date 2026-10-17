from datetime import datetime
from pathlib import Path

from poller.edgar import parse_feed

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
