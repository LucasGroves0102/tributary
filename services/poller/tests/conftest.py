"""EDGAR's latest-filings feed, paged, served from the saved real response (feed.xml).

The saved page holds 40 real entries (21:45 back to 20:07 ET on 2026-10-16) in the feed's own
envelope. `Feed` serves them as EDGAR pages them (`start`, `count`), so a test can list them as
the feed stood at an earlier poll (the newest ones not yet listed, or one listed late) without
building any XML of its own. Nothing here touches the network."""

import re
from pathlib import Path

import httpx
import pytest

FIXTURE = (Path(__file__).parent / "fixtures" / "feed.xml").read_bytes()
_ENTRY = re.compile(rb"<entry>.*?</entry>\s*", re.S)
HEAD = FIXTURE[:_ENTRY.search(FIXTURE).start()]  # type: ignore[union-attr]
ENTRIES = _ENTRY.findall(FIXTURE)  # newest first


class Feed:
    """The feed's current listing: indexes into ENTRIES, newest first. `fail` maps a page's
    `start` to an HTTP status; `paging=False` ignores `start`, as a feed that stopped paging."""

    def __init__(self, listed=None, updated=None, fail=None, paging=True):
        self.listed = list(range(len(ENTRIES))) if listed is None else list(listed)
        self.updated = updated
        self.fail = fail or {}
        self.paging = paging
        self.requests: list[httpx.URL] = []

    def page(self, start: int, count: int) -> bytes:
        head = HEAD
        if self.updated:
            head = re.sub(rb"(</author>\s*<updated>)[^<]*", rb"\g<1>" + self.updated.encode(),
                          head)
        body = b"".join(ENTRIES[i] for i in self.listed[start:start + count])
        return head + body + b"</feed>\n"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url)
        start = int(request.url.params.get("start", "0")) if self.paging else 0
        count = int(request.url.params.get("count", "40"))
        if start in self.fail:
            return httpx.Response(self.fail[start], content=b"error")
        return httpx.Response(200, content=self.page(start, count))

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))


@pytest.fixture
def feed():
    return Feed
