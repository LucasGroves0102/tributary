from datetime import UTC, datetime

from tributary_sec import acceptance_time


def test_acceptance_time_is_shifted_back_to_utc():
    # filings.raw had this Form 4 at 2026-10-17T01:56:48Z (21:56:48 EDT)
    assert acceptance_time("2026-10-17T05:56:48.000Z") == datetime(2026, 10, 17, 1, 56, 48,
                                                                   tzinfo=UTC)
    # EST is 5 hours behind: 16:00 EST shows as 02:00Z the next day
    assert acceptance_time("2026-01-15T02:00:00.000Z") == datetime(2026, 1, 14, 21, 0, tzinfo=UTC)
    assert acceptance_time("") is None
    assert acceptance_time(None) is None
