"""Outside uptime check for tributary.lucasgroves.dev, run by .github/workflows/uptime.yml.

Checks the web app, both APIs' /health and the pipeline's freshness (/v1/status: when the
newest filing was ingested). A failed request is retried once before it counts. Writes a
Markdown report and `down=true|false` / `title=...` to $GITHUB_OUTPUT; always exits 0 (the
workflow turns the result into an issue).

Freshness: EDGAR publishes filings on business days, 6 am to 10 pm ET, and is closed on
weekends and federal holidays. During weekday hours (10 am to 5 pm ET, non-holiday) the newest
filing must have been ingested in the last STALE_BUSINESS; otherwise in the last STALE_ANY
(covers a long weekend). Standard library only: the runner needs no installs.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

BASE_URL = "https://tributary.lucasgroves.dev"
ET = ZoneInfo("America/New_York")
STALE_BUSINESS = timedelta(hours=3)
STALE_ANY = timedelta(days=4)
TIMEOUT_S = 20
RETRY_AFTER_S = 30


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


Fetch = Callable[[str], Response]


def fetch(url: str) -> Response:
    req = urllib.request.Request(url, headers={"User-Agent": "tributary-uptime-check"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return Response(r.status, r.read())
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return Response(0, str(e).encode())


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th (1-based; -1 = last) weekday (Mon=0) of a month."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = (date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """A holiday on Saturday is observed on Friday, on Sunday on Monday."""
    shift = {5: -1, 6: 1}.get(d.weekday(), 0)
    return d + timedelta(days=shift)


def federal_holidays(year: int) -> set[date]:
    """US federal holidays (observed dates), when EDGAR doesn't publish."""
    return {
        _observed(date(year, 1, 1)), _nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3),
        _nth_weekday(year, 5, 0, -1), _observed(date(year, 6, 19)), _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 10, 0, 2), _observed(date(year, 11, 11)),
        _nth_weekday(year, 11, 3, 4), _observed(date(year, 12, 25)),
    }


def business_hours(now: datetime) -> bool:
    et = now.astimezone(ET)
    return (et.weekday() < 5 and et.date() not in federal_holidays(et.year)
            and 10 <= et.hour < 17)


def stale(last_ingested: datetime, now: datetime) -> str | None:
    """Why the pipeline looks stalled, or None."""
    age = now - last_ingested
    limit = STALE_BUSINESS if business_hours(now) else STALE_ANY
    if age > limit:
        return (f"newest filing ingested {_ago(age)} ago (limit {_ago(limit)}"
                f"{' during EDGAR business hours' if limit == STALE_BUSINESS else ''})")
    return None


def _ago(d: timedelta) -> str:
    h = d.total_seconds() / 3600
    return f"{h / 24:.1f} days" if h >= 48 else f"{h:.1f} h"


def check(base: str, now: datetime, get: Fetch = fetch,
          sleep: Callable[[float], None] = time.sleep) -> tuple[list[str], list[str]]:
    """(problems, checks passed). Each target is retried once after RETRY_AFTER_S."""
    targets = [("web app", "/", lambda r: b"Tributary" in r.body),
               ("Query API", "/api/query/health", None),
               ("agent API", "/api/agent/health", None),
               ("pipeline status", "/api/query/v1/status", None)]
    results = {name: get(base + path) for name, path, _ in targets}

    def ok(name: str, extra: Callable[[Response], bool] | None) -> bool:
        r = results[name]
        return r.status == 200 and (extra is None or extra(r))

    if not all(ok(n, x) for n, _, x in targets):
        sleep(RETRY_AFTER_S)
        for name, path, extra in targets:
            if not ok(name, extra):
                results[name] = get(base + path)

    problems, passed = [], []
    for name, path, extra in targets:
        r = results[name]
        if ok(name, extra):
            passed.append(f"{name} ({path}): 200")
        else:
            detail = f"HTTP {r.status}" if r.status else r.body.decode(errors="replace")[:200]
            problems.append(f"{name} ({path}): {detail}")
    if ok("pipeline status", None):
        try:
            last = json.loads(results["pipeline status"].body)["last_ingested_at"]
            why = stale(datetime.fromisoformat(last), now) if last else "no filings ingested"
        except (ValueError, KeyError, TypeError) as e:
            why = f"unreadable /v1/status: {e}"
        if why:
            problems.append(f"pipeline freshness: {why}")
        else:
            passed.append(f"pipeline freshness: last filing ingested {last}")
    return problems, passed


def title(problems: list[str]) -> str:
    if any(not p.startswith("pipeline freshness") for p in problems):
        return "Tributary is down"
    return "Tributary pipeline is stale"


def report(base: str, now: datetime, problems: list[str], passed: list[str]) -> str:
    lines = [f"Checked {base} at {now.isoformat(timespec='seconds')} from GitHub Actions.", ""]
    if problems:
        lines += ["**Failing**", *[f"- {p}" for p in problems], ""]
    if passed:
        lines += ["**Passing**", *[f"- {p}" for p in passed], ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=os.environ.get("UPTIME_BASE_URL", BASE_URL))
    ap.add_argument("--report", default="uptime-report.md")
    args = ap.parse_args(argv)
    now = datetime.now(UTC)
    problems, passed = check(args.base, now)
    text = report(args.base, now, problems, passed)
    with open(args.report, "w") as f:
        f.write(text)
    print(text)
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"down={'true' if problems else 'false'}\ntitle={title(problems)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
