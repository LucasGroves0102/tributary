"""The public status page's data (`GET /v1/status/data`, no token): how fresh, complete and
checked the data is. Aggregate counts and timestamps only, from migration 028's SECURITY DEFINER
functions (072: the checks' tiers, findings per 1,000 figures, and the parser-output checks'
`ops.public_parser_quality`), plus the last recorded runs of the adversarial and citation suites
and the golden 10-K figures (`data/recorded_results.json`, written by
tests/status/recorded_results.py).

Tiers (ops.quality_tier) say what a finding means for a reader: `error` (a figure shown that can't
be right), `warning` (one to double-check), `kept_out` (a figure gold already leaves out),
`coverage` (a figure missing, not wrong), `info` (restatements, swings on lumpy lines, sums against
restated years). An older database without them reads every check by its severity.

`gaps` (076): the coverage sweep's open gaps per kind and reason over the listed companies it
measured (`ops.public_coverage_gaps`): counts only, never a company.

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


def _rate(x: Any) -> float | None:
    """Findings per 1,000 figures or documents: three decimals (0.001 is one in a million)."""
    return None if x is None else round(float(x), 3)


def _per_1000(part: Any, whole: Any) -> float | None:
    return _rate(1000 * part / whole) if part is not None and whole else None


def _quality(rows: list[dict]) -> dict:
    first = rows[0] if rows else {}
    return {
        "companies_checked": first.get("companies_checked", 0),
        "last_checked_at": first.get("last_checked_at"),
        # gold figures (quarters, TTM and computed) of the companies checked: per_1000's base
        "figures": first.get("figures"),
        "checks": [{"check": r["check_name"], "severity": r["severity"],
                    "tier": r.get("tier") or r["severity"],
                    "findings": r["findings"], "companies": r["companies"],
                    "per_1000": _rate(r.get("per_1000"))}
                   for r in rows],
    }


def _parser(rows: list[dict]) -> dict:
    first = rows[0] if rows else {}
    checked = first.get("documents_checked", 0)
    return {
        "documents_checked": checked,
        "last_checked_at": first.get("last_checked_at"),
        "checks": [{"check": r["check_name"], "severity": r["severity"],
                    "documents": r["documents"], "per_1000": _per_1000(r["documents"], checked)}
                   for r in rows],
    }


def _gaps(rows: list[dict]) -> dict:
    first = rows[0] if rows else {}
    return {
        "listed": first.get("listed"),
        "refreshed_at": first.get("refreshed_at"),
        "kinds": [{"kind": r["kind"], "reason": r["reason"], "companies": r["companies"]}
                  for r in rows],
    }


def shape(raw: dict, recorded: dict) -> dict:
    ingest, completeness = raw.get("ingest") or {}, raw.get("completeness") or {}
    filings, via_feed = ingest.get("filings") or 0, ingest.get("via_feed") or 0
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
                          "indexed_filings", "sections", "last_indexed_at", "catalog_companies"),
        "quality": _quality(raw.get("quality") or []),
        "parser": _parser(raw.get("parser") or []),
        "gaps": _gaps(raw.get("gaps") or []),
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
        counts, data-quality check totals by tier (with findings per 1,000 figures), the
        parser-output checks, the coverage sweep's gaps per kind and reason, and the last recorded
        suite runs. Counts and timestamps only; never a company, filing, value or text."""
        body, at = cache.get()
        return {"as_of": at.isoformat(), "max_age_seconds": CACHE_SECONDS, **body}
