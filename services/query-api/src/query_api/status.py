"""The public status page's data (`GET /v1/status/data`, no token): how fresh, complete and
checked the data is. Aggregate counts and timestamps only, from migration 028's SECURITY DEFINER
functions, plus the last recorded runs of the adversarial and citation suites and the golden
10-K figures (`data/recorded_results.json`, written by tests/status/recorded_results.py).

`shape` copies named fields only, so a function that started returning more columns still
couldn't put them on a public route. Results are cached for CACHE_SECONDS per process."""

import json
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from importlib import resources
from typing import Any

from fastapi import FastAPI

CACHE_SECONDS = 60
INGEST_DAYS = 7  # ops.public_ingest's window
COMPLETENESS_DAYS = 30  # ops.public_completeness's window (the nightly check's)


def recorded_results() -> dict:
    """The committed summary of the recorded suites (read once per process)."""
    text = resources.files("query_api").joinpath("data/recorded_results.json").read_text()
    return json.loads(text)


def _pick(row: dict, *keys: str) -> dict:
    return {k: row.get(k) for k in keys}


def _round(x: Any) -> float | None:
    return None if x is None else round(float(x), 1)


def shape(raw: dict, recorded: dict) -> dict:
    ingest, completeness = raw.get("ingest") or {}, raw.get("completeness") or {}
    filings, via_feed = ingest.get("filings") or 0, ingest.get("via_feed") or 0
    quality = raw.get("quality") or []
    first = quality[0] if quality else {}
    return {
        "freshness": _pick(raw.get("freshness") or {}, "last_accepted_at", "last_ingested_at"),
        "ingest": {
            "window_days": INGEST_DAYS,
            "window_start": ingest.get("window_start"),
            "filings": filings,
            "via_feed": via_feed,
            "live_pct": round(100 * via_feed / filings, 1) if filings else None,
            "p50_seconds": _round(ingest.get("p50_seconds")),
            "p95_seconds": _round(ingest.get("p95_seconds")),
        },
        "completeness": {
            "window_days": COMPLETENESS_DAYS,
            "window_start": completeness.get("window_start"),
            "days_checked": completeness.get("days") or 0,
            **_pick(completeness, "indexed", "missing", "republished", "days_with_missing",
                    "last_checked_at"),
        },
        "coverage": _pick(raw.get("coverage") or {}, "companies_with_financials", "documents",
                          "indexed_filings", "sections", "last_indexed_at"),
        "quality": {
            "companies_checked": first.get("companies_checked", 0),
            "last_checked_at": first.get("last_checked_at"),
            "checks": [{"check": r["check_name"], "severity": r["severity"],
                        "findings": r["findings"], "companies": r["companies"]}
                       for r in quality],
        },
        "recorded": recorded,
    }


class Cached:
    """load() at most once per `seconds` (one caller refreshes; the others wait for it)."""

    def __init__(self, load: Callable[[], dict], clock: Callable[[], datetime],
                 seconds: int = CACHE_SECONDS) -> None:
        self._load, self._clock, self._ttl = load, clock, timedelta(seconds=seconds)
        self._lock = threading.Lock()
        self._value: dict | None = None
        self._at: datetime | None = None

    def get(self) -> tuple[dict, datetime]:
        with self._lock:
            now = self._clock()
            if self._value is None or self._at is None or now - self._at >= self._ttl:
                self._value, self._at = self._load(), now
            return self._value, self._at


def add_status_routes(app: FastAPI, *, store: Any, clock: Callable[[], datetime]) -> None:
    recorded = recorded_results()
    cache = Cached(lambda: shape(store.data_status(), recorded), clock)

    @app.get("/v1/status/data", response_model=dict)
    def data_status() -> dict:
        """Public (no token): freshness, ingest latency, completeness against EDGAR, coverage
        counts, data-quality check totals and the last recorded suite runs. Counts and
        timestamps only; never a company, filing, value or text."""
        body, at = cache.get()
        return {"as_of": at.isoformat(), "max_age_seconds": CACHE_SECONDS, **body}
