"""Fetch and parse the EDGAR latest-filings Atom feed."""

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime

import httpx

FEED_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
FEED_PARAMS = {"action": "getcurrent", "output": "atom", "count": "100"}  # 100 is the max

_NS = {"a": "http://www.w3.org/2005/Atom"}
# "4 - WhiteFiber, Inc. (0002042022) (Issuer)" -> name, cik, role
_TITLE_RE = re.compile(r"^.+? - (?P<name>.+) \((?P<cik>\d{10})\) \((?P<role>[^)]+)\)$")
_ACCESSION_RE = re.compile(r"accession-number=(\d{10}-\d{2}-\d{6})")


@dataclass(frozen=True)
class Filing:
    """One feed entry. A filing with several filers (e.g. a Form 4's insider and issuer)
    appears once per filer, so the identity is (accession_no, cik), not accession_no alone."""

    accession_no: str
    cik: str
    company_name: str
    role: str  # Issuer, Reporting, Subject, Filed by, Filer
    form_type: str
    filed_at: datetime  # SEC acceptance time, with timezone (midnight ET from daily-index)
    url: str  # filing index page
    source: str = "feed"  # feed: live poller; daily-index: backfill

    @property
    def key(self) -> tuple[str, str]:
        return (self.accession_no, self.cik)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["filed_at"] = self.filed_at.isoformat()
        return d


def fetch_feed(client: httpx.Client) -> bytes:
    """Return the raw Atom XML as bytes (the feed is ISO-8859-1; ElementTree reads the
    declaration). Pass a client from tributary_sec.sec_client."""
    resp = client.get(FEED_URL, params=FEED_PARAMS)
    resp.raise_for_status()
    return resp.content


def parse_feed(xml: bytes) -> list[Filing]:
    """Turn Atom entries into Filing records, oldest first. Malformed entries are skipped."""
    root = ET.fromstring(xml)
    filings = []
    for entry in root.findall("a:entry", _NS):
        filing = _parse_entry(entry)
        if filing is not None:
            filings.append(filing)
    filings.reverse()  # feed is newest first; publish in filing order
    return filings


def _parse_entry(entry: ET.Element) -> Filing | None:
    title = entry.findtext("a:title", "", _NS)
    entry_id = entry.findtext("a:id", "", _NS)
    updated = entry.findtext("a:updated", "", _NS)
    category = entry.find("a:category", _NS)
    link = entry.find("a:link", _NS)

    title_m = _TITLE_RE.match(title.strip())
    acc_m = _ACCESSION_RE.search(entry_id)
    if not (title_m and acc_m and updated and category is not None and link is not None):
        return None

    return Filing(
        accession_no=acc_m.group(1),
        cik=title_m.group("cik"),
        company_name=title_m.group("name"),
        role=title_m.group("role"),
        form_type=category.get("term", ""),
        filed_at=datetime.fromisoformat(updated),
        url=link.get("href", ""),
    )
