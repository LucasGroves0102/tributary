"""SEC timestamp quirks."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def acceptance_time(s: str | None) -> datetime | None:
    """Submissions' acceptanceDateTime (data.sec.gov API and submissions.zip) is labelled UTC
    but is ahead of UTC by the US Eastern offset: a filing accepted at 2026-10-16 21:56:48 EDT
    (01:56:48Z) appears as 2026-10-17T05:56:48.000Z. Returns the real (UTC) time."""
    if not s:
        return None
    shifted = datetime.fromisoformat(s)
    offset = shifted.astimezone(EASTERN).utcoffset() or timedelta(0)
    return shifted + offset
