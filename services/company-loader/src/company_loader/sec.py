"""Fetch company reference data from the SEC."""

import httpx

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


def fetch_tickers(client: httpx.Client) -> dict:
    resp = client.get(TICKERS_URL)
    resp.raise_for_status()
    return resp.json()


def fetch_submissions(client: httpx.Client, cik: str) -> dict | None:
    """None if the SEC has no record for this CIK."""
    resp = client.get(SUBMISSIONS_URL.format(cik=cik))
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()
