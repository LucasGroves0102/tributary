"""Small response helpers shared by app.py and ui.py."""

from urllib.parse import quote

EDGAR = "https://www.sec.gov/Archives/edgar/data"


def filing_url(cik: str | None, accession_no: str) -> str | None:
    """EDGAR filing index, e.g. https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/"""
    if not cik:
        return None
    return f"{EDGAR}/{int(cik)}/{accession_no.replace('-', '')}/"


def document_url(cik: str | None, accession_no: str, document: str | None) -> str | None:
    """A filing's primary document on EDGAR (public), from the submissions index."""
    base = filing_url(cik, accession_no)
    if base is None or not document:
        return None
    return base + quote(document)


def period(prefix: str, year: int | None, quarter: int | None) -> str | None:
    """e.g. FY2026 Q1, CY2025 Q4; None when the fiscal year end is unknown."""
    return f"{prefix}{year} Q{quarter}" if year and quarter else None
