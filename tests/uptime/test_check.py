"""deploy/uptime/check.py: what counts as down or stale. No network: fetch is injected."""

import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "uptime_check", Path(__file__).parents[2] / "deploy" / "uptime" / "check.py")
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

# Wed 2026-10-28 14:00 ET (18:00 UTC): EDGAR business hours
WEDNESDAY = datetime(2026, 10, 28, 18, 0, tzinfo=UTC)
SATURDAY = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)


def site(status=None, last_ingested=WEDNESDAY - timedelta(minutes=10)):
    status = status or {}
    calls = []

    def get(url):
        calls.append(url)
        path = url.removeprefix("https://t.example")
        code = status.get(path, 200)
        if path == "/":
            return check.Response(code, b"<title>Tributary</title>")
        if path == "/api/query/v1/status":
            iso = last_ingested.isoformat() if last_ingested else None
            return check.Response(code, json.dumps({"last_ingested_at": iso}).encode())
        return check.Response(code, b'{"status":"ok"}')

    return get, calls


def run(get, now=WEDNESDAY):
    sleeps = []
    problems, passed = check.check("https://t.example", now, get, sleeps.append)
    return problems, passed, sleeps


def test_all_up():
    get, calls = site()
    problems, passed, sleeps = run(get)
    assert problems == [] and len(passed) == 5 and sleeps == [] and len(calls) == 4


def test_a_failure_is_retried_once_and_a_recovery_counts_as_up():
    get, calls = site({"/api/agent/health": 502})
    problems, _, sleeps = run(get)
    assert sleeps == [check.RETRY_AFTER_S]
    assert calls.count("https://t.example/api/agent/health") == 2
    assert problems == ["agent API (/api/agent/health): HTTP 502"]
    assert check.title(problems) == "Tributary is down"

    flaky = iter([check.Response(502, b""), check.Response(200, b"ok")])
    def once_bad(url):
        return next(flaky) if url.endswith("/api/agent/health") else get(url)
    assert run(once_bad)[0] == []


def test_web_app_must_be_tributary_not_just_200():
    def get(url):
        return check.Response(200, b"parked domain") if url.endswith("/") else site()[0](url)
    assert run(get)[0] == ["web app (/): HTTP 200"]


def test_unreachable_reports_the_error():
    def get(url):
        return check.Response(0, b"<urlopen error timed out>")
    problems = run(get)[0]
    assert len(problems) == 4 and "timed out" in problems[0]


def test_stale_in_business_hours_but_not_on_a_weekend():
    get, _ = site(last_ingested=WEDNESDAY - timedelta(hours=4))
    problems = run(get)[0]
    assert problems == ["pipeline freshness: newest filing ingested 4.0 h ago "
                        "(limit 3.0 h during EDGAR business hours)"]
    assert check.title(problems) == "Tributary pipeline is stale"

    get, _ = site(last_ingested=SATURDAY - timedelta(hours=40))  # Friday's last filings
    assert run(get, SATURDAY)[0] == []
    get, _ = site(last_ingested=SATURDAY - timedelta(days=5))
    assert "5.0 days" in run(get, SATURDAY)[0][0]


def test_no_filings_at_all_is_stale():
    get, _ = site(last_ingested=None)
    assert run(get)[0] == ["pipeline freshness: no filings ingested"]


def test_federal_holidays_are_not_business_hours():
    assert date(2026, 11, 26) in check.federal_holidays(2026)  # Thanksgiving
    assert date(2026, 7, 3) in check.federal_holidays(2026)    # July 4 is a Saturday
    assert date(2027, 5, 31) in check.federal_holidays(2027)   # Memorial Day (last Monday)
    thanksgiving = datetime(2026, 11, 26, 18, 0, tzinfo=UTC)
    assert not check.business_hours(thanksgiving)
    assert check.business_hours(WEDNESDAY)
    assert not check.business_hours(datetime(2026, 10, 28, 23, 0, tzinfo=UTC))  # 7 pm ET


def test_main_writes_report_and_outputs(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(check, "check", lambda base, now: (["agent API (/x): HTTP 502"], ["ok"]))
    assert check.main(["--base", "https://t.example", "--report", str(tmp_path / "r.md")]) == 0
    assert out.read_text() == "down=true\ntitle=Tributary is down\n"
    assert "**Failing**\n- agent API (/x): HTTP 502" in (tmp_path / "r.md").read_text()
