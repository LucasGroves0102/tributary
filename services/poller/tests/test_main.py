from datetime import datetime

from poller.edgar import Filing
from poller.main import SeenSet, new_filings

T0 = datetime.fromisoformat("2026-10-16T12:00:00-04:00")


def filing(acc: str, cik: str) -> Filing:
    return Filing(acc, cik, "Co", "Issuer", "8-K", T0, "u")


def test_new_filings_skips_seen():
    seen = SeenSet()
    batch = [filing("a", "1"), filing("a", "2")]
    assert new_filings(batch, seen) == batch
    assert new_filings(batch + [filing("b", "1")], seen) == [filing("b", "1")]


def test_seen_set_is_bounded():
    seen = SeenSet(maxlen=2)
    for k in [("a", "1"), ("b", "1"), ("c", "1")]:
        seen.add_if_new(k)
    assert seen.add_if_new(("a", "1"))  # evicted, so new again
    assert not seen.add_if_new(("c", "1"))
