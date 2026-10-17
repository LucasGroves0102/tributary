"""EDGAR daily master index: every filing disseminated on a day, one row per filer."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import httpx

from poller.edgar import Filing

BASE = "https://www.sec.gov/Archives/edgar/daily-index"
EASTERN = ZoneInfo("America/New_York")


def quarter_url(year: int, quarter: int) -> str:
    return f"{BASE}/{year}/QTR{quarter}"


def available_days(client: httpx.Client, year: int, quarter: int) -> set[date]:
    """Days with a master index. Missing days return 403, not 404, so list instead of probe."""
    resp = client.get(f"{quarter_url(year, quarter)}/index.json")
    if resp.status_code == 404:  # quarter not started
        return set()
    resp.raise_for_status()
    names = (i["name"] for i in resp.json()["directory"]["item"])
    return {
        datetime.strptime(n, "master.%Y%m%d.idx").date()
        for n in names
        if n.startswith("master.") and n.endswith(".idx")
    }


def fetch_master(client: httpx.Client, day: date) -> bytes:
    quarter = (day.month - 1) // 3 + 1
    resp = client.get(f"{quarter_url(day.year, quarter)}/master.{day:%Y%m%d}.idx")
    resp.raise_for_status()
    return resp.content


def parse_master(raw: bytes) -> list[Filing]:
    """A free-text header ending in a line of dashes, then rows like
    2042022|WhiteFiber, Inc.|4|20261016|edgar/data/2042022/0001213900-26-110436.txt"""
    lines = raw.decode("latin-1").splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("-----")) + 1
    except StopIteration:
        raise ValueError("not a master index: no header separator") from None
    filings = []
    for line in lines[start:]:
        fields = line.split("|")
        if len(fields) < 5:
            continue
        # split from both ends: the company name is the only field that could contain "|"
        cik, form, filed, path = fields[0], fields[-3], fields[-2], fields[-1]
        name = "|".join(fields[1:-3])
        accession = path.rsplit("/", 1)[-1].removesuffix(".txt")
        filings.append(Filing(
            accession_no=accession,
            cik=cik.zfill(10),
            company_name=name,
            role="Unknown",  # the index doesn't say
            form_type=form,
            filed_at=datetime.combine(datetime.strptime(filed, "%Y%m%d").date(), time(),
                                      EASTERN),
            url=(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                 f"{accession.replace('-', '')}/{accession}-index.htm"),
            source="daily-index",
        ))
    return filings
