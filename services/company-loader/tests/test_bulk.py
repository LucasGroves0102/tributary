from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from company_loader.bulk import SUBMISSIONS_ZIP_URL, cik_entries, download, run_rounds

LAST_MODIFIED = "Sat, 17 Oct 2026 07:12:03 GMT"


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_streams_to_file(tmp_path):
    def handler(req):
        assert str(req.url) == SUBMISSIONS_ZIP_URL and "If-Modified-Since" not in req.headers
        return httpx.Response(200, stream=httpx.ByteStream(b"PK zip bytes"),
                              headers={"Last-Modified": LAST_MODIFIED})

    dest = tmp_path / "submissions.zip"
    assert download(client(handler), dest, None) == datetime(2026, 10, 17, 7, 12, 3, tzinfo=UTC)
    assert dest.read_bytes() == b"PK zip bytes"


def test_download_skips_unchanged(tmp_path):
    seen = {}

    def handler(req):
        seen["since"] = req.headers["If-Modified-Since"]
        return httpx.Response(304)

    since = datetime(2026, 10, 17, 7, 12, 3, tzinfo=UTC)
    assert download(client(handler), tmp_path / "submissions.zip", since) is None
    assert seen["since"] == LAST_MODIFIED
    assert not (tmp_path / "submissions.zip").exists()


def test_if_modified_since_from_a_non_utc_timestamp(tmp_path):
    seen = {}

    def handler(req):
        seen["since"] = req.headers["If-Modified-Since"]
        return httpx.Response(304)

    # Postgres returns timestamptz in the session's time zone
    since = datetime(2026, 10, 17, 0, 12, 3, tzinfo=timezone(timedelta(hours=-7)))
    assert download(client(handler), tmp_path / "submissions.zip", since) is None
    assert seen["since"] == LAST_MODIFIED


def test_truncated_download_keeps_no_file(tmp_path):
    def handler(req):
        return httpx.Response(200, stream=httpx.ByteStream(b"PK"), headers={"Content-Length": "10"})

    with pytest.raises(OSError, match="truncated"):
        download(client(handler), tmp_path / "submissions.zip", None)
    assert not (tmp_path / "submissions.zip").exists()


def test_errors_raise(tmp_path):
    with pytest.raises(httpx.HTTPStatusError):
        download(client(lambda req: httpx.Response(403)), tmp_path / "submissions.zip", None)


def test_pages_follow_their_main_entry(bulk_zip):
    with bulk_zip.open("rb") as f:
        groups = {cik: [e.name for e in es] for cik, es in cik_entries(f)}
    assert groups == {
        "0000004926": ["CIK0000004926.json"],
        "0000320193": ["CIK0000320193.json", "CIK0000320193-submissions-001.json"],
        "0001801661": ["CIK0001801661.json"],
    }


def test_rounds_pause_between_runs_and_stop_once_complete():
    results = iter([{"loaded": 500, "complete": False}, {"loaded": 3, "complete": True}])
    paused = []
    counts = run_rounds(lambda: next(results), rounds=4, pause_seconds=300, sleep=paused.append)
    assert counts == {"loaded": 3, "complete": True}
    assert paused == [300]
