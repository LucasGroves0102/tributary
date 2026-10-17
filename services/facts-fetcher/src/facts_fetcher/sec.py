"""Fetch XBRL company facts from data.sec.gov."""

import httpx

COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def fetch_company_facts(client: httpx.Client, cik: str) -> bytes | None:
    """Raw JSON bytes, or None if the SEC has no XBRL facts for this CIK."""
    resp = client.get(COMPANY_FACTS_URL.format(cik=cik))
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content
